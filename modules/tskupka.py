"""Tskupka submission of saved exports. No automatic retries of POST requests."""
import math
import re
import threading
import logging
from modules.finance import Finance

import requests


class TskupkaError(Exception):
    def __init__(self, message, rejected=False):
        super().__init__(message)
        self.rejected = rejected


class TskupkaClient:
    def __init__(self, api_key):
        self.api_key = api_key.strip()

    def request(self, method, path, **kwargs):
        try:
            with requests.Session() as client:
                client.trust_env = False
                response = client.request(method, 'https://tskupka.cc/v1' + path,
                    headers={'Authorization': 'Bearer ' + self.api_key},
                    timeout=(5, 30), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise TskupkaError('Нет ответа Tskupka. Результат операции неизвестен.') from None
        if response.status_code != 200:
            rejected = response.status_code in {400, 401, 403, 404, 409, 413, 415, 422, 429}
            raise TskupkaError(f'Tskupka: HTTP {response.status_code}.', rejected=rejected)
        try:
            payload = response.json()
        except ValueError:
            raise TskupkaError('Некорректный ответ Tskupka. Результат операции неизвестен.') from None
        if not isinstance(payload, dict) or payload.get('ok') is not True or not isinstance(payload.get('data'), dict):
            raise TskupkaError('Неожиданный ответ Tskupka. Результат операции неизвестен.')
        return payload['data']

    def create(self, tokens):
        return self.request('POST', '/tasks', data={'skip_confirmation': 'true'},
                            files={'file': ('tokens.txt', ('\n'.join(tokens) + '\n').encode(), 'text/plain')})

    def status(self, task_id):
        return self.request('GET', f'/tasks/{task_id}')

    def me(self):
        return self.request('GET', '/me')


def price_from(data, key):
    """Значение суммы из ответа Tskupka по ключу ('initial_total' | 'final_total').

    price_result приходит объектом ({initial_total, final_total, deduction, ...}).
    Поддержан и старый формат, где price_result — число/строка-число (тогда оба ключа
    дают это значение). None — если поля/суммы ещё нет. money_minor проверит число."""
    result = data.get('price_result')
    if isinstance(result, dict):
        return result.get(key)
    return result


def safe_summary(data):
    """Only documented numeric totals reach storage/UI; never arbitrary API data."""
    result = {}
    for key in ('total_tokens', 'unique_tokens', 'duplicates', 'in_db', 'tokens_count', 'amount_paid'):
        value = data.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            result[key] = value
    return result


class TskupkaService:
    def __init__(self, db, api_key):
        self.db = db
        self.client = TskupkaClient(api_key)
        self.refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not self.configured or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, name='TskupkaPrices', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _poll(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                logging.getLogger(__name__).error('Ошибка опроса Tskupka: %s', type(exc).__name__)
            self._stop.wait(1)

    def poll_once(self):
        if not self.configured:
            return
        for export_id in Finance(self.db).due_tasks():
            if self._stop.is_set():
                break
            try:
                self.refresh(export_id, background=True)
            except (TskupkaError, RuntimeError, ValueError):
                continue

    @property
    def configured(self):
        return bool(self.client.api_key)

    def balance(self):
        """Текущий баланс аккаунта Tskupka (₽). Только по запросу — без фонового опроса."""
        value = self.client.me().get('balance')
        if type(value) not in (int, float) or not math.isfinite(value):
            raise TskupkaError('Tskupka вернула некорректный баланс.')
        return float(value)

    def submit(self, export_id):
        if not self.db.claim_tskupka_export(export_id):
            raise RuntimeError('Выгрузка уже отправлена или результат прошлой отправки неизвестен.')
        try:
            data = self.client.create(self.db.get_export_tokens(export_id))
            task_id = data.get('task_id')
            if type(task_id) is not int or not 0 < task_id <= 2**63 - 1:
                raise TskupkaError('Tskupka не вернула ID задачи. Повторная отправка заблокирована.')
        except TskupkaError as exc:
            self.db.save_tskupka_task(export_id, 'rejected' if exc.rejected else 'unknown', error=str(exc))
            raise
        # If this write fails, the durable 'sending' reservation still prevents duplicates.
        self.db.save_tskupka_task(export_id, 'submitted', task_id=task_id, summary=safe_summary(data))
        Finance(self.db).release_poll(export_id)
        return self.db.get_tskupka_task(export_id)

    def refresh(self, export_id, background=False):
        task = self.db.get_tskupka_task(export_id)
        if not task or not task['task_id']:
            raise ValueError('No task ID')
        if not self.refresh_lock.acquire(blocking=False):
            raise RuntimeError('Обновление Tskupka уже выполняется.')
        claimed = False
        try:
            claimed = Finance(self.db).claim_poll(export_id, force=not background)
            if not claimed:
                return task
            data = self.client.status(task['task_id'])
            if data.get('id') != task['task_id']:
                raise TskupkaError('Tskupka вернула другой ID задачи.')
            status = data.get('status')
            if not isinstance(status, str) or not re.fullmatch(r'[a-z_]{1,64}', status):
                raise TskupkaError('Tskupka вернула неизвестный формат статуса.')
            self.db.save_tskupka_task(export_id, 'submitted', remote_status=status,
                                     summary={**task['summary'], **safe_summary(data)})
            raw_price = data.get('price_result')
            initial = price_from(data, 'initial_total')
            final_value = price_from(data, 'final_total')
            # Неизвестная структура: объект без initial_total и final_total.
            if isinstance(raw_price, dict) and initial is None and final_value is None:
                raise TskupkaError('Неизвестный формат price_result: сумма ещё не учтена.')
            final_ready = bool(data.get('finished_at')) or status in (
                'completed', 'manually_completed', 'cancelled', 'rejected')
            try:
                if background:
                    # Авто-опрос: как только есть предварительная сумма (initial_total) —
                    # фиксируем её и прекращаем опрос (задача выпадает из due_tasks).
                    if initial is not None:
                        Finance(self.db).receive_price(export_id, initial)
                elif final_ready and final_value is not None:
                    # Ручное обновление: подтягиваем актуальный final_total. Если он ещё
                    # не готов — ничего не меняем (оставляем ранее записанную сумму).
                    Finance(self.db).set_price(export_id, final_value)
            except ValueError:
                raise TskupkaError('Неизвестный формат price_result: сумма ещё не учтена.') from None
        except TskupkaError as exc:
            self.db.save_tskupka_task(export_id, 'submitted', error=str(exc))
            raise
        finally:
            try:
                if claimed:
                    Finance(self.db).release_poll(export_id)
            finally:
                self.refresh_lock.release()
        return self.db.get_tskupka_task(export_id)
