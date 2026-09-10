"""Interactive credential setup. Passwords and generated secrets are never printed."""
import argparse
import getpass
import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description='Настройка доступа к панели')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    args = parser.parse_args()
    username = input('Логин [admin]: ').strip() or 'admin'
    if len(username) > 100 or any(c in username for c in '\r\n'):
        raise SystemExit('Некорректный логин')
    public_url = input('Адрес панели (https://panel.example.com): ').strip().rstrip('/')
    try:
        url = urlsplit(public_url)
        url.port
    except ValueError:
        raise SystemExit('Некорректный адрес или порт панели')
    if (url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or
            url.path or url.query or url.fragment):
        raise SystemExit('Нужен полный адрес панели без пути')
    if url.scheme == 'http' and url.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise SystemExit('Для сервера нужен HTTPS')
    password = getpass.getpass('Новый пароль (не менее 14 символов): ')
    if len(password) < 14 or len(password) > 1024:
        raise SystemExit('Пароль должен содержать от 14 до 1024 символов')
    if password != getpass.getpass('Повторите пароль: '):
        raise SystemExit('Пароли не совпадают')
    values = {
        'DASHBOARD_USERNAME': username,
        'DASHBOARD_PASSWORD_HASH': generate_password_hash(password),
        'DASHBOARD_SECRET_KEY': secrets.token_urlsafe(48),
        'DASHBOARD_PUBLIC_URL': public_url,
    }
    if args.env_file.is_symlink():
        raise SystemExit('Файл окружения не должен быть символической ссылкой')
    target = args.env_file.resolve()
    lines = target.read_text(encoding='utf-8').splitlines() if target.exists() else []
    output, written = [], set()
    for line in lines:
        key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
        if key in values:
            if key not in written:
                output.append(key + '=' + values[key])
                written.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in written:
            output.append(key + '=' + value)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write('\n'.join(output) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    print('Доступ настроен. Перезапустите сервис панели. Значения секретов сохранены только в .env.')


if __name__ == '__main__':
    main()
