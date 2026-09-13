"""Offline regression tests: no external HTTP, production data, or workers."""
import ast
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from queue import Queue

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from werkzeug.security import generate_password_hash
from modules.database import Database
from core.pipeline import TokenPipeline
from modules.validator import TokenValidator, ValidationUnavailable
from scripts.prepare_service_config import prepare
import dashboard_api as api


class ReviewChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.config = {'database': {'path': str(self.path / 'test.db')},
                       'dashboard': {'host': '127.0.0.1'},
                       'logging': {'file': str(self.path / 'test.log')},
                       'telegram': {'chat_id': '12345'},
                       'lzt': {'enabled': False},
                       'cleaner': {'close_channels': True}}
        self.pipe = TokenPipeline.__new__(TokenPipeline)
        self.pipe.db = Database(self.config['database']['path'])
        self.pipe.config = self.config
        self.pipe.config_path = str(self.path / 'config.json')
        Path(self.pipe.config_path).write_text(json.dumps(self.config))
        self.pipe.running = True
        self.pipe._intake_lock = threading.Lock()
        self.pipe._export_lock = threading.Lock()
        self.pipe.close_channels = True
        self.pipe.new_tokens_queue = Queue()
        self.pipe.validator = SimpleNamespace(validate_token=Mock(return_value=(True, 'synthetic')))
        self.pipe.telegram = SimpleNamespace(send_tokens_file=Mock(return_value=True))
        self.pipe.ready_tokens_notification_sent = False
        self.password = secrets.token_urlsafe(24)
        env = patch.dict(os.environ, {
            'DASHBOARD_SECRET_KEY': secrets.token_urlsafe(48),
            'DASHBOARD_PASSWORD_HASH': generate_password_hash(self.password),
            'DASHBOARD_USERNAME': 'review',
            'DASHBOARD_PUBLIC_URL': 'http://localhost',
            'TELEGRAM_ALLOWED_USER_IDS': '12345'})
        env.start()
        self.addCleanup(env.stop)
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('External HTTP prohibited'))
        network.start()
        self.addCleanup(network.stop)
        api.init_dashboard(self.pipe, self.config)
        self.client = api.app.test_client()
        csrf = self.client.get('/api/auth/session').json['csrf']
        response = self.client.post('/api/auth/login', json={'username': 'review', 'password': self.password}, headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 200)
        self.headers = {'X-CSRF-Token': response.json['csrf']}

    def ready(self, token):
        self.pipe.db.add_token(token)
        self.pipe.db.update_token_status(token, 'ready')

    def export(self):
        # Execute the real route worker synchronously; no workers or network start.
        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        with patch.object(api.threading, 'Thread', ImmediateThread), patch('core.pipeline.time.sleep'):
            response = self.client.post('/api/tokens/export', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        return self.client.get('/api/tokens/export/status').json

    def test_python_syntax(self):
        files = [ROOT / p for p in __import__('subprocess').check_output(
            ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '*.py'],
            cwd=ROOT, text=True).splitlines()]
        for path in files:
            with self.subTest(path=path.relative_to(ROOT)):
                ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        print(f'Parsed {len(files)} Python files')

    def test_authentication_and_csrf_on_new_routes(self):
        anonymous = api.app.test_client()
        for route in ('/api/tokens/export/status', '/api/tokens/export/download', '/api/lzt/balance'):
            self.assertEqual(anonymous.get(route).status_code, 401)
        for route in ('/api/tokens/export', '/api/tokens/upload', '/api/settings/toggle'):
            self.assertEqual(anonymous.post(route, json={}).status_code, 401)
            self.assertEqual(self.client.post(route, json={}).status_code, 403)

    def test_stream_refreshes_unchanged_data_and_closes_after_revocation(self):
        class Clock:
            value = 1000
            def time(self): return self.value
            def monotonic(self): return self.value
            def sleep(self, seconds): self.value += seconds
        clock = Clock()
        with patch.object(api, 'time', clock):
            response = self.client.get('/api/stream', buffered=False)
            try:
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('Connection', response.headers)
                chunks = iter(response.response)
                self.assertIn(b'retry:', next(chunks))
                self.assertIn(b'event: update', next(chunks))
                self.assertIn(b'event: update', next(chunks))
                self.assertEqual(clock.value, 1005)
                with api.state_lock:
                    api.auth_sessions.clear()
                self.assertIn(b'event: auth-expired', next(chunks))
                with self.assertRaises(StopIteration):
                    next(chunks)
            finally:
                response.close()
        slots = [api.stream_slots.acquire(blocking=False) for _ in range(4)]
        for acquired in slots:
            if acquired:
                api.stream_slots.release()
        self.assertEqual(slots, [True] * 4)

    def test_stream_rejects_excess_connections_and_anonymous_clients(self):
        self.assertEqual(api.app.test_client().get('/api/stream').status_code, 401)
        for _ in range(4):
            self.assertTrue(api.stream_slots.acquire(blocking=False))
        try:
            self.assertEqual(self.client.get('/api/stream').status_code, 503)
            self.assertEqual(self.client.get('/api/health').status_code, 200)
        finally:
            for _ in range(4):
                api.stream_slots.release()

    def test_happy_export_download(self):
        self.ready('synthetic-record-A')
        job = self.export()
        self.assertTrue(job['available'])
        self.assertIsNone(job['error'])
        self.assertEqual(self.client.get('/api/tokens/export/download').data, b'synthetic-record-A\n')
        self.assertEqual(self.pipe.db.get_token_info('synthetic-record-A')['status'], 'sent')
        self.pipe.telegram.send_tokens_file.assert_called_once_with(['synthetic-record-A'])

    def test_toggle_persists_in_normal_writable_configuration(self):
        response = self.client.post('/api/settings/toggle', json={'key': 'close_channels', 'value': False}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.pipe.close_channels)
        self.assertFalse(json.loads(Path(self.pipe.config_path).read_text())['cleaner']['close_channels'])

    def test_removed_lzt_toggle_is_rejected(self):
        response = self.client.post('/api/settings/toggle', json={'key': 'lzt_enabled', 'value': True}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_telegram_failure_is_explicit(self):
        self.ready('synthetic-record-A')
        self.pipe.telegram.send_tokens_file.return_value = False
        job = self.export()
        self.assertIsNone(job['error'])
        self.assertEqual(job['valid'], 1)
        self.assertEqual(self.pipe.db.get_token_info('synthetic-record-A')['status'], 'sent')
        self.assertEqual(job['delivery'], 'failed')
        self.assertEqual(job['history'][0]['delivery'], 'failed')

    def test_empty_export_preserves_download(self):
        self.ready('synthetic-record-A')
        self.pipe.telegram.send_tokens_file.return_value = False
        self.assertTrue(self.export()['available'])
        self.assertEqual(self.client.get('/api/tokens/export/download').status_code, 200)
        self.assertTrue(self.export()['available'])
        self.assertEqual(self.client.get('/api/tokens/export/download').status_code, 200)
        self.assertEqual(self.pipe.db.get_token_info('synthetic-record-A')['status'], 'sent')

    def test_mid_export_database_error_rolls_back_entire_batch(self):
        self.ready('synthetic-record-A')
        self.ready('synthetic-record-B')
        with self.pipe.db.get_connection() as conn:
            conn.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON export_items
                WHEN NEW.token='synthetic-record-B' BEGIN
                SELECT RAISE(ABORT, 'synthetic write failure'); END""")
        job = self.export()
        self.assertEqual(job['error'], 'IntegrityError')
        self.assertEqual(self.pipe.db.get_token_info('synthetic-record-A')['status'], 'ready')
        self.assertEqual(self.pipe.db.get_token_info('synthetic-record-B')['status'], 'ready')
        self.assertEqual(self.client.get('/api/tokens/export/download').status_code, 404)
        self.pipe.telegram.send_tokens_file.assert_not_called()

    def test_stopped_worker_rejects_upload(self):
        self.pipe.running = False
        response = self.client.post('/api/tokens/upload', json={'tokens': 'synthetic-record-A'}, headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.pipe.new_tokens_queue.qsize(), 0)
        self.assertIsNone(self.pipe.db.get_token_info('synthetic-record-A'))

    def test_explicit_monitoring_mode_has_no_upload_or_export(self):
        # Same pipeline=None mode selected by the shipped systemd entrypoint.
        with patch.object(api, 'pipeline', None):
            for route in ('/api/tokens/upload', '/api/tokens/export'):
                response = self.client.post(route, json={'tokens': 'synthetic-record-A'}, headers=self.headers)
                self.assertEqual(response.status_code, 503)

    def test_upload_reports_database_failure(self):
        with patch.object(self.pipe.db, 'add_token', side_effect=sqlite3.OperationalError('synthetic write failure')):
            response = self.client.post('/api/tokens/upload', json={'tokens': 'synthetic-record-A'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['added'], 0)
        self.assertEqual(response.json['errors'], 1)
        self.assertFalse(response.json['success'])
        self.assertEqual(self.pipe.new_tokens_queue.qsize(), 0)
        self.assertIsNone(self.pipe.db.get_token_info('synthetic-record-A'))

    def test_download_survives_database_reopen_and_api_reinitialization(self):
        self.ready('synthetic-record-A')
        self.export()
        self.pipe.db = Database(self.config['database']['path'])
        api.init_dashboard(self.pipe, self.config)
        self.assertEqual(self.pipe.db.get_export_tokens(), ['synthetic-record-A'])
        self.assertEqual(self.pipe.db.list_exports()[0]['delivery'], 'sent')

    def test_history_keeps_multiple_exports(self):
        self.ready('synthetic-record-A')
        first = self.export()['export_id']
        self.ready('synthetic-record-B')
        second = self.export()['export_id']
        self.assertNotEqual(first, second)
        self.assertEqual(self.client.get(f'/api/tokens/export/download?id={first}').data, b'synthetic-record-A\n')
        self.assertEqual(self.client.get('/api/tokens/export/download').data, b'synthetic-record-B\n')
        self.assertEqual(len(self.pipe.db.list_exports()), 2)

    def test_unknown_validation_preserves_ready_record(self):
        self.ready('synthetic-record-A')
        self.pipe.validator.validate_token.side_effect = ValidationUnavailable()
        job = self.export()
        self.assertEqual(job['deferred'], 1)
        self.assertEqual(job['invalid'], 0)
        self.assertEqual(self.pipe.db.get_token_info('synthetic-record-A')['status'], 'ready')

    def test_strict_validator_distinguishes_network_failures(self):
        validator = TokenValidator(max_retries=1)
        self.addCleanup(validator.close)
        for status in (429, 500, 502):
            response = Mock(status_code=status)
            response.json.return_value = {'retry_after': 0}
            with self.subTest(status=status), patch.object(validator.transport, 'request', return_value=response):
                with self.assertRaises(ValidationUnavailable):
                    validator.validate_token('synthetic-' * 8, strict=True)
        from modules.discord_transport import DiscordUnavailable
        with patch.object(validator.transport, 'request', side_effect=DiscordUnavailable):
            with self.assertRaises(ValidationUnavailable):
                validator.validate_token('synthetic-' * 8, strict=True)
        with patch.object(validator.transport, 'request', return_value=Mock(status_code=401)):
            self.assertEqual(validator.validate_token('synthetic-' * 8, strict=True), (False, None))

    def test_upload_duplicates_are_not_requeued(self):
        self.ready('synthetic-record-A')
        result = self.pipe.add_manual_tokens('synthetic-record-A\nsynthetic-record-B\nsynthetic-record-B')
        self.assertEqual(result, {'added': 1, 'total': 3, 'duplicates': 2, 'errors': 0})
        self.assertEqual(self.pipe.new_tokens_queue.qsize(), 1)
        self.assertEqual(self.pipe.db.get_token_info('synthetic-record-A')['status'], 'ready')

    def test_failed_setting_write_does_not_change_live_value(self):
        with patch('modules.configuration.os.replace', side_effect=PermissionError):
            response = self.client.post('/api/settings/toggle', json={'key': 'close_channels', 'value': False}, headers=self.headers)
        self.assertEqual(response.status_code, 500)
        self.assertTrue(self.pipe.close_channels)
        self.assertTrue(json.loads(Path(self.pipe.config_path).read_text())['cleaner']['close_channels'])

    def test_purchase_estimate_math(self):
        from modules.purchase_task import PurchaseTaskManager
        items = [{'item_id': i, 'price': p} for i, p in enumerate([10, 20, 30, 40], 1)]
        lzt = SimpleNamespace(get_balance=Mock(return_value=55),
                              search_accounts=Mock(side_effect=[(items, 4), ([], 4)]))
        mgr = PurchaseTaskManager(lzt, self.pipe.db, Mock())
        result = mgr.estimate(80, 60)
        self.assertEqual(result['count'], 4)
        self.assertEqual(result['total_cost'], 100)
        self.assertEqual(result['balance'], 55)
        self.assertEqual(result['max_affordable'], 2)  # 10+20<=55, +30>55

    def test_purchase_task_buys_and_skips_dead(self):
        from modules.purchase_task import PurchaseTaskManager
        from modules.lzt_monitor import PurchaseError
        items = [{'item_id': i, 'price': 10} for i in range(1, 6)]
        lzt = SimpleNamespace(get_balance=Mock(return_value=1000),
                              search_accounts=Mock(side_effect=[(items, 5), ([], 5)]))
        def fake_buy(item_id, price):
            if item_id % 2 == 0:
                raise PurchaseError('dead account')
            return f'tok{item_id}', {'seller': {'username': 'seller'}}
        lzt.fast_buy = Mock(side_effect=fake_buy)
        intake = Mock()
        mgr = PurchaseTaskManager(lzt, self.pipe.db, intake)
        mgr.BUY_DELAY = 0
        mgr.start(80, 60, 3, 5)
        mgr._thread.join(timeout=5)
        snap = mgr.snapshot()
        self.assertEqual(snap['status'], 'done')
        self.assertEqual(snap['bought'], 3)        # item_ids 1,3,5
        self.assertEqual(snap['skipped_dead'], 2)  # item_ids 2,4
        self.assertEqual(intake.call_count, 3)

    def test_purchase_task_stops_when_out_of_balance(self):
        from modules.purchase_task import PurchaseTaskManager
        from modules.lzt_monitor import PurchaseError
        items = [{'item_id': i, 'price': 10} for i in range(1, 6)]
        lzt = SimpleNamespace(get_balance=Mock(return_value=1000),
                              search_accounts=Mock(side_effect=[(items, 5), ([], 5)]))
        def fake_buy(item_id, price):
            if item_id == 2:
                raise PurchaseError('Not enough balance', out_of_balance=True)
            return f'tok{item_id}', {}
        lzt.fast_buy = Mock(side_effect=fake_buy)
        mgr = PurchaseTaskManager(lzt, self.pipe.db, Mock())
        mgr.BUY_DELAY = 0
        mgr.start(80, 60, 5, 5)
        mgr._thread.join(timeout=5)
        snap = mgr.snapshot()
        self.assertEqual(snap['bought'], 1)  # bought #1, then #2 hit balance and stopped

    def test_purchase_endpoints_require_auth_and_csrf(self):
        anonymous = api.app.test_client()
        self.assertEqual(anonymous.get('/api/purchase/status').status_code, 401)
        self.assertEqual(anonymous.get('/api/purchase/estimate?pmax=80&chat_min=60').status_code, 401)
        for route in ('/api/purchase/start', '/api/purchase/stop'):
            self.assertEqual(anonymous.post(route, json={}).status_code, 401)
            self.assertEqual(self.client.post(route, json={}).status_code, 403)

    def test_purchase_estimate_endpoint(self):
        self.pipe.estimate_purchase = Mock(return_value={
            'count': 4, 'total_cost': 100, 'balance': 55, 'max_affordable': 2,
            'collected': 4, 'truncated': False})
        response = self.client.get('/api/purchase/estimate?pmax=80&chat_min=60', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['max_affordable'], 2)
        self.assertEqual(self.client.get('/api/purchase/estimate?pmax=abc&chat_min=60',
                                         headers=self.headers).status_code, 400)

    def test_purchase_start_rejects_over_balance(self):
        self.pipe.estimate_purchase = Mock(return_value={'max_affordable': 2})
        self.pipe.purchase = SimpleNamespace(is_active=lambda: False)
        self.pipe.start_purchase = Mock(return_value={'status': 'running'})
        over = self.client.post('/api/purchase/start',
            json={'pmax': 80, 'chat_min': 60, 'count': 5, 'cleaner_workers': 10}, headers=self.headers)
        self.assertEqual(over.status_code, 409)
        self.pipe.start_purchase.assert_not_called()
        ok = self.client.post('/api/purchase/start',
            json={'pmax': 80, 'chat_min': 60, 'count': 2, 'cleaner_workers': 10}, headers=self.headers)
        self.assertEqual(ok.status_code, 202)
        self.pipe.start_purchase.assert_called_once_with(80.0, 60, 2, 10)

    def test_atomic_export_only_claims_each_record_once(self):
        self.ready('synthetic-record-A')
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.pipe.db.commit_export(['synthetic-record-A'], []), range(2)))
        self.assertEqual(sum(len(r['valid_tokens']) for r in results), 1)
        self.assertEqual(len(self.pipe.db.list_exports()), 1)
        self.assertEqual(self.pipe.db.get_today_statistics()['tokens_sent'], 1)

    def test_schema_v2_migration_preserves_records_and_backs_up(self):
        self.ready('synthetic-record-A')
        with self.pipe.db.get_connection() as conn:
            conn.execute('DROP TABLE export_items')
            conn.execute('DROP TABLE export_batches')
            conn.execute('PRAGMA user_version=2')
        migrated = Database(self.config['database']['path'])
        self.assertEqual(migrated.schema_version(), 4)
        self.assertEqual(migrated.get_token_info('synthetic-record-A')['status'], 'ready')
        self.assertEqual(len(list(self.path.glob('test.db.backup_*'))), 1)
        Database(self.config['database']['path'])
        self.assertEqual(len(list(self.path.glob('test.db.backup_*'))), 1)

    def test_service_config_preserves_paths_and_placeholders(self):
        source, target = self.path / 'source.json', self.path / 'data/config.json'
        source.write_text(json.dumps({'database': {'path': 'data/test.db'}, 'lzt': {'api_token': '${LZT_API_TOKEN}'}}))
        prepare(source, target)
        saved = json.loads(target.read_text())
        self.assertEqual(saved['database']['path'], str((self.path / 'data/test.db').resolve()))
        self.assertEqual(saved['lzt']['api_token'], '${LZT_API_TOKEN}')
        with self.assertRaises(FileExistsError):
            prepare(source, target)

    def test_worker_entrypoint_wires_pipeline_and_stops_on_server_failure(self):
        worker = Mock(db=self.pipe.db)
        with patch.object(sys, 'argv', ['dashboard_api.py', '--with-worker', '--config', self.pipe.config_path]), \
             patch.object(api, 'load_env_file'), patch.object(api, 'load_config', return_value=self.config), \
             patch.object(api, 'init_dashboard') as initialize, \
             patch.object(api, 'run_dashboard', side_effect=RuntimeError('synthetic server error')), \
             patch('core.pipeline.TokenPipeline', return_value=worker), \
             patch('main.check_config', return_value=True), \
             patch.object(api.signal, 'signal'), patch.object(api.logging, 'FileHandler'), \
             patch.object(api.logging, 'basicConfig'):
            with self.assertRaises(RuntimeError):
                api.main()
        initialize.assert_called_once_with(worker, self.config, config_file=self.pipe.config_path)
        worker.start.assert_called_once()
        worker.stop.assert_called_once()

    def test_real_http_login_and_download_with_waitress(self):
        import http.cookiejar
        import urllib.request
        from waitress import create_server
        self.ready('synthetic-record-A')
        self.export()
        server = create_server(api.app, host='127.0.0.1', port=0, threads=8)
        stopped = threading.Event()
        def serve():
            while not stopped.is_set():
                server.asyncore.loop(timeout=0.05, count=1, map=server._map)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        base = f'http://localhost:{server.effective_port}'
        try:
            with opener.open(base + '/healthz', timeout=5) as response:
                self.assertEqual(json.load(response)['status'], 'ok')
            with opener.open(base + '/assets/app.js', timeout=5) as response:
                self.assertEqual(response.read(), (ROOT / 'dashboard/assets/app.js').read_bytes())
            with opener.open(base + '/api/auth/session', timeout=5) as response:
                csrf = json.load(response)['csrf']
            request = urllib.request.Request(base + '/api/auth/login', method='POST',
                data=json.dumps({'username': 'review', 'password': self.password}).encode(),
                headers={'Content-Type': 'application/json', 'X-CSRF-Token': csrf})
            with opener.open(request, timeout=5) as response:
                login = json.load(response)
                self.assertTrue(login['authenticated'])
            with opener.open(base + '/api/tokens/export/download', timeout=5) as response:
                self.assertEqual(response.read(), b'synthetic-record-A\n')
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            streams = []
            try:
                for _ in range(4):
                    stream = opener.open(base + '/api/stream', timeout=5)
                    streams.append(stream)
                    self.assertEqual(stream.headers.get_content_type(), 'text/event-stream')
                    self.assertEqual(stream.readline(), b'retry: 3000\n')
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    opener.open(base + '/api/stream', timeout=5)
                self.assertEqual(rejected.exception.code, 503)
                with opener.open(base + '/api/health', timeout=5) as response:
                    self.assertEqual(response.status, 200)
                revoke = urllib.request.Request(base + '/api/auth/revoke', method='POST',
                    data=b'{}', headers={'Content-Type': 'application/json',
                                        'X-CSRF-Token': login['csrf']})
                with opener.open(revoke, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                for stream in streams:
                    self.assertIn(b'event: auth-expired', stream.read())
            finally:
                for stream in streams:
                    stream.close()
        finally:
            stopped.set()
            server.pull_trigger()
            thread.join(timeout=2)
            server.task_dispatcher.shutdown()
            server.close()
            self.assertFalse(thread.is_alive())

    def test_network_metrics_are_authenticated_and_hide_credentials(self):
        from modules.cleaner import ProxyManager
        from modules.discord_transport import DiscordTransport
        self.assertEqual(api.app.test_client().get('/api/network').status_code, 401)
        raw = 'http://synthetic-user:synthetic-password@127.0.0.1:18080'
        self.pipe.db.upsert_proxy_result(raw, True, 1)
        manager = ProxyManager(db=self.pipe.db)
        manager.acquire('synthetic-record-A')
        self.pipe.discord_transport = DiscordTransport(manager)
        self.pipe.count_cleaner_workers = Mock(return_value=3)
        result = self.client.get('/api/network')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['assigned_proxies'], 1)
        self.assertEqual(result.json['free_proxies'], 0)
        self.assertNotIn('synthetic-password', result.get_data(as_text=True))
        self.assertNotIn('synthetic-record-A', result.get_data(as_text=True))

    def test_cleaner_network_failure_requeues_without_marking_cleaned(self):
        from queue import Queue
        self.pipe.validated_queue = Queue()
        self.pipe.cleaned_queue = Queue()
        self.pipe.proxy_manager = Mock()
        self.pipe.discord_transport = Mock()
        self.ready('synthetic-record-A')
        self.pipe.validated_queue.put({'token': 'synthetic-record-A'})
        def fail(api_client, *args, **kwargs):
            api_client.request_failed = True
        def finish(seconds):
            self.pipe.running = False
        with patch('modules.cleaner.process_token', side_effect=fail), patch('core.pipeline.time.sleep', side_effect=finish):
            self.pipe._cleaning_worker()
        self.assertEqual(self.pipe.db.get_token_info('synthetic-record-A')['status'], 'validated')
        self.assertEqual(self.pipe.validated_queue.qsize(), 1)
        self.assertEqual(self.pipe.cleaned_queue.qsize(), 0)


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
