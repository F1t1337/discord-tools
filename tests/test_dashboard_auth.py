"""Dashboard boundary checks with temporary data and no external services."""
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

from werkzeug.security import generate_password_hash

import dashboard_api as api


class DashboardAuthTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = {'database': {'path': str(Path(directory.name) / 'test.db')}}
        self.password = secrets.token_urlsafe(24)
        env = patch.dict(os.environ, {
            'DASHBOARD_SECRET_KEY': secrets.token_urlsafe(48),
            'DASHBOARD_PASSWORD_HASH': generate_password_hash(self.password),
            'DASHBOARD_USERNAME': 'admin',
            'DASHBOARD_PUBLIC_URL': 'http://localhost',
        })
        env.start()
        self.addCleanup(env.stop)
        api.init_dashboard(None, self.config)
        self.client = api.app.test_client()

    def login(self, base_url, origin, csrf=None):
        response = self.client.get('/api/auth/session', base_url=base_url)
        self.assertEqual(response.status_code, 200)
        return self.client.post('/api/auth/login', base_url=base_url,
                                json={'username': 'admin', 'password': self.password},
                                headers={'Origin': origin, 'X-CSRF-Token': csrf or response.json['csrf']})

    def test_non_ascii_csrf_is_rejected_without_server_error(self):
        response = self.login('http://localhost', 'http://localhost', csrf='é')
        self.assertEqual(response.status_code, 403)

    def test_ipv6_loopback_login(self):
        with patch.dict(os.environ, {'DASHBOARD_PUBLIC_URL': 'http://[::1]:5000'}):
            api.init_dashboard(None, self.config)
        response = self.login('http://[::1]:5000', 'http://[::1]:5000')
        self.assertEqual(response.status_code, 200)

    def test_browser_origin_for_uppercase_host_and_default_port(self):
        for configured, browser in (
            ('http://LOCALHOST:80', 'http://localhost'),
            ('https://PANEL.EXAMPLE.COM:443', 'https://panel.example.com'),
            ('https://пример.рф', 'https://xn--e1afmkfd.xn--p1ai'),
        ):
            with self.subTest(configured=configured):
                with patch.dict(os.environ, {'DASHBOARD_PUBLIC_URL': configured}):
                    api.init_dashboard(None, self.config)
                self.client = api.app.test_client()
                self.assertEqual(self.login(browser, browser).status_code, 200)

    def test_other_origins_still_rejected(self):
        for origin in ('http://localhost:5001', 'https://localhost', 'http://evil.example', 'null'):
            with self.subTest(origin=origin):
                self.assertEqual(self.login('http://localhost', origin).status_code, 403)

    def test_nondefault_port_preserved(self):
        with patch.dict(os.environ, {'DASHBOARD_PUBLIC_URL': 'http://localhost:5000'}):
            api.init_dashboard(None, self.config)
        self.assertEqual(self.login('http://localhost:5000', 'http://localhost:5000').status_code, 200)
        self.assertEqual(self.login('http://localhost:5000', 'http://localhost').status_code, 403)

    def test_untrusted_host_cannot_reach_authenticated_data(self):
        self.assertEqual(self.login('http://localhost', 'http://localhost').status_code, 200)
        response = self.client.get('/api/settings', headers={'Host': 'evil.example'})
        self.assertIn(response.status_code, (400, 401))
        self.assertNotIn('database_file', response.json)
