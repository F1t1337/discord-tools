"""Create a writable service config without overwriting an existing one."""
import argparse
import json
import os
from pathlib import Path


def prepare(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    config = json.loads(source.read_text(encoding='utf-8'))
    for section, key in (('database', 'path'), ('logging', 'file'), ('proxy', 'file')):
        value = config.get(section, {}).get(key)
        if value and not value.startswith('${') and not Path(value).is_absolute():
            config[section][key] = str(source.parent / value)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation deliberately refuses to overwrite an existing config.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return destination


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=root / 'config.json')
    parser.add_argument('--destination', type=Path, default=root / 'data/config.json')
    args = parser.parse_args()
    try:
        destination = prepare(args.source, args.destination)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f'Конфигурация не создана: {type(exc).__name__}\n')
    print(f'Создан файл конфигурации: {destination}')


if __name__ == '__main__':
    main()
