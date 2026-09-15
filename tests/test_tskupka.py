"""Tskupka contract tests: all external HTTP is mocked, all records synthetic."""
import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import requests
from werkzeug.security import generate_password_hash

import dashboard_api as api
from modules.database import Database
from modules.tskupka import TskupkaService


def reply(data=None, code=200):
    response = requests.Response()
    response.status_code = code
    response._content = json.dumps({'ok': True, 'data': data or {}}).encode()
    return response


class TskupkaTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = {'database': {'path': str(Path(directory.name) / 'test.db')}}
        self.key = secrets.token_urlsafe(40)
        self.password = secrets.token_urlsafe(24)
        env = patch.dict(os.environ, {
            'DASHBOARD_SECRET_KEY': secrets.token_urlsafe(48),
            'DASHBOARD_PASSWORD_HASH': generate_password_hash(self.password),
            'DASHBOARD_USERNAME': 'review', 'DASHBOARD_PUBLIC_URL': 'http://localhost',
            'TSKUPKA_API_KEY': self.key,
        })
        env.start()
        self.addCleanup(env.stop)
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected HTTP'))
        self.http = network.start()
        self.addCleanup(network.stop)
        api.init_dashboard(None, self.config)
        self.client = api.app.test_client()
        csrf = self.client.get('/api/auth/session').json['csrf']
        login = self.client.post('/api/auth/login', json={'username': 'review', 'password': self.password},
                                 headers={'X-CSRF-Token': csrf})
        self.headers = {'X-CSRF-Token': login.json['csrf']}
        api.db.add_token('synthetic-tskupka-token')
        api.db.update_token_status('synthetic-tskupka-token', 'ready')
        self.export_id = api.db.commit_export(['synthetic-tskupka-token'], [])['export_id']
        self.url = f'/api/tokens/export/{self.export_id}/tskupka'

    def respond(self, data=None, code=200):
        self.http.side_effect = None
        self.http.return_value = reply(data, code)

    def submit(self):
        return self.client.post(self.url, json={}, headers=self.headers)

    def test_send_without_confirmation_and_keep_download_in_monitor_mode(self):
        self.respond({'task_id': 91, 'unique_tokens': 1, 'tokens': 'never-store-this', 'secret': self.key})
        result = self.submit()
        self.assertEqual(result.status_code, 200)
        method, url = self.http.call_args.args
        self.assertEqual((method, url), ('POST', 'https://tskupka.cc/v1/tasks'))
        kwargs = self.http.call_args.kwargs
        self.assertEqual(kwargs['data'], {'skip_confirmation': 'true'})
        self.assertEqual(kwargs['files']['file'][1], b'synthetic-tskupka-token\n')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer ' + self.key)
        self.assertFalse(kwargs['allow_redirects'])
        status = self.client.get('/api/tokens/export/status')
        self.assertTrue(status.json['tskupka_configured'])
        self.assertFalse(status.json['worker_running'])
        self.assertEqual(status.json['history'][0]['tskupka']['task_id'], 91)
        for secret in ('synthetic-tskupka-token', 'never-store-this', self.key):
            self.assertNotIn(secret, status.get_data(as_text=True))
            self.assertNotIn(secret, result.get_data(as_text=True))
        self.assertEqual(self.client.get('/api/tokens/export/download').data, b'synthetic-tskupka-token\n')

    def test_auth_and_csrf_and_missing_key(self):
        for suffix in ('', '/refresh'):
            self.assertEqual(api.app.test_client().post(self.url + suffix).status_code, 401)
            self.assertEqual(self.client.post(self.url + suffix).status_code, 403)
        api.tskupka.client.api_key = ''
        self.assertEqual(self.submit().status_code, 503)
        self.assertIsNone(api.db.get_tskupka_task(self.export_id))
        self.http.assert_not_called()

    def test_missing_export_and_invalid_id_do_not_issue_http(self):
        for ident, code in ((9999, 404), (0, 400), (2**64, 400)):
            response = self.client.post(f'/api/tokens/export/{ident}/tskupka', headers=self.headers)
            self.assertEqual(response.status_code, code)
        self.http.assert_not_called()

    def test_duplicate_is_blocked_after_database_reopen(self):
        self.respond({'task_id': 91})
        self.assertEqual(self.submit().status_code, 200)
        api.tskupka = TskupkaService(Database(self.config['database']['path']), self.key)
        self.assertEqual(self.submit().status_code, 409)
        self.http.assert_called_once()

    def test_atomic_claim_allows_only_one_parallel_submission(self):
        self.respond({'task_id': 91})
        def submit(_):
            try:
                return api.tskupka.submit(self.export_id)['task_id']
            except RuntimeError:
                return 'duplicate'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(submit, range(2))), [91, 'duplicate'])
        self.http.assert_called_once()

    def test_timeout_blocks_retries_and_does_not_leak_exception(self):
        self.http.side_effect = requests.Timeout(self.key)
        response = self.submit()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(self.key, response.get_data(as_text=True))
        self.assertEqual(api.db.get_tskupka_task(self.export_id)['state'], 'unknown')
        api.tskupka = TskupkaService(Database(self.config['database']['path']), self.key)
        self.assertEqual(self.submit().status_code, 409)
        self.http.assert_called_once()

    def test_definite_rejection_can_be_retried(self):
        self.respond(code=401)
        self.assertEqual(self.submit().status_code, 502)
        self.assertEqual(api.db.get_tskupka_task(self.export_id)['state'], 'rejected')
        self.respond({'task_id': 92})
        self.assertEqual(self.submit().status_code, 200)
        self.assertEqual(self.http.call_count, 2)

    def test_malformed_success_does_not_allow_resend(self):
        self.respond({'task_id': True})
        self.assertEqual(self.submit().status_code, 502)
        self.assertEqual(self.submit().status_code, 409)
        self.http.assert_called_once()

    def test_crash_reservation_is_not_resubmitted(self):
        self.assertTrue(api.db.claim_tskupka_export(self.export_id))
        api.tskupka = TskupkaService(Database(self.config['database']['path']), self.key)
        self.assertEqual(self.submit().status_code, 409)
        self.http.assert_not_called()

    def test_refresh_keeps_task_and_prior_result_on_failure(self):
        self.respond({'task_id': 91, 'unique_tokens': 1})
        self.assertEqual(self.submit().status_code, 200)
        self.respond({'id': 91, 'status': 'completed', 'amount_paid': 17.5, 'checker_stats': {'token': self.key}})
        result = self.client.post(self.url + '/refresh', headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['task']['remote_status'], 'completed')
        self.assertEqual(result.json['task']['summary'], {'unique_tokens': 1, 'amount_paid': 17.5})
        self.assertNotIn(self.key, result.get_data(as_text=True))
        self.http.side_effect = requests.Timeout()
        self.assertEqual(self.client.post(self.url + '/refresh', headers=self.headers).status_code, 502)
        self.assertEqual(api.db.get_tskupka_task(self.export_id)['remote_status'], 'completed')
        self.assertEqual(self.submit().status_code, 409)

    def test_object_price_result_records_final_total(self):
        # Tskupka отдаёт price_result объектом; берём final_total как итоговую выплату.
        self.respond({'task_id': 91, 'unique_tokens': 1})
        self.assertEqual(self.submit().status_code, 200)
        self.respond({'id': 91, 'status': 'manually_completed',
                      'price_result': {'initial_total': 1172.16, 'final_total': 1172.16, 'deduction': 0.0}})
        self.assertEqual(self.client.post(self.url + '/refresh', headers=self.headers).status_code, 200)
        with api.db.get_connection() as conn:
            row = conn.execute('SELECT price_minor FROM tskupka_tasks WHERE export_id=?', (self.export_id,)).fetchone()
        self.assertEqual(row['price_minor'], 117216)  # 1172.16 ₽

    def test_manual_refresh_pulls_initial_when_final_not_ready(self):
        # Незавершённая задача (awaiting_manual, final_total=0 плейсхолдер): ручное
        # обновление подтягивает предварительную сумму initial_total, не final.
        self.respond({'task_id': 91, 'unique_tokens': 1})
        self.assertEqual(self.submit().status_code, 200)
        self.respond({'id': 91, 'status': 'awaiting_manual', 'finished_at': None,
                      'price_result': {'initial_total': 3601.08, 'final_total': 0.0, 'deduction': 0.0}})
        self.assertEqual(self.client.post(self.url + '/refresh', headers=self.headers).status_code, 200)
        row = api.db.get_tskupka_task(self.export_id)
        self.assertEqual(row['price_minor'], 360108)
        self.assertIsNone(row['error'])
        self.assertEqual(row['remote_status'], 'awaiting_manual')

    def test_force_resend_unblocks_unknown_result(self):
        # Первая отправка: сервер 503 → результат неизвестен, обычный повтор заблокирован.
        self.respond(code=503)
        self.assertEqual(self.submit().status_code, 502)
        self.assertEqual(api.db.get_tskupka_task(self.export_id)['state'], 'unknown')
        self.assertEqual(self.submit().status_code, 409)
        # Форс-повтор владельцем проходит и создаёт задачу.
        self.respond({'task_id': 92})
        forced = self.client.post(self.url + '?force=1', json={}, headers=self.headers)
        self.assertEqual(forced.status_code, 200)
        self.assertEqual(api.db.get_tskupka_task(self.export_id)['task_id'], 92)

    def test_v4_migration_preserves_existing_export_and_backs_up(self):
        with api.db.get_connection() as conn:
            conn.execute('DROP TABLE tskupka_tasks')
            conn.execute('PRAGMA user_version=4')
        db = Database(self.config['database']['path'])
        self.assertEqual(db.schema_version(), 6)
        self.assertEqual(db.get_export_tokens(self.export_id), ['synthetic-tskupka-token'])
        self.assertEqual(len(list(Path(db.db_path).parent.glob('test.db.backup_*'))), 1)
        Database(db.db_path)
        self.assertEqual(len(list(Path(db.db_path).parent.glob('test.db.backup_*'))), 1)
