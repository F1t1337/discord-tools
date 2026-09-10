"""Apply the versioned migration used at application startup."""
import argparse
from modules.configuration import load_config
from modules.database import Database


def main():
    parser = argparse.ArgumentParser(description="Миграция SQLite с резервной копией")
    parser.add_argument('--config', help='Путь к config.json')
    args = parser.parse_args()
    config = load_config(args.config)
    db = Database(config['database']['path'])
    print(f"Схема БД: версия {db.schema_version()}. Данные сохранены.")


if __name__ == '__main__':
    main()
