"""Owner identities and dashboard configuration. No credentials are logged."""

import os
import re
from urllib.parse import urlsplit


def telegram_owner_ids(value=None, private_chat_id=None):
    if value is None or value == "":
        value = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    if isinstance(value, (list, tuple, set)):
        parts = value
    else:
        parts = re.split(r"[,\s]+", str(value or "").strip())
    owners = {str(part).strip() for part in parts if str(part).strip()}
    if not owners and str(private_chat_id or "").isdigit() and int(private_chat_id) > 0:
        owners = {str(private_chat_id)}
    if not owners or any(not owner.isdigit() or int(owner) <= 0 for owner in owners):
        raise ValueError("Укажите числовые Telegram ID владельцев в TELEGRAM_ALLOWED_USER_IDS")
    return frozenset(owners)


def dashboard_settings(config):
    dashboard = config.get("dashboard", {})
    secret = os.environ.get("DASHBOARD_SECRET_KEY", "")
    password_hash = os.environ.get("DASHBOARD_PASSWORD_HASH", "")
    username = os.environ.get("DASHBOARD_USERNAME", "admin").strip()
    if len(secret) < 32:
        raise ValueError("Задайте DASHBOARD_SECRET_KEY длиной не менее 32 символов")
    if not password_hash.startswith(("scrypt:", "pbkdf2:sha256:")) or password_hash.count("$") != 2:
        raise ValueError("Создайте DASHBOARD_PASSWORD_HASH через scripts/setup_dashboard.py")
    if not username or len(username) > 100:
        raise ValueError("DASHBOARD_USERNAME должен содержать от 1 до 100 символов")
    public_url = os.environ.get("DASHBOARD_PUBLIC_URL", "").strip().rstrip("/")
    parts = urlsplit(public_url)
    if (parts.scheme not in {"http", "https"} or not parts.hostname or
            parts.username or parts.password or parts.path not in {"", "/"} or
            parts.query or parts.fragment):
        raise ValueError("DASHBOARD_PUBLIC_URL: полный адрес панели без пути, например https://panel.example.com")
    if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Для удалённого доступа DASHBOARD_PUBLIC_URL должен использовать HTTPS")
    try:
        parts.port
    except ValueError:
        raise ValueError("Некорректный порт в DASHBOARD_PUBLIC_URL") from None
    if dashboard.get('host', '127.0.0.1') not in {'127.0.0.1', '::1', 'localhost'}:
        raise ValueError("dashboard.host должен быть loopback-адресом; удалённый доступ работает через HTTPS-прокси")
    return {
        "secret": secret, "password_hash": password_hash, "username": username,
        "origin": f"{parts.scheme}://{parts.netloc}", "hostname": parts.hostname,
        "secure": parts.scheme == "https",
        "session_hours": max(1, min(int(dashboard.get("session_hours", 8)), 24)),
    }
