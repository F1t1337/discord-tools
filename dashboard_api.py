"""Authenticated administrative dashboard. Standalone mode never starts workers."""
import argparse
import csv
import hmac
import io
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash

from modules.access_control import dashboard_settings, telegram_owner_ids
from modules.configuration import PROJECT_ROOT, load_config
from modules.database import Database

logger = logging.getLogger(__name__)
ASSETS = PROJECT_ROOT / 'dashboard'
app = Flask(__name__, static_folder=None)
pipeline = None
config = None
db = None
settings = None
started_at = time.time()
stop_requested = False
state_lock = threading.Lock()
login_attempts = OrderedDict()
auth_sessions = {}
TOKEN_PATTERN = re.compile(r'(?:mfa\.[\w-]{20,}|[\w-]{20,}\.[\w-]{5,}\.[\w-]{20,}|\b\d{6,}:[A-Za-z0-9_-]{25,})')
STATUSES = {'pending_review', 'new', 'validated', 'cleaning', 'cleaned', 'ready', 'sent', 'invalid', 'locked'}
# Панель принимает крупные списки прокси одной вставкой (до ~5000 строк).
MAX_BODY_BYTES = 1024 * 1024
MAX_PROXY_IMPORT = 5000

# Фоновая проверка прокси на живость: одно задание за раз, прогресс отдаётся панели.
proxy_job_lock = threading.Lock()
proxy_job = {'running': False, 'kind': None, 'total': 0, 'done': 0, 'alive': 0,
             'dead': 0, 'started_at': None, 'finished_at': None, 'error': None}

# Фоновая выгрузка готовых токенов: одно задание за раз. Валидные токены хранятся
# в памяти для скачивания из панели и параллельно уходят файлом в Telegram.
export_job_lock = threading.Lock()
export_job = {'running': False, 'total': 0, 'done': 0, 'valid': 0, 'invalid': 0,
              'started_at': None, 'finished_at': None, 'error': None, 'available': False}
export_tokens_buffer = []


def init_dashboard(pipeline_instance, config_dict):
    global pipeline, config, db, settings, started_at, stop_requested
    settings = dashboard_settings(config_dict)
    pipeline, config = pipeline_instance, config_dict
    app.config.update(
        SECRET_KEY=settings['secret'],
        SESSION_COOKIE_NAME='discord_admin_session',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=settings['secure'],
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_REFRESH_EACH_REQUEST=False,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=settings['session_hours']),
        MAX_CONTENT_LENGTH=MAX_BODY_BYTES,
        TRUSTED_HOSTS=[settings['hostname']],
    )
    db = pipeline.db if pipeline is not None else Database(config['database']['path'])
    started_at, stop_requested = time.time(), False
    with state_lock:
        auth_sessions.clear()
        login_attempts.clear()
    with proxy_job_lock:
        proxy_job.update(running=False, kind=None, total=0, done=0, alive=0,
                         dead=0, started_at=None, finished_at=None, error=None)
    with export_job_lock:
        export_job.update(running=False, total=0, done=0, valid=0, invalid=0,
                          started_at=None, finished_at=None, error=None, available=False)
        export_tokens_buffer.clear()
    logger.info('Защищённая панель инициализирована')


def authenticated():
    sid = session.get('sid')
    with state_lock:
        now = time.time()
        expired = [key for key, expires in auth_sessions.items() if expires <= now]
        for key in expired:
            auth_sessions.pop(key, None)
        return bool(sid and sid in auth_sessions)


def error(message, status=400):
    return jsonify(error=message), status


@app.before_request
def protect_request():
    if settings is None:
        return error('Панель не настроена', 503)
    if request.path.startswith('/api/'):
        public = request.endpoint in {'get_session', 'login'}
        if not public and not authenticated():
            return error('Войдите в панель', 401)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('Origin')
            if origin and origin != settings['origin']:
                return error('Источник запроса не разрешён', 403)
            expected = session.get('csrf', '')
            actual = request.headers.get('X-CSRF-Token', '')
            if not expected or len(actual) > 200 or not hmac.compare_digest(expected, actual):
                return error('Обновите страницу и повторите действие', 403)


