#!/usr/bin/env python3
"""
Миграция БД - добавление seller_id и таблицы seller_statistics
"""
import sqlite3
import sys

def migrate_database(db_path='data/tokens.db'):
    """Добавляет новые колонки и таблицы в существующую БД"""
    
    print(f"🔄 Начало миграции БД: {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Добавляем колонку seller_id в таблицу tokens (если её нет)
        try:
            cursor.execute("ALTER TABLE tokens ADD COLUMN seller_id INTEGER")
            print("✅ Добавлена колонка seller_id в таблицу tokens")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print("⏭️  Колонка seller_id уже существует")
            else:
                raise
        
        # 2. Создаем таблицу seller_statistics (если её нет)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS seller_statistics (
                seller_id INTEGER PRIMARY KEY,
                seller_username TEXT,
                total_bought INTEGER DEFAULT 0,
                total_valid INTEGER DEFAULT 0,
                total_invalid INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                valid_percent REAL DEFAULT 0,
                last_purchase_at REAL,
                created_at REAL NOT NULL
            )
        """)
        print("✅ Таблица seller_statistics создана")
        
        conn.commit()
        conn.close()
        
        print("✅ Миграция завершена успешно!")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка миграции: {e}")
        return False


if __name__ == "__main__":
    # Путь к БД можно передать аргументом
    db_path = sys.argv[1] if len(sys.argv) > 1 else 'data/tokens.db'
    
    success = migrate_database(db_path)
    sys.exit(0 if success else 1)
