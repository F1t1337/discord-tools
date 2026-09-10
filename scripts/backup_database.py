"""Consistent SQLite backup, including committed data from the WAL."""
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Резервная копия SQLite')
    parser.add_argument('database', type=Path)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    source_path = args.database.resolve()
    if not source_path.is_file():
        raise SystemExit('База данных не найдена')
    args.directory.mkdir(parents=True, exist_ok=True)
    target = args.directory / ('tokens-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.db')
    target.touch(mode=0o600, exist_ok=False)
    source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    print('Резервная копия создана: ' + str(target))


if __name__ == '__main__':
    main()
