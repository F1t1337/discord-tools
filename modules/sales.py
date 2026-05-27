import json
import logging
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class SalesManager:
    """
    Модуль продажи Discord-токенов.

    Workflow:
    1. Submit в tokenbuyrobot → ждать цену (PENDING_USER / expected_payment)
    2. Подтвердить tokenbuyrobot (action=sell) → ждать COMPLETED / final_payment
    3. Повторно очистить те же токены (re_clean_callback)
    4. Submit в tskupka с skip_confirmation=true → ждать завершения
    5. Записать результаты в Google Sheets
    6. Уведомление о завершении круга
    """

    # ── internal statuses ───────────────────────────────────────
    PENDING_STATUS = "PENDING"
    TOKENBUYROBOT_PRICE_PENDING_STATUS = "TOKENBUYROBOT_PRICE_PENDING"
    TOKENBUYROBOT_COMPLETION_PENDING_STATUS = "TOKENBUYROBOT_COMPLETION_PENDING"
    RECLEANING_STATUS = "RECLEANING"
    TSKUPKA_PENDING_STATUS = "TSKUPKA_PENDING"
    COMPLETED_STATUS = "COMPLETED"
    WORKFLOW_FAILED_STATUS = "WORKFLOW_FAILED"
    CANCELED_STATUS = "CANCELED"

    CLOSED_STATUSES = {CANCELED_STATUS, COMPLETED_STATUS}

    COMPLETED_PROVIDER_STATUSES = {
        "completed", "complete", "done", "finished", "success", "succeeded",
    }
    FAILED_PROVIDER_STATUSES = {
        "cancelled", "canceled", "failed", "rejected", "error", "expired",
    }

    PROVIDERS = {
        "tokenbuyrobot": {
            "name": "TokenBuyRobot",
            "base_url": "https://tokenbuyrobot.com",
            "submit_endpoint": "/api/v1/submit",
            "status_endpoint": "/api/v1/status/{submission_id}",
            "decision_endpoint": "/api/v1/decide/{submission_id}",
            "submit_file_field": "file",
            "auth": "X-API-Key",
            "default_api_key_env": "TOKENBUYROBOT_API_KEY",
        },
        "tskupka": {
            "name": "Tskupka",
            "base_url": "https://tskupka.cc",
            "submit_endpoint": "/v1/tasks",
            "status_endpoint": "/v1/tasks/{task_id}",
            "submit_file_field": "file",
            "auth": "Authorization: Bearer",
            "default_api_key_env": "TSKUPKA_API_KEY",
        },
    }

    def __init__(self, config: Dict = None, sheets_logger=None):
        config = config or {}
        self.enabled = bool(config.get("enabled", False))
        # Всегда используем абсолютный путь, чтобы os.replace работал на Windows
        self.outbox_path = os.path.abspath(
            config.get("outbox_path", "data/sales/submissions.jsonl")
        )
        self._file_lock = threading.Lock()   # защищаем файл от гонок
        self.timeout = float(config.get("timeout", 20))
        self.provider_overrides = config.get("providers", {})
        self.sheets_logger = sheets_logger  # GoogleSheetsLogger | None

        wf = config.get("workflow", {})
        self.price_initial_delay = float(wf.get("price_initial_delay", 10))
        self.price_poll_interval = float(wf.get("price_poll_interval", 10))
        self.completion_poll_interval = float(wf.get("completion_poll_interval", 30))
        self.completion_timeout = float(wf.get("completion_timeout", 3600))  # 60 мин

        logger.info("🧾 Sales %s", "enabled" if self.enabled else "disabled")

    # ── public API ──────────────────────────────────────────────

    def create_submission(self, items: List[Dict], source: str = "telegram") -> Dict:
        if not self.enabled:
            return {"ok": False, "error": "Sales disabled"}
        if not items:
            return {"ok": False, "error": "No items"}

        safe_items = [self._make_item(it) for it in items]
        submission = {
            "submission_id": self._new_id(),
            "status": self.PENDING_STATUS,
            "source": source,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "accepted_count": len(safe_items),
            "total_price": sum(float(it.get("price") or 0) for it in safe_items),
            "workflow": {},
            "items": safe_items,
        }
        self._append_submission(submission)
        logger.info("🧾 Заявка создана: %s (%d шт.)", submission["submission_id"], len(safe_items))
        return {
            "ok": True,
            "submission_id": submission["submission_id"],
            "accepted_count": len(safe_items),
        }

    def run_workflow(
        self,
        submission_id: str,
        notify_callback: Callable = None,
        re_clean_callback: Callable = None,
    ) -> Dict:
        """
        Запускает полный цикл продажи.

        Args:
            submission_id: ID заявки
            notify_callback: fn(title, message, level) для Telegram-уведомлений
            re_clean_callback: fn(tokens: list[str]) -> int — синхронная повторная очистка,
                               возвращает количество успешно очищенных токенов
        """
        if not self.enabled:
            return {"ok": False, "error": "Sales disabled"}
        try:
            return self._run_workflow(submission_id, notify_callback, re_clean_callback)
        except Exception as e:
            logger.exception("❌ Workflow failed: %s", submission_id)
            return self._fail(submission_id, str(e), notify_callback)

    def get_submission(self, submission_id: str) -> Optional[Dict]:
        for s in self._read_submissions():
            if s.get("submission_id") == submission_id:
                return s
        return None

    def cancel_submission(self, submission_id: str) -> Dict:
        return self._set_status(submission_id, self.CANCELED_STATUS)

    def get_summary(self, limit: int = 5) -> Dict:
        all_subs = self._read_submissions()
        recent = self._newest_first(all_subs)[:limit]

        by_status: Dict[str, int] = {}
        for s in all_subs:
            st = str(s.get("status", "")).upper()
            by_status[st] = by_status.get(st, 0) + 1

        active = [
            s for s in all_subs
            if str(s.get("status", "")).upper() not in self.CLOSED_STATUSES
        ]

        return {
            "enabled": self.enabled,
            "total": len(all_subs),
            "active": len(active),
            "completed": by_status.get(self.COMPLETED_STATUS, 0),
            "failed": by_status.get(self.WORKFLOW_FAILED_STATUS, 0),
            "canceled": by_status.get(self.CANCELED_STATUS, 0),
            "recent": [
                {
                    "submission_id": s.get("submission_id"),
                    "status": s.get("status"),
                    "created_at": s.get("created_at"),
                    "accepted_count": s.get("accepted_count", 0),
                    "total_price": s.get("total_price", 0),
                    "workflow": s.get("workflow", {}),
                }
                for s in recent
            ],
        }

    # ── workflow ────────────────────────────────────────────────

    def _run_workflow(
        self,
        submission_id: str,
        notify_cb: Callable,
        re_clean_cb: Callable,
    ) -> Dict:
        n = self._notifier(notify_cb)
        sheets = self.sheets_logger

        submission = self.get_submission(submission_id)
        if not submission:
            return {"ok": False, "error": "Submission not found"}

        # Сумма, потраченная на покупку этих токенов
        tokens_cost = sum(float(it.get("price") or 0) for it in submission.get("items", []))
        tokens = [it["token"] for it in submission.get("items", []) if it.get("token")]

        # Создаём строку в Google Sheets сразу
        sheets_row = sheets.create_row(submission_id, tokens_cost) if sheets else None

        # ── 1. Submit tokenbuyrobot ──────────────────────────────
        n("🧾 Workflow", "Отправляю в tokenbuyrobot...", "INFO")
        tbr = self._submit_to(submission_id, "tokenbuyrobot")
        if not tbr.get("ok"):
            if sheets:
                sheets.update_failed(sheets_row, "tbr_submit")
            return self._fail(submission_id, tbr.get("error"), notify_cb)

        tbr_id = tbr.get("provider_submission_id")
        self._update_fields(submission_id, {
            "status": self.TOKENBUYROBOT_PRICE_PENDING_STATUS,
            "workflow": {"stage": "waiting_tbr_price", "tokenbuyrobot_id": tbr_id},
        })

        if self.price_initial_delay > 0:
            time.sleep(self.price_initial_delay)

        # ── 2. Ждём цену от tokenbuyrobot ───────────────────────
        tbr_expected = self._poll_price("tokenbuyrobot", tbr_id, submission_id, notify_cb)
        n("💰 TokenBuyRobot", f"Ожидаемая цена: <b>{tbr_expected}</b> ₽", "SUCCESS")
        if sheets:
            sheets.update_tbr_expected(sheets_row, tbr_expected)

        # ── 3. Подтверждаем tokenbuyrobot сразу ─────────────────
        confirm = self._confirm("tokenbuyrobot", tbr_id)
        if not confirm.get("ok"):
            if sheets:
                sheets.update_failed(sheets_row, "tbr_confirm")
            return self._fail(submission_id, confirm.get("error"), notify_cb)

        n("✅ TokenBuyRobot подтвержден", "Жду завершения...", "INFO")
        if sheets:
            sheets.update_tbr_confirmed(sheets_row)
        self._update_fields(submission_id, {
            "status": self.TOKENBUYROBOT_COMPLETION_PENDING_STATUS,
            "workflow": {
                "stage": "waiting_tbr_completion",
                "tokenbuyrobot_id": tbr_id,
                "tokenbuyrobot_expected_price": tbr_expected,
            },
        })

        # ── 4. Ждём завершения tokenbuyrobot ────────────────────
        tbr_completion = self._poll_completion(
            "tokenbuyrobot", tbr_id, submission_id, notify_cb,
            timeout=self.completion_timeout,
        )
        tbr_data = (tbr_completion or {}).get("data")
        final_payment = self._extract_field(tbr_data, "final_payment") if tbr_data else None
        tbr_final = final_payment if (final_payment and final_payment > 0) else tbr_expected

        n("✅ TokenBuyRobot завершен", f"Итоговая цена: <b>{tbr_final}</b> ₽", "SUCCESS")
        if sheets:
            sheets.update_tbr_final(sheets_row, tbr_final)

        # ── 5. Повторная очистка токенов ─────────────────────────
        self._update_fields(submission_id, {
            "status": self.RECLEANING_STATUS,
            "workflow": {
                "stage": "recleaning",
                "tokenbuyrobot_id": tbr_id,
                "tokenbuyrobot_price": tbr_final,
            },
        })
        if sheets:
            sheets.update_recleaning(sheets_row, len(tokens))

        cleaned_count = 0
        if re_clean_cb and tokens:
            n("🧹 Очистка", f"Повторная очистка <b>{len(tokens)}</b> токенов...", "INFO")
            try:
                cleaned_count = re_clean_cb(tokens) or 0
            except Exception as e:
                logger.warning("⚠️ Re-clean error: %s", e)
                cleaned_count = 0
            n("✅ Очистка завершена", f"Очищено: <b>{cleaned_count}</b> из {len(tokens)}", "INFO")
        else:
            logger.info("⚠️ re_clean_callback не задан, пропускаем очистку")

        # ── 6. Submit tskupka (skip_confirmation=true) ───────────
        n("🧾 Workflow", "Отправляю в tskupka...", "INFO")
        if sheets:
            sheets.update_tsk_submitted(sheets_row)
        tsk = self._submit_to(
            submission_id, "tskupka",
            extra_data={"skip_confirmation": "true"},
        )
        if not tsk.get("ok"):
            if sheets:
                sheets.update_failed(sheets_row, "tsk_submit")
            return self._fail(submission_id, tsk.get("error"), notify_cb)

        tsk_id = tsk.get("provider_submission_id")
        self._update_fields(submission_id, {
            "status": self.TSKUPKA_PENDING_STATUS,
            "workflow": {
                "stage": "waiting_tsk_completion",
                "tokenbuyrobot_id": tbr_id,
                "tokenbuyrobot_price": tbr_final,
                "tskupka_id": tsk_id,
            },
        })

        # ── 7. Ждём завершения tskupka ────────────────────────────
        n("⏳ Tskupka", "Жду завершения проверки...", "INFO")
        tsk_completion = self._poll_completion(
            "tskupka", tsk_id, submission_id, notify_cb,
            timeout=self.completion_timeout,
        )
        tsk_data = (tsk_completion or {}).get("data")

        tsk_price: Optional[float] = None
        if tsk_data:
            # Предпочитаем final_total из price_result, потом amount_paid
            inner = tsk_data.get("data") if isinstance(tsk_data, dict) and "data" in tsk_data else tsk_data
            if isinstance(inner, dict):
                price_result = inner.get("price_result")
                if isinstance(price_result, dict):
                    tsk_price = self._num(price_result.get("final_total"))
                if not tsk_price or tsk_price <= 0:
                    tsk_price = self._num(inner.get("amount_paid"))
            if not tsk_price or tsk_price <= 0:
                tsk_price = self._find_price(tsk_data)

        n("✅ Tskupka завершена", f"Цена: <b>{tsk_price}</b> ₽", "SUCCESS")

        # ── 8. Считаем прибыль и обновляем таблицу ───────────────
        profit = (tbr_final or 0) + (tsk_price or 0) - (tokens_cost or 0)
        if sheets:
            sheets.update_complete(sheets_row, tsk_price or 0, profit)

        # ── 9. Финал ─────────────────────────────────────────────
        final = self._update_fields(submission_id, {
            "status": self.COMPLETED_STATUS,
            "workflow": {
                "stage": "completed",
                "tokenbuyrobot_id": tbr_id,
                "tokenbuyrobot_price": tbr_final,
                "tskupka_id": tsk_id,
                "tskupka_price": tsk_price,
                "tokens_cost": tokens_cost,
                "profit": profit,
            },
        })

        n(
            "🏁 Круг завершён",
            f"💰 TokenBuyRobot: <b>{tbr_final}</b> ₽\n"
            f"💰 Tskupka: <b>{tsk_price}</b> ₽\n"
            f"💸 Потрачено: <b>{tokens_cost}</b> ₽\n"
            f"📈 Прибыль: <b>{round(profit, 2)}</b> ₽",
            "SUCCESS",
        )
        return {"ok": True, "submission": final}

    # ── provider interactions ────────────────────────────────────

    def _submit_to(
        self,
        submission_id: str,
        provider: str,
        extra_data: Dict = None,
    ) -> Dict:
        submission = self.get_submission(submission_id)
        if not submission:
            return {"ok": False, "error": "Submission not found"}

        api_key = self._api_key(provider)
        if not api_key:
            return {"ok": False, "error": f"Не задан {self._api_key_env(provider)}"}

        tokens_text = self._build_tokens_text(submission)
        if not tokens_text:
            return {"ok": False, "error": "Нет токенов для отправки"}

        info = self._provider_info(provider)
        url = self._make_url(info, "submit_endpoint")
        field = info.get("submit_file_field", "file")
        filename = f"{submission_id}.txt"

        try:
            resp = requests.post(
                url,
                files={field: (filename, tokens_text.encode("utf-8"), "text/plain")},
                data=extra_data or {},
                headers=self._headers(provider, content_type=False),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            return {"ok": False, "error": str(e)}

        data = self._json(resp)
        ok = 200 <= resp.status_code < 300
        pid = self._extract_id(data) if ok else None

        wf = (self.get_submission(submission_id) or {}).get("workflow", {}).copy()
        wf[f"{provider}_submission_id"] = pid
        self._update_fields(submission_id, {"workflow": wf})

        logger.debug("📤 %s submit: status=%s, pid=%s, data=%s", provider, resp.status_code, pid, data)
        return {
            "ok": ok,
            "error": None if ok else self._err(resp, data),
            "provider_submission_id": pid,
        }

    def _poll_price(self, provider, pid, submission_id, notify_cb):
        """Ждём пока провайдер вернёт цену > 0."""
        if not pid:
            raise ValueError(f"{provider}: нет submission_id")

        while True:
            result = self._get_status(provider, pid)
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or f"{provider} status failed")

            data = result.get("data")
            logger.debug("📦 %s poll_price: %s", provider, data)

            status = self._find_status(data)
            if self._is_failed(status):
                raise RuntimeError(f"{provider} задача отклонена: {status}")

            price = self._find_price(data)
            if price is not None and price > 0:
                wf = (self.get_submission(submission_id) or {}).get("workflow", {}).copy()
                wf[f"{provider}_price"] = price
                self._update_fields(submission_id, {"workflow": wf})
                return price

            logger.info("⏳ %s poll_price: status=%s, price=%s", provider, status, price)
            if notify_cb:
                notify_cb(
                    f"⏳ {provider}",
                    f"Жду цену... статус: <b>{status or '?'}</b>",
                    "POLL",
                )
            time.sleep(self.price_poll_interval)

    def _poll_completion(self, provider, pid, submission_id, notify_cb, timeout=None):
        """Ждём завершения заявки у провайдера."""
        if not pid:
            raise ValueError(f"{provider}: нет submission_id")

        deadline = time.monotonic() + timeout if timeout else None

        while True:
            result = self._get_status(provider, pid)
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or f"{provider} status failed")

            data = result.get("data")
            status = self._find_status(data)

            wf = (self.get_submission(submission_id) or {}).get("workflow", {}).copy()
            wf[f"{provider}_last_status"] = status
            self._update_fields(submission_id, {"workflow": wf})

            if self._is_done(status) or self._is_provider_done(provider, data):
                if notify_cb:
                    notify_cb(f"✅ {provider} завершена", f"Статус: <b>{status}</b>", "SUCCESS")
                return {"status": status, "data": data}

            if self._is_failed(status):
                raise RuntimeError(f"{provider} задача отклонена: {status}")

            if deadline and time.monotonic() >= deadline:
                if notify_cb:
                    notify_cb(
                        f"⏰ {provider} таймаут",
                        f"Время вышло, статус: <b>{status or 'unknown'}</b>",
                        "WARNING",
                    )
                return {"status": status, "data": data}

            logger.info("⏳ %s poll_completion: status=%s", provider, status)
            if notify_cb:
                notify_cb(
                    f"⏳ {provider}",
                    f"Статус: <b>{status or 'unknown'}</b>, "
                    f"проверка через {int(self.completion_poll_interval)} сек.",
                    "POLL",
                )
            time.sleep(self.completion_poll_interval)

    def _get_status(self, provider, pid) -> Dict:
        info = self._provider_info(provider)
        url = self._make_url(info, "status_endpoint", pid)
        try:
            resp = requests.get(url, headers=self._headers(provider), timeout=self.timeout)
        except requests.RequestException as e:
            return {"ok": False, "error": str(e)}
        data = self._json(resp)
        ok = 200 <= resp.status_code < 300
        return {"ok": ok, "error": None if ok else self._err(resp, data), "data": data}

    def _confirm(self, provider, pid) -> Dict:
        """Подтверждает заявку у провайдера (только tokenbuyrobot — action=sell).
        tskupka использует skip_confirmation=true при сабмите, confirm не нужен.
        """
        info = self._provider_info(provider)
        url = self._make_url(info, "decision_endpoint", pid)

        try:
            if provider == "tokenbuyrobot":
                resp = requests.post(
                    url,
                    json={"action": "sell"},
                    headers=self._headers(provider),
                    timeout=self.timeout,
                )
            else:
                resp = requests.post(url, headers=self._headers(provider), timeout=self.timeout)
        except requests.RequestException as e:
            return {"ok": False, "error": str(e)}

        data = self._json(resp)
        # 409 = уже подтверждено / auto-approved, считаем успехом
        ok = 200 <= resp.status_code < 300 or resp.status_code == 409
        if resp.status_code == 409:
            logger.info("✅ %s confirm: auto-approved (409), pid=%s", provider, pid)
        return {"ok": ok, "error": None if ok else self._err(resp, data), "data": data}

    # ── provider-specific completion ────────────────────────────

    def _is_provider_done(self, provider: str, data) -> bool:
        """Дополнительная проверка завершения, специфичная для провайдера."""
        if provider != "tskupka" or not data:
            return False
        # tskupka: завершено если amount_paid > 0 или finished_at установлен
        inner = data.get("data") if isinstance(data, dict) and "data" in data else data
        if not isinstance(inner, dict):
            return False
        amount = self._num(inner.get("amount_paid"))
        if amount and amount > 0:
            return True
        if inner.get("finished_at"):
            return True
        return False

    # ── storage ─────────────────────────────────────────────────

    def _read_submissions_nolock(self) -> List[Dict]:
        """Читает файл без захвата lock — вызывать только внутри критических секций."""
        if not os.path.exists(self.outbox_path):
            return []
        subs = []
        with open(self.outbox_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    subs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return subs

    def _read_submissions(self) -> List[Dict]:
        with self._file_lock:
            return self._read_submissions_nolock()

    def _write_submissions(self, submissions: List[Dict]):
        """Атомарная запись через temp-файл. Блокировка уже удерживается вызывающим кодом."""
        outbox_dir = os.path.dirname(self.outbox_path)
        if outbox_dir and not os.path.exists(outbox_dir):
            os.makedirs(outbox_dir, exist_ok=True)
        # Создаём temp-файл в той же директории → гарантируем один диск
        fd, tmp = tempfile.mkstemp(
            prefix=".sub-", suffix=".jsonl",
            dir=outbox_dir or ".", text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for s in submissions:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
            # os.replace на Windows может упасть с WinError 5 если файл заблокирован;
            # делаем несколько попыток с небольшой паузой
            last_err = None
            for attempt in range(5):
                try:
                    os.replace(tmp, self.outbox_path)
                    tmp = None  # успешно, не удалять
                    return
                except PermissionError as e:
                    last_err = e
                    time.sleep(0.1 * (attempt + 1))
            raise last_err
        except Exception:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise

    def _append_submission(self, submission: Dict):
        outbox_dir = os.path.dirname(self.outbox_path)
        if outbox_dir and not os.path.exists(outbox_dir):
            os.makedirs(outbox_dir, exist_ok=True)
        with self._file_lock:
            with open(self.outbox_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(submission, ensure_ascii=False) + "\n")

    def _update_fields(self, submission_id: str, fields: Dict) -> Optional[Dict]:
        with self._file_lock:
            subs = self._read_submissions_nolock()
            target = None
            for s in subs:
                if s.get("submission_id") == submission_id:
                    s.update(fields)
                    s["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    target = s
                    break
            if target:
                self._write_submissions(subs)
            return target

    def _set_status(self, submission_id: str, new_status: str) -> Dict:
        if not self.enabled:
            return {"ok": False, "error": "Sales disabled"}
        with self._file_lock:
            subs = self._read_submissions_nolock()
            for s in subs:
                if s.get("submission_id") != submission_id:
                    continue
                cur = str(s.get("status", "")).upper()
                if cur in self.CLOSED_STATUSES:
                    return {"ok": False, "error": f"Заявка уже закрыта ({cur})", "submission": s}
                s["status"] = new_status
                s["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._write_submissions(subs)
                return {"ok": True, "submission": s}
        return {"ok": False, "error": "Заявка не найдена"}

    def _fail(self, submission_id, error, notify_cb=None):
        self._update_fields(submission_id, {
            "status": self.WORKFLOW_FAILED_STATUS,
            "workflow_error": error,
        })
        if notify_cb:
            notify_cb("❌ Workflow ошибка", f"<code>{error}</code>", "ERROR")
        return {"ok": False, "error": error}

    # ── helpers ──────────────────────────────────────────────────

    def _make_item(self, item: Dict) -> Dict:
        return {
            "db_id": item.get("id"),
            "token": item.get("token"),
            "username": item.get("username"),
            "seller_username": item.get("seller_username"),
            "price": item.get("price"),
        }

    def _new_id(self) -> str:
        return f"sale-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

    def _build_tokens_text(self, submission: Dict) -> str:
        lines = []
        for item in submission.get("items", []):
            t = item.get("token")
            if t and str(t).strip():
                lines.append(str(t).strip())
        return "\n".join(lines)

    def _provider_info(self, provider: str) -> Dict:
        base = dict(self.PROVIDERS.get(provider, {}))
        base.update(self.provider_overrides.get(provider, {}))
        return base

    def _api_key_env(self, provider: str) -> str:
        info = self._provider_info(provider)
        return info.get("api_key_env") or info.get("default_api_key_env", "SALES_API_KEY")

    def _api_key(self, provider: str) -> str:
        return os.environ.get(self._api_key_env(provider), "")

    def _make_url(self, info: Dict, endpoint_key: str, pid: str = None) -> str:
        base = (info.get("base_url") or "").rstrip("/")
        ep = info.get(endpoint_key, "")
        ep = ep.replace("{submission_id}", str(pid or ""))
        ep = ep.replace("{task_id}", str(pid or ""))
        return f"{base}/{ep.lstrip('/')}"

    def _headers(self, provider: str, content_type: bool = True) -> Dict[str, str]:
        info = self._provider_info(provider)
        auth = info.get("auth", "Authorization: Bearer")
        key = self._api_key(provider)
        h = {"Accept": "application/json"}
        if content_type:
            h["Content-Type"] = "application/json"
        if auth == "X-API-Key":
            h["X-API-Key"] = key
        elif auth.lower().startswith("authorization"):
            h["Authorization"] = f"Bearer {key}"
        else:
            h[auth] = key
        return h

    def _json(self, resp):
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def _err(self, resp, data=None) -> str:
        base = f"HTTP {resp.status_code}"
        if data and isinstance(data, dict):
            detail = data.get("detail") or data.get("message") or data.get("error")
            if detail:
                return f"{base}: {str(detail)[:200]}"
        return base

    def _extract_id(self, data) -> Optional[str]:
        if not isinstance(data, dict):
            return None
        for key in ("submission_id", "task_id", "id", "uuid"):
            v = data.get(key)
            if v:
                return str(v)
        nested = data.get("data")
        if isinstance(nested, dict):
            return self._extract_id(nested)
        return None

    def _find_price(self, value) -> Optional[float]:
        """Ищет цену рекурсивно. Приоритет final_ полям, пропускает нули."""
        if isinstance(value, dict):
            # Приоритет: финальные поля
            for key in ("final_total", "final_payment", "final_price"):
                if key in value:
                    p = self._num(value[key])
                    if p is not None and p > 0:
                        return p
            # Общий поиск по ключевым словам
            for key, v in value.items():
                kl = str(key).lower()
                if any(w in kl for w in ("price", "amount", "cost", "payment")):
                    p = self._num(v)
                    if p is not None and p > 0:
                        return p
            # Рекурсия в вложенные объекты
            for v in value.values():
                p = self._find_price(v)
                if p is not None:
                    return p
        elif isinstance(value, list):
            for v in value:
                p = self._find_price(v)
                if p is not None:
                    return p
        return None

    def _find_status(self, value) -> Optional[str]:
        if isinstance(value, dict):
            for key, v in value.items():
                if str(key).lower() in {"status", "state"} and v is not None:
                    return str(v)
            for v in value.values():
                s = self._find_status(v)
                if s:
                    return s
        return None

    def _extract_field(self, data, field_name) -> Optional[float]:
        """Извлекает числовое значение конкретного поля рекурсивно."""
        if isinstance(data, dict):
            if field_name in data:
                return self._num(data[field_name])
            for v in data.values():
                result = self._extract_field(v, field_name)
                if result is not None:
                    return result
        return None

    def _is_done(self, status) -> bool:
        if not status:
            return False
        return str(status).strip().lower() in self.COMPLETED_PROVIDER_STATUSES

    def _is_failed(self, status) -> bool:
        if not status:
            return False
        return str(status).strip().lower() in self.FAILED_PROVIDER_STATUSES

    def _num(self, value):
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return float(value.replace(",", ".").strip())
            except ValueError:
                return None
        return None

    def _notifier(self, cb):
        def n(title, msg, level):
            if cb:
                cb(title, msg, level)
        return n

    def _newest_first(self, subs: List[Dict]) -> List[Dict]:
        indexed = list(enumerate(subs))
        indexed.sort(key=lambda p: (p[1].get("created_at", ""), p[0]), reverse=True)
        return [s for _, s in indexed]
