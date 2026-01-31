import sqlite3
import logging
from typing import List, Dict, Optional
from datetime import datetime, date
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class Database:
    """Класс для работы с SQLite базой данных"""
    
    def __init__(self, db_path: str = "data/tokens.db"):
        """
        Инициализация базы данных
        
        Args:
            db_path: Путь к файлу базы данных
        """
        self.db_path = db_path
        self._create_tables()
        logger.info(f"📦 База данных инициализирована: {db_path}")
    
    @contextmanager
    def get_connection(self):
        """Контекстный менеджер для подключения к БД"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Возвращать результаты как словари
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"❌ Ошибка БД: {e}")
            raise
        finally:
            conn.close()
    
    def _create_tables(self):
        """Создает таблицы если их нет"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Таблица токенов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT NOT NULL UNIQUE,
                    username TEXT,
                    lzt_item_id INTEGER,
                    seller_id INTEGER,
                    price REAL,
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at REAL NOT NULL,
                    validated_at REAL,
                    cleaned_at REAL,
                    sent_at REAL,
                    error TEXT,
                    cleaning_progress TEXT,
                    UNIQUE(token)
                )
            """)
            
            # Таблица статистики
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS statistics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL UNIQUE,
                    tokens_bought INTEGER DEFAULT 0,
                    tokens_valid INTEGER DEFAULT 0,
                    tokens_cleaned INTEGER DEFAULT 0,
                    tokens_sent INTEGER DEFAULT 0,
                    money_spent REAL DEFAULT 0,
                    success_rate REAL DEFAULT 0
                )
            """)
            
            # Таблица статистики продавцов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS seller_statistics (
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
            
            # Таблица логов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    level TEXT NOT NULL,
                    module TEXT NOT NULL,
                    message TEXT NOT NULL
                )
            """)
            
            # Индексы для быстрого поиска
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tokens_status ON tokens(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tokens_created ON tokens(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)")
            
            conn.commit()
    
    # ==================== РАБОТА С ТОКЕНАМИ ====================
    
    def add_token(self, token: str, lzt_item_id: int = None, seller_id: int = None, 
                  seller_username: str = None, price: float = None) -> Optional[int]:
        """
        Добавляет новый токен в базу
        
        Args:
            token: Discord токен
            lzt_item_id: ID товара с LZT
            seller_id: ID продавца
            seller_username: Username продавца
            price: Цена покупки
            
        Returns:
            ID добавленной записи или None при ошибке
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO tokens (token, lzt_item_id, seller_id, seller_username, price, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'new', ?)
                """, (token, lzt_item_id, None, seller_username, price, datetime.now().timestamp()))
                
                token_id = cursor.lastrowid
                
                # Обновляем статистику продавца
                if seller_username:
                    self._update_seller_stats_purchase(seller_username, price)
                
                logger.info(f"➕ Токен добавлен в БД: ID={token_id}, Seller={seller_id}")
                return token_id
                
        except sqlite3.IntegrityError:
            logger.warning(f"⚠️ Токен уже существует в БД")
            return None
    
    def update_token_status(self, token: str, status: str, username: str = None, error: str = None, cleaning_progress: str = None):
        """
        Обновляет статус токена
        
        Args:
            token: Discord токен
            status: Новый статус (new, validated, cleaning, cleaned, ready, sent, invalid, locked)
            username: Username (если валидация прошла)
            error: Текст ошибки
            cleaning_progress: Прогресс очистки (для отображения)
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            timestamp_field = None
            if status == 'validated':
                timestamp_field = 'validated_at'
            elif status == 'cleaned':
                timestamp_field = 'cleaned_at'
            elif status == 'sent':
                timestamp_field = 'sent_at'
            
            if timestamp_field:
                cursor.execute(f"""
                    UPDATE tokens 
                    SET status = ?, username = COALESCE(?, username), error = ?, cleaning_progress = ?, {timestamp_field} = ?
                    WHERE token = ?
                """, (status, username, error, cleaning_progress, datetime.now().timestamp(), token))
            else:
                cursor.execute("""
                    UPDATE tokens 
                    SET status = ?, username = COALESCE(?, username), error = ?, cleaning_progress = ?
                    WHERE token = ?
                """, (status, username, error, cleaning_progress, token))
            
            logger.info(f"🔄 Статус токена обновлен: {status}")
    
    def get_tokens_by_status(self, status: str) -> List[Dict]:
        """
        Получает токены по статусу
        
        Args:
            status: Статус токенов
            
        Returns:
            Список словарей с данными токенов
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tokens WHERE status = ? ORDER BY created_at DESC", (status,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def get_token_info(self, token: str) -> Optional[Dict]:
        """
        Получает информацию о токене
        
        Args:
            token: Discord токен
            
        Returns:
            Словарь с данными или None
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tokens WHERE token = ?", (token,))
            
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_ready_tokens(self, limit: int = 50) -> List[Dict]:
        """
        Получает готовые токены для отправки
        
        Args:
            limit: Максимальное количество
            
        Returns:
            Список токенов со статусом 'ready'
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM tokens 
                WHERE status = 'ready' 
                ORDER BY created_at ASC 
                LIMIT ?
            """, (limit,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def count_tokens_by_status(self) -> Dict[str, int]:
        """
        Подсчитывает количество токенов по статусам
        
        Returns:
            Словарь {статус: количество}
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, COUNT(*) as count FROM tokens GROUP BY status")
            
            rows = cursor.fetchall()
            return {row['status']: row['count'] for row in rows}
    
    def delete_token(self, token: str):
        """Удаляет токен из базы"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tokens WHERE token = ?", (token,))
            logger.info(f"🗑️ Токен удален из БД")
    
    # ==================== СТАТИСТИКА ====================
    
    def update_statistics(self, tokens_bought: int = 0, tokens_valid: int = 0, 
                         tokens_cleaned: int = 0, tokens_sent: int = 0, 
                         money_spent: float = 0):
        """
        Обновляет статистику за сегодня
        
        Args:
            tokens_bought: Куплено токенов
            tokens_valid: Валидных токенов
            tokens_cleaned: Очищенных токенов
            tokens_sent: Отправлено токенов
            money_spent: Потрачено денег
        """
        today = date.today().isoformat()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Получаем текущие значения
            cursor.execute("SELECT * FROM statistics WHERE date = ?", (today,))
            row = cursor.fetchone()
            
            if row:
                # Обновляем существующую запись
                cursor.execute("""
                    UPDATE statistics 
                    SET tokens_bought = tokens_bought + ?,
                        tokens_valid = tokens_valid + ?,
                        tokens_cleaned = tokens_cleaned + ?,
                        tokens_sent = tokens_sent + ?,
                        money_spent = money_spent + ?
                    WHERE date = ?
                """, (tokens_bought, tokens_valid, tokens_cleaned, tokens_sent, money_spent, today))
            else:
                # Создаем новую запись
                cursor.execute("""
                    INSERT INTO statistics (date, tokens_bought, tokens_valid, tokens_cleaned, tokens_sent, money_spent)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (today, tokens_bought, tokens_valid, tokens_cleaned, tokens_sent, money_spent))
            
            # Обновляем success_rate
            cursor.execute("""
                UPDATE statistics 
                SET success_rate = CASE 
                    WHEN tokens_bought > 0 THEN (tokens_sent * 100.0 / tokens_bought)
                    ELSE 0
                END
                WHERE date = ?
            """, (today,))
    
    def get_today_statistics(self) -> Dict:
        """Получает статистику за сегодня"""
        today = date.today().isoformat()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM statistics WHERE date = ?", (today,))
            
            row = cursor.fetchone()
            if row:
                return dict(row)
            else:
                return {
                    'date': today,
                    'tokens_bought': 0,
                    'tokens_valid': 0,
                    'tokens_cleaned': 0,
                    'tokens_sent': 0,
                    'money_spent': 0,
                    'success_rate': 0
                }
    
    def get_statistics_range(self, days: int = 7) -> List[Dict]:
        """
        Получает статистику за период
        
        Args:
            days: Количество дней
            
        Returns:
            Список статистики по дням
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM statistics 
                ORDER BY date DESC 
                LIMIT ?
            """, (days,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def get_total_statistics(self) -> Dict:
        """Получает общую статистику за все время"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    SUM(tokens_bought) as total_bought,
                    SUM(tokens_valid) as total_valid,
                    SUM(tokens_cleaned) as total_cleaned,
                    SUM(tokens_sent) as total_sent,
                    SUM(money_spent) as total_money_spent,
                    AVG(success_rate) as avg_success_rate
                FROM statistics
            """)
            
            row = cursor.fetchone()
            return dict(row) if row else {}
    
    # ==================== ЛОГИ ====================
    
    def add_log(self, level: str, module: str, message: str):
        """
        Добавляет лог в базу
        
        Args:
            level: Уровень (INFO, WARNING, ERROR)
            module: Название модуля
            message: Сообщение
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO logs (timestamp, level, module, message)
                VALUES (?, ?, ?, ?)
            """, (datetime.now().timestamp(), level, module, message))
    
    def get_recent_logs(self, limit: int = 100, level: str = None) -> List[Dict]:
        """
        Получает последние логи
        
        Args:
            limit: Количество логов
            level: Фильтр по уровню
            
        Returns:
            Список логов
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if level:
                cursor.execute("""
                    SELECT * FROM logs 
                    WHERE level = ?
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (level, limit))
            else:
                cursor.execute("""
                    SELECT * FROM logs 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (limit,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def clear_old_logs(self, days: int = 30):
        """
        Удаляет старые логи
        
        Args:
            days: Старше скольки дней удалять
        """
        cutoff = (datetime.now().timestamp() - (days * 24 * 60 * 60))
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM logs WHERE timestamp < ?", (cutoff,))
            deleted = cursor.rowcount
            
            if deleted > 0:
                logger.info(f"🗑️ Удалено старых логов: {deleted}")


# Пример использования
    
    # ==================== СТАТИСТИКА ПРОДАВЦОВ ====================
    
    def _update_seller_stats_purchase(self, seller_username: str, price: float = None):
        """Обновляет статистику продавца при покупке"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Проверяем существует ли продавец
                cursor.execute("SELECT seller_username FROM seller_statistics WHERE seller_username = ?", (seller_username,))
                exists = cursor.fetchone()
                
                if exists:
                    # Обновляем существующую запись
                    cursor.execute("""
                        UPDATE seller_statistics
                        SET total_bought = total_bought + 1,
                            total_spent = total_spent + ?,
                            avg_price = (total_spent + ?) / (total_bought + 1),
                            last_purchase_at = ?
                        WHERE seller_username = ?
                    """, (price or 0, price or 0, datetime.now().timestamp(), seller_username))
                else:
                    # Создаем новую запись
                    cursor.execute("""
                        INSERT INTO seller_statistics 
                        (seller_username, total_bought, total_spent, avg_price, last_purchase_at, created_at)
                        VALUES (?, 1, ?, ?, ?, ?)
                    """, (seller_username, price or 0, price or 0, datetime.now().timestamp(), datetime.now().timestamp()))
                
        except Exception as e:
            logger.error(f"❌ Ошибка обновления статистики продавца: {e}")
    
    def update_seller_stats_validation(self, seller_username: str, is_valid: bool):
        """Обновляет статистику продавца после валидации (когда токен становится invalid)"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Увеличиваем счетчик невалидных только если токен стал invalid
                if not is_valid:
                    cursor.execute("""
                        UPDATE seller_statistics
                        SET total_invalid = total_invalid + 1
                        WHERE seller_username = ?
                    """, (seller_username,))
                
                # Пересчитываем процент валидности
                # valid = total_bought - total_invalid
                cursor.execute("""
                    UPDATE seller_statistics
                    SET valid_percent = CASE 
                        WHEN total_bought > 0 
                        THEN ((total_bought - total_invalid) * 100.0) / total_bought
                        ELSE 0
                    END
                    WHERE seller_username = ?
                """, (seller_username,))
                
        except Exception as e:
            logger.error(f"❌ Ошибка обновления валидации продавца: {e}")
    
    def get_seller_statistics(self, limit: int = None) -> List[Dict]:
        """
        Получает статистику по продавцам
        
        Args:
            limit: Максимальное количество продавцов (None = все)
            
        Returns:
            Список словарей со статистикой продавцов
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                if limit:
                    cursor.execute("""
                        SELECT 
                            seller_username,
                            total_bought,
                            total_invalid,
                            total_spent,
                            avg_price,
                            valid_percent,
                            last_purchase_at
                        FROM seller_statistics
                        WHERE total_bought > 0
                        ORDER BY total_bought DESC
                        LIMIT ?
                    """, (limit,))
                else:
                    cursor.execute("""
                        SELECT 
                            seller_username,
                            total_bought,
                            total_invalid,
                            total_spent,
                            avg_price,
                            valid_percent,
                            last_purchase_at
                        FROM seller_statistics
                        WHERE total_bought > 0
                        ORDER BY total_bought DESC
                    """)
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Ошибка получения статистики продавцов: {e}")
            return []
    
    def get_seller_by_username(self, seller_username: str) -> Optional[Dict]:
        """Получает статистику конкретного продавца"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT *
                    FROM seller_statistics
                    WHERE seller_username = ?
                """, (seller_username,))
                
                row = cursor.fetchone()
                return dict(row) if row else None
                
        except Exception as e:
            logger.error(f"❌ Ошибка получения продавца: {e}")
            return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Создаем базу
    db = Database("test_tokens.db")
    
    # Добавляем токены
    print("\n📝 Добавление токенов...")
    token1_id = db.add_token("MTAx.test.token123", lzt_item_id=12345, price=25.0)
    token2_id = db.add_token("MTAy.test.token456", lzt_item_id=12346, price=30.0)
    
    # Обновляем статусы
    print("\n🔄 Обновление статусов...")
    db.update_token_status("MTAx.test.token123", "validated", username="TestUser#1234")
    db.update_token_status("MTAy.test.token456", "invalid", error="Token locked")
    
    # Получаем токены по статусу
    print("\n📊 Токены со статусом 'validated':")
    validated = db.get_tokens_by_status("validated")
    for token in validated:
        print(f"  - {token['username']}: {token['token'][:20]}...")
    
    # Статистика по статусам
    print("\n📈 Статистика по статусам:")
    stats = db.count_tokens_by_status()
    for status, count in stats.items():
        print(f"  {status}: {count}")
    
    # Обновляем статистику
    print("\n💰 Обновление статистики...")
    db.update_statistics(tokens_bought=2, tokens_valid=1, money_spent=55.0)
    
    # Получаем статистику за сегодня
    print("\n📊 Статистика за сегодня:")
    today_stats = db.get_today_statistics()
    print(f"  Куплено: {today_stats['tokens_bought']}")
    print(f"  Валидных: {today_stats['tokens_valid']}")
    print(f"  Потрачено: {today_stats['money_spent']} ₽")
    print(f"  Success rate: {today_stats['success_rate']:.1f}%")
    
    # Добавляем лог
    print("\n📝 Добавление логов...")
    db.add_log("INFO", "test", "Тестовое сообщение")
    db.add_log("ERROR", "test", "Тестовая ошибка")
    
    # Получаем логи
    print("\n📋 Последние логи:")
    logs = db.get_recent_logs(limit=5)
    for log in logs:
        print(f"  [{log['level']}] {log['module']}: {log['message']}")
    
    print("\n✅ Тест завершен!")