import logging
import time
import threading
from queue import Queue
from typing import Dict, List
from datetime import datetime

from modules.lzt_monitor import LZTMonitor
from modules.validator import TokenValidator
from modules.cleaner import process_token
from modules.database import Database
from modules.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)


class TokenPipeline:
    """
    Автоматический конвейер обработки токенов
    
    Workflow:
    1. LZT Monitor -> Мониторинг новых покупок
    2. Validator #1 -> Первая проверка
    3. Cleaner -> Очистка токенов
    4. Validator #2 -> Финальная проверка
    5. Database -> Сохранение
    6. Telegram -> Отправка пакетов
    """
    
    def __init__(self, config: Dict):
        """
        Инициализация Pipeline
        
        Args:
            config: Конфигурация системы
        """
        self.config = config
        self.running = False
        
        # Очереди для передачи между этапами
        self.new_tokens_queue = Queue()      # Новые токены из LZT
        self.validated_queue = Queue()       # Прошедшие первую валидацию
        self.cleaned_queue = Queue()         # Очищенные токены
        self.ready_queue = Queue()           # Готовые к отправке
        
        # Инициализация модулей
        self._init_modules()
        
        # Потоки для каждого этапа
        self.threads = []
        
        logger.info("🚀 Pipeline инициализирован")
    
    def _init_modules(self):
        """Инициализирует все модули"""
        # LZT Monitor
        self.lzt_monitor = LZTMonitor(
            api_token=self.config['lzt']['api_token'],
            check_interval=self.config['lzt']['check_interval'],
            min_balance_alert=self.config['lzt']['min_balance_alert']
        )
        
        # Validator
        self.validator = TokenValidator(
            timeout=self.config['validator']['timeout'],
            max_retries=self.config['validator']['max_retries']
        )
        
        # Database
        self.db = Database(self.config['database']['path'])
        
        # Telegram Bot
        self.telegram = TelegramBot(
            bot_token=self.config['telegram']['bot_token'],
            chat_id=self.config['telegram']['chat_id']
        )
        
        logger.info("✅ Все модули инициализированы")
    
    # ==================== ЭТАП 1: МОНИТОРИНГ LZT ====================
    
    def _lzt_monitoring_worker(self):
        """Поток мониторинга новых покупок на LZT"""
        logger.info("🔍 [LZT] Запуск мониторинга...")
        
        while self.running:
            try:
                # Проверяем новые покупки
                new_purchases = self.lzt_monitor.get_new_purchases()
                
                for purchase in new_purchases:
                    # Проверяем есть ли токен уже в БД
                    existing = self.db.get_token_info(purchase['token'])
                    
                    if existing:
                        logger.debug(f"⏭️ [LZT] Токен {purchase['item_id']} уже в БД, пропускаем")
                        continue
                    
                    # Добавляем в базу данных
                    token_id = self.db.add_token(
                        token=purchase['token'],
                        lzt_item_id=purchase['item_id'],
                        price=purchase['price']
                    )
                    
                    if token_id:
                        logger.info(f"➕ [LZT] Новая покупка: {purchase['username']} за {purchase['price']} ₽")
                        
                        # Обновляем статистику
                        self.db.update_statistics(
                            tokens_bought=1,
                            money_spent=purchase['price']
                        )
                        
                        # ВАЖНО: Отправляем в очередь валидации
                        self.new_tokens_queue.put(purchase)
                        logger.info(f"📥 [LZT] Токен добавлен в очередь валидации. Размер очереди: {self.new_tokens_queue.qsize()}")
                        
                        # Уведомление в Telegram
                        self.telegram.send_new_purchase(
                            item_id=purchase['item_id'],
                            price=purchase['price'],
                            username=purchase['username']
                        )
                
                # Проверяем баланс
                if self.lzt_monitor.check_balance_alert():
                    balance = self.lzt_monitor.get_balance()
                    if balance:
                        self.telegram.send_balance_alert(
                            current_balance=balance,
                            min_balance=self.config['lzt']['min_balance_alert']
                        )
                
                # Ждем до следующей проверки
                time.sleep(self.config['lzt']['check_interval'])
                
            except Exception as e:
                logger.error(f"❌ [LZT] Ошибка: {e}")
                self.telegram.send_error("LZT Monitor", str(e))
                time.sleep(60)
    
    # ==================== ЭТАП 2: ПЕРВАЯ ВАЛИДАЦИЯ ====================
    
    def _validation_worker(self):
        """Поток первой валидации токенов"""
        logger.info("✅ [Validator #1] Запуск валидации...")
        
        while self.running:
            try:
                # Получаем токен из очереди
                purchase = self.new_tokens_queue.get(timeout=1)
                
                logger.info(f"🔍 [Validator #1] Проверка токена {purchase.get('username', 'Unknown')}...")
                
                # Валидируем токен с обработкой исключений
                try:
                    is_valid, username = self.validator.validate_token(purchase['token'])
                except Exception as e:
                    logger.error(f"❌ [Validator #1] Ошибка валидации: {e}")
                    # Возвращаем токен обратно в очередь для повторной попытки
                    self.new_tokens_queue.put(purchase)
                    self.new_tokens_queue.task_done()
                    time.sleep(5)
                    continue
                
                if is_valid:
                    logger.info(f"✅ [Validator #1] Токен валиден: {username}")
                    
                    # Обновляем в БД
                    self.db.update_token_status(
                        token=purchase['token'],
                        status='validated',
                        username=username
                    )
                    
                    # Обновляем статистику
                    self.db.update_statistics(tokens_valid=1)
                    
                    # Отправляем на очистку
                    purchase['username'] = username
                    self.validated_queue.put(purchase)
                    
                else:
                    logger.warning(f"❌ [Validator #1] Токен невалиден")
                    
                    # Обновляем в БД
                    self.db.update_token_status(
                        token=purchase['token'],
                        status='invalid',
                        error='Failed first validation'
                    )
                
                self.new_tokens_queue.task_done()
                
            except:
                # Queue пустая - ждем
                time.sleep(1)
                continue
    
    # ==================== ЭТАП 3: ОЧИСТКА ====================
    
    def _cleaning_worker(self):
        """Поток очистки токенов"""
        logger.info("🧹 [Cleaner] Запуск очистки...")
        
        # Импортируем cleaner модуль
        from modules.cleaner import ProxyManager, ProgressTracker, DiscordAPI, process_token
        
        # Создаем ProxyManager если включены прокси
        proxy_manager = None
        if self.config.get('proxy', {}).get('enabled'):
            try:
                proxy_manager = ProxyManager()
                logger.info("🔐 [Cleaner] Прокси включены")
            except Exception as e:
                logger.warning(f"⚠️ [Cleaner] Прокси недоступны: {e}")
        
        while self.running:
            try:
                # Получаем токен из очереди
                purchase = self.validated_queue.get(timeout=1)
                
                logger.info(f"🧹 [Cleaner] Очистка токена: {purchase['username']}")
                
                # Обновляем статус
                self.db.update_token_status(
                    token=purchase['token'],
                    status='cleaning'
                )
                
                # Создаем ProgressTracker который обновляет БД
                class DatabaseProgressTracker:
                    def __init__(self, db, token):
                        self.db = db
                        self.token = token
                        self.max_workers = 1
                    
                    def add_token(self, token, username):
                        pass
                    
                    def update_status(self, token, status, **kwargs):
                        # Обновляем прогресс в БД
                        self.db.update_token_status(
                            self.token,
                            'cleaning',
                            cleaning_progress=status
                        )
                        logger.debug(f"[Cleaner Progress] {status}")
                    
                    def mark_completed(self, token):
                        pass
                
                progress_tracker = DatabaseProgressTracker(self.db, purchase['token'])
                
                # Создаем API
                if proxy_manager:
                    api = DiscordAPI(proxy_manager, progress_tracker)
                else:
                    # API без прокси
                    from modules.cleaner import RateLimiter
                    import requests
                    
                    class SimpleAPI:
                        def __init__(self):
                            self.rate_limiter = RateLimiter()
                        
                        def safe_request(self, method, url, headers, max_retries=3):
                            for attempt in range(max_retries):
                                self.rate_limiter.wait()
                                try:
                                    response = requests.request(method, url, headers=headers, timeout=10)
                                    self.rate_limiter.update_from_headers(response.headers)
                                    return response
                                except:
                                    if attempt == max_retries - 1:
                                        return None
                                    continue
                            return None
                    
                    api = SimpleAPI()
                
                # Запускаем очистку через process_token
                try:
                    process_token(api, purchase['token'], progress_tracker)
                    
                    logger.info(f"✅ [Cleaner] Токен очищен: {purchase['username']}")
                    
                    # Обновляем статус
                    self.db.update_token_status(
                        token=purchase['token'],
                        status='cleaned'
                    )
                    
                    # Обновляем статистику
                    self.db.update_statistics(tokens_cleaned=1)
                    
                    # Отправляем на финальную валидацию
                    self.cleaned_queue.put(purchase)
                    
                except Exception as clean_error:
                    logger.error(f"❌ [Cleaner] Ошибка очистки: {clean_error}")
                    
                    # Все равно отправляем на финальную валидацию
                    # (валидатор проверит работоспособность)
                    self.db.update_token_status(
                        token=purchase['token'],
                        status='cleaned',
                        error=f'Cleaning error: {str(clean_error)}'
                    )
                    self.cleaned_queue.put(purchase)
                
                self.validated_queue.task_done()
                
            except:
                # Queue пустая - ждем
                time.sleep(1)
                continue
    
    # ==================== ЭТАП 4: ФИНАЛЬНАЯ ВАЛИДАЦИЯ ====================
    
    def _final_validation_worker(self):
        """Поток финальной валидации после очистки"""
        logger.info("✅ [Validator #2] Запуск финальной валидации...")
        
        while self.running:
            try:
                # Получаем токен из очереди
                purchase = self.cleaned_queue.get(timeout=1)
                
                logger.info(f"🔍 [Validator #2] Финальная проверка {purchase.get('username', 'Unknown')}...")
                
                # Валидируем токен еще раз с обработкой исключений
                try:
                    is_valid, username = self.validator.validate_token(purchase['token'])
                except Exception as e:
                    logger.error(f"❌ [Validator #2] Ошибка валидации: {e}")
                    # Возвращаем токен обратно в очередь для повторной попытки
                    self.cleaned_queue.put(purchase)
                    self.cleaned_queue.task_done()
                    time.sleep(5)
                    continue
                
                if is_valid:
                    logger.info(f"✅ [Validator #2] Токен готов: {username}")
                    
                    # Обновляем в БД
                    self.db.update_token_status(
                        token=purchase['token'],
                        status='ready',
                        username=username
                    )
                    
                    # Отправляем в очередь готовых
                    self.ready_queue.put(purchase)
                    
                else:
                    logger.warning(f"❌ [Validator #2] Токен стал невалидным после очистки")
                    
                    # Обновляем в БД
                    self.db.update_token_status(
                        token=purchase['token'],
                        status='invalid',
                        error='Failed final validation'
                    )
                
                self.cleaned_queue.task_done()
                
            except:
                # Queue пустая - ждем
                time.sleep(1)
                continue
    
    # ==================== ЭТАП 5: ОТПРАВКА В TELEGRAM ====================
    
    def _telegram_sender_worker(self):
        """Поток отправки токенов в Telegram"""
        logger.info("📤 [Telegram] Запуск отправки...")
        
        while self.running:
            try:
                # Проверяем сколько готовых токенов в БД
                ready_tokens = self.db.get_ready_tokens(
                    limit=self.config['telegram']['max_tokens']
                )
                
                # Проверяем достаточно ли токенов для отправки
                if len(ready_tokens) >= self.config['telegram']['min_tokens']:
                    logger.info(f"📦 [Telegram] Отправка пакета из {len(ready_tokens)} токенов...")
                    
                    # Отправляем пакет
                    success = self.telegram.send_tokens_batch(
                        tokens_data=ready_tokens,
                        min_tokens=self.config['telegram']['min_tokens'],
                        max_tokens=self.config['telegram']['max_tokens']
                    )
                    
                    if success:
                        # Помечаем токены как отправленные
                        for token_data in ready_tokens[:self.config['telegram']['max_tokens']]:
                            self.db.update_token_status(
                                token=token_data['token'],
                                status='sent'
                            )
                        
                        # Обновляем статистику
                        sent_count = min(len(ready_tokens), self.config['telegram']['max_tokens'])
                        self.db.update_statistics(tokens_sent=sent_count)
                        
                        logger.info(f"✅ [Telegram] Пакет отправлен: {sent_count} токенов")
                else:
                    logger.debug(f"⏳ [Telegram] Ожидание токенов: {len(ready_tokens)}/{self.config['telegram']['min_tokens']}")
                
                # Проверяем каждые 60 секунд
                time.sleep(60)
                
            except Exception as e:
                logger.error(f"❌ [Telegram] Ошибка: {e}")
                self.telegram.send_error("Telegram Sender", str(e))
                time.sleep(60)
    
    # ==================== СТАТИСТИКА ====================
    
    def _statistics_worker(self):
        """Поток отправки статистики"""
        logger.info("📊 [Stats] Запуск отправки статистики...")
        
        while self.running:
            try:
                # Отправляем статистику каждые 6 часов
                time.sleep(6 * 60 * 60)
                
                # Получаем статистику
                stats = self.db.get_today_statistics()
                
                # Отправляем в Telegram
                self.telegram.send_statistics(stats)
                
                logger.info("📊 [Stats] Статистика отправлена")
                
            except Exception as e:
                logger.error(f"❌ [Stats] Ошибка: {e}")
    
    # ==================== УПРАВЛЕНИЕ PIPELINE ====================
    
    def start(self):
        """Запускает весь pipeline"""
        if self.running:
            logger.warning("⚠️ Pipeline уже запущен")
            return
        
        self.running = True
        
        logger.info("🚀 Запуск Pipeline...")
        
        # Отправляем уведомление
        self.telegram.send_notification(
            title="Pipeline запущен",
            message="Автоматическая обработка токенов начата",
            level="SUCCESS"
        )
        
        # Создаем и запускаем потоки
        # 1 поток для LZT Monitor
        thread = threading.Thread(target=self._lzt_monitoring_worker, name="LZT Monitor", daemon=True)
        thread.start()
        self.threads.append(thread)
        logger.info(f"▶️ Поток запущен: LZT Monitor")
        
        # Несколько потоков для Validator #1
        validator_threads = self.config.get('validator', {}).get('max_workers', 5)
        for i in range(validator_threads):
            thread = threading.Thread(target=self._validation_worker, name=f"Validator #1-{i+1}", daemon=True)
            thread.start()
            self.threads.append(thread)
        logger.info(f"▶️ Потоков запущено: Validator #1 x{validator_threads}")
        
        # Несколько потоков для Cleaner
        cleaner_threads = self.config.get('cleaner', {}).get('max_workers', 5)
        for i in range(cleaner_threads):
            thread = threading.Thread(target=self._cleaning_worker, name=f"Cleaner-{i+1}", daemon=True)
            thread.start()
            self.threads.append(thread)
        logger.info(f"▶️ Потоков запущено: Cleaner x{cleaner_threads}")
        
        # Несколько потоков для Validator #2
        final_validator_threads = self.config.get('validator', {}).get('max_workers', 5)
        for i in range(final_validator_threads):
            thread = threading.Thread(target=self._final_validation_worker, name=f"Validator #2-{i+1}", daemon=True)
            thread.start()
            self.threads.append(thread)
        logger.info(f"▶️ Потоков запущено: Validator #2 x{final_validator_threads}")
        
        # 1 поток для Telegram Sender
        thread = threading.Thread(target=self._telegram_sender_worker, name="Telegram Sender", daemon=True)
        thread.start()
        self.threads.append(thread)
        logger.info(f"▶️ Поток запущен: Telegram Sender")
        
        # 1 поток для Statistics
        thread = threading.Thread(target=self._statistics_worker, name="Statistics", daemon=True)
        thread.start()
        self.threads.append(thread)
        logger.info(f"▶️ Поток запущен: Statistics")
        
        logger.info(f"✅ Pipeline запущен успешно! Всего потоков: {len(self.threads)}")
    
    def stop(self):
        """Останавливает pipeline"""
        if not self.running:
            logger.warning("⚠️ Pipeline не запущен")
            return
        
        logger.info("⏹️ Остановка Pipeline...")
        
        self.running = False
        
        # Ждем завершения потоков
        for thread in self.threads:
            thread.join(timeout=5)
        
        # Закрываем validator
        self.validator.close()
        
        # Отправляем уведомление
        self.telegram.send_notification(
            title="Pipeline остановлен",
            message="Автоматическая обработка токенов остановлена",
            level="WARNING"
        )
        
        logger.info("✅ Pipeline остановлен")
    
    def get_status(self) -> Dict:
        """Возвращает статус pipeline"""
        return {
            'running': self.running,
            'queues': {
                'new_tokens': self.new_tokens_queue.qsize(),
                'validated': self.validated_queue.qsize(),
                'cleaned': self.cleaned_queue.qsize(),
                'ready': self.ready_queue.qsize()
            },
            'threads': {
                thread.name: thread.is_alive()
                for thread in self.threads
            }
        }


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Конфигурация
    config = {
        'lzt': {
            'api_token': 'your_lzt_token',
            'check_interval': 60,
            'min_balance_alert': 100
        },
        'validator': {
            'timeout': 10,
            'max_retries': 3
        },
        'database': {
            'path': 'data/tokens.db'
        },
        'telegram': {
            'bot_token': 'your_bot_token',
            'chat_id': 'your_chat_id',
            'min_tokens': 30,
            'max_tokens': 50
        },
        'proxy': {
            'enabled': False
        }
    }
    
    # Создаем pipeline
    pipeline = TokenPipeline(config)
    
    # Запускаем
    pipeline.start()
    
    try:
        # Держим программу запущенной
        while True:
            time.sleep(10)
            status = pipeline.get_status()
            print(f"\nСтатус очередей: {status['queues']}")
    except KeyboardInterrupt:
        print("\n⏹️ Остановка...")
        pipeline.stop()