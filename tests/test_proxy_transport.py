"""Proxy isolation and Discord rate-limit checks, using synthetic accounts only."""
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from modules.cleaner import ProxyManager, DiscordAPI, parse_proxy
from modules.database import Database
from modules.discord_transport import DiscordTransport, NetworkMonitor, ProxyUnavailable, DiscordUnavailable
from modules.validator import TokenValidator, ValidationUnavailable

ROOT = Path(__file__).resolve().parent.parent


def response(status=200, body=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body or {'username': 'synthetic'}).encode()
    result.headers.update(headers or {})
    return result


class ProxyTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(str(Path(self.temp.name) / 'test.db'))
        self.manager = ProxyManager(db=self.db)
        self.transport = DiscordTransport(self.manager)
        self.token = 'synthetic-account-A-' * 4
        self.other = 'synthetic-account-B-' * 4
        self.url = 'https://discord.com/api/v9/users/@me'
        self.clock = 1000.0
        for token in (self.token, self.other):
            self.db.add_token(token)
        self.original_request = requests.sessions.Session.request
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected HTTP'))
        self.guard = guard.start()
        self.addCleanup(guard.stop)

    def add_proxy(self, raw='127.0.0.1:18080'):
        self.db.upsert_proxy_result(raw, True, 1)
        return raw

    def advance(self, seconds):
        self.clock += seconds

    def test_empty_pool_never_issues_http(self):
        with self.assertRaises(ProxyUnavailable):
            self.transport.request(self.token, 'GET', self.url)
        self.guard.assert_not_called()
        self.assertEqual(self.transport.monitor.snapshot()['last_minute']['accounts_waiting_proxy'], 1)

    def test_unique_persistent_bindings_and_exhaustion(self):
        self.add_proxy()
        self.add_proxy('127.0.0.1:18081')
        first, second = self.manager.acquire(self.token), self.manager.acquire(self.other)
        self.assertNotEqual(first, second)
        self.assertIsNone(self.manager.acquire('third-account'))
        restarted = ProxyManager(db=Database(self.db.db_path))
        self.assertEqual(restarted.acquire(self.token), first)
        self.assertEqual(restarted.acquire(self.other), second)

    def test_simultaneous_bindings_cannot_share_one_proxy(self):
        self.add_proxy()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.manager.acquire, [str(n) for n in range(8)]))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(len(self.manager.leases_snapshot()), 1)

    def test_proxy_aliases_do_not_create_extra_capacity(self):
        self.add_proxy('LOCALHOST:18080')
        self.add_proxy('http://localhost:18080')
        self.assertIsNotNone(self.manager.acquire(self.token))
        self.assertIsNone(self.manager.acquire(self.other))

    def test_dead_lease_rotates_to_alive_and_blocks_without_pool(self):
        # Аренда — не вечная привязка: умерший прокси заменяется живым, при пустом
        # пуле аккаунт ждёт (None). Прокси возвращается в пул через release().
        raw = self.add_proxy()
        assigned = self.manager.acquire(self.token)
        self.assertIsNotNone(assigned)
        self.add_proxy('127.0.0.1:18081')
        self.db.set_proxy_result(raw, False)  # арендованный прокси умер
        rotated = self.manager.acquire(self.token)
        self.assertIsNotNone(rotated)
        self.assertNotEqual(rotated, assigned)  # выдан другой живой прокси
        self.db.set_proxy_result('127.0.0.1:18081', False)  # живых больше нет
        self.assertIsNone(self.manager.acquire(self.token))
        # После release прокси свободен для повторного использования другим аккаунтом.
        self.db.set_proxy_result('127.0.0.1:18081', True)
        first = self.manager.acquire(self.token)
        self.manager.release(self.token)
        self.assertEqual(self.manager.acquire(self.other), first)

    def test_every_attempt_explicitly_uses_proxy_ignoring_environment(self):
        self.add_proxy()
        calls = []
        def send(session, method, url, **kwargs):
            calls.append((session.trust_env, kwargs))
            return response()
        with patch.dict('os.environ', {'HTTPS_PROXY': 'http://wrong.invalid:1', 'NO_PROXY': '*'}), \
             patch('requests.sessions.Session.request', new=send):
            self.transport.request(self.token, 'GET', self.url)
        self.assertFalse(calls[0][0])
        self.assertEqual(calls[0][1]['proxies'], {'http': 'http://127.0.0.1:18080', 'https': 'http://127.0.0.1:18080'})
        self.assertFalse(calls[0][1]['allow_redirects'])

    def test_proxy_error_retries_same_proxy_without_direct_fallback(self):
        self.add_proxy()
        self.add_proxy('127.0.0.1:18081')
        with patch('requests.sessions.Session.request', side_effect=requests.exceptions.ProxyError) as send, \
             patch('modules.discord_transport.time.sleep'):
            with self.assertRaises(DiscordUnavailable):
                self.transport.request(self.token, 'GET', self.url, max_retries=2)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].kwargs['proxies'], send.call_args_list[1].kwargs['proxies'])
        self.assertTrue(send.call_args_list[0].kwargs['proxies'].get('https'))  # только через прокси

    def test_429_obeys_retry_after_and_records_last_attempt(self):
        self.add_proxy()
        limited = response(429, {'retry_after': 2.5, 'global': True}, {'Retry-After': '3'})
        with patch('modules.discord_transport.time.monotonic', side_effect=lambda: self.clock), \
             patch('modules.discord_transport.time.sleep', side_effect=self.advance), \
             patch('requests.sessions.Session.request', side_effect=[limited, limited]) as send:
            with self.assertRaises(DiscordUnavailable):
                self.transport.request(self.token, 'GET', self.url, max_retries=2)
            self.assertGreaterEqual(self.clock, 1003)
            snapshot = self.transport.monitor.snapshot()
        self.assertEqual(snapshot['last_minute']['rate_limits'], 2)
        self.assertEqual(snapshot['recent_errors'][0]['scope'], 'global')
        self.assertEqual(send.call_args_list[0].kwargs['proxies'], send.call_args_list[1].kwargs['proxies'])
        self.assertTrue(snapshot['cooldowns'])

    def test_429_non_json_uses_retry_after_header(self):
        self.add_proxy()
        limited = response(429, headers={'Retry-After': '4.2'})
        limited._content = b'<html>rate limited</html>'
        with patch('modules.discord_transport.time.monotonic', side_effect=lambda: self.clock), \
             patch('modules.discord_transport.time.sleep', side_effect=self.advance), \
             patch('requests.sessions.Session.request', side_effect=[limited, response()]):
            self.transport.request(self.token, 'GET', self.url)
            self.assertGreaterEqual(self.clock, 1004.2)

    def test_global_cooldown_survives_next_request_and_stage(self):
        self.add_proxy()
        with patch('modules.discord_transport.time.monotonic', side_effect=lambda: self.clock), \
             patch('modules.discord_transport.time.sleep', side_effect=self.advance), \
             patch('requests.sessions.Session.request', side_effect=[response(429, {'retry_after': 10, 'global': True}), response()]):
            with self.assertRaises(DiscordUnavailable):
                self.transport.request(self.token, 'GET', self.url, max_retries=1)
            self.transport.request(self.token, 'DELETE', 'https://discord.com/api/v9/channels/123', stage='cleaner')
            self.assertGreaterEqual(self.clock, 1010)

    def test_shared_resource_cooldown_is_observed_by_other_account(self):
        self.add_proxy()
        self.add_proxy('127.0.0.1:18081')
        url = 'https://discord.com/api/v9/channels/123/messages/456'
        with patch('modules.discord_transport.time.monotonic', side_effect=lambda: self.clock), \
             patch('modules.discord_transport.time.sleep', side_effect=self.advance), \
             patch('requests.sessions.Session.request', side_effect=[response(429, {'retry_after': 7}, {'X-RateLimit-Scope': 'shared'}), response()]):
            with self.assertRaises(DiscordUnavailable):
                self.transport.request(self.token, 'DELETE', url, max_retries=1)
            self.transport.request(self.other, 'DELETE', url.replace('456', '789'))
            self.assertGreaterEqual(self.clock, 1007)

    def test_validator_cleaner_and_export_use_same_binding(self):
        self.add_proxy()
        validator = TokenValidator(transport=self.transport)
        cleaner = DiscordAPI(self.manager, Mock(), transport=self.transport)
        with patch('requests.sessions.Session.request', return_value=response()) as send, \
             patch.object(cleaner.rate_limiter, 'wait'):
            validator.validate_token(self.token, strict=True)
            cleaner.safe_request('GET', self.url, {'Authorization': self.token})
            validator.validate_token(self.token, strict=True, stage='export')
        proxies = [call.kwargs['proxies'] for call in send.call_args_list]
        self.assertEqual(len(proxies), 3)
        self.assertTrue(all(p == proxies[0] for p in proxies))

    def test_cleaner_exhaustion_sets_failure_without_changing_cleanup(self):
        cleaner = DiscordAPI(self.manager, Mock(), transport=self.transport)
        self.assertIsNone(cleaner.safe_request('GET', self.url, {'Authorization': self.token}))
        self.assertTrue(cleaner.request_failed)
        self.guard.assert_not_called()

    def test_socks_dns_is_resolved_by_proxy(self):
        self.assertTrue(parse_proxy('socks5://user:pass@localhost:1080')['https'].startswith('socks5h://'))
        self.assertTrue(parse_proxy('socks4://localhost:1080')['https'].startswith('socks4a://'))
        self.assertIsNone(parse_proxy('ftp://localhost:1080')['https'])

    def test_real_https_connect_routes_two_accounts_to_distinct_local_proxies(self):
        import shutil
        import socket
        import socketserver
        import ssl
        import subprocess
        import threading
        if not shutil.which('openssl'):
            self.skipTest('OpenSSL required for local TLS certificate')
        cert, key = Path(self.temp.name) / 'cert.pem', Path(self.temp.name) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=discord.com',
            '-addext', 'subjectAltName=DNS:discord.com'], check=True, capture_output=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        seen, failures = {}, []
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    self.request.settimeout(5)
                    data = b''
                    while b'\r\n\r\n' not in data:
                        chunk = self.request.recv(4096)
                        if not chunk:
                            return
                        data += chunk
                    if not data.startswith(b'CONNECT discord.com:443 '):
                        raise AssertionError('Expected CONNECT, no external forwarding allowed')
                    self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                    with context.wrap_socket(self.request, server_side=True) as connection:
                        data = b''
                        while b'\r\n\r\n' not in data:
                            chunk = connection.recv(4096)
                            if not chunk:
                                return
                            data += chunk
                        authorization = next(line for line in data.decode().split('\r\n') if line.lower().startswith('authorization:'))
                        seen.setdefault(self.server.server_address[1], []).append(authorization)
                        body = b'{"username":"synthetic"}'
                        connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
                except Exception as exc:
                    failures.append(type(exc).__name__)
        servers = [socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler) for _ in range(2)]
        for server in servers:
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.add_proxy(f'127.0.0.1:{server.server_address[1]}')
        # Два аккаунта обрабатываются одновременно — держим их аренды параллельно,
        # поэтому им достаются РАЗНЫЕ прокси (одновременно один прокси не делится).
        self.manager.acquire(self.token)
        self.manager.acquire(self.other)
        real_session = requests.sessions.Session
        def session_factory():
            session = real_session()
            session.verify = str(cert)
            return session
        resolve = socket.getaddrinfo
        def local_only(host, *args, **kwargs):
            if host not in ('127.0.0.1', 'localhost'):
                raise AssertionError('Direct DNS/network resolution prohibited')
            return resolve(host, *args, **kwargs)
        with patch('requests.sessions.Session.request', new=self.original_request), \
             patch('requests.Session', side_effect=session_factory), \
             patch('socket.getaddrinfo', side_effect=local_only), \
             patch.dict('os.environ', {'HTTPS_PROXY': 'http://wrong.invalid:1', 'NO_PROXY': '*'}):
            for token in (self.token, self.other, self.token, self.other):
                result = self.transport.request(token, 'GET', self.url)
                self.assertEqual(result.status_code, 200)
        self.assertFalse(failures)
        self.assertEqual(len(seen), 2)
        identities = [set(items) for items in seen.values()]
        self.assertTrue(all(len(items) == 1 for items in identities))
        self.assertNotEqual(identities[0], identities[1])

    def test_cleaning_functions_unchanged(self):
        source = (ROOT / 'modules/cleaner.py').read_text()
        expected = {'check_message_has_links_or_attachments': 'd7a2e23bd4b86f4ad7596767011e63f4619afa1f63d41548b8cd032e15edcf27', 'process_token': 'e4bda458d754045290ed398ee5998f9808d8de7422cb977eb91ca8b1622fe207'}
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and node.name in expected:
                actual = hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest()
                self.assertEqual(actual, expected[node.name], node.name)
