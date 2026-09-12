"""Proxy-only Discord HTTP and operational metrics; no cleaning decisions."""
from collections import deque
from contextlib import contextmanager
import hashlib
import logging
import math
import re
import threading
import time
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)


class ProxyUnavailable(RuntimeError):
    pass


class DiscordUnavailable(RuntimeError):
    pass


def account_key(token):
    return hashlib.sha256(token.encode()).hexdigest()


def proxy_endpoint(url):
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.hostname}:{parts.port}'


def resource_route(url):
    # Keep the channel/guild major parameter; message IDs do not define a bucket.
    return re.sub(r'(/messages)/\d+', r'\1/:id', urlsplit(url).path)


def retry_seconds(response):
    values = []
    try:
        body = response.json()
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    for value in (body.get('retry_after'), response.headers.get('Retry-After'),
                  response.headers.get('X-RateLimit-Reset-After')):
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds >= 0:
                values.append(seconds)
        except (ValueError, TypeError):
            continue
    return (max(values) if values else 2.0), body


class NetworkMonitor:
    def __init__(self):
        self._lock = threading.RLock()
        self._events = deque(maxlen=50000)
        self._cooldowns = {}
        self._shared_cooldowns = {}
        self._account_locks = {}
        self._active = {}
        self.started = time.monotonic()

    @contextmanager
    def account(self, key):
        with self._lock:
            lock = self._account_locks.setdefault(key, threading.Lock())
        with lock:
            yield

    def wait(self, key, url):
        while True:
            with self._lock:
                delay = max(self._cooldowns.get(key, 0),
                            self._shared_cooldowns.get(resource_route(url), 0)) - time.monotonic()
            if delay <= 0:
                return
            time.sleep(delay)

    def active(self, key, stage, endpoint):
        with self._lock:
            self._active[key] = {'account': key[:12], 'stage': stage, 'proxy': endpoint}

    def inactive(self, key):
        with self._lock:
            self._active.pop(key, None)

    def record(self, key, stage, endpoint, method, url, response=None, error=None):
        now = time.monotonic()
        status = response.status_code if response is not None else None
        delay, scope = 0, None
        headers = response.headers if response is not None else {}
        if status == 429:
            delay, body = retry_seconds(response)
            scope = ('global' if body.get('global') is True or headers.get('X-RateLimit-Global') == 'true'
                     else headers.get('X-RateLimit-Scope', 'user'))
            if scope not in {'global', 'shared', 'user'}:
                scope = 'unknown'
        elif headers.get('X-RateLimit-Remaining') == '0':
            delay, _ = retry_seconds(response)
        event = {'account': key[:12], 'proxy': endpoint, 'stage': stage,
                 'method': method, 'route': re.sub(r'/\d+', '/:id', urlsplit(url).path),
                 'status': status, 'error': error, 'retry_after': delay, 'scope': scope,
                 'at': time.time(), '_time': now}
        event['bucket'] = re.sub(r'[^A-Za-z0-9_-]', '', headers.get('X-RateLimit-Bucket', ''))[:100]
        with self._lock:
            if delay:
                self._cooldowns[key] = max(self._cooldowns.get(key, 0), now + delay + 0.1)
                if scope == 'shared':
                    route = resource_route(url)
                    self._shared_cooldowns[route] = max(self._shared_cooldowns.get(route, 0), now + delay + 0.1)
            self._events.append(event)
        if status == 429:
            logger.warning('Discord 429 account=%s proxy=%s stage=%s scope=%s retry_after=%.2fs route=%s',
                           key[:12], endpoint, stage, scope, delay, event['route'])

    def snapshot(self):
        now = time.monotonic()
        with self._lock:
            while self._events and self._events[0]['_time'] < now - 300:
                self._events.popleft()
            events = list(self._events)
            cooldowns = {key[:12]: round(deadline - now, 1) for key, deadline in self._cooldowns.items() if deadline > now}
            active = list(self._active.values())
        def summary(seconds, stage=None):
            rows = [e for e in events if e['_time'] >= now - seconds and (stage is None or e['stage'] == stage)]
            blocked = [e for e in rows if e['error'] == 'ProxyUnavailable']
            rows = [e for e in rows if e['error'] != 'ProxyUnavailable']
            limited = [e for e in rows if e['status'] == 429]
            return {'requests': len(rows), 'rate_limits': len(limited),
                    'rate_limit_percent': round(100 * len(limited) / len(rows), 2) if rows else 0,
                    'network_errors': sum(e['error'] is not None for e in rows),
                    'forbidden': sum(e['status'] in (401, 403) for e in rows),
                    'retry_after_seconds': round(sum(e['retry_after'] for e in limited), 1),
                    'accounts_limited': len({e['account'] for e in limited}),
                    'accounts_waiting_proxy': len({e['account'] for e in blocked})}
        return {'available': True, 'uptime_seconds': int(now - self.started),
                'last_minute': summary(60), 'last_five_minutes': summary(300),
                'cleaner_last_five_minutes': summary(300, 'cleaner'),
                'window_truncated': len(events) == self._events.maxlen,
                'cooldowns': cooldowns, 'active': active,
                'recent_errors': [{k: v for k, v in e.items() if k != '_time'}
                                  for e in events if e['status'] == 429 or e['error']][-50:]}


class DiscordTransport:
    def __init__(self, proxy_manager, monitor=None):
        self.proxy_manager = proxy_manager
        self.monitor = monitor or NetworkMonitor()

    def request(self, token, method, url, *, headers=None, timeout=15, max_retries=3,
                stage='cleaner', before_request=None):
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.hostname not in {'discord.com', 'discordapp.com'}:
            raise ValueError('Discord transport only accepts HTTPS Discord endpoints')
        key = account_key(token)
        if not token:
            raise ValueError('Missing account token')
        with self.monitor.account(key):
            try:
                for attempt in range(max(1, max_retries)):
                    # Revalidate the existing binding, never replace it on failure/429.
                    info = self.proxy_manager.acquire(token) if self.proxy_manager is not None else None
                    if not info or not all(info.get('proxies', {}).get(k) for k in ('http', 'https')):
                        self.monitor.record(key, stage, '', method, url, error='ProxyUnavailable')
                        raise ProxyUnavailable('Нет доступного закреплённого прокси')
                    endpoint = proxy_endpoint(info['proxies']['https'])
                    self.monitor.active(key, stage, endpoint)
                    self.monitor.wait(key, url)
                    if before_request:
                        before_request()
                    # A pool change during a long cooldown must also fail closed.
                    current = self.proxy_manager.acquire(token)
                    if current != info:
                        self.monitor.record(key, stage, endpoint, method, url, error='ProxyUnavailable')
                        raise ProxyUnavailable('Закреплённый прокси стал недоступен')
                    try:
                        with requests.Session() as session:
                            session.trust_env = False
                            response = session.request(method, url, headers={**(headers or {}), 'Authorization': token},
                                proxies=dict(info['proxies']), timeout=timeout, allow_redirects=False)
                    except requests.RequestException as exc:
                        self.monitor.record(key, stage, endpoint, method, url, error=type(exc).__name__)
                        if attempt == max(1, max_retries) - 1:
                            raise DiscordUnavailable('Закреплённый прокси или Discord недоступен') from None
                        time.sleep(2 ** attempt)
                        continue
                    self.monitor.record(key, stage, endpoint, method, url, response=response)
                    if response.status_code == 429:
                        if attempt == max(1, max_retries) - 1:
                            raise DiscordUnavailable('Discord rate limit; повторная попытка после cooldown')
                        continue
                    return response
            finally:
                self.monitor.inactive(key)
