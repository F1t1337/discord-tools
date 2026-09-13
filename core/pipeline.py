import logging
import os
import time
import threading
from queue import Queue
from typing import Dict

from modules.lzt_monitor import LZTMonitor
from modules.purchase_task import PurchaseTaskManager
from modules.validator import TokenValidator, ValidationUnavailable
from modules.cleaner import process_token
from modules.discord_transport import DiscordTransport
from modules.database import Database
from modules.configuration import save_config_value
from modules.telegram_bot import TelegramBot
from modules.cloudflare_helper import CloudflareHelper

logger = logging.getLogger(__name__)


class TokenPipeline:
    """
    Автоматический конвейер обработки токенов
    
    Workflow:
    1. LZT Monitor -> Мониторинг новых покупок
    2. Validator #1 -> Первая проверка
    3. Cleaner -> Очистка токенов
    4. Validator #2 -> Финальная проверка
    5. Database -> Сохранение как 'ready'
    6. Telegram -> Уведомление о готовности
    """
    
    def __init__(self, config: Dict, config_path: str = "config.json"):
        """
        Инициализация Pipeline

        Args:
            config: Конфигурация системы
            config_path: Путь к файлу config.json (для сохранения настроек)
        """
        self.config = config
        self.config_path = config_path
        self.running = False
        self._intake_lock = threading.Lock()
        self._export_lock = threading.Lock()

        # Закрывать ли чаты при очистке (переключается из Telegram)
        self.close_channels = bool(config.get('cleaner', {}).get('close_channels', True))

        # Очереди для передачи между этапами
        self.new_tokens_queue = Queue()      # Новые токены из LZT
        self.validated_queue = Queue()       # Прошедшие первую валидацию
        self.cleaned_queue = Queue()         # Очищенные токены
        self.ready_queue = Queue()           # Готовые к отправке
        
        # Флаг для отслеживания уведомлений о готовых токенах
        self.ready_tokens_notification_sent = False
        
        # Инициализация модулей
        self._init_modules()
        
        # Регистрируем команды бота
        self._register_bot_commands()
        
        # Потоки для каждого этапа
        self.threads = []

        # Управление потоками очистки на лету
        self._cleaner_lock = threading.Lock()
        self._cleaner_workers = []  # список dict {thread, stop_event}
        self.cleaner_target = max(1, int(config.get('cleaner', {}).get('max_workers', 5) or 1))

        logger.info("🚀 Pipeline инициализирован")
    
    def _init_modules(self):
        """Инициализирует все модули"""
        # Database (нужна для LZT Monitor)
        self.db = Database(self.config['database']['path'])
        
        # LZT Market клиент: баланс, поиск и покупка аккаунтов (без роли монитора)
        self.lzt_monitor = LZTMonitor(
            api_token=self.config['lzt']['api_token'],
            check_interval=self.config['lzt'].get('check_interval', 60),
            min_balance_alert=self.config['lzt'].get('min_balance_alert', 100),
            database=self.db  # Передаем БД для проверки дубликатов
        )

        from modules.cleaner import ProxyManager
        self.proxy_manager = ProxyManager(db=self.db) if self.config.get('proxy', {}).get('enabled') else None
        self.discord_transport = DiscordTransport(self.proxy_manager)

        # Validator
        self.validator = TokenValidator(
            timeout=self.config['validator']['timeout'],
            max_retries=self.config['validator']['max_retries'],
            transport=self.discord_transport
        )
        
        # Telegram Bot
        self.telegram = TelegramBot(
            bot_token=self.config['telegram']['bot_token'],
            chat_id=self.config['telegram']['chat_id'],
            allowed_user_ids=self.config['telegram'].get('allowed_user_ids'),
        )
        
        # Cloudflare Tunnel Helper
        self.cloudflare = CloudflareHelper()

        # Менеджер задачи покупки аккаунтов с LZT Market
        self.purchase = PurchaseTaskManager(
            lzt_monitor=self.lzt_monitor,
            db=self.db,
            intake_cb=self.enqueue_purchased,
            set_cleaner_workers_cb=self.set_cleaner_workers,
        )

        logger.info("✅ Все модули инициализированы")
    
    def _register_bot_commands(self):
        """Регистрирует обработчики команд Telegram бота"""
        self.telegram.register_command_handler('stats', self._handle_stats_command)
        self.telegram.register_command_handler('balance', self._handle_balance_command)
        self.telegram.register_command_handler('export_tokens', self._handle_export_tokens_command)
        self.telegram.register_command_handler('status', self._handle_status_command)
        self.telegram.register_command_handler('upload_tokens', self._handle_upload_tokens_command)
        self.telegram.register_command_handler('settings', self._handle_settings_command)
        self.telegram.register_command_handler('toggle_close_channels', self._handle_toggle_close_channels_command)
        self.telegram.register_token_upload_handler(self._handle_tokens_uploaded)

        logger.info("✅ Команды Telegram бота зарегистрированы")

    # ==================== РУЧНАЯ ЗАГРУЗКА ТОКЕНОВ ====================

    def _handle_upload_tokens_command(self, chat_id: str = None, message_id: int = None):
        """Переводит чат в режим ожидания токенов (txt-файл или список текстом)"""
        try:
            self.telegram.send_upload_prompt(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.error(f"❌ Ошибка при запросе загрузки токенов: {e}")
            self.telegram.send_error("Upload Tokens", str(e))

    def _handle_tokens_uploaded(self, chat_id: str, raw_text: str):
        """
        Принимает сырой текст с токенами (из .txt файла или сообщения),
        парсит, добавляет в БД и в очередь валидации.
        """
        tokens = []
        for line in raw_text.splitlines():
            t = line.strip()
            if not t or t.startswith('#'):
                continue
            # Формат "login:pass:token" — берём последний сегмент
            if ':' in t and not t.startswith('mfa.'):
                parts = [p.strip() for p in t.split(':') if p.strip()]
                if parts:
                    t = parts[-1]
            tokens.append(t)

        if not tokens:
            self.telegram.send_message(
                "⚠️ Не удалось распознать ни одного токена.",
                chat_id=chat_id
            )
            return

        added = 0
        errors = 0

        for token in tokens:
            try:
                # Пытаемся добавить в БД, но при дубликате всё равно
                # кладём токен в очередь валидации — уникальность не проверяем.
                try:
                    self.db.add_token(
                        token=token,
                        seller_username='manual_upload',
                        price=0
                    )
                except Exception as db_err:
                    logger.debug(f"add_token: {db_err}")

                self.new_tokens_queue.put({
                    'token': token,
                    'item_id': None,
                    'username': 'Manual',
                    'seller_username': 'manual_upload',
                    'price': 0
                })
                added += 1
            except Exception as e:
                logger.error(f"❌ Ошибка добавления токена: {e}")
                errors += 1

        logger.info(f"📥 Ручная загрузка от {chat_id}: всего {len(tokens)}, "
                    f"принято {added}, ошибок {errors}")

        self.telegram.send_upload_result(
            chat_id=chat_id,
            added=added,
            duplicates=0,
            errors=errors,
            total=len(tokens)
        )
    
    # ==================== ОБРАБОТЧИКИ КОМАНД ====================
    
    def _handle_stats_command(self, chat_id: str = None, message_id: int = None):
        """Обработчик команды статистики"""
        try:
            stats = self.db.get_today_statistics()
            self.telegram.send_statistics(stats, chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.error(f"❌ Ошибка получения статистики: {e}")
            self.telegram.send_error("Statistics", str(e))
    def _handle_sellers_stats_command(self, chat_id: str = None, message_id: int = None):
        """Обработчик команды статистики продавцов"""
        try:
            sellers = self.db.get_seller_statistics(limit=None)  # Получаем всех продавцов
            self.telegram.send_sellers_statistics(sellers, chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.error(f"❌ Ошибка получения статистики продавцов: {e}")
            self.telegram.send_error("Sellers Statistics", str(e))


    def _handle_export_tokens_command(self, chat_id: str = None, message_id: int = None):
        """
        Прогоняет готовые токены через валидатор:
        - валидные  → статус 'sent',   попадают в .txt файл
        - невалидные → статус 'invalid'
        """
        try:
            ready_tokens = self.db.get_ready_tokens(limit=1000)

            if not ready_tokens:
                self.telegram.send_notification(
                    title="⚠️ Нет готовых токенов",
                    message="Нет токенов для выгрузки.",
                    level="WARNING",
                    chat_id=chat_id,
                )
                return

            total = len(ready_tokens)
            # Уведомление о начале
            progress_text = (
                f"📤 <b>Выгрузка токенов</b>\n\n"
                f"Проверяю {total} токенов...\n"
                f"⏳ Пожалуйста, подождите."
            )
            msg_id = self.telegram.send_message(progress_text, chat_id=chat_id)

            valid_tokens = []
            invalid_count = 0

            for i, token_data in enumerate(ready_tokens, 1):
                token = token_data['token']
                try:
                    is_valid, username = self.validator.validate_token(token)
                except Exception as e:
                    logger.warning(f"⚠️ [Export] Ошибка валидации токена: {e}")
                    is_valid = False

                if is_valid:
                    valid_tokens.append(token)
                    self.db.update_token_status(token=token, status='sent')
                else:
                    invalid_count += 1
                    self.db.update_token_status(
                        token=token,
                        status='invalid',
                        error='Failed validation on export'
                    )

                # Обновляем прогресс каждые 10 токенов
                if msg_id and i % 10 == 0:
                    pct = int(i / total * 100)
                    try:
                        self.telegram.edit_message(
                            msg_id,
                            (
                                f"📤 <b>Выгрузка токенов</b>\n\n"
                                f"⏳ Проверено: {i}/{total} ({pct}%)\n"
                                f"✅ Валидных: {len(valid_tokens)}\n"
                                f"❌ Невалидных: {invalid_count}"
                            ),
                            chat_id=chat_id,
                        )
                    except Exception:
                        pass

                time.sleep(0.3)

            self.ready_tokens_notification_sent = False
            logger.info(
                f"📤 [Export] Завершено: валидных={len(valid_tokens)}, невалидных={invalid_count}"
            )

            # Итоговое сообщение
            summary = (
                f"📤 <b>Выгрузка завершена</b>\n\n"
                f"🔍 Проверено: {total}\n"
                f"✅ Валидных (→ sent): {len(valid_tokens)}\n"
                f"❌ Невалидных (→ invalid): {invalid_count}"
            )
            if msg_id:
                try:
                    self.telegram.edit_message(msg_id, summary, chat_id=chat_id)
                except Exception:
                    self.telegram.send_message(summary, chat_id=chat_id)
            else:
                self.telegram.send_message(summary, chat_id=chat_id)

            # Отправляем файл только если есть валидные токены
            if valid_tokens:
                self.telegram.send_tokens_file(valid_tokens, chat_id=chat_id)
            else:
                self.telegram.send_notification(
                    title="⚠️ Нет валидных токенов",
                    message="Все токены оказались невалидными, файл не отправлен.",
                    level="WARNING",
                    chat_id=chat_id,
                )

        except Exception as e:
            logger.error(f"❌ Ошибка выгрузки токенов: {e}")
            self.telegram.send_error("Export Tokens", str(e))

    def _handle_balance_command(self, chat_id: str = None, message_id: int = None):
        """Обработчик команды баланса"""
        try:
            balance = self.lzt_monitor.get_balance()
            if balance is not None:
                self.telegram.send_balance_info(
                    current_balance=balance,
                    min_balance=self.config['lzt']['min_balance_alert'],
                    chat_id=chat_id,
                    message_id=message_id
                )
            else:
                self.telegram.send_error("Balance", "Не удалось получить баланс LZT")
        except Exception as e:
            logger.error(f"❌ Ошибка получения баланса: {e}")
            self.telegram.send_error("Balance", str(e))


    def _handle_check_valid_command(self, chat_id: str = None, message_id: int = None):
        """Проверка готовых токенов через обычный validator, удаляет невалидные"""
        try:
            logger.info("✅ [Command] Начало проверки валидности готовых токенов")
            
            ready_tokens = self.db.get_ready_tokens(limit=1000)
            
            if not ready_tokens:
                self.telegram.send_notification(
                    title="⚠️ Нет токенов для проверки",
                    message="В данный момент нет готовых токенов для проверки.",
                    level="WARNING",
                    chat_id=chat_id
                )
                return
            
            total = len(ready_tokens)
            logger.info(f"✅ [Command] Найдено {total} готовых токенов для проверки")
            
            start_text = (
                f"✅ <b>Проверка валидности токенов</b>\n\n"
                f"Проверяю {total} готовых токенов...\n\n"
                f"⏳ Пожалуйста, подождите."
            )
            
            if message_id:
                self.telegram.edit_message(message_id, start_text, chat_id=chat_id)
            else:
                msg_result = self.telegram.send_message(start_text, chat_id=chat_id)
                if msg_result:
                    message_id = msg_result
            
            valid_count = 0
            invalid_count = 0
            
            for i, token_data in enumerate(ready_tokens, 1):
                token = token_data['token']
                
                is_valid, username = self.validator.validate_token(token)
                
                if is_valid:
                    valid_count += 1
                    logger.info(f"✅ [{i}/{total}] Токен валиден: {username}")
                else:
                    invalid_count += 1
                    logger.warning(f"❌ [{i}/{total}] Токен невалиден, удаляю из БД")
                    
                    self.db.update_token_status(
                        token=token,
                        status='invalid',
                        error='Failed validation check'
                    )
                    
                    # Обновляем статистику продавца
                    token_info = self.db.get_token_info(token)
                    if token_info and token_info.get('seller_username'):
                        self.db.update_seller_stats_validation(token_info['seller_username'], is_valid=False)
                
                if message_id and i % 5 == 0:
                    progress_text = (
                        f"✅ <b>Проверка валидности токенов</b>\n\n"
                        f"⏳ Проверено: {i}/{total}\n"
                        f"📊 Прогресс: {int(i/total*100)}%\n\n"
                        f"✅ Валидных: {valid_count}\n"
                        f"❌ Невалидных: {invalid_count}\n\n"
                        f"Пожалуйста, подождите..."
                    )
                    try:
                        self.telegram.edit_message(message_id, progress_text, chat_id=chat_id)
                    except:
                        pass
                
                time.sleep(0.5)
            
            logger.info(f"✅ [Command] Проверка завершена: валидных={valid_count}, невалидных={invalid_count}")
            
            keyboard = {"inline_keyboard": [[{"text": "« Назад", "callback_data": "menu"}]]}
            
            if invalid_count == 0:
                emoji = "✅"
                result_text = "Все токены валидны!"
            elif valid_count == 0:
                emoji = "❌"
                result_text = "Все токены невалидны!"
            else:
                emoji = "⚠️"
                result_text = "Проверка завершена"
            
            final_text = (
                f"{emoji} <b>{result_text}</b>\n\n"
                f"📊 <b>Результаты проверки:</b>\n"
                f"🔍 Проверено: {total}\n"
                f"✅ Валидных: {valid_count}\n"
                f"❌ Невалидных: {invalid_count}\n\n"
            )
            
            if invalid_count > 0:
                final_text += f"🗑️ Невалидные токены удалены из базы\n\n"
            
            if valid_count > 0:
                final_text += f"📦 Валидных токенов готово к отправке: <b>{valid_count}</b>"
            else:
                final_text += f"⚠️ Нет валидных токенов для отправки"
            
            if message_id:
                self.telegram.edit_message(message_id, final_text, reply_markup=keyboard, chat_id=chat_id)
            else:
                self.telegram.send_message(final_text, reply_markup=keyboard, chat_id=chat_id)
                
        except Exception as e:
            logger.error(f"❌ [Command] Ошибка проверки валидности: {e}")
            self.telegram.send_error("Check Valid", str(e))
    
    def _handle_run_checker_command(self, chat_id: str = None, message_id: int = None):
        """Запуск упрощенного чекера (валид + проспам), НЕ удаляет токены"""
        try:
            logger.info("🔍 [Command] Запуск упрощенного чекера")
            
            ready_tokens = self.db.get_ready_tokens(limit=1000)
            
            if not ready_tokens:
                self.telegram.send_notification(
                    title="⚠️ Нет токенов для проверки",
                    message="В данный момент нет готовых токенов для запуска чекера.",
                    level="WARNING",
                    chat_id=chat_id
                )
                return
            
            total = len(ready_tokens)
            tokens_list = [t['token'] for t in ready_tokens]
            
            logger.info(f"🔍 [Command] Запуск чекера для {total} токенов")
            
            start_text = (
                f"🔍 <b>Запуск чекера токенов</b>\n\n"
                f"Анализирую {total} готовых токенов...\n\n"
                f"⏳ Проверяю:\n"
                f"• Валидность\n"
                f"• Проспам (анализ сообщений)\n\n"
                f"Это может занять некоторое время."
            )
            
            if message_id:
                self.telegram.edit_message(message_id, start_text, chat_id=chat_id)
            else:
                msg_result = self.telegram.send_message(start_text, chat_id=chat_id)
                if msg_result:
                    message_id = msg_result
            
            from modules.advanced_checker_simplified import SimpleDiscordChecker
            
            checker = SimpleDiscordChecker(threads=10, transport=self.discord_transport)
            
            last_update = [time.time()]
            
            def progress_callback(current, total_count, result):
                if message_id and (time.time() - last_update[0] >= 3):
                    progress_text = (
                        f"🔍 <b>Запуск чекера токенов</b>\n\n"
                        f"⏳ Проверено: {current}/{total_count}\n"
                        f"📊 Прогресс: {int(current/total_count*100)}%\n\n"
                        f"Анализирую токены...\n"
                        f"Пожалуйста, подождите."
                    )
                    try:
                        self.telegram.edit_message(message_id, progress_text, chat_id=chat_id)
                        last_update[0] = time.time()
                    except:
                        pass
            
            stats = checker.check_tokens(tokens_list, progress_callback=progress_callback)
            
            logger.info(f"✅ [Command] Чекер завершен за {stats['elapsed_time']:.1f}с")
            
            keyboard = {"inline_keyboard": [[{"text": "« Назад", "callback_data": "menu"}]]}
            
            report_text = (
                f"📊 <b>Результаты чекера</b>\n\n"
                f"📦 <b>Всего проверено:</b> {stats['total']}\n"
                f"⏱️ <b>Время:</b> {stats['elapsed_time']:.1f}с\n\n"
                f"<b>Валидность:</b>\n"
                f"✅ Валидных: {stats['valid']}\n"
                f"❌ Невалидных: {stats['invalid']}\n\n"
                f"<b>Проспам:</b>\n"
                f"✉️ Проспам: {stats['spam']}\n"
                f"🏆 Непроспам: {stats['non_spam']}\n\n"
            )
            
            if stats['valid'] > 0:
                spam_percent = (stats['spam'] / stats['valid']) * 100 if stats['valid'] > 0 else 0
                non_spam_percent = (stats['non_spam'] / stats['valid']) * 100 if stats['valid'] > 0 else 0
                
                report_text += (
                    f"<b>Статистика валидных:</b>\n"
                    f"📈 Проспам: {spam_percent:.1f}%\n"
                    f"📉 Непроспам: {non_spam_percent:.1f}%\n\n"
                )
            
            report_text += (
                f"💡 <b>Примечание:</b>\n"
                f"Токены НЕ удалены из базы.\n"
                f"Используйте 'Проверить валид' для удаления невалидных."
            )
            
            if message_id:
                self.telegram.edit_message(message_id, report_text, reply_markup=keyboard, chat_id=chat_id)
            else:
                self.telegram.send_message(report_text, reply_markup=keyboard, chat_id=chat_id)
                
        except Exception as e:
            logger.error(f"❌ [Command] Ошибка запуска чекера: {e}")
            import traceback
            traceback.print_exc()
            self.telegram.send_error("Run Checker", str(e))

    def _handle_ready_tokens_command(self, chat_id: str = None, message_id: int = None):
        """Обработчик команды информации о готовых токенах"""
        try:
            ready_tokens = self.db.get_ready_tokens(limit=1000)
            count = len(ready_tokens)
            
            self.telegram.send_ready_tokens_info(count, chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.error(f"❌ Ошибка получения информации о токенах: {e}")
            self.telegram.send_error("Ready Tokens", str(e))
    
    def _handle_status_command(self, chat_id: str = None, message_id: int = None):
        """Обработчик команды статуса системы"""
        try:
            status = self.get_status()
            self.telegram.send_system_status(status, chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.error(f"❌ Ошибка получения статуса: {e}")
            self.telegram.send_error("Status", str(e))
    
    def _handle_settings_command(self, chat_id: str = None, message_id: int = None):
        """Показывает меню настроек."""
        try:
            self.telegram.send_settings_menu(
                close_channels=self.close_channels,
                chat_id=chat_id,
                message_id=message_id,
            )
        except Exception as e:
            logger.error(f"❌ Ошибка открытия настроек: {e}")
            self.telegram.send_error("Settings", str(e))

    def _handle_toggle_close_channels_command(self, chat_id: str = None, message_id: int = None):
        """Переключает закрытие чатов при очистке и сохраняет настройку."""
        try:
            new_value = not self.close_channels
            self._persist_config_value('cleaner', 'close_channels', new_value)
            self.close_channels = new_value

            state = "включено" if self.close_channels else "выключено"
            logger.info(f"⚙️ Закрытие чатов при очистке: {state}")

            self.telegram.send_settings_menu(
                close_channels=self.close_channels,
                chat_id=chat_id,
                message_id=message_id,
            )
        except Exception as e:
            logger.error(f"❌ Ошибка переключения закрытия чатов: {e}")
            self.telegram.send_error("Settings", str(e))

    def _persist_config_value(self, section: str, key: str, value):
        """
        Сохраняет одно значение в config.json, не трогая остальные поля
        и не раскрывая ${ENV} плейсхолдеры (читаем сырой файл, а не self.config).
        """
        save_config_value(self.config_path, self.config, section, key, value)

    # ==================== ИНТЕРФЕЙС ДЛЯ ВЕБ-ПАНЕЛИ ====================

    def set_close_channels(self, value: bool) -> bool:
        """Включает/выключает закрытие чатов при очистке и сохраняет настройку."""
        self._persist_config_value('cleaner', 'close_channels', bool(value))
        self.close_channels = bool(value)
        logger.info(f"⚙️ Закрытие чатов при очистке: {'включено' if self.close_channels else 'выключено'}")
        return self.close_channels

    def get_lzt_balance(self):
        """Возвращает текущий баланс LZT или None при ошибке."""
        try:
            return self.lzt_monitor.get_balance()
        except Exception as e:
            logger.error(f"❌ Ошибка получения баланса LZT: {e}")
            return None

    # ==================== ЗАДАЧА ПОКУПКИ АККАУНТОВ ====================

    def enqueue_purchased(self, token: str, item_id=None, price: float = 0,
                          seller_username: str = 'lzt_market'):
        """Ставит купленный аккаунт в конвейер обработки.

        Сохраняет запись в БД, обновляет статистику покупок и кладёт токен в очередь
        первой валидации. Работает только при запущенном конвейере.
        """
        with self._intake_lock:
            if not self.running:
                raise RuntimeError('Обработка остановлена')
            token_id = self.db.add_token(
                token=token, lzt_item_id=item_id,
                seller_username=seller_username, price=price)
            if token_id is None:
                return None  # дубликат — уже в БД
            self.db.update_statistics(tokens_bought=1, money_spent=price or 0)
            self.new_tokens_queue.put({
                'token': token, 'item_id': item_id,
                'username': 'LZT', 'seller_username': seller_username,
                'price': price,
            })
            logger.info(f"🛒 Куплен аккаунт (item {item_id}) за {price} ₽ — в очереди обработки")
            return token_id

    def estimate_purchase(self, pmax: float, chat_min: int) -> dict:
        """Оценка задачи: подходящих аккаунтов, стоимость, баланс, макс. к покупке."""
        return self.purchase.estimate(pmax, chat_min)

    def start_purchase(self, pmax: float, chat_min: int, count: int,
                       cleaner_workers: int) -> dict:
        """Запускает задачу покупки. Требует запущенный конвейер."""
        if not self.running:
            raise RuntimeError('Сначала запустите обработку')
        return self.purchase.start(pmax, chat_min, count, cleaner_workers)

    def stop_purchase(self) -> bool:
        """Мягко останавливает задачу покупки (уже купленное продолжит обработку)."""
        return self.purchase.stop()

    def purchase_status(self) -> dict:
        """Снимок состояния задачи покупки + стадии конвейера."""
        return self.purchase.snapshot()

    @staticmethod
    def _parse_manual_tokens(raw_text: str):
        """Разбирает вставленный текст в список токенов (поддержка login:pass:token)."""
        tokens = []
        for line in (raw_text or '').splitlines():
            item = line.strip()
            if not item or item.startswith('#'):
                continue
            # Формат "login:pass:token" — берём последний сегмент
            if ':' in item and not item.startswith('mfa.'):
                parts = [p.strip() for p in item.split(':') if p.strip()]
                if parts:
                    item = parts[-1]
            tokens.append(item)
        return tokens

    def add_manual_tokens(self, raw_text: str) -> dict:
        """Accept only persisted new records while workers are running."""
        tokens = self._parse_manual_tokens(raw_text)
        added = duplicates = errors = 0
        with self._intake_lock:
            if not self.running:
                raise RuntimeError('Обработка остановлена')
            for token in tokens:
                try:
                    record_id = self.db.add_token(
                        token=token, seller_username='manual_upload', price=0)
                except Exception as exc:
                    errors += 1
                    logger.error('Ошибка сохранения загружаемой записи: %s', type(exc).__name__)
                    continue
                if record_id is None:
                    duplicates += 1
                    continue
                self.new_tokens_queue.put({
                    'token': token, 'item_id': None, 'username': 'Manual',
                    'seller_username': 'manual_upload', 'price': 0,
                })
                added += 1
        return {'added': added, 'total': len(tokens), 'duplicates': duplicates, 'errors': errors}

    def export_ready_tokens(self, progress_cb=None, send_telegram=True) -> dict:
        """Save an atomic, durable export before attempting Telegram delivery."""
        if not self._export_lock.acquire(blocking=False):
            raise RuntimeError('Выгрузка уже выполняется')
        try:
            if not self.running:
                raise RuntimeError('Обработка остановлена')
            ready_tokens = self.db.get_ready_tokens(limit=1000)
            total = len(ready_tokens)
            valid_tokens, invalid_tokens = [], []
            deferred = 0
            for i, token_data in enumerate(ready_tokens, 1):
                token = token_data['token']
                try:
                    is_valid, _ = self.validator.validate_token(token, strict=True, stage='export')
                except ValidationUnavailable:
                    deferred += 1
                else:
                    (valid_tokens if is_valid else invalid_tokens).append(token)
                if progress_cb:
                    progress_cb(i, total, len(valid_tokens), len(invalid_tokens))
            result = self.db.commit_export(valid_tokens, invalid_tokens)
            result.update(total=total, deferred=deferred, delivery='not_needed')
            self.ready_tokens_notification_sent = False
            if result['export_id'] is not None and not send_telegram:
                result['delivery'] = 'not_requested'
                self.db.set_export_delivery(result['export_id'], 'not_requested')
            elif result['export_id'] is not None:
                delivery = 'failed'
                try:
                    if self.telegram.send_tokens_file(result['valid_tokens']):
                        delivery = 'sent'
                except Exception as exc:
                    logger.error('Ошибка доставки выгрузки: %s', type(exc).__name__)
                result['delivery'] = delivery
                # Even failure to record delivery cannot discard the saved file.
                try:
                    self.db.set_export_delivery(result['export_id'], delivery)
                except Exception as exc:
                    result['delivery'] = 'unknown'
                    logger.error('Ошибка сохранения статуса доставки: %s', type(exc).__name__)
            return result
        finally:
            self._export_lock.release()

    def _handle_dashboard_url_command(self, chat_id: str = None, message_id: int = None):
        """Обработчик команды получения Dashboard URL"""
        try:
            # Получаем Cloudflare URL
            tunnel_url = self.cloudflare.get_public_url()
            
            # Отправляем URL пользователю
            self.telegram.send_dashboard_url(tunnel_url=tunnel_url, chat_id=chat_id, message_id=message_id)
            
            if tunnel_url:
                logger.info(f"✅ Dashboard URL отправлен: {tunnel_url}")
            else:
                logger.info("⚠️ Cloudflare Tunnel не запущен, отправлен localhost URL")
                
        except Exception as e:
            logger.error(f"❌ Ошибка получения Dashboard URL: {e}")
            self.telegram.send_error("Dashboard URL", str(e))
    
    def reset_token_cleaning(self, token: str) -> bool:
        """
        Сбрасывает очистку токена - возвращает его в статус 'validated'
        
        Args:
            token: Discord токен
            
        Returns:
            True если успешно
        """
        try:
            # Получаем информацию о токене
            token_info = self.db.get_token_info(token)
            
            if not token_info:
                logger.error(f"❌ Токен не найден: {token[:20]}...")
                return False
            
            # Проверяем что токен в статусе cleaning
            if token_info['status'] != 'cleaning':
                logger.warning(f"⚠️ Токен не в статусе cleaning: {token_info['status']}")
                return False
            
            # Возвращаем в очередь на очистку
            logger.info(f"🔄 Сброс очистки токена: {token_info.get('username', 'Unknown')}")
            
            # Обновляем статус в БД
            self.db.update_token_status(
                token=token,
                status='validated',
                cleaning_progress=None
            )
            
            # Добавляем обратно в очередь на очистку
            purchase_data = {
                'token': token,
                'username': token_info.get('username', 'Unknown'),
                'item_id': token_info.get('lzt_item_id'),
                'price': token_info.get('price', 0)
            }
            
            self.validated_queue.put(purchase_data)
            
            logger.info(f"✅ Токен возвращен в очередь очистки")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка сброса очистки: {e}")
            return False
    
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
                    is_valid, username = self.validator.validate_token(purchase['token'], strict=True, stage='validator_initial')
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
    
    def _cleaning_worker(self, stop_event=None):
        """Поток очистки токенов.

        Args:
            stop_event: событие остановки конкретного потока (для регулировки
                        количества потоков очистки на лету).
        """
        logger.info("🧹 [Cleaner] Запуск очистки...")

        # Импортируем cleaner модуль
        from modules.cleaner import DiscordAPI, process_token

        # Общий менеджер прокси на весь pipeline (по одному прокси на токен)
        proxy_manager = self.proxy_manager

        while self.running and (stop_event is None or not stop_event.is_set()):
            try:
                # Получаем токен из очереди
                purchase = self.validated_queue.get(timeout=1)
                
                logger.info(f"🧹 [Cleaner] Начало очистки токена {purchase.get('username', 'Unknown')}...")
                
                # Обновляем статус в БД
                self.db.update_token_status(
                    token=purchase['token'],
                    status='cleaning',
                    cleaning_progress='Инициализация...'
                )
                
                # Создаем простой прогресс-трекер для БД
                class DBProgressTracker:
                    def __init__(self, db, token):
                        self.db = db
                        self.token = token
                    
                    def update_status(self, token, status, **kwargs):
                        # Обновляем прогресс в БД
                        self.db.update_token_status(
                            token=self.token,
                            status='cleaning',
                            cleaning_progress=status
                        )
                    
                    def add_token(self, token, username):
                        pass
                    
                    def mark_completed(self, token):
                        pass
                
                progress_tracker = DBProgressTracker(self.db, purchase['token'])

                # Выдаём этому токену выделенный прокси из пула
                proxy_info = proxy_manager.acquire(purchase['token']) if proxy_manager is not None else None
                if proxy_info is None:
                    self.db.update_token_status(token=purchase['token'], status='validated',
                                                cleaning_progress='Ожидание закреплённого прокси')
                    self.validated_queue.put(purchase)
                    self.validated_queue.task_done()
                    time.sleep(5)
                    continue

                # Создаем API клиент
                api = DiscordAPI(proxy_manager, progress_tracker, proxy_info=proxy_info, transport=self.discord_transport)

                # Запускаем очистку
                process_token(api, purchase['token'], progress_tracker,
                              close_channels=self.close_channels)
                if api.request_failed:
                    self.db.update_token_status(token=purchase['token'], status='validated',
                                                cleaning_progress='Ожидание после сетевой ошибки или rate limit')
                    self.validated_queue.put(purchase)
                    self.validated_queue.task_done()
                    time.sleep(5)
                    continue

                logger.info(f"✅ [Cleaner] Очистка завершена для {purchase.get('username', 'Unknown')}")
                
                # Обновляем статус в БД
                self.db.update_token_status(
                    token=purchase['token'],
                    status='cleaned'
                )
                
                # Обновляем статистику
                self.db.update_statistics(tokens_cleaned=1)
                
                # Отправляем на финальную проверку
                self.cleaned_queue.put(purchase)
                
                self.validated_queue.task_done()
                
            except:
                # Queue пустая - ждем
                time.sleep(1)
                continue
    
    # ==================== ЭТАП 4: ФИНАЛЬНАЯ ВАЛИДАЦИЯ ====================
    
    def _final_validation_worker(self):
        """Поток финальной валидации после очистки"""
        logger.info("🔍 [Validator #2] Запуск финальной валидации...")
        
        while self.running:
            try:
                # Получаем токен из очереди
                purchase = self.cleaned_queue.get(timeout=1)
                
                logger.info(f"🔍 [Validator #2] Финальная проверка токена {purchase.get('username', 'Unknown')}...")
                
                # Валидируем токен с обработкой исключений
                try:
                    is_valid, username = self.validator.validate_token(purchase['token'], strict=True, stage='validator_final')
                except Exception as e:
                    logger.error(f"❌ [Validator #2] Ошибка валидации: {e}")
                    # Возвращаем токен обратно в очередь для повторной попытки
                    self.cleaned_queue.put(purchase)
                    self.cleaned_queue.task_done()
                    time.sleep(5)
                    continue
                
                if is_valid:
                    logger.info(f"✅ [Validator #2] Токен прошел финальную проверку: {username}")
                    
                    # Обновляем в БД - статус READY (готов к отправке)
                    self.db.update_token_status(
                        token=purchase['token'],
                        status='ready',
                        username=username
                    )
                    
                    logger.info(f"📦 [Validator #2] Токен готов к отправке")
                    
                    # Проверяем количество готовых токенов
                    ready_tokens = self.db.get_ready_tokens(limit=1000)
                    ready_count = len(ready_tokens)
                    
                    logger.debug(f"📊 [Validator #2] Всего готовых токенов: {ready_count}")
                    
                    # Параметры из конфига
                    notification_threshold = self.config['telegram'].get('notification_threshold', 40)
                    
                    # Если достигли порога И еще не отправляли уведомление
                    if ready_count >= notification_threshold and not self.ready_tokens_notification_sent:
                        logger.info(f"🔔 [Validator #2] Достигнут порог {notification_threshold} токенов! Отправка уведомления...")
                        
                        try:
                            # Отправляем уведомление
                            self.telegram.send_notification(
                                title="🎉 Готовые токены накоплены!",
                                message=(
                                    f"Накоплено <b>{ready_count}</b> готовых токенов!\n\n"
                                    f"Состояние записей доступно в панели и меню статуса."
                                ),
                                level="SUCCESS"
                            )
                            
                            # Устанавливаем флаг
                            self.ready_tokens_notification_sent = True
                            
                            logger.info(f"✅ [Validator #2] Уведомление отправлено в Telegram")
                        except Exception as e:
                            logger.error(f"❌ [Validator #2] Ошибка отправки уведомления: {e}")
                    
                    # Сбрасываем флаг если токенов стало меньше порога (значит пользователь отправил)
                    elif ready_count < notification_threshold:
                        if self.ready_tokens_notification_sent:
                            logger.info(f"🔄 [Validator #2] Количество токенов упало ниже порога ({ready_count} < {notification_threshold}), сброс флага уведомлений")
                        self.ready_tokens_notification_sent = False
                    
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

        # Управление вынесено в веб-панель. Telegram оставлен только для
        # уведомлений и отправки готовых токенов: меню и polling не запускаем.
        self.telegram.send_notification(
            title="Pipeline запущен",
            message="Автоматическая обработка токенов начата.\n\nУправление — в веб-панели.",
            level="SUCCESS"
        )

        # Создаем и запускаем потоки конвейера. Аккаунты поступают не из монитора,
        # а из задачи покупки (запускается отдельно из панели).

        # Несколько потоков для Validator #1
        validator_threads = self.config.get('validator', {}).get('max_workers', 5)
        for i in range(validator_threads):
            thread = threading.Thread(target=self._validation_worker, name=f"Validator #1-{i+1}", daemon=True)
            thread.start()
            self.threads.append(thread)
        logger.info(f"▶️ Потоков запущено: Validator #1 x{validator_threads}")
        
        # Несколько потоков для Cleaner (управляются на лету через set_cleaner_workers)
        cleaner_threads = max(1, int(self.config.get('cleaner', {}).get('max_workers', 5) or 1))
        self._spawn_cleaner_workers(cleaner_threads)
        logger.info(f"▶️ Потоков запущено: Cleaner x{cleaner_threads}")
        
        # Несколько потоков для Validator #2
        final_validator_threads = self.config.get('validator', {}).get('max_workers', 5)
        for i in range(final_validator_threads):
            thread = threading.Thread(target=self._final_validation_worker, name=f"Validator #2-{i+1}", daemon=True)
            thread.start()
            self.threads.append(thread)
        logger.info(f"▶️ Потоков запущено: Validator #2 x{final_validator_threads}")
        
        # 1 поток для Statistics
        thread = threading.Thread(target=self._statistics_worker, name="Statistics", daemon=True)
        thread.start()
        self.threads.append(thread)
        logger.info(f"▶️ Поток запущен: Statistics")
        
        logger.info(f"✅ Pipeline запущен успешно! Всего потоков: {len(self.threads) + len(self._cleaner_workers)}")

    # ==================== УПРАВЛЕНИЕ ПОТОКАМИ ОЧИСТКИ ====================

    def _spawn_cleaner_workers(self, count: int):
        """Запускает `count` новых потоков очистки (под _cleaner_lock)."""
        with self._cleaner_lock:
            for _ in range(count):
                index = len(self._cleaner_workers) + 1
                stop_event = threading.Event()
                thread = threading.Thread(
                    target=self._cleaning_worker, args=(stop_event,),
                    name=f"Cleaner-{index}", daemon=True)
                thread.start()
                self._cleaner_workers.append({'thread': thread, 'stop_event': stop_event})

    def _prune_cleaner_workers(self):
        """Убирает из списка уже завершившиеся потоки очистки (под _cleaner_lock)."""
        self._cleaner_workers = [
            worker for worker in self._cleaner_workers
            if worker['thread'].is_alive() and not worker['stop_event'].is_set()
        ]

    def count_cleaner_workers(self) -> int:
        """Возвращает число активных потоков очистки."""
        with self._cleaner_lock:
            return sum(1 for w in self._cleaner_workers
                       if w['thread'].is_alive() and not w['stop_event'].is_set())

    def set_cleaner_workers(self, count: int) -> int:
        """
        Регулирует число потоков очистки на лету.

        Увеличение — запускает новые потоки; уменьшение — сигналит лишним потокам
        завершиться после текущего токена. Значение сохраняется в config.json.

        Returns:
            Фактическое целевое число потоков.
        """
        count = max(1, min(200, int(count)))
        with self._cleaner_lock:
            # Ошибка записи должна остановить применение настройки.
            self._persist_config_value('cleaner', 'max_workers', count)
            self._prune_cleaner_workers()
            current = len(self._cleaner_workers)
            if self.running and count > current:
                for _ in range(count - current):
                    index = len(self._cleaner_workers) + 1
                    stop_event = threading.Event()
                    thread = threading.Thread(
                        target=self._cleaning_worker, args=(stop_event,),
                        name=f"Cleaner-{index}", daemon=True)
                    thread.start()
                    self._cleaner_workers.append({'thread': thread, 'stop_event': stop_event})
            elif self.running and count < current:
                # Ретайрим лишние потоки (последние в списке)
                for worker in self._cleaner_workers[count:]:
                    worker['stop_event'].set()
                self._cleaner_workers = self._cleaner_workers[:count]
            self.cleaner_target = count

        logger.info(f"🧵 Число потоков очистки установлено: {count}")
        return count

    def stop(self):
        """Останавливает pipeline"""
        if not self.running:
            logger.warning("⚠️ Pipeline не запущен")
            return

        logger.info("⏹️ Остановка Pipeline...")

        with self._intake_lock:
            self.running = False

        # Останавливаем задачу покупки (уже купленное завершит обработку по мере выхода)
        try:
            self.purchase.stop()
        except Exception:
            logger.warning("Не удалось остановить задачу покупки")

        # Останавливаем Telegram polling
        self.telegram.stop_polling()

        # Останавливаем потоки очистки
        with self._cleaner_lock:
            cleaner_workers = list(self._cleaner_workers)
            for worker in cleaner_workers:
                worker['stop_event'].set()
        for worker in cleaner_workers:
            worker['thread'].join(timeout=5)
        with self._cleaner_lock:
            self._cleaner_workers = []

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
    
    def get_thread_states(self) -> Dict[str, bool]:
        """Состояние всех рабочих потоков, включая потоки очистки."""
        states = {thread.name: thread.is_alive() for thread in self.threads}
        with self._cleaner_lock:
            for worker in self._cleaner_workers:
                states[worker['thread'].name] = (
                    worker['thread'].is_alive() and not worker['stop_event'].is_set())
        return states

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
            'threads': self.get_thread_states(),
            'counts': self.db.count_tokens_by_status(),
        }
