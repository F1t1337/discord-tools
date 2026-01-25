import requests
import time
import json
import logging
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class LZTMonitor:
    """Класс для мониторинга покупок на LZT Market"""
    
    BASE_URL = "https://prod-api.lzt.market"
    DISCORD_CATEGORY_ID = 22  # ID категории Discord
    
    def __init__(self, api_token: str, check_interval: int = 60, min_balance_alert: float = 100, database=None):
        """
        Инициализация LZT монитора
        
        Args:
            api_token: API токен LZT
            check_interval: Интервал проверки новых покупок (секунды)
            min_balance_alert: Минимальный баланс для уведомления
            database: Объект Database для проверки существующих токенов
        """
        self.api_token = api_token
        self.check_interval = check_interval
        self.min_balance_alert = min_balance_alert
        self.database = database
        self.headers = {
            'Authorization': f'Bearer {api_token}',
            'accept': 'application/json'
        }
        self.last_check_time = None
        self.processed_items = set()  # ID уже обработанных покупок
        self.user_id = None  # ID пользователя (получим при первом запросе)
        
    def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """
        Выполняет запрос к API LZT
        
        Args:
            endpoint: Эндпоинт API
            params: Параметры запроса
            
        Returns:
            Ответ API в виде словаря или None при ошибке
        """
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                logger.error("❌ LZT API: Неверный токен авторизации")
                return None
            else:
                logger.error(f"❌ LZT API Error: {response.status_code} - {response.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Ошибка подключения к LZT API: {e}")
            return None
    
    def get_balance(self) -> Optional[float]:
        """
        Получает текущий баланс аккаунта
        
        Returns:
            Баланс или None при ошибке
        """
        data = self._make_request("/me")
        
        if data and 'user' in data:
            balance = data['user'].get('balance', 0)
            # Сохраняем user_id для последующих запросов
            if not self.user_id:
                self.user_id = data['user'].get('user_id')
            logger.info(f"💰 Текущий баланс LZT: {balance} ₽")
            return float(balance)
        
        return None
    
    def check_balance_alert(self) -> bool:
        """
        Проверяет баланс и возвращает True если нужно отправить уведомление
        
        Returns:
            True если баланс ниже минимального
        """
        balance = self.get_balance()
        
        if balance is not None and balance < self.min_balance_alert:
            logger.warning(f"⚠️ Низкий баланс LZT: {balance} ₽ (минимум: {self.min_balance_alert} ₽)")
            return True
        
        return False
    
    def get_purchased_accounts(self, category_id: int = None) -> List[Dict]:
        """
        Получает список купленных аккаунтов (заказы)
        
        Args:
            category_id: ID категории (22 для Discord)
            
        Returns:
            Список купленных аккаунтов
        """
        # Получаем user_id если еще не получили
        if not self.user_id:
            self.get_balance()
        
        if not self.user_id:
            logger.error("❌ Не удалось получить user_id")
            return []
        
        params = {
            'order_by': 'pdate_to_down',  # Новые первыми
        }
        
        if category_id:
            params['category_id'] = category_id
        
        # Используем эндпоинт /user/{user_id}/orders
        data = self._make_request(f"/user/{self.user_id}/orders", params=params)
        
        if data and 'items' in data:
            return data['items']
        
        return []
    
    def get_account_details(self, item_id: int) -> Optional[Dict]:
        """
        Получает детальную информацию об аккаунте
        
        Args:
            item_id: ID товара
            
        Returns:
            Информация об аккаунте
        """
        data = self._make_request(f"/{item_id}")
        
        if data and 'item' in data:
            return data['item']
        
        return None
    
    def get_account_goods(self, item_id: int) -> Optional[Dict]:
        """
        Получает данные для входа в купленный аккаунт
        
        Args:
            item_id: ID товара
            
        Returns:
            Данные аккаунта (логин, пароль, токен и т.д.)
        """
        # Простой GET запрос к /{item_id} возвращает данные с токеном в поле login
        data = self._make_request(f"/{item_id}")
        
        if data and 'item' in data:
            return data['item']
        
        return None
    
    def extract_token_from_account(self, item_id: int) -> Optional[str]:
        """
        Извлекает Discord токен из купленного аккаунта
        
        Args:
            item_id: ID товара
            
        Returns:
            Discord токен или None
        """
        # Получаем данные аккаунта
        account = self.get_account_goods(item_id)
        
        if not account:
            logger.warning(f"⚠️ Не удалось получить данные аккаунта {item_id}")
            return None
        
        # 1. Токен находится в поле login
        if 'login' in account and account['login']:
            token = account['login']
            if isinstance(token, str) and len(token) > 50:
                logger.info(f"✅ Токен найден в поле 'login'")
                return token
        
        # 2. Проверяем поле password (иногда там токен)
        if 'password' in account and account['password']:
            password = account['password']
            if isinstance(password, str) and len(password) > 50:
                logger.info(f"✅ Токен найден в поле 'password'")
                return password
        
        # 3. Проверяем поле token
        if 'token' in account and account['token']:
            return account['token']
        
        # 4. Проверяем login_data
        if 'login_data' in account and account['login_data']:
            login_data = account['login_data']
            if isinstance(login_data, str) and len(login_data) > 50:
                return login_data
        
        # 5. Проверяем account_data
        if 'account_data' in account and account['account_data']:
            account_data = account['account_data']
            if isinstance(account_data, str) and len(account_data) > 50:
                return account_data
        
        logger.warning(f"⚠️ Не удалось извлечь токен из аккаунта {item_id}")
        logger.debug(f"Доступные поля: {list(account.keys())}")
        return None
    
    def get_new_purchases(self) -> List[Dict]:
        """
        Получает новые покупки Discord аккаунтов с момента последней проверки
        
        Returns:
            Список новых покупок с токенами
        """
        accounts = self.get_purchased_accounts(category_id=self.DISCORD_CATEGORY_ID)
        new_purchases = []
        
        # При первом запуске проверяем последние 10 аккаунтов
        if self.last_check_time is None:
            self.last_check_time = datetime.now().timestamp()
            logger.info(f"🔄 Первый запуск: проверяем последние 10 покупок...")
            
            # Берем последние 10 аккаунтов
            recent_accounts = accounts[:10]
            
            for account in recent_accounts:
                item_id = account.get('item_id')
                
                # Получаем токен
                token = self.extract_token_from_account(item_id)
                
                if token:
                    # Проверяем есть ли в БД
                    if self.database:
                        existing = self.database.get_token_info(token)
                        
                        if existing:
                            # Токен уже есть в БД - пропускаем
                            self.processed_items.add(item_id)
                            logger.debug(f"⏭️ Токен {item_id} уже в БД, пропускаем")
                            continue
                    
                    # Токен новый - обрабатываем
                    logger.info(f"🆕 Найден новый токен при запуске: Item ID {item_id}")
                    
                    purchase_info = {
                        'item_id': item_id,
                        'token': token,
                        'price': account.get('price', 0),
                        'username': account.get('title', 'Unknown'),
                        'seller': account.get('seller', 'Unknown')
                    }
                    
                    new_purchases.append(purchase_info)
                    self.processed_items.add(item_id)
                else:
                    self.processed_items.add(item_id)
            
            logger.info(f"✅ Проверка при запуске завершена: найдено {len(new_purchases)} новых токенов")
            return new_purchases
        
        # Обычная проверка - ищем только новые item_id
        for account in accounts:
            item_id = account.get('item_id')
            
            # Пропускаем уже обработанные
            if item_id in self.processed_items:
                continue
            
            # Это новая покупка! Извлекаем токен
            logger.info(f"🆕 Обнаружена новая покупка: Item ID {item_id}")
            
            token = self.extract_token_from_account(item_id)
            
            if token:
                purchase_info = {
                    'item_id': item_id,
                    'token': token,
                    'price': account.get('price', 0),
                    'username': account.get('title', 'Unknown'),
                    'seller': account.get('seller', 'Unknown')
                }
                
                new_purchases.append(purchase_info)
                self.processed_items.add(item_id)
                
                logger.info(f"✅ Новая покупка добавлена: {purchase_info['username']} за {purchase_info['price']} ₽")
            else:
                logger.warning(f"⚠️ Не удалось получить токен для покупки {item_id}")
                self.processed_items.add(item_id)
        
        return new_purchases
    
    def start_monitoring(self, callback_func):
        """
        Запускает непрерывный мониторинг новых покупок
        
        Args:
            callback_func: Функция которая будет вызвана при новых покупках
                          Принимает список словарей с информацией о покупках
        """
        logger.info(f"🔍 Запуск мониторинга LZT Market (интервал: {self.check_interval}с)")
        
        # Первая проверка баланса
        self.check_balance_alert()
        
        while True:
            try:
                # Получаем новые покупки
                new_purchases = self.get_new_purchases()
                
                if new_purchases:
                    logger.info(f"📦 Найдено новых покупок: {len(new_purchases)}")
                    callback_func(new_purchases)
                
                # Периодически проверяем баланс (каждые 10 проверок)
                if len(self.processed_items) % 10 == 0:
                    if self.check_balance_alert():
                        # Можно добавить вызов функции для отправки уведомления
                        pass
                
                # Ждем до следующей проверки
                time.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                logger.info("⏹️ Остановка мониторинга LZT")
                break
            except Exception as e:
                logger.error(f"❌ Ошибка в мониторинге LZT: {e}")
                time.sleep(self.check_interval)


# Пример использования
if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Пример конфигурации
    config = {
        "api_token": "your_lzt_token_here",
        "check_interval": 60,
        "min_balance_alert": 100
    }
    
    # Создаем монитор
    monitor = LZTMonitor(
        api_token=config['api_token'],
        check_interval=config['check_interval'],
        min_balance_alert=config['min_balance_alert']
    )
    
    # Функция обработки новых покупок
    def handle_new_purchases(purchases):
        """Обработка новых покупок"""
        for purchase in purchases:
            print(f"\n📦 Новая покупка:")
            print(f"   Token: {purchase['token'][:20]}...")
            print(f"   Username: {purchase['username']}")
            print(f"   Price: {purchase['price']} ₽")
            print(f"   Seller: {purchase['seller']}")
    
    # Запускаем мониторинг
    monitor.start_monitoring(handle_new_purchases)