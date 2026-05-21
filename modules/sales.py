import json
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class SalesManager:
    """
    Безопасный каркас для будущей продажи цифровых товаров.

    Важно: этот модуль намеренно не отправляет учетные данные, токены или
    другие секреты во внешние API. Текущая реализация создает локальную
    черновую заявку только из метаданных.
    """

    SUPPORTED_MODES = {"local_queue", "external_submit", "price_workflow"}
    PENDING_STATUS = "PENDING"
    SUBMITTED_STATUS = "SUBMITTED"
    SUBMIT_FAILED_STATUS = "SUBMIT_FAILED"
    TSKUPKA_PRICE_PENDING_STATUS = "TSKUPKA_PRICE_PENDING"
    TOKENBUYROBOT_PRICE_PENDING_STATUS = "TOKENBUYROBOT_PRICE_PENDING"
    TOKENBUYROBOT_COMPLETION_PENDING_STATUS = "TOKENBUYROBOT_COMPLETION_PENDING"
    TSKUPKA_COMPLETION_PENDING_STATUS = "TSKUPKA_COMPLETION_PENDING"
    COMPLETED_STATUS = "COMPLETED"
    WORKFLOW_FAILED_STATUS = "WORKFLOW_FAILED"
    SOLD_STATUS = "SOLD"
    CANCELED_STATUS = "CANCELED"
    CLOSED_STATUSES = {SOLD_STATUS, CANCELED_STATUS, COMPLETED_STATUS}
    ACTIVE_STATUSES = {
        PENDING_STATUS,
        SUBMITTED_STATUS,
        SUBMIT_FAILED_STATUS,
        TSKUPKA_PRICE_PENDING_STATUS,
        TOKENBUYROBOT_PRICE_PENDING_STATUS,
        TOKENBUYROBOT_COMPLETION_PENDING_STATUS,
        TSKUPKA_COMPLETION_PENDING_STATUS,
    }
    COMPLETED_PROVIDER_STATUSES = {"completed", "complete", "done", "finished", "success", "succeeded"}
    SENSITIVE_KEYS = {
        "token",
        "password",
        "secret",
        "cookie",
        "authorization",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "bearer",
    }
    SUPPORTED_PROVIDERS = {
        "tokenbuyrobot": {
            "name": "TokenBuyRobot",
            "docs_url": "https://tokenbuyrobot.com/api/docs",
            "base_url": "https://tokenbuyrobot.com",
            "submit_endpoint": "/api/v1/submit",
            "status_endpoint": "/api/v1/status/{submission_id}",
            "decision_endpoint": "/api/v1/decide/{submission_id}",
            "auth": "X-API-Key",
        },
        "tskupka": {
            "name": "Tskupka",
            "docs_url": "https://tskupka.cc/api",
            "base_url": "https://tskupka.cc",
            "submit_endpoint": "/v1/tasks",
            "status_endpoint": "/v1/tasks/{task_id}",
            "decision_endpoint": "/v1/tasks/{task_id}/confirm",
            "auth": "Authorization: Bearer",
        },
    }

    def __init__(self, config: Dict = None):
        config = config or {}
        self.enabled = bool(config.get("enabled", False))
        self.mode = config.get("mode", "local_queue")
        self.provider = config.get("provider", "tokenbuyrobot")
        self.outbox_path = config.get("outbox_path", "data/sales/submissions.jsonl")
        self.allow_external_token_submit = bool(config.get("allow_external_token_submit", False))
        self.api_key_env = config.get("api_key_env", "SALES_API_KEY")
        self.api_key = os.environ.get(self.api_key_env, "")
        self.timeout = float(config.get("timeout", 20))
        self.base_url_override = config.get("base_url")
        self.submit_endpoint_override = config.get("submit_endpoint")
        self.auth_header_override = config.get("auth_header")
        self.auth_scheme_override = config.get("auth_scheme")
        self.submit_file_field = config.get("submit_file_field", "submit")
        self.provider_overrides = config.get("providers", {})
        self.workflow_config = config.get("workflow", {})
        self.price_initial_delay = float(self.workflow_config.get("price_initial_delay", 10))
        self.price_poll_interval = float(self.workflow_config.get("price_poll_interval", 10))
        self.completion_poll_interval = float(self.workflow_config.get("completion_poll_interval", 30))

        if self.mode not in self.SUPPORTED_MODES:
            logger.warning(
                "⚠️ Sales mode '%s' не поддерживается, используется local_queue",
                self.mode,
            )
            self.mode = "local_queue"

        if self.provider not in self.SUPPORTED_PROVIDERS:
            logger.warning(
                "⚠️ Sales provider '%s' не поддерживается, используется tokenbuyrobot",
                self.provider,
            )
            self.provider = "tokenbuyrobot"

        if self.allow_external_token_submit:
            logger.warning(
                "⚠️ External sales token submission requested but disabled by safety guard"
            )
            self.allow_external_token_submit = False

        self.workflow_enabled = self.mode == "price_workflow"
        self.external_submit_enabled = self.mode in {"external_submit", "price_workflow"}
        if self.mode == "external_submit" and not self.api_key:
            logger.warning(
                "⚠️ Sales external submit enabled, but %s is not set",
                self.api_key_env,
            )

        if self.enabled:
            logger.info(
                "🧾 Sales infrastructure enabled in %s mode for %s",
                self.mode,
                self.provider,
            )
        else:
            logger.info("🧾 Sales infrastructure disabled")

    def create_submission(
        self,
        items: List[Dict],
        source: str = "telegram_command",
        provider: Optional[str] = None,
    ) -> Dict:
        """
        Создает локальную заявку на продажу из безопасных метаданных.

        Args:
            items: Записи из БД. Поле token всегда отбрасывается.
            source: Откуда создана заявка.
            provider: Целевой провайдер для ручной обработки заявки.

        Returns:
            Словарь с локальным submission_id и количеством позиций.
        """
        if not self.enabled:
            return {
                "ok": False,
                "error": "Sales infrastructure is disabled",
                "submission_id": None,
                "accepted_count": 0,
            }

        provider = provider or self.provider
        if provider not in self.SUPPORTED_PROVIDERS:
            return {
                "ok": False,
                "error": f"Unsupported sales provider: {provider}",
                "submission_id": None,
                "accepted_count": 0,
            }

        if not items:
            return {
                "ok": False,
                "error": "No items provided",
                "submission_id": None,
                "accepted_count": 0,
            }

        safe_items = [self._sanitize_item(item) for item in items]
        submission = {
            "submission_id": self._new_submission_id(),
            "status": self.PENDING_STATUS,
            "provider": provider,
            "provider_info": self.SUPPORTED_PROVIDERS[provider],
            "source": source,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "accepted_count": len(safe_items),
            "total_price": self._sum_price(safe_items),
            "external_submit_enabled": self.external_submit_enabled,
            "external_submit_blocked": not self.external_submit_enabled,
            "external_submit_reason": None if self.external_submit_enabled else "External submit disabled by mode",
            "credential_submit_blocked": True,
            "credential_submit_reason": (
                "Automatic transfer of Discord tokens, passwords, cookies, "
                "API keys, or other credentials is disabled."
            ),
            "items": safe_items,
        }

        self._append_submission(submission)
        external_result = None

        if self.mode == "external_submit":
            external_result = self.submit_submission(submission["submission_id"])
            if external_result.get("submission"):
                submission = external_result["submission"]

        logger.info(
            "🧾 Sales draft created: %s (%s items)",
            submission["submission_id"],
            submission["accepted_count"],
        )

        return {
            "ok": True,
            "submission_id": submission["submission_id"],
            "status": submission["status"],
            "provider": provider,
            "accepted_count": submission["accepted_count"],
            "total_price": submission["total_price"],
            "external_submit": external_result,
            "workflow_enabled": self.workflow_enabled,
            "outbox_path": self.outbox_path,
        }

    def get_provider_info(self, provider: str = None) -> Dict:
        """Возвращает справочную информацию о sales-провайдере без API-ключей."""
        selected = provider or self.provider
        info = self.SUPPORTED_PROVIDERS.get(selected)
        if not info:
            return {}
        return {
            "provider": selected,
            **info,
            "external_submit_enabled": self.external_submit_enabled,
            "api_key_env": self.api_key_env,
        }

    def submit_submission(self, submission_id: str) -> Dict:
        """Отправляет сохраненную заявку во внешний API безопасным payload."""
        if not self.enabled:
            return {
                "ok": False,
                "error": "Sales infrastructure is disabled",
                "submission": None,
            }

        if not self.external_submit_enabled:
            return {
                "ok": False,
                "error": "External submit is disabled",
                "submission": self.get_submission(submission_id),
            }

        submissions = self._read_submissions()
        target = None
        for submission in submissions:
            if submission.get("submission_id") == submission_id:
                target = submission
                break

        if not target:
            return {
                "ok": False,
                "error": "Submission not found",
                "submission": None,
            }

        if str(target.get("status", "")).upper() in self.CLOSED_STATUSES:
            return {
                "ok": False,
                "error": f"Submission already closed with status {target.get('status')}",
                "submission": target,
            }

        result = self._submit_to_provider(target)
        target.update(result["submission_updates"])
        target["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._write_submissions(submissions)

        return {
            "ok": result["ok"],
            "error": result.get("error"),
            "submission": target,
            "provider_submission_id": result.get("provider_submission_id"),
            "status_code": result.get("status_code"),
        }

    def run_price_workflow(self, submission_id: str, notify_callback=None) -> Dict:
        """
        Запускает полный workflow:
        tskupka price -> tokenbuyrobot price -> confirm tokenbuyrobot ->
        tokenbuyrobot completion -> confirm tskupka -> tskupka completion.
        """
        if not self.enabled:
            return {"ok": False, "error": "Sales infrastructure is disabled"}
        if not self.workflow_enabled:
            return {"ok": False, "error": "Price workflow is disabled"}

        try:
            self._notify(notify_callback, "🧾 Sales workflow", "Отправляю заявку в tskupka...", "INFO")
            tskupka_submit = self._submit_to_provider_name(submission_id, "tskupka")
            if not tskupka_submit.get("ok"):
                return self._fail_workflow(submission_id, tskupka_submit.get("error"), notify_callback)

            tskupka_id = tskupka_submit.get("provider_submission_id")
            self._update_submission_fields(
                submission_id,
                {
                    "status": self.TSKUPKA_PRICE_PENDING_STATUS,
                    "provider": "tskupka",
                    "workflow": {
                        "stage": "waiting_tskupka_price",
                        "tskupka_submission_id": tskupka_id,
                    },
                },
            )

            if self.price_initial_delay > 0:
                time.sleep(self.price_initial_delay)

            tskupka_price = self._wait_for_price("tskupka", tskupka_id, submission_id, notify_callback)
            self._notify(
                notify_callback,
                "💰 Tskupka цена получена",
                f"Цена tskupka: <b>{tskupka_price}</b>",
                "SUCCESS",
            )

            self._notify(notify_callback, "🧾 Sales workflow", "Отправляю заявку в tokenbuyrobot...", "INFO")
            tokenbuyrobot_submit = self._submit_to_provider_name(
                submission_id,
                "tokenbuyrobot",
                extra_payload={"tskupka_price": tskupka_price},
            )
            if not tokenbuyrobot_submit.get("ok"):
                return self._fail_workflow(submission_id, tokenbuyrobot_submit.get("error"), notify_callback)

            tokenbuyrobot_id = tokenbuyrobot_submit.get("provider_submission_id")
            self._update_submission_fields(
                submission_id,
                {
                    "status": self.TOKENBUYROBOT_PRICE_PENDING_STATUS,
                    "provider": "tokenbuyrobot",
                    "workflow": {
                        "stage": "waiting_tokenbuyrobot_price",
                        "tskupka_submission_id": tskupka_id,
                        "tskupka_price": tskupka_price,
                        "tokenbuyrobot_submission_id": tokenbuyrobot_id,
                    },
                },
            )

            if self.price_initial_delay > 0:
                time.sleep(self.price_initial_delay)

            tokenbuyrobot_price = self._wait_for_price(
                "tokenbuyrobot",
                tokenbuyrobot_id,
                submission_id,
                notify_callback,
            )
            self._notify(
                notify_callback,
                "💰 TokenBuyRobot цена получена",
                f"Цена tokenbuyrobot: <b>{tokenbuyrobot_price}</b>",
                "SUCCESS",
            )

            confirm = self._confirm_provider_submission(
                "tokenbuyrobot",
                tokenbuyrobot_id,
                {
                    "submission_id": submission_id,
                    "accepted_price": tokenbuyrobot_price,
                    "tskupka_price": tskupka_price,
                },
            )
            if not confirm.get("ok"):
                return self._fail_workflow(submission_id, confirm.get("error"), notify_callback)

            self._update_submission_fields(
                submission_id,
                {
                    "status": self.TOKENBUYROBOT_COMPLETION_PENDING_STATUS,
                    "workflow": {
                        "stage": "waiting_tokenbuyrobot_completion",
                        "tskupka_submission_id": tskupka_id,
                        "tskupka_price": tskupka_price,
                        "tokenbuyrobot_submission_id": tokenbuyrobot_id,
                        "tokenbuyrobot_price": tokenbuyrobot_price,
                    },
                },
            )
            self._notify(
                notify_callback,
                "✅ TokenBuyRobot подтвержден",
                "Жду завершения заявки tokenbuyrobot...",
                "INFO",
            )

            self._wait_for_completion("tokenbuyrobot", tokenbuyrobot_id, submission_id, notify_callback)

            tskupka_confirm = self._confirm_provider_submission(
                "tskupka",
                tskupka_id,
                {
                    "submission_id": submission_id,
                    "accepted_price": tskupka_price,
                    "tokenbuyrobot_price": tokenbuyrobot_price,
                    "tokenbuyrobot_submission_id": tokenbuyrobot_id,
                },
            )
            if not tskupka_confirm.get("ok"):
                return self._fail_workflow(submission_id, tskupka_confirm.get("error"), notify_callback)

            self._update_submission_fields(
                submission_id,
                {
                    "status": self.TSKUPKA_COMPLETION_PENDING_STATUS,
                    "workflow": {
                        "stage": "waiting_tskupka_completion",
                        "tskupka_submission_id": tskupka_id,
                        "tskupka_price": tskupka_price,
                        "tokenbuyrobot_submission_id": tokenbuyrobot_id,
                        "tokenbuyrobot_price": tokenbuyrobot_price,
                    },
                },
            )
            self._notify(
                notify_callback,
                "✅ Tskupka подтверждена",
                "Жду завершения заявки tskupka...",
                "INFO",
            )

            self._wait_for_completion("tskupka", tskupka_id, submission_id, notify_callback)

            final_submission = self._update_submission_fields(
                submission_id,
                {
                    "status": self.COMPLETED_STATUS,
                    "workflow": {
                        "stage": "completed",
                        "tskupka_submission_id": tskupka_id,
                        "tskupka_price": tskupka_price,
                        "tokenbuyrobot_submission_id": tokenbuyrobot_id,
                        "tokenbuyrobot_price": tokenbuyrobot_price,
                    },
                },
            )
            self._notify(
                notify_callback,
                "🏁 Продажа завершена",
                (
                    f"Tskupka: <b>{tskupka_price}</b>\n"
                    f"TokenBuyRobot: <b>{tokenbuyrobot_price}</b>"
                ),
                "SUCCESS",
            )
            return {"ok": True, "submission": final_submission}
        except Exception as e:
            logger.exception("❌ Sales workflow failed")
            return self._fail_workflow(submission_id, str(e), notify_callback)

    def list_submissions(self, limit: int = 10, status: str = None) -> List[Dict]:
        """Читает последние локальные заявки из outbox."""
        submissions = self._read_submissions()

        if status:
            normalized_status = status.upper()
            submissions = [
                item for item in submissions
                if str(item.get("status", "")).upper() == normalized_status
            ]

        submissions = self._newest_first(submissions)
        if limit is None:
            return submissions
        return submissions[:limit]

    def get_submission(self, submission_id: str) -> Optional[Dict]:
        """Находит локальную заявку по ID."""
        for submission in self._read_submissions():
            if submission.get("submission_id") == submission_id:
                return submission
        return None

    def mark_sold(self, submission_id: str) -> Dict:
        """Помечает заявку как проданную."""
        return self._update_submission_status(submission_id, self.SOLD_STATUS)

    def cancel_submission(self, submission_id: str) -> Dict:
        """Отменяет заявку и оставляет ее в истории."""
        return self._update_submission_status(submission_id, self.CANCELED_STATUS)

    def _read_submissions(self) -> List[Dict]:
        if not os.path.exists(self.outbox_path):
            return []

        submissions = []
        with open(self.outbox_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning(
                        "⚠️ Не удалось прочитать sales-заявку из outbox, строка %s: %s",
                        line_no,
                        e,
                    )
                    continue
                submissions.append(item)
        return submissions

    def get_summary(self, limit: int = 5) -> Dict:
        """Сводка для Telegram и dashboard-статуса."""
        all_submissions = self._read_submissions()
        recent_submissions = self._newest_first(all_submissions)[:limit]

        pending = []
        sold = []
        canceled = []
        for item in all_submissions:
            status = str(item.get("status", "")).upper()
            if status == self.PENDING_STATUS:
                pending.append(item)
            elif status == self.SOLD_STATUS:
                sold.append(item)
            elif status == self.CANCELED_STATUS:
                canceled.append(item)

        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "provider": self.provider,
            "outbox_path": self.outbox_path,
            "external_submit_enabled": self.external_submit_enabled,
            "provider_info": self.get_provider_info(),
            "total": len(all_submissions),
            "pending": len(pending),
            "submitted": self._count_by_status(all_submissions, self.SUBMITTED_STATUS),
            "submit_failed": self._count_by_status(all_submissions, self.SUBMIT_FAILED_STATUS),
            "sold": len(sold),
            "canceled": len(canceled),
            "items_pending": self._sum_count(pending),
            "items_sold": self._sum_count(sold),
            "total_pending_price": self._sum_submission_price(pending),
            "total_sold_price": self._sum_submission_price(sold),
            "recent_submissions": [
                {
                    "submission_id": item.get("submission_id"),
                    "status": item.get("status"),
                    "provider": item.get("provider"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "accepted_count": item.get("accepted_count", 0),
                    "total_price": item.get("total_price", 0),
                    "workflow": item.get("workflow", {}),
                }
                for item in recent_submissions
            ],
        }

    def _sanitize_item(self, item: Dict) -> Dict:
        """Оставляет только метаданные, не пригодные для авторизации."""
        return {
            "db_id": item.get("id"),
            "username": item.get("username"),
            "lzt_item_id": item.get("lzt_item_id"),
            "seller_username": item.get("seller_username"),
            "price": item.get("price"),
            "created_at": item.get("created_at"),
            "validated_at": item.get("validated_at"),
            "cleaned_at": item.get("cleaned_at"),
        }

    def _append_submission(self, submission: Dict):
        outbox_dir = os.path.dirname(self.outbox_path)
        if outbox_dir and not os.path.exists(outbox_dir):
            os.makedirs(outbox_dir, exist_ok=True)

        with open(self.outbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(submission, ensure_ascii=False) + "\n")

    def _update_submission_status(self, submission_id: str, new_status: str) -> Dict:
        if not self.enabled:
            return {
                "ok": False,
                "error": "Sales infrastructure is disabled",
                "submission": None,
            }

        submissions = self._read_submissions()
        target = None

        for submission in submissions:
            if submission.get("submission_id") != submission_id:
                continue

            current_status = str(submission.get("status", "")).upper()
            if current_status in self.CLOSED_STATUSES:
                if current_status == new_status:
                    return {
                        "ok": True,
                        "submission": submission,
                    }
                return {
                    "ok": False,
                    "error": f"Submission already closed with status {current_status}",
                    "submission": submission,
                }

            submission["status"] = new_status
            submission["updated_at"] = datetime.now().isoformat(timespec="seconds")
            target = submission
            break

        if not target:
            return {
                "ok": False,
                "error": "Submission not found",
                "submission": None,
            }

        self._write_submissions(submissions)
        logger.info("🧾 Sales submission %s marked as %s", submission_id, new_status)
        return {
            "ok": True,
            "submission": target,
        }

    def _write_submissions(self, submissions: List[Dict]):
        outbox_dir = os.path.dirname(self.outbox_path)
        if outbox_dir and not os.path.exists(outbox_dir):
            os.makedirs(outbox_dir, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(
            prefix=".submissions-",
            suffix=".jsonl",
            dir=outbox_dir or ".",
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for submission in submissions:
                    f.write(json.dumps(submission, ensure_ascii=False) + "\n")
            os.replace(tmp_path, self.outbox_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _new_submission_id(self) -> str:
        return f"local-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    def _submit_to_provider_name(
        self,
        submission_id: str,
        provider: str,
        extra_payload: Dict = None,
    ) -> Dict:
        submission = self.get_submission(submission_id)
        if not submission:
            return {"ok": False, "error": "Submission not found"}

        result = self._submit_to_provider(submission, provider=provider, extra_payload=extra_payload)
        updates = result.get("submission_updates", {})
        workflow = submission.get("workflow", {})
        workflow.update({
            f"{provider}_submission_id": result.get("provider_submission_id"),
            f"{provider}_submit_status_code": result.get("status_code"),
        })
        updates["workflow"] = workflow
        self._update_submission_fields(submission_id, updates)
        return result

    def _wait_for_price(self, provider: str, provider_submission_id: str,
                        submission_id: str, notify_callback=None):
        if not provider_submission_id:
            raise ValueError(f"{provider} submission id is missing")

        while True:
            status_result = self._get_provider_status(provider, provider_submission_id)
            if not status_result.get("ok"):
                raise RuntimeError(status_result.get("error") or f"{provider} status request failed")

            price = self._extract_price(status_result.get("data"))
            if price is not None:
                workflow = self.get_submission(submission_id).get("workflow", {})
                workflow[f"{provider}_price"] = price
                workflow[f"{provider}_last_status"] = self._extract_status(status_result.get("data"))
                self._update_submission_fields(submission_id, {"workflow": workflow})
                return price

            self._notify(
                notify_callback,
                f"⏳ {provider} price",
                f"Цена еще не появилась, следующая проверка через {int(self.price_poll_interval)} сек.",
                "INFO",
            )
            time.sleep(self.price_poll_interval)

    def _wait_for_completion(self, provider: str, provider_submission_id: str,
                             submission_id: str, notify_callback=None):
        if not provider_submission_id:
            raise ValueError(f"{provider} submission id is missing")

        while True:
            status_result = self._get_provider_status(provider, provider_submission_id)
            if not status_result.get("ok"):
                raise RuntimeError(status_result.get("error") or f"{provider} status request failed")

            status = self._extract_status(status_result.get("data"))
            workflow = self.get_submission(submission_id).get("workflow", {})
            workflow[f"{provider}_last_status"] = status
            self._update_submission_fields(submission_id, {"workflow": workflow})

            if self._is_completed_status(status):
                self._notify(
                    notify_callback,
                    f"✅ {provider} завершена",
                    f"Статус: <b>{status}</b>",
                    "SUCCESS",
                )
                return status

            self._notify(
                notify_callback,
                f"⏳ {provider} completion",
                (
                    f"Текущий статус: <b>{status or 'unknown'}</b>\n"
                    f"Следующая проверка через {int(self.completion_poll_interval)} сек."
                ),
                "INFO",
            )
            time.sleep(self.completion_poll_interval)

    def _get_provider_status(self, provider: str, provider_submission_id: str) -> Dict:
        url = self._provider_url(provider, "status_endpoint", provider_submission_id)
        try:
            response = requests.get(
                url,
                headers=self._submit_headers(provider),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            return {"ok": False, "error": str(e), "data": None}

        data = self._safe_json(response)
        ok = 200 <= response.status_code < 300
        return {
            "ok": ok,
            "error": None if ok else self._response_error(response, data),
            "status_code": response.status_code,
            "data": data,
        }

    def _confirm_provider_submission(self, provider: str, provider_submission_id: str,
                                     payload: Dict = None) -> Dict:
        url = self._provider_url(provider, "decision_endpoint", provider_submission_id)
        body = payload or {}
        unsafe_path = self._find_sensitive_key(body)
        if unsafe_path:
            return {"ok": False, "error": f"Sensitive field blocked in confirm payload: {unsafe_path}"}

        try:
            response = requests.post(
                url,
                json=body,
                headers=self._submit_headers(provider),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            return {"ok": False, "error": str(e)}

        data = self._safe_json(response)
        ok = 200 <= response.status_code < 300
        return {
            "ok": ok,
            "error": None if ok else self._response_error(response, data),
            "status_code": response.status_code,
            "data": data,
        }

    def _update_submission_fields(self, submission_id: str, fields: Dict) -> Optional[Dict]:
        submissions = self._read_submissions()
        target = None
        for submission in submissions:
            if submission.get("submission_id") == submission_id:
                submission.update(fields)
                submission["updated_at"] = datetime.now().isoformat(timespec="seconds")
                target = submission
                break

        if target:
            self._write_submissions(submissions)
        return target

    def _fail_workflow(self, submission_id: str, error: str, notify_callback=None) -> Dict:
        submission = self._update_submission_fields(
            submission_id,
            {
                "status": self.WORKFLOW_FAILED_STATUS,
                "workflow_error": error,
            },
        )
        self._notify(
            notify_callback,
            "❌ Sales workflow failed",
            f"<code>{error or 'unknown error'}</code>",
            "ERROR",
        )
        return {"ok": False, "error": error, "submission": submission}

    def _notify(self, callback, title: str, message: str, level: str):
        if callback:
            callback(title, message, level)

    def _submit_to_provider(self, submission: Dict, provider: str = None,
                            extra_payload: Dict = None) -> Dict:
        provider = provider or submission.get("provider")
        api_key_env = self._provider_api_key_env(provider)
        api_key = self._provider_api_key(provider)

        if not api_key:
            error = f"Missing API key env var: {api_key_env}"
            return {
                "ok": False,
                "error": error,
                "submission_updates": self._external_submit_updates(
                    status=self.SUBMIT_FAILED_STATUS,
                    error=error,
                ),
            }

        submit_text = self._build_submit_text(submission)
        if not submit_text:
            error = "No db_id values available for submit text payload"
            return {
                "ok": False,
                "error": error,
                "submission_updates": self._external_submit_updates(
                    status=self.SUBMIT_FAILED_STATUS,
                    error=error,
                ),
            }
        unsafe_text = self._find_sensitive_text(submit_text)
        if unsafe_text:
            error = "Sensitive value blocked in submit text payload"
            return {
                "ok": False,
                "error": error,
                "submission_updates": self._external_submit_updates(
                    status=self.SUBMIT_FAILED_STATUS,
                    error=error,
                ),
            }

        try:
            url = self._submit_url(provider)
        except ValueError as e:
            error = str(e)
            return {
                "ok": False,
                "error": error,
                "submission_updates": self._external_submit_updates(
                    status=self.SUBMIT_FAILED_STATUS,
                    error=error,
                ),
            }

        try:
            filename = f"{submission.get('submission_id') or 'submission'}.txt"
            files = {
                self._submit_file_field(provider): (
                    filename,
                    submit_text.encode("utf-8"),
                    "text/plain",
                )
            }
            response = requests.post(
                url,
                files=files,
                headers=self._submit_headers(provider, include_content_type=False),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            error = str(e)
            return {
                "ok": False,
                "error": error,
                "submission_updates": self._external_submit_updates(
                    status=self.SUBMIT_FAILED_STATUS,
                    error=error,
                    url=url,
                ),
            }

        provider_submission_id = None
        response_data = None
        if response.content:
            try:
                response_data = response.json()
                provider_submission_id = self._extract_provider_submission_id(response_data)
            except ValueError:
                response_data = None

        ok = 200 <= response.status_code < 300
        error = None if ok else self._response_error(response, response_data)
        return {
            "ok": ok,
            "error": error,
            "status_code": response.status_code,
            "provider_submission_id": provider_submission_id,
            "submission_updates": self._external_submit_updates(
                status=self.SUBMITTED_STATUS if ok else self.SUBMIT_FAILED_STATUS,
                error=error,
                url=url,
                status_code=response.status_code,
                provider_submission_id=provider_submission_id,
                response_data=response_data,
                request_format="multipart_txt_db_id",
                request_line_count=len([line for line in submit_text.splitlines() if line.strip()]),
            ),
        }

    def _build_submit_text(self, submission: Dict) -> str:
        lines = []
        for item in submission.get("items", []):
            db_id = item.get("db_id")
            if db_id is not None and str(db_id).strip():
                lines.append(str(db_id).strip())
        return "\n".join(lines)

    def _submit_file_field(self, provider: str) -> str:
        info = self._provider_info(provider)
        return info.get("submit_file_field") or self.submit_file_field

    def _submit_url(self, provider: str) -> str:
        return self._provider_url(provider, "submit_endpoint")

    def _provider_url(self, provider: str, endpoint_key: str,
                      provider_submission_id: str = None) -> str:
        info = self._provider_info(provider)
        base_url = (info.get("base_url") or "").rstrip("/")
        endpoint = info.get(endpoint_key) or ""
        if not base_url:
            raise ValueError(f"Sales provider {provider} base_url is not configured")

        endpoint = endpoint.replace("{submission_id}", str(provider_submission_id or ""))
        endpoint = endpoint.replace("{task_id}", str(provider_submission_id or ""))
        return f"{base_url}/{endpoint.lstrip('/')}"

    def _submit_headers(self, provider: str, include_content_type: bool = True) -> Dict[str, str]:
        info = self._provider_info(provider)
        auth = info.get("auth", "Authorization: Bearer")
        api_key = self._provider_api_key(provider)
        headers = {"Accept": "application/json"}
        if include_content_type:
            headers["Content-Type"] = "application/json"

        if auth == "X-API-Key":
            headers["X-API-Key"] = api_key
            return headers

        if auth.lower().startswith("authorization"):
            scheme = info.get("auth_scheme") or "Bearer"
            headers["Authorization"] = f"{scheme} {api_key}"
            return headers

        headers[auth] = api_key
        return headers

    def _provider_info(self, provider: str) -> Dict:
        base = dict(self.SUPPORTED_PROVIDERS.get(provider, {}))
        override = self.provider_overrides.get(provider, {})
        base.update(override)

        if provider == self.provider:
            if self.base_url_override:
                base["base_url"] = self.base_url_override
            if self.submit_endpoint_override:
                base["submit_endpoint"] = self.submit_endpoint_override
            if self.auth_header_override:
                base["auth"] = self.auth_header_override
            if self.auth_scheme_override:
                base["auth_scheme"] = self.auth_scheme_override

        return base

    def _provider_api_key_env(self, provider: str) -> str:
        info = self._provider_info(provider)
        default_env = {
            "tskupka": "TSKUPKA_API_KEY",
            "tokenbuyrobot": "TOKENBUYROBOT_API_KEY",
        }.get(provider, self.api_key_env)
        if info.get("api_key_env"):
            return info["api_key_env"]
        if provider == self.provider and self.mode == "external_submit":
            return self.api_key_env
        return default_env

    def _provider_api_key(self, provider: str) -> str:
        return os.environ.get(self._provider_api_key_env(provider), "")

    def _safe_json(self, response):
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def _response_error(self, response, response_data=None) -> str:
        base = f"Provider HTTP {response.status_code}"
        preview = self._response_preview(response, response_data)
        return f"{base}: {preview}" if preview else base

    def _response_preview(self, response, response_data=None) -> Optional[str]:
        if response_data is not None:
            try:
                text = json.dumps(self._redact_sensitive(response_data), ensure_ascii=False)
            except (TypeError, ValueError):
                text = str(response_data)
        else:
            text = getattr(response, "text", "") or ""

        text = " ".join(str(text).split())
        if not text:
            return None
        return text[:300]

    def _redact_sensitive(self, value):
        if isinstance(value, dict):
            redacted = {}
            for key, nested in value.items():
                if str(key).lower() in self.SENSITIVE_KEYS:
                    redacted[key] = "***"
                else:
                    redacted[key] = self._redact_sensitive(nested)
            return redacted
        if isinstance(value, list):
            return [self._redact_sensitive(item) for item in value]
        return value

    def _external_submit_updates(
        self,
        status: str,
        error: str = None,
        url: str = None,
        status_code: int = None,
        provider_submission_id: str = None,
        response_data: Dict = None,
        request_format: str = None,
        request_line_count: int = None,
    ) -> Dict:
        external = {
            "ok": status == self.SUBMITTED_STATUS,
            "attempted_at": datetime.now().isoformat(timespec="seconds"),
            "status_code": status_code,
            "provider_submission_id": provider_submission_id,
            "error": error,
        }
        if url:
            external["url"] = url
        if response_data is not None:
            external["response_keys"] = sorted(response_data.keys()) if isinstance(response_data, dict) else []
        if request_format:
            external["request_format"] = request_format
        if request_line_count is not None:
            external["request_line_count"] = request_line_count

        return {
            "status": status,
            "external_submit": external,
        }

    def _extract_provider_submission_id(self, response_data) -> Optional[str]:
        if not isinstance(response_data, dict):
            return None

        for key in ("submission_id", "task_id", "id", "uuid"):
            value = response_data.get(key)
            if value:
                return str(value)

        data = response_data.get("data")
        if isinstance(data, dict):
            return self._extract_provider_submission_id(data)
        return None

    def _find_sensitive_key(self, value, path: str = "$") -> Optional[str]:
        if isinstance(value, dict):
            for key, nested in value.items():
                key_text = str(key).lower()
                if key_text in self.SENSITIVE_KEYS:
                    return f"{path}.{key}"
                found = self._find_sensitive_key(nested, f"{path}.{key}")
                if found:
                    return found
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                found = self._find_sensitive_key(nested, f"{path}[{index}]")
                if found:
                    return found
        return None

    def _find_sensitive_text(self, value: str) -> bool:
        if not value:
            return False
        return any(marker in value.lower() for marker in self.SENSITIVE_KEYS)

    def _extract_price(self, value):
        if isinstance(value, dict):
            for key, nested in value.items():
                key_text = str(key).lower()
                if any(part in key_text for part in ("price", "amount", "cost")):
                    parsed = self._parse_number(nested)
                    if parsed is not None:
                        return parsed

            for nested in value.values():
                parsed = self._extract_price(nested)
                if parsed is not None:
                    return parsed
        elif isinstance(value, list):
            for nested in value:
                parsed = self._extract_price(nested)
                if parsed is not None:
                    return parsed
        return None

    def _extract_status(self, value):
        if isinstance(value, dict):
            for key, nested in value.items():
                key_text = str(key).lower()
                if key_text in {"status", "state"} and nested is not None:
                    return str(nested)

            for nested in value.values():
                status = self._extract_status(nested)
                if status:
                    return status
        elif isinstance(value, list):
            for nested in value:
                status = self._extract_status(nested)
                if status:
                    return status
        return None

    def _is_completed_status(self, status) -> bool:
        if status is None:
            return False
        return str(status).strip().lower() in self.COMPLETED_PROVIDER_STATUSES

    def _parse_number(self, value):
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            normalized = value.replace(",", ".").strip()
            try:
                return float(normalized)
            except ValueError:
                return None
        return None

    def _sum_price(self, items: List[Dict]) -> float:
        total = 0.0
        for item in items:
            try:
                total += float(item.get("price") or 0)
            except (TypeError, ValueError):
                continue
        return round(total, 2)

    def _sum_count(self, submissions: List[Dict]) -> int:
        return sum(int(item.get("accepted_count") or 0) for item in submissions)

    def _sum_submission_price(self, submissions: List[Dict]) -> float:
        return round(sum(float(item.get("total_price") or 0) for item in submissions), 2)

    def _count_by_status(self, submissions: List[Dict], status: str) -> int:
        return sum(
            1 for item in submissions
            if str(item.get("status", "")).upper() == status
        )

    def _newest_first(self, submissions: List[Dict]) -> List[Dict]:
        indexed = list(enumerate(submissions))
        indexed.sort(
            key=lambda pair: (pair[1].get("created_at", ""), pair[0]),
            reverse=True,
        )
        return [item for _, item in indexed]
