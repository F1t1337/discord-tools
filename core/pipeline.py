import json
import logging
import os
import time
import threading
from queue import Queue
from typing import Dict, List
from datetime import datetime

import concurrent.futures

from modules.lzt_monitor import LZTMonitor
from modules.validator import TokenValidator
from modules.cleaner import process_token
from modules.database import Database
from modules.telegram_bot import TelegramBot
from modules.cloudflare_helper import CloudflareHelper
from modules.sales import SalesManager
from modules.google_sheets import GoogleSheetsLogger

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
    6. Telegram -> РУЧНАЯ отправка по кнопке
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
        
        logger.info("🚀 Pipeline инициализирован")
    
    def _init_modules(self):
        """Инициализирует все модули"""
        # Database (нужна для LZT Monitor)
        self.db = Database(self.config['database']['path'])
        
        # LZT Monitor
        self.lzt_monitor = LZTMonitor(
            api_token=self.config['lzt']['api_token'],
            check_interval=self.config['lzt']['check_interval'],
            min_balance_alert=self.config['lzt']['min_balance_alert'],
            database=self.db  # Передаем БД для проверки дубликатов
        )
        
        # Validator
        self.validator = TokenValidator(
            timeout=self.config['validator']['timeout'],
            max_retries=self.config['validator']['max_retries']
        )
        
        # Telegram Bot
        self.telegram = TelegramBot(
            bot_token=self.config['telegram']['bot_token'],
            chat_id=self.config['telegram']['chat_id']
        )
        
        # Cloudflare Tunnel Helper
        self.cloudflare = CloudflareHelper()

        # Google Sheets logger
        self.sheets = GoogleSheetsLogger(self.config.get('google_sheets', {}))

        # Sales
        self.sales = SalesManager(
            self.config.get('sales', {}),
            sheets_logger=self.sheets,
            db=self.db,
        )

        logger.info("✅ Все модули инициализированы")
    
    def _register_bot_commands(self):
        """Регистрирует обработчики команд Telegram бота"""
        self.telegram.register_command_handler('stats', self._handle_stats_command)
        self.telegram.register_command_handler('balance', self._handle_balance_command)
        self.telegram.register_command_handler('send_tokens', self._handle_send_tokens_command)
        self.telegram.register_command_handler('export_tokens', self._handle_export_tokens_command)
        self.telegram.register_command_handler('status', self._handle_status_command)
        self.telegram.register_command_handler('upload_tokens', self._handle_upload_tokens_command)
        self.telegram.register_command_handler('sales_status', self._handle_sales_status_command)
        self.telegram.register_command_handler('sales_cancel', self._handle_sales_cancel_command)
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

    def _handle_sales_status_command(self, chat_id: str = None, message_id: int = None, payload: str = None):
        """Показывает состояние безопасной локальной инфраструктуры продажи."""
        try:
            summary = self.sales.get_summary()
            self.telegram.send_sales_status(summary, chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.error(f"❌ Ошибка получения статуса продаж: {e}")
            self.telegram.send_error("Sales Status", str(e))

    def _handle_sales_cancel_command(self, chat_id: str = None, message_id: int = None, payload: str = None):
        """Отменяет локальную sales-заявку и возвращает позиции в ready."""
        try:
            if not payload:
                self.telegram.send_sale_action_result(
                    "Не указана заявка",
                    "Нужно выбрать заявку из меню продаж.",
                    success=False,
                    chat_id=chat_id,
                    message_id=message_id,
                )
                return

            result = self.sales.cancel_submission(payload)
            if not result.get('ok'):
                self.telegram.send_sale_action_result(
                    "Не удалось отменить заявку",
                    result.get('error', 'Неизвестная ошибка'),
                    success=False,
                    chat_id=chat_id,
                    message_id=message_id,
                )
                return

            submission = result['submission']
            updated = self._set_submission_items_status(submission, 'ready')
            self.ready_tokens_notification_sent = False

            self.telegram.send_sale_action_result(
                "Заявка отменена",
                (
                    f"<code>{payload}</code>\n\n"
                    f"Позиции возвращены в готовые: <b>{updated}</b>."
                ),
                success=True,
                chat_id=chat_id,
                message_id=message_id,
            )
        except Exception as e:
            logger.error(f"❌ Ошибка отмены sales-заявки: {e}")
            self.telegram.send_error("Sales Cancel", str(e))

    def _set_submission_items_status(self, submission: Dict, status: str) -> int:
        """Обновляет статусы позиций заявки по внутренним ID без чтения токенов."""
        updated = 0
        for item in submission.get('items', []):
            db_id = item.get('db_id')
            if not db_id:
                continue
            if self.db.update_token_status_by_id(db_id, status=status):
                updated += 1
        return updated

    
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
    
    def _handle_send_tokens_command(self, chat_id: str = None, message_id: int = None):
        """Продажа готовых токенов через tskupka + tokenbuyrobot workflow."""
        try:
            ready_tokens = self.db.get_ready_tokens(limit=1000)

            if not ready_tokens:
                self.telegram.send_notification(
                    title="⚠️ Нет готовых токенов",
                    message="Нет токенов для продажи.",
                    level="WARNING",
                    chat_id=chat_id,
                )
                return

            self.telegram.send_notification(
                title="🔍 Проверка",
                message=f"Проверяю {len(ready_tokens)} токенов...",
                level="INFO",
                chat_id=chat_id,
            )

            valid_tokens = []
            valid_records = []
            invalid_count = 0

            for token_data in ready_tokens:
                token = token_data['token']
                is_valid, username = self.validator.validate_token(token)

                if is_valid:
                    valid_tokens.append(token)
                    valid_records.append(token_data)
                else:
                    invalid_count += 1
                    self.db.update_token_status(token=token, status='invalid',
                                                error='Failed validation before send')
                time.sleep(0.5)

            if not valid_tokens:
                self.telegram.send_notification(
                    title="❌ Нет валидных токенов",
                    message=f"Все {invalid_count} токенов невалидны.",
                    level="ERROR",
                    chat_id=chat_id,
                )
                return

            if not self.sales.enabled:
                self.telegram.send_notification(
                    title="⚠️ Продажи выключены",
                    message="Включите модуль продаж в config.json.",
                    level="WARNING",
                    chat_id=chat_id,
                )
                return

            result = self.sales.create_submission(valid_records)
            if not result.get('ok'):
                self.telegram.send_error("Sales", result.get('error', 'Ошибка'))
                return

            for token in valid_tokens:
                self.db.update_token_status(token=token, status='sale_pending')
            self.ready_tokens_notification_sent = False

            submission_id = result['submission_id']
            msg = (
                f"✅ <b>{result['accepted_count']}</b> токенов"
            )
            if invalid_count:
                msg += f" (❌ {invalid_count} невалидных убрано)"
            msg += "\n\nWorkflow: tskupka → tokenbuyrobot → завершение"

            self.telegram.send_notification(
                title="🧾 Заявка создана",
                message=msg,
                level="SUCCESS",
                chat_id=chat_id,
            )
            self._start_sales_workflow(submission_id, chat_id)

        except Exception as e:
            logger.error(f"❌ Ошибка продажи токенов: {e}")
            self.telegram.send_error("Send Tokens", str(e))

    def _start_sales_workflow(self, submission_id: str, chat_id: str = None):
        """Запускает долгий sales workflow в фоне, чтобы не блокировать Telegram."""
        thread = threading.Thread(
            target=self._sales_workflow_worker,
            args=(submission_id, chat_id),
            name=f"SalesWorkflow-{submission_id[-8:]}",
            daemon=True,
        )
        thread.start()
        self.threads.append(thread)

    def _sales_workflow_worker(self, submission_id: str, chat_id: str = None):
        poll_message_id = None

        def notify(title: str, message: str, level: str):
            nonlocal poll_message_id
            emoji = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌",
                     "SUCCESS": "✅", "POLL": "⏳"}.get(level, "📢")
            text = f"{emoji} <b>{title}</b>\n\n<code>{submission_id}</code>\n\n{message}"
            if level == "POLL":
                if poll_message_id:
                    self.telegram.edit_message(poll_message_id, text, chat_id=chat_id)
                else:
                    poll_message_id = self.telegram.send_message(text, chat_id=chat_id)
            else:
                poll_message_id = None
                self.telegram.send_message(text, chat_id=chat_id)

        result = self.sales.run_workflow(
            submission_id,
            notify_callback=notify,
            re_clean_callback=self._reclean_tokens_sync,
        )
        if result.get('ok'):
            submission = result.get('submission') or {}
            updated = self._set_submission_items_status(submission, 'sent')
            self.db.update_statistics(tokens_sent=updated)
        else:
            logger.error(f"❌ Sales workflow failed for {submission_id}: {result.get('error')}")

    def _reclean_tokens_sync(self, tokens: list) -> int:
        """
        Синхронно повторно очищает токены прямо в потоке воркфлоу.
        Мёртвые токены cleaner выбрасывает сам (safe_request с 3 попытками
        по 10 сек каждая), так что внешний таймаут не нужен.
        Возвращает количество обработанных токенов.
        """
        if not tokens:
            return 0

        from modules.cleaner import ProxyManager, DiscordAPI

        class _NullTracker:
            def add_token(self, *a, **kw): pass
            def update_status(self, *a, **kw): pass
            def mark_completed(self, *a, **kw): pass

        tracker = _NullTracker()

        proxy_manager = None
        if self.config.get('proxy', {}).get('enabled'):
            try:
                proxy_manager = ProxyManager()
            except Exception as e:
                logger.warning("⚠️ [Reclean] Proxy недоступны: %s", e)

        max_workers = min(len(tokens), self.config.get('cleaner', {}).get('max_workers', 5))

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(process_token, DiscordAPI(proxy_manager, tracker), token, tracker,
                                self.close_channels)
                for token in tokens
            ]
            concurrent.futures.wait(futures)

        logger.info("✅ [Reclean] Обработано %d токенов", len(tokens))
        return len(tokens)
    
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
            
            checker = SimpleDiscordChecker(threads=10)
            
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
            min_required = self.config['telegram']['min_tokens']
            
            self.telegram.send_ready_tokens_info(count, min_required, chat_id=chat_id, message_id=message_id)
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
            self.close_channels = not self.close_channels
            self._persist_close_channels_setting(self.close_channels)

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

    def _persist_close_channels_setting(self, value: bool):
        """
        Сохраняет настройку close_channels в config.json, не трогая остальные поля
        и не раскрывая ${ENV} плейсхолдеры (читаем сырой файл, а не self.config).
        """
        # Держим in-memory конфиг в актуальном состоянии
        self.config.setdefault('cleaner', {})['close_channels'] = value

        try:
            if not os.path.exists(self.config_path):
                logger.warning(f"⚠️ config.json не найден ({self.config_path}), настройка не сохранена на диск")
                return
            with open(self.config_path, 'r', encoding='utf-8') as f:
                raw_config = json.load(f)
            raw_config.setdefault('cleaner', {})['close_channels'] = value
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(raw_config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ Не удалось сохранить настройку close_channels: {e}")

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
                        seller_username=purchase.get('seller_username', 'Unknown'),
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
                
                # Создаем API клиент
                api = DiscordAPI(proxy_manager, progress_tracker)
                
                # Запускаем очистку
                process_token(api, purchase['token'], progress_tracker,
                              close_channels=self.close_channels)
                
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
                    is_valid, username = self.validator.validate_token(purchase['token'])
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
                                    f"Вы можете отправить их, нажав кнопку "
                                    f"<b>'📦 Отправить токены'</b> в главном меню."
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
        
        # Получаем Cloudflare URL для Mini App
        tunnel_url = self.cloudflare.get_public_url()
        miniapp_url = f"{tunnel_url}/miniapp" if tunnel_url else None
        
        if miniapp_url:
            logger.info(f"🚀 Mini App доступен: {miniapp_url}")
        else:
            logger.info("⚠️ Cloudflare Tunnel не запущен, Mini App будет недоступен")
        
        # Отправляем главное меню в Telegram
        self.telegram.send_main_menu(miniapp_url=miniapp_url)
        
        # Отправляем уведомление
        notification_text = "Автоматическая обработка токенов начата.\n\nИспользуйте кнопки для управления системой."
        
        if miniapp_url:
            notification_text += "\n\n🚀 Mini App доступен - нажмите кнопку 'Открыть Dashboard' для быстрого доступа!"
        else:
            notification_text += "\n\n💡 Запустите Cloudflare Tunnel для удаленного доступа к Dashboard"
        
        self.telegram.send_notification(
            title="Pipeline запущен",
            message=notification_text,
            level="SUCCESS"
        )
        
        # Запускаем Telegram polling
        self.telegram.start_polling()
        
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
        
        # Останавливаем Telegram polling
        self.telegram.stop_polling()
        
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
            },
            'counts': self.db.count_tokens_by_status(),
            'sales': self.sales.get_summary(limit=3)
        }