@app.after_request
def secure_response(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if settings and settings['secure']:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


@app.errorhandler(Exception)
def handle_error(exc):
    if isinstance(exc, HTTPException):
        messages = {400: 'Неверные параметры запроса', 404: 'Страница не найдена',
                    405: 'Метод не поддерживается', 413: 'Запрос слишком большой'}
        return error(messages.get(exc.code, 'Запрос отклонён'), exc.code)
    logger.error('Ошибка dashboard: %s', type(exc).__name__)
    return error('Не удалось выполнить запрос. Проверьте журнал сервера.', 500)


@app.get('/')
def index():
    return send_from_directory(ASSETS, 'index.html')


@app.get('/assets/<name>')
def asset(name):
    if name not in {'app.js', 'app.css'}:
        return error('Файл не найден', 404)
    return send_from_directory(ASSETS, name)


@app.get('/miniapp')
def miniapp():
    # The WebView uses the same login and session as any browser.
    return redirect('/')


@app.get('/healthz')
def health():
    return jsonify(status='ok')


@app.get('/api/auth/session')
def get_session():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    logged_in = authenticated()
    return jsonify(authenticated=logged_in, csrf=session['csrf'],
                   username=settings['username'] if logged_in else None)


def login_allowed():
    now = time.monotonic()
    address = request.remote_addr or 'unknown'
    with state_lock:
        for key in ('*', address):
            attempts = login_attempts.setdefault(key, deque())
            while attempts and attempts[0] < now - 300:
                attempts.popleft()
            if len(attempts) >= (60 if key == '*' else 10):
                return False
        for key in ('*', address):
            login_attempts[key].append(now)
            login_attempts.move_to_end(key)
        while len(login_attempts) > 2048:
            key = next(iter(login_attempts))
            if key == '*':
                login_attempts.move_to_end(key)
            else:
                login_attempts.popitem(last=False)
    return True


@app.post('/api/auth/login')
def login():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error('Нужен JSON-объект')
    username, password = data.get('username'), data.get('password')
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 100 or len(password) > 1024:
        return error('Неверный логин или пароль', 401)
    if not login_allowed():
        response, status = error('Слишком много попыток. Повторите через 5 минут.', 429)
        response.headers['Retry-After'] = '300'
        return response, status
    try:
        valid_password = check_password_hash(settings['password_hash'], password)
    except (ValueError, TypeError):
        return error('Ошибка настройки пароля на сервере', 503)
    if not valid_password or not hmac.compare_digest(username.encode(), settings['username'].encode()):
        logger.warning('Неуспешная попытка входа в панель')
        return error('Неверный логин или пароль', 401)
    with state_lock:
        auth_sessions.pop(session.get('sid'), None)
        # Bounded server-side session registry: logout revokes the session.
        now = time.time()
        for key in [key for key, expires in auth_sessions.items() if expires <= now]:
            auth_sessions.pop(key, None)
        if len(auth_sessions) >= 128:
            auth_sessions.pop(next(iter(auth_sessions)))
        sid = secrets.token_urlsafe(32)
        auth_sessions[sid] = now + settings['session_hours'] * 3600
    session.clear()
    session.permanent = True
    session['sid'] = sid
    session['csrf'] = secrets.token_urlsafe(32)
    logger.info('Владелец вошёл в панель')
    return jsonify(authenticated=True, username=settings['username'], csrf=session['csrf'])


@app.post('/api/auth/logout')
def logout():
    with state_lock:
        auth_sessions.pop(session.get('sid'), None)
    session.clear()
    return jsonify(success=True)


@app.post('/api/auth/revoke')
def revoke_sessions():
    with state_lock:
        auth_sessions.clear()
    session.clear()
    logger.info('Все сессии панели отозваны владельцем')
    return jsonify(success=True)


def bounded_int(name, default, lower, upper):
    raw = request.args.get(name, str(default))
    try:
        value = int(raw)
    except (ValueError, TypeError):
        raise ValueError(name)
    if not lower <= value <= upper:
        raise ValueError(name)
    return value


def paging(default=25):
    return bounded_int('limit', default, 1, 100), bounded_int('offset', 0, 0, 10_000_000)


def scrub(value):
    text = str(value or '')
    text = TOKEN_PATTERN.sub('[скрыто]', text)
    for key, secret in os.environ.items():
        if any(word in key for word in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY')) and len(secret) >= 8:
            text = text.replace(secret, '[скрыто]')
    return text


@app.get('/api/status')
def get_status():
    counts = db.count_tokens_by_status()
    live = None
    if pipeline is not None:
        live = {
            'running': pipeline.running,
            'queues': {name: queue.qsize() for name, queue in (
                ('new_tokens', pipeline.new_tokens_queue), ('validated', pipeline.validated_queue),
                ('cleaned', pipeline.cleaned_queue), ('ready', pipeline.ready_queue))},
            'threads': pipeline.get_thread_states(),
        }
    return jsonify(
        mode='connected' if live is not None else 'monitor',
        running=bool(live and live['running']), stopping=stop_requested,
        uptime_seconds=int(time.time() - started_at),
        queues=live['queues'] if live else None,
        threads=live['threads'] if live else {}, counts=counts,
        schema_version=db.schema_version(),
        pending_count=sum(counts.get(status, 0) for status in ('new', 'validated', 'cleaning', 'cleaned')),
        recovery_mode='manual_review',
    )


@app.get('/api/statistics/today')
def today_stats():
    return jsonify(db.get_today_statistics())


@app.get('/api/statistics/total')
def total_stats():
    return jsonify(db.get_total_statistics())


@app.get('/api/statistics/range')
def statistics_range():
    try:
        days = bounded_int('days', 14, 1, 90)
    except ValueError:
        return error('Период должен быть от 1 до 90 дней')
    rows = {row['date']: row for row in db.get_statistics_range(days=days)}
    result = []
    today = datetime.now().date()
    for n in range(days - 1, -1, -1):
        day = (today - timedelta(days=n)).isoformat()
        result.append(rows.get(day, {'date': day, 'tokens_bought': 0, 'tokens_cleaned': 0,
                                     'tokens_sent': 0, 'money_spent': 0}))
    return jsonify(result)


@app.get('/api/accounts')
@app.get('/api/tokens')
def accounts():
    try:
        limit, offset = paging()
    except ValueError:
        return error('Неверные параметры страницы')
    status = request.args.get('status') or None
    search = request.args.get('search', '').strip()
    if (status and status not in STATUSES) or len(search) > 100:
        return error('Неверный фильтр')
    result = db.list_account_metadata(status=status, search=search, limit=limit, offset=offset)
    for item in result['items']:
        item['cleaning_progress'] = scrub(item.get('cleaning_progress'))
    return jsonify(result)


@app.get('/api/tokens/cleaning')
def cleaning_accounts():
    result = db.list_account_metadata(status='cleaning', limit=100)
    for item in result['items']:
        item['cleaning_progress'] = scrub(item.get('cleaning_progress'))
    return jsonify(result)


@app.get('/api/sellers')
def sellers():
    return jsonify(items=db.get_seller_statistics())


@app.get('/api/logs')
def get_logs():
    level = request.args.get('level', '')
    if level not in {'', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}:
        return error('Неверный уровень журнала')
    try:
        limit = bounded_int('limit', 100, 1, 300)
    except ValueError:
        return error('Лимит должен быть от 1 до 300')
    path = Path(config.get('logging', {}).get('file', PROJECT_ROOT / 'data/logs/pipeline.log'))
    if not path.is_file():
        return jsonify(items=[], available=False)
    with path.open('rb') as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 262144))
        lines = handle.read(262144).decode('utf-8', errors='replace').splitlines()
    if size > 262144:
        lines = lines[1:]
    result = []
    for line in reversed(lines):
        # Only the logger's structured records are exposed, never raw payloads or tracebacks.
        match = re.match(r'^(\d{4}-\d{2}-\d{2} [\d:,]+) - (\S+) - (DEBUG|INFO|WARNING|ERROR|CRITICAL) - (.*)$', line)
        if not match or (level and match[3] != level):
            continue
        message = match[4]
        if 'Получено сообщение от' in message:
            message = 'Текст входящего сообщения скрыт'
        result.append({'time': match[1], 'module': match[2], 'level': match[3], 'message': scrub(message)[:2000]})
        if len(result) >= limit:
            break
    return jsonify(items=result, available=True)


@app.get('/api/settings')
@app.get('/api/config')
def get_settings():
    try:
        owners = sorted(telegram_owner_ids(config.get('telegram', {}).get('allowed_user_ids'),
                                          config.get('telegram', {}).get('chat_id')))
    except ValueError:
        owners = []
    return jsonify(
        public_url=settings['origin'], username=settings['username'],
        https=settings['secure'], session_hours=settings['session_hours'],
        telegram_owners=owners, schema_version=db.schema_version(),
        database_file=Path(db.db_path).name,
        worker_connected=pipeline is not None,
        monitoring_only=pipeline is None,
        lzt_enabled=bool(getattr(pipeline, 'lzt_enabled', config.get('lzt', {}).get('enabled'))
                         if pipeline is not None else config.get('lzt', {}).get('enabled')),
        close_channels=bool(getattr(pipeline, 'close_channels', config.get('cleaner', {}).get('close_channels'))
                            if pipeline is not None else config.get('cleaner', {}).get('close_channels')),
        validator_workers=config.get('validator', {}).get('max_workers', 0),
        cleaner_workers=(pipeline.count_cleaner_workers() if pipeline is not None
                         and hasattr(pipeline, 'count_cleaner_workers')
                         else config.get('cleaner', {}).get('max_workers', 0)),
        cleaner_workers_config=config.get('cleaner', {}).get('max_workers', 0),
        proxy_enabled=bool(config.get('proxy', {}).get('enabled')),
        proxy_counts=db.count_proxies_by_status(),
    )


@app.get('/api/accounts/report.csv')
def report():
    status = request.args.get('status') or None
    search = request.args.get('search', '').strip()
    if (status and status not in STATUSES) or len(search) > 100:
        return error('Неверный фильтр')
    rows = db.list_account_metadata(status, search, limit=10000)['items']
    buf = io.StringIO(newline='')
    writer = csv.writer(buf)
    writer.writerow(['ID', 'Username', 'Seller', 'Status', 'Price', 'Created'])
    for row in rows:
        values = [row['id'], row['username'], row['seller_username'], row['status'], row['price'], row['created_at']]
        safe = []
        for value in values:
            text = str(value if value is not None else '')
            if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
                text = "'" + text
            safe.append(text)
        writer.writerow(safe)
    return Response('\ufeff' + buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=accounts-report.csv'})


# ==================== ПУЛ ПРОКСИ ====================

def proxy_public_row(row):
    """Публичное представление прокси для панели: scheme://host:port без учётных данных."""
    from urllib.parse import urlsplit
    from modules.cleaner import parse_proxy
    parsed = parse_proxy(row['proxy'])
    has_auth = bool(parsed.get('auth'))
    endpoint = ''
    url = parsed.get('http')
    if url:
        # Собираем адрес из разобранных частей — учётные данные не попадают наружу.
        bits = urlsplit(url)
        if bits.hostname and bits.port:
            endpoint = f"{bits.scheme}://{bits.hostname}:{bits.port}"
    if not endpoint:
        # Не удалось разобрать — не раскрываем возможные учётные данные
        endpoint = '[не распознан]'
    return {
        'id': row['id'],
        'endpoint': endpoint,
        'auth': has_auth,
        'status': row['status'],
        'ping': row['ping'],
        'last_checked_at': row['last_checked_at'],
    }


def parse_proxy_lines(text):
    """Разбивает вставленный текст на уникальные строки прокси."""
    parts = re.split(r'[\s,;]+', text or '')
    seen, result = set(), []
    for part in parts:
        part = part.strip()
        if part and part not in seen:
            seen.add(part)
            result.append(part)
    return result


def reload_pipeline_proxies():
    if pipeline is not None and getattr(pipeline, 'proxy_manager', None) is not None:
        try:
            pipeline.proxy_manager.reload()
        except Exception:
            logger.warning('Не удалось перечитать пул прокси в pipeline')


def start_proxy_job(kind, proxy_strings):
    """Запускает фоновую проверку прокси. Возвращает False, если задание уже идёт."""
    from modules.cleaner import check_proxy_list
    with proxy_job_lock:
        if proxy_job['running']:
            return False
        proxy_job.update(running=True, kind=kind, total=len(proxy_strings), done=0,
                         alive=0, dead=0, started_at=time.time(), finished_at=None, error=None)

    def worker():
        def on_result(proxy_str, alive, ping):
            try:
                db.upsert_proxy_result(proxy_str, alive, ping)
            except Exception:
                logger.warning('Не удалось сохранить результат проверки прокси')
            with proxy_job_lock:
                proxy_job['done'] += 1
                proxy_job['alive' if alive else 'dead'] += 1
        try:
            check_proxy_list(proxy_strings, max_workers=40, timeout=8, on_result=on_result)
        except Exception as exc:
            with proxy_job_lock:
                proxy_job['error'] = type(exc).__name__
            logger.error('Ошибка проверки прокси: %s', type(exc).__name__)
        finally:
            with proxy_job_lock:
                proxy_job['running'] = False
                proxy_job['finished_at'] = time.time()
            reload_pipeline_proxies()

    threading.Thread(target=worker, name='ProxyCheck', daemon=True).start()
    return True


@app.get('/api/proxies')
def list_proxies():
    with proxy_job_lock:
        job = dict(proxy_job)
    return jsonify(items=[proxy_public_row(row) for row in db.list_proxies()],
                   counts=db.count_proxies_by_status(), job=job,
                   proxy_enabled=bool(config.get('proxy', {}).get('enabled')))


@app.post('/api/proxies/import')
def import_proxies():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error('Нужен JSON-объект')
    raw = data.get('proxies')
    if not isinstance(raw, str) or len(raw) > MAX_BODY_BYTES:
        return error('Передайте список прокси текстом')
    proxies = parse_proxy_lines(raw)
    if not proxies:
        return error('Не найдено ни одного прокси')
    if len(proxies) > MAX_PROXY_IMPORT:
        return error(f'Слишком много прокси за один раз (максимум {MAX_PROXY_IMPORT})')
    db.add_proxies(proxies)
    if not start_proxy_job('import', proxies):
        return error('Проверка уже выполняется, дождитесь завершения', 409)
    return jsonify(success=True, queued=len(proxies)), 202


@app.post('/api/proxies/recheck')
def recheck_proxies():
    proxies = [row['proxy'] for row in db.list_proxies()]
    if not proxies:
        return error('Пул прокси пуст', 409)
    if not start_proxy_job('recheck', proxies):
        return error('Проверка уже выполняется, дождитесь завершения', 409)
    return jsonify(success=True, queued=len(proxies)), 202


@app.post('/api/proxies/clear')
def clear_proxies():
    data = request.get_json(silent=True) or {}
    scope = data.get('scope', 'dead')
    if scope not in ('dead', 'all'):
        return error('Неверная область очистки')
    with proxy_job_lock:
        if proxy_job['running']:
            return error('Дождитесь завершения проверки прокси', 409)
    removed = db.delete_proxies(scope)
    reload_pipeline_proxies()
    return jsonify(success=True, removed=removed)


@app.post('/api/settings/cleaner-workers')
def set_cleaner_workers():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error('Нужен JSON-объект')
    value = data.get('workers')
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 200:
        return error('Число потоков должно быть от 1 до 200')
    if pipeline is not None and hasattr(pipeline, 'set_cleaner_workers'):
        applied = pipeline.set_cleaner_workers(value)
        return jsonify(success=True, workers=applied, live=True)
    # Режим мониторинга: рабочий процесс не запущен — только сохраняем в config.json
    persist_config_value('cleaner', 'max_workers', value)
    return jsonify(success=True, workers=value, live=False)


def persist_config_value(section, key, value):
    """Сохраняет одно значение в config.json (режим мониторинга без pipeline)."""
    config.setdefault(section, {})[key] = value
    path = PROJECT_ROOT / 'config.json'
    try:
        if not path.exists():
            return
        with path.open('r', encoding='utf-8') as handle:
            raw = json.load(handle)
        raw.setdefault(section, {})[key] = value
        with path.open('w', encoding='utf-8') as handle:
            json.dump(raw, handle, ensure_ascii=False, indent=2)
    except Exception:
        logger.warning('Не удалось сохранить настройку %s.%s', section, key)


# ==================== ТОКЕНЫ, БАЛАНС, ПЕРЕКЛЮЧАТЕЛИ ====================

@app.get('/api/lzt/balance')
def lzt_balance():
    if pipeline is None or not hasattr(pipeline, 'get_lzt_balance'):
        return jsonify(balance=None, min_balance=config.get('lzt', {}).get('min_balance_alert', 0),
                       enabled=bool(config.get('lzt', {}).get('enabled')), available=False)
    balance = pipeline.get_lzt_balance()
    return jsonify(balance=balance,
                   min_balance=config.get('lzt', {}).get('min_balance_alert', 0),
                   enabled=bool(config.get('lzt', {}).get('enabled')), available=True)


@app.post('/api/settings/toggle')
def toggle_setting():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error('Нужен JSON-объект')
    key, value = data.get('key'), data.get('value')
    if key not in {'close_channels', 'lzt_enabled'} or not isinstance(value, bool):
        return error('Неверные параметры переключателя')
    if pipeline is not None:
        applied = (pipeline.set_close_channels(value) if key == 'close_channels'
                   else pipeline.set_lzt_enabled(value))
        return jsonify(success=True, key=key, value=applied, live=True)
    # Режим мониторинга: только сохраняем в config.json.
    section, name = ('cleaner', 'close_channels') if key == 'close_channels' else ('lzt', 'enabled')
    persist_config_value(section, name, value)
    return jsonify(success=True, key=key, value=value, live=False)


@app.post('/api/tokens/upload')
def upload_tokens():
    if pipeline is None or not hasattr(pipeline, 'add_manual_tokens'):
        return error('Загрузка токенов доступна только при запущенной обработке', 503)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error('Нужен JSON-объект')
    raw = data.get('tokens')
    if not isinstance(raw, str) or not raw.strip():
        return error('Вставьте токены')
    if len(raw) > MAX_BODY_BYTES:
        return error('Слишком большой список токенов')
    result = pipeline.add_manual_tokens(raw)
    return jsonify(success=True, added=result['added'], total=result['total'])


@app.post('/api/tokens/export')
def export_tokens():
    if pipeline is None or not hasattr(pipeline, 'export_ready_tokens'):
        return error('Выгрузка доступна только при запущенной обработке', 503)
    with export_job_lock:
        if export_job['running']:
            return error('Выгрузка уже выполняется, дождитесь завершения', 409)
        export_job.update(running=True, total=0, done=0, valid=0, invalid=0,
                          started_at=time.time(), finished_at=None, error=None, available=False)
        export_tokens_buffer.clear()

    def worker():
        def progress(done, total, valid, invalid):
            with export_job_lock:
                export_job.update(done=done, total=total, valid=valid, invalid=invalid)
        try:
            result = pipeline.export_ready_tokens(progress_cb=progress)
            with export_job_lock:
                export_tokens_buffer.clear()
                export_tokens_buffer.extend(result['valid_tokens'])
                export_job.update(total=result['total'], valid=len(result['valid_tokens']),
                                  invalid=result['invalid'], available=bool(result['valid_tokens']))
        except Exception as exc:
            with export_job_lock:
                export_job['error'] = type(exc).__name__
            logger.error('Ошибка выгрузки токенов: %s', type(exc).__name__)
        finally:
            with export_job_lock:
                export_job['running'] = False
                export_job['finished_at'] = time.time()

    threading.Thread(target=worker, name='TokenExport', daemon=True).start()
    return jsonify(success=True), 202


@app.get('/api/tokens/export/status')
def export_status():
    with export_job_lock:
        return jsonify(dict(export_job))


@app.get('/api/tokens/export/download')
def export_download():
    with export_job_lock:
        if export_job['running']:
            return error('Выгрузка ещё выполняется', 409)
        tokens = list(export_tokens_buffer)
    if not tokens:
        return error('Нет готовых токенов для скачивания', 404)
    body = '\n'.join(tokens) + '\n'
    return Response(body, mimetype='text/plain; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename=tokens.txt'})


@app.post('/api/control/stop')
def stop_pipeline():
    global stop_requested
    if pipeline is None or not pipeline.running:
        return error('В этом процессе обработка не запущена', 409)
    with state_lock:
        if stop_requested:
            return jsonify(success=True, stopping=True), 202
        stop_requested = True
    def stop_worker():
        global stop_requested
        try:
            pipeline.stop()
        finally:
            stop_requested = False
    threading.Thread(target=stop_worker, name='DashboardStop', daemon=True).start()
    return jsonify(success=True, stopping=True), 202


@app.get('/api/health')
def authenticated_health():
    return jsonify(status='ok', schema_version=db.schema_version())


def run_dashboard(host='127.0.0.1', port=5000, debug=False):
    if settings is None:
        raise RuntimeError('Сначала вызовите init_dashboard')
    from waitress import serve
    options = dict(host=host, port=port, threads=8, max_request_body_size=MAX_BODY_BYTES,
                   clear_untrusted_proxy_headers=True)
    if settings['secure']:
        if host not in {'127.0.0.1', '::1', 'localhost'}:
            raise ValueError('За HTTPS-прокси приложение должно слушать только loopback')
        options.update(trusted_proxy='127.0.0.1', trusted_proxy_count=1,
                       trusted_proxy_headers={'x-forwarded-for', 'x-forwarded-proto'})
    serve(app, **options)


def main():
    parser = argparse.ArgumentParser(description='Защищённая панель мониторинга без запуска обработчиков')
    parser.add_argument('--config', help='Путь к config.json')
    args = parser.parse_args()
    configuration = load_config(args.config)
    dashboard_settings(configuration)
    log_path = Path(configuration.get('logging', {}).get('file', PROJECT_ROOT / 'data/logs/pipeline.log'))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        handlers=[logging.FileHandler(log_path, encoding='utf-8'), logging.StreamHandler()])
    init_dashboard(None, configuration)
    logger.info('Панель мониторинга доступна по настроенному адресу; вход обязателен')
    run_dashboard(configuration.get('dashboard', {}).get('host', '127.0.0.1'),
                  int(configuration.get('dashboard', {}).get('port', 5000)))


if __name__ == '__main__':
    main()
