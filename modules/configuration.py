"""Configuration loading shared by the worker and the monitoring-only server."""

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env_file(env_path=None):
    path = Path(env_path or PROJECT_ROOT / '.env')
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def resolve_env_placeholders(value):
    if isinstance(value, dict):
        return {key: resolve_env_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_env_placeholders(item) for item in value]
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


def load_config(config_path=None):
    path = Path(config_path or PROJECT_ROOT / "config.json").resolve()
    load_env_file(path.parent / ".env")
    config = resolve_env_placeholders(json.loads(path.read_text(encoding="utf-8")))
    for section, key in (("database", "path"), ("logging", "file")):
        value = config.get(section, {}).get(key)
        if value and not Path(value).is_absolute():
            config[section][key] = str(path.parent / value)
    return config
