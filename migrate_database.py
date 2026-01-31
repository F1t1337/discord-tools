"""
Скрипт миграции базы данных для использования seller_username вместо seller_id
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = "data/tokens.db"

def migrate_database():
    """Мигрирует базу данных на новую структуру с seller_username"""
    
    if not os.path.exists(DB_PATH):
        print(f"❌ База данных не найдена: {DB_PATH}")
        return
    
    # Бэкап
    backup_path = f"{DB_PATH}.backup_{int(datetime.now().timestamp())}"
    import shutil
    shutil.copy2(DB_PATH, backup_path)
    print(f"✅ Создан бэкап: {backup_path}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 1. СНАЧАЛА добавляем поле seller_username в таблицу tokens если его нет
        print("🔧 Проверка поля seller_username в таблице tokens...")
        cursor.execute("PRAGMA table_info(tokens)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'seller_username' not in columns:
            print("📝 Добавление поля seller_username в таблицу tokens...")
            cursor.execute("ALTER TABLE tokens ADD COLUMN seller_username TEXT")
            print("✅ Поле seller_username добавлено")
        else:
            print("✅ Поле seller_username уже существует")
        
        # 2. Создаем новую таблицу seller_statistics с правильной структурой
        print("📝 Создание новой таблицы seller_statistics_new...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS seller_statistics_new (
                seller_username TEXT PRIMARY KEY,
                total_bought INTEGER DEFAULT 0,
                total_invalid INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                avg_price REAL DEFAULT 0,
                valid_percent REAL DEFAULT 0,
                last_purchase_at REAL,
                created_at REAL NOT NULL
            )
        """)
        
        # 3. Проверяем есть ли старая таблица
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seller_statistics'")
        old_table_exists = cursor.fetchone() is not None
        
        if old_table_exists:
            print("📊 Миграция данных из старой таблицы...")
            
            # Получаем данные из старой таблицы
            cursor.execute("""
                SELECT 
                    seller_username,
                    total_bought,
                    total_invalid,
                    total_spent,
                    valid_percent,
                    last_purchase_at,
                    created_at
                FROM seller_statistics
                WHERE seller_username IS NOT NULL
            """)
            
            old_data = cursor.fetchall()
            print(f"📦 Найдено {len(old_data)} записей для миграции")
            
            # Переносим данные
            for row in old_data:
                seller_username, total_bought, total_invalid, total_spent, valid_percent, last_purchase_at, created_at = row
                
                # Рассчитываем среднюю цену
                avg_price = (total_spent / total_bought) if total_bought > 0 else 0
                
                cursor.execute("""
                    INSERT OR REPLACE INTO seller_statistics_new 
                    (seller_username, total_bought, total_invalid, total_spent, avg_price, valid_percent, last_purchase_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (seller_username, total_bought, total_invalid, total_spent, avg_price, valid_percent, last_purchase_at, created_at))
            
            print(f"✅ Мигрировано {len(old_data)} записей")
            
            # Удаляем старую таблицу
            cursor.execute("DROP TABLE seller_statistics")
            print("🗑️  Старая таблица удалена")
        
        # 4. Переименовываем новую таблицу
        cursor.execute("ALTER TABLE seller_statistics_new RENAME TO seller_statistics")
        print("✅ Новая таблица переименована")
        
        # 5. Пересчитываем статистику из tokens (только если есть seller_username)
        print("🔄 Пересчет статистики из таблицы tokens...")
        
        # Получаем все токены с seller_username
        cursor.execute("""
            SELECT seller_username, price, status
            FROM tokens
            WHERE seller_username IS NOT NULL AND seller_username != ''
        """)
        
        tokens = cursor.fetchall()
        print(f"📦 Найдено {len(tokens)} токенов с продавцами")
        
        if len(tokens) > 0:
            # Группируем по продавцам
            sellers_data = {}
            for seller_username, price, status in tokens:
                if seller_username not in sellers_data:
                    sellers_data[seller_username] = {
                        'total_bought': 0,
                        'total_invalid': 0,
                        'total_spent': 0,
                        'first_seen': datetime.now().timestamp()
                    }
                
                sellers_data[seller_username]['total_bought'] += 1
                sellers_data[seller_username]['total_spent'] += (price or 0)
                
                if status == 'invalid':
                    sellers_data[seller_username]['total_invalid'] += 1
            
            # Обновляем статистику
            for seller_username, data in sellers_data.items():
                total_bought = data['total_bought']
                total_invalid = data['total_invalid']
                total_valid = total_bought - total_invalid
                total_spent = data['total_spent']
                avg_price = total_spent / total_bought if total_bought > 0 else 0
                valid_percent = (total_valid / total_bought * 100) if total_bought > 0 else 0
                
                cursor.execute("""
                    INSERT OR REPLACE INTO seller_statistics 
                    (seller_username, total_bought, total_invalid, total_spent, avg_price, valid_percent, last_purchase_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (seller_username, total_bought, total_invalid, total_spent, avg_price, valid_percent, 
                      datetime.now().timestamp(), data['first_seen']))
            
            print(f"✅ Обновлена статистика для {len(sellers_data)} продавцов")
        else:
            print("💡 Нет токенов с информацией о продавцах (это нормально для новой установки)")
        
        conn.commit()
        
        # Показываем результат
        cursor.execute("SELECT COUNT(*) FROM seller_statistics")
        count = cursor.fetchone()[0]
        print(f"\n📊 Итого продавцов в базе: {count}")
        
        if count > 0:
            # Показываем топ-5
            cursor.execute("""
                SELECT seller_username, total_bought, total_invalid, avg_price, valid_percent
                FROM seller_statistics
                ORDER BY total_bought DESC
                LIMIT 5
            """)
            
            print("\n🏆 Топ-5 продавцов:")
            for row in cursor.fetchall():
                username, bought, invalid, avg_price, valid_pct = row
                valid = bought - invalid
                print(f"  👤 {username}: 📦 {bought} | ✅ {valid} | ❌ {invalid} | 💯 {valid_pct:.1f}% | 💰 {avg_price:.1f} ₽")
        
        print(f"\n✅ Миграция успешно завершена!")
        print(f"💾 Бэкап сохранен: {backup_path}")
        input("Нажмите Enter чтобы продолжить...")
    except Exception as e:
        print(f"\n❌ Ошибка миграции: {e}")
        conn.rollback()
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    print("="*60)
    print("🔧 МИГРАЦИЯ БАЗЫ ДАННЫХ")
    print("="*60)
    print()
    
    migrate_database()
    input("\nНажмите Enter чтобы выйти...")