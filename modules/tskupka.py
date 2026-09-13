"""Tskupka submission of saved exports. No automatic retries of POST requests."""
import math
import re
import threading

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

    @property
    def configured(self):
        return bool(self.client.api_key)

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
        return self.db.get_tskupka_task(export_id)

    def refresh(self, export_id):
        task = self.db.get_tskupka_task(export_id)
        if not task or not task['task_id']:
            raise ValueError('No task ID')
        if not self.refresh_lock.acquire(blocking=False):
            raise RuntimeError('Обновление Tskupka уже выполняется.')
        try:
            data = self.client.status(task['task_id'])
            if data.get('id') != task['task_id']:
                raise TskupkaError('Tskupka вернула другой ID задачи.')
            status = data.get('status')
            if not isinstance(status, str) or not re.fullmatch(r'[a-z_]{1,64}', status):
                raise TskupkaError('Tskupka вернула неизвестный формат статуса.')
            self.db.save_tskupka_task(export_id, 'submitted', remote_status=status,
                                     summary={**task['summary'], **safe_summary(data)})
        except TskupkaError as exc:
            self.db.save_tskupka_task(export_id, 'submitted', error=str(exc))
            raise
        finally:
            self.refresh_lock.release()
        return self.db.get_tskupka_task(export_id)
