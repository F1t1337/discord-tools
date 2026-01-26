"""
Модуль для мониторинга покупок на LZT Market
Обновлено: добавлен флаг для предотвращения спама уведомлений о низком балансе
"""
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
        
        # НОВОЕ: Флаг для отслеживания отправки уведомления о низком балансе
        self.low_balance_notification_sent = False
        
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
                logger.error(f"❌ LZT API error: {response.status_code} - {response.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Ошибка запроса к LZT API: {e}")
            return None
    
    def get_balance(self) -> Optional[float]:
        """
        Получает текущий баланс пользователя
        
        Returns:
            Баланс в рублях или None при ошибке
        """
        data = self._make_request("/me")
        
        if data and 'user' in data:
            balance = float(data['user'].get('balance', 0))
            self.user_id = data['user'].get('user_id')
            logger.debug(f"💰 Баланс LZT: {balance} ₽")
            return balance
        
        return None
    
    def check_balance_alert(self) -> bool:
        """
        Проверяет баланс и возвращает True если нужно отправить уведомление
        
        ОБНОВЛЕНО: Теперь проверяет флаг уведомления
        - Если баланс низкий И уведомление не было отправлено → возвращает True и устанавливает флаг
        - Если баланс восстановился → сбрасывает флаг
        - Если баланс низкий НО уведомление уже отправлено → возвращает False (не спамим)
        
        Returns:
            True если баланс ниже минимального И нужно отправить уведомление
        """
        balance = self.get_balance()
        
        if balance is None:
            return False
        
        # Проверяем восстановился ли баланс
        if balance >= self.min_balance_alert:
            # Баланс в норме - сбрасываем флаг
            if self.low_balance_notification_sent:
                logger.info(f"✅ Баланс восстановлен: {balance} ₽ (минимум: {self.min_balance_alert} ₽)")
                self.low_balance_notification_sent = False
            return False
        
        # Баланс низкий
        if not self.low_balance_notification_sent:
            # Уведомление еще не отправляли - отправляем
            logger.warning(f"⚠️ Низкий баланс LZT: {balance} ₽ (минимум: {self.min_balance_alert} ₽)")
            self.low_balance_notification_sent = True
            return True
        else:
            # Уведомление уже отправлено - не спамим
            logger.debug(f"Баланс все еще низкий: {balance} ₽, но уведомление уже отправлено")
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
    
    def get_new_purchases(self) -> List[Dict]:
        """
        Получает новые покупки Discord токенов с момента последней проверки
        
        Returns:
            Список новых покупок с токенами
        """
        purchases = self.get_purchased_accounts(category_id=self.DISCORD_CATEGORY_ID)
        new_purchases = []
        
        logger.debug(f"📋 [LZT] Получено покупок из API: {len(purchases)}")
        
        for purchase in purchases:
            item_id = purchase.get('item_id')
            
            # Пропускаем уже обработанные
            if item_id in self.processed_items:
                logger.debug(f"⏭️ [LZT] Item {item_id} уже обработан ранее")
                continue
            
            logger.debug(f"🔍 [LZT] Проверка item {item_id}")
            
            # ИСПРАВЛЕНО: Передаем весь purchase, а не только item
            # Извлекаем Discord токен
            token = self._extract_discord_token(purchase)
            
            if token:
                logger.debug(f"✅ [LZT] Токен найден для item {item_id}")
                
                # Проверяем есть ли токен уже в БД (если передана БД)
                if self.database:
                    existing = self.database.get_token_info(token)
                    if existing:
                        logger.debug(f"⏭️ Токен {item_id} уже в БД, пропускаем")
                        self.processed_items.add(item_id)
                        continue
                
                purchase_info = {
                    'item_id': item_id,
                    'token': token,
                    'price': purchase.get('price', 0),
                    'username': purchase.get('title', 'Unknown'),  # Берем title из purchase
                    'purchase_date': purchase.get('purchase_date')
                }
                
                new_purchases.append(purchase_info)
                self.processed_items.add(item_id)
                
                logger.info(f"🆕 Новая покупка: {purchase_info['username']} за {purchase_info['price']} ₽")
            else:
                logger.debug(f"⏭️ [LZT] Токен не найден для item {item_id}: {purchase.get('title', 'Unknown')}")
        
        logger.debug(f"📊 [LZT] Обработано: всего={len(purchases)}, новых={len(new_purchases)}, обработанных ранее={len(self.processed_items)}")
        
        return new_purchases
    
    def _extract_discord_token(self, purchase_data: Dict) -> Optional[str]:
        """
        Извлекает Discord токен из данных покупки
        
        Args:
            purchase_data: Полные данные покупки из API
            
        Returns:
            Discord токен или None
        """
        # Токен хранится в поле 'login'!
        login = purchase_data.get('login', '')
        if login and len(login) > 50:
            logger.debug(f"✅ [LZT] Токен найден в поле 'login' (длина: {len(login)})")
            return login.strip()
        
        # Дополнительные проверки на случай других форматов
        possible_fields = [
            'content',
            'description',
            'token',
            'discord_token',
        ]
        
        for field in possible_fields:
            value = purchase_data.get(field, '')
            if value and isinstance(value, str) and len(value) > 50:
                logger.debug(f"✅ [LZT] Токен найден в поле '{field}' (длина: {len(value)})")
                return value.strip()
        
        # Проверяем вложенный объект loginData
        login_data = purchase_data.get('loginData', {})
        if login_data and isinstance(login_data, dict):
            login_value = login_data.get('login', '')
            if login_value and len(login_value) > 50:
                logger.debug(f"✅ [LZT] Токен найден в loginData['login'] (длина: {len(login_value)})")
                return login_value.strip()
        
        # Если ничего не нашли
        logger.debug(f"⚠️ [LZT] Токен не найден. Доступные поля: {list(purchase_data.keys())[:10]}...")
        return None
    
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