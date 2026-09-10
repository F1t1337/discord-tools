"""Authenticated administrative dashboard. Standalone mode never starts workers."""
import argparse
import csv
import hmac
import io
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
        MAX_CONTENT_LENGTH=16 * 1024,
        TRUSTED_HOSTS=[settings['hostname']],
    )
    db = pipeline.db if pipeline is not None else Database(config['database']['path'])
    started_at, stop_requested = time.time(), False
    with state_lock:
        auth_sessions.clear()
        login_attempts.clear()
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
            'threads': {thread.name: thread.is_alive() for thread in pipeline.threads},
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
        lzt_enabled=bool(config.get('lzt', {}).get('enabled')),
        close_channels=bool(config.get('cleaner', {}).get('close_channels')),
        validator_workers=config.get('validator', {}).get('max_workers', 0),
        cleaner_workers=config.get('cleaner', {}).get('max_workers', 0),
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
    options = dict(host=host, port=port, threads=8, max_request_body_size=16384,
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
