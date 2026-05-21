import requests
import logging
from typing import List, Dict, Optional, Callable
from datetime import datetime
import io
import time
import threading

logger = logging.getLogger(__name__)


class TelegramBot:
    """Класс для работы с Telegram Bot API с поддержкой команд и кнопок"""
    
    def __init__(self, bot_token: str, chat_id: str):
        """
        Инициализация Telegram бота
        
        Args:
            bot_token: Токен бота от @BotFather
            chat_id: ID чата для отправки сообщений
        """
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.file_base_url = f"https://api.telegram.org/file/bot{bot_token}"
        self.last_update_id = 0
        self.command_handlers = {}
        self.is_polling = False
        self.polling_thread = None
        self.last_menu_message_id = None  # Хранить ID последнего меню
        # Чаты, ожидающие ввод токенов (callback "upload_tokens")
        self.pending_uploads = set()
        # Обработчик загруженных токенов: fn(chat_id: str, raw_text: str)
        self.token_upload_handler: Optional[Callable] = None
        
    def _send_request(self, method: str, data: dict = None, files: dict = None) -> Optional[Dict]:
        """Отправляет запрос к Telegram API"""
        url = f"{self.base_url}/{method}"
        
        try:
            if files:
                response = requests.post(url, data=data, files=files, timeout=30)
            else:
                response = requests.post(url, json=data, timeout=30)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"❌ Telegram API error: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Ошибка отправки в Telegram: {e}")
            return None
    
    def send_message(self, text: str, parse_mode: str = "HTML", reply_markup: dict = None, chat_id: str = None) -> Optional[int]:
        """
        Отправляет текстовое сообщение
        
        Returns:
            message_id если успешно
        """
        data = {
            "chat_id": chat_id or self.chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        
        if reply_markup:
            data["reply_markup"] = reply_markup
        
        result = self._send_request("sendMessage", data=data)
        
        if result and result.get('ok'):
            message_id = result.get('result', {}).get('message_id')
            logger.info(f"✅ Сообщение отправлено в Telegram (message_id: {message_id})")
            return message_id
        
        return None
    
    def edit_message(self, message_id: int, text: str, parse_mode: str = "HTML", reply_markup: dict = None, chat_id: str = None) -> bool:
        """
        Редактирует существующее сообщение
        
        Args:
            message_id: ID сообщения для редактирования
            text: Новый текст
            parse_mode: Режим форматирования
            reply_markup: Новая клавиатура
            chat_id: ID чата
            
        Returns:
            True если успешно
        """
        data = {
            "chat_id": chat_id or self.chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode
        }
        
        if reply_markup:
            data["reply_markup"] = reply_markup
        
        result = self._send_request("editMessageText", data=data)
        
        if result and result.get('ok'):
            logger.info(f"✅ Сообщение отредактировано (message_id: {message_id})")
            return True
        
        return False
    
    def send_main_menu(self, chat_id: str = None, message_id: int = None, miniapp_url: str = None):
        """Отправляет или редактирует главное меню"""
        keyboard_buttons = [
            [
                {"text": "📊 Статистика", "callback_data": "stats"},
                {"text": "💰 Баланс LZT", "callback_data": "balance"},
            ],
            [
                {"text": "📦 Продать токены", "callback_data": "send_tokens"},
                {"text": "📥 Загрузить токены", "callback_data": "upload_tokens"},
            ],
            [
                {"text": "🔄 Статус", "callback_data": "status"},
                {"text": "🧾 Продажи", "callback_data": "sales_status"},
            ],
        ]

        keyboard = {"inline_keyboard": keyboard_buttons}

        text = "🤖 <b>Discord Token Manager</b>\n\nВыберите действие:"

        if message_id:
            success = self.edit_message(message_id, text, reply_markup=keyboard, chat_id=chat_id)
            if success:
                self.last_menu_message_id = message_id
            return success
        else:
            new_message_id = self.send_message(text, reply_markup=keyboard, chat_id=chat_id)
            if new_message_id:
                self.last_menu_message_id = new_message_id
            return new_message_id is not None
    
    def send_tokens_file(self, tokens: List[str], filename: str = None, chat_id: str = None) -> bool:
        """Отправляет токены в виде .txt файла"""
        if not tokens:
            logger.warning("⚠️ Нет токенов для отправки")
            return False
        
        if not filename:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"discord_tokens_{timestamp}.txt"
        
        content = "\n".join(tokens)
        file_bytes = content.encode('utf-8')
        file_obj = io.BytesIO(file_bytes)
        file_obj.name = filename
        
        data = {
            "chat_id": chat_id or self.chat_id,
            "caption": f"✅ <b>Готовые токены</b>\n\n📦 Количество: {len(tokens)} шт.",
            "parse_mode": "HTML"
        }
        
        files = {"document": file_obj}
        
        result = self._send_request("sendDocument", data=data, files=files)
        
        if result and result.get('ok'):
            logger.info(f"✅ Файл с {len(tokens)} токенами отправлен в Telegram")
            return True
        
        return False
    
    def send_notification(self, title: str, message: str, level: str = "INFO", chat_id: str = None) -> bool:
        """Отправляет уведомление"""
        emoji = {
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "ERROR": "❌",
            "SUCCESS": "✅"
        }.get(level, "📢")
        
        text = f"{emoji} <b>{title}</b>\n\n{message}"
        result = self.send_message(text, chat_id=chat_id)
        return result is not None
    def send_sellers_statistics(self, sellers: List[Dict], chat_id: str = None, message_id: int = None) -> bool:
        """Отправляет статистику по продавцам"""
        if not sellers:
            text = "📊 <b>Статистика продавцов</b>\n\n⚠️ Нет данных о продавцах"
        else:
            text = "👥 <b>Статистика продавцов</b>\n\n"
            
            for i, seller in enumerate(sellers, 1):
                username = seller.get('seller_username', 'Unknown')
                total_bought = seller.get('total_bought', 0)
                total_invalid = seller.get('total_invalid', 0)
                total_valid = total_bought - total_invalid
                avg_price = seller.get('avg_price', 0)
                valid_percent = seller.get('valid_percent', 0)
                
                text += (
                    f"{i}. 👤 <b>{username}</b>\n"
                    f"   📦 Куплено: {total_bought} | ✅ {total_valid} | ❌ {total_invalid} | "
                    f"💯 {valid_percent:.1f}% | 💰 {avg_price:.1f} ₽\n\n"
                )
        
        keyboard = {"inline_keyboard": [[{"text": "« Назад", "callback_data": "menu"}]]}
        
        if message_id:
            return self.edit_message(message_id, text, reply_markup=keyboard, chat_id=chat_id)
        else:
            result = self.send_message(text, reply_markup=keyboard, chat_id=chat_id)
            return result is not None

    
    def send_statistics(self, stats: Dict, chat_id: str = None, message_id: int = None) -> bool:
        """Отправляет или редактирует статистику"""
        # Кнопка "Назад"
        keyboard = {
            "inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "menu"}]]
        }
        
        text = (
            f"📊 <b>Статистика за сегодня</b>\n\n"
            f"📥 Куплено токенов: {stats.get('tokens_bought', 0)}\n"
            f"✅ Валидных: {stats.get('tokens_valid', 0)}\n"
            f"🧹 Очищенных: {stats.get('tokens_cleaned', 0)}\n"
            f"📤 Отправлено: {stats.get('tokens_sent', 0)}\n"
            f"💵 Потрачено: {stats.get('money_spent', 0):.2f} ₽\n"
            f"📈 Success Rate: {stats.get('success_rate', 0):.1f}%"
        )
        
        if message_id:
            return self.edit_message(message_id, text, reply_markup=keyboard, chat_id=chat_id)
        else:
            result = self.send_message(text, reply_markup=keyboard, chat_id=chat_id)
            return result is not None
    
    def send_balance_alert(self, current_balance: float, min_balance: float) -> bool:
        """Отправляет уведомление о низком балансе"""
        text = (
            f"⚠️ <b>Низкий баланс LZT Market!</b>\n\n"
            f"💰 Текущий баланс: {current_balance:.2f} ₽\n"
            f"📉 Минимум: {min_balance:.2f} ₽\n\n"
            f"Пожалуйста, пополните баланс для продолжения покупок."
        )
        
        result = self.send_message(text)
        return result is not None
    
    def send_balance_info(self, current_balance: float, min_balance: float, chat_id: str = None, message_id: int = None) -> bool:
        """Отправляет или редактирует информацию о балансе"""
        status = "✅" if current_balance >= min_balance else "⚠️"
        
        # Кнопка "Назад"
        keyboard = {
            "inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "menu"}]]
        }
        
        text = (
            f"{status} <b>Баланс LZT Market</b>\n\n"
            f"💰 Текущий баланс: {current_balance:.2f} ₽\n"
            f"📉 Минимум: {min_balance:.2f} ₽\n"
        )
        
        if current_balance < min_balance:
            text += f"\n⚠️ Баланс ниже минимального!"
        
        if message_id:
            return self.edit_message(message_id, text, reply_markup=keyboard, chat_id=chat_id)
        else:
            result = self.send_message(text, reply_markup=keyboard, chat_id=chat_id)
            return result is not None
    
    def send_new_purchase(self, item_id: int, price: float, username: str = None) -> bool:
        """Отправляет уведомление о новой покупке"""
        text = (
            f"🛒 <b>Новая покупка</b>\n\n"
            f"🆔 Item ID: {item_id}\n"
            f"💰 Цена: {price} ₽\n"
        )
        
        if username:
            text += f"👤 Username: {username}\n"
        
        text += f"\n⏳ Токен добавлен в очередь обработки..."
        
        result = self.send_message(text)
        return result is not None
    
    def send_error(self, module: str, error_text: str) -> bool:
        """Отправляет уведомление об ошибке"""
        text = (
            f"❌ <b>Ошибка в модуле {module}</b>\n\n"
            f"<code>{error_text}</code>"
        )
        
        result = self.send_message(text)
        return result is not None
    
    def send_ready_tokens_info(self, count: int, min_required: int, chat_id: str = None, message_id: int = None) -> bool:
        """Отправляет или редактирует информацию о готовых токенах"""
        if count >= min_required:
            status = "✅"
            message = f"Готово {count} токенов. Можно отправлять!"
        else:
            status = "⏳"
            message = f"Готово только {count} токенов. Нужно минимум {min_required}."
        
        # Кнопка "Назад"
        keyboard = {
            "inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "menu"}]]
        }
        
        text = (
            f"{status} <b>Готовые токены</b>\n\n"
            f"📦 Готово к отправке: {count} шт.\n"
            f"📊 Минимум для отправки: {min_required} шт.\n\n"
            f"{message}"
        )
        
        if message_id:
            return self.edit_message(message_id, text, reply_markup=keyboard, chat_id=chat_id)
        else:
            result = self.send_message(text, reply_markup=keyboard, chat_id=chat_id)
            return result is not None
    
    def send_system_status(self, status_data: Dict, chat_id: str = None, message_id: int = None) -> bool:
        """Отправляет или редактирует статус системы"""
        running = status_data.get('running', False)
        counts = status_data.get('counts', {})
        
        status_emoji = "🟢" if running else "🔴"
        status_text = "Работает" if running else "Остановлена"
        
        # Кнопка "Назад"
        keyboard = {
            "inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "menu"}]]
        }
        
        text = (
            f"{status_emoji} <b>Статус системы: {status_text}</b>\n\n"
            f"📊 <b>Токены по статусам:</b>\n"
            f"🆕 Новые: {counts.get('new', 0)}\n"
            f"✅ Валидные: {counts.get('validated', 0)}\n"
            f"🧹 Очищаются: {counts.get('cleaning', 0)}\n"
            f"✨ Очищены: {counts.get('cleaned', 0)}\n"
            f"📦 Готовы: {counts.get('ready', 0)}\n"
            f"🧾 В продаже: {counts.get('sale_pending', 0)}\n"
            f"📤 Отправлены: {counts.get('sent', 0)}\n"
            f"❌ Невалидные: {counts.get('invalid', 0)}"
        )
        
        if message_id:
            return self.edit_message(message_id, text, reply_markup=keyboard, chat_id=chat_id)
        else:
            result = self.send_message(text, reply_markup=keyboard, chat_id=chat_id)
            return result is not None

    def send_dashboard_url(self, tunnel_url: str = None, chat_id: str = None, message_id: int = None) -> bool:
        """Отправляет или редактирует информацию о Dashboard URL"""
        # Кнопка "Назад"
        keyboard = {
            "inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "menu"}]]
        }
        
        if tunnel_url:
            text = (
                f"🌐 <b>Dashboard доступен!</b>\n\n"
                f"🔗 <b>URL:</b>\n"
                f"<code>{tunnel_url}</code>\n\n"
                f"📱 Откройте эту ссылку в браузере на любом устройстве\n\n"
                f"💡 <b>Совет:</b> Сохраните ссылку, она действует пока запущен Cloudflare Tunnel"
            )
        else:
            text = (
                f"⚠️ <b>Cloudflare Tunnel не запущен</b>\n\n"
                f"Dashboard доступен только локально:\n"
                f"<code>http://localhost:5000</code>\n\n"
                f"📝 <b>Как включить удаленный доступ:</b>\n"
                f"1. Установите Cloudflare Tunnel\n"
                f"2. Запустите: <code>cloudflared tunnel --url http://localhost:5000</code>\n"
                f"3. Снова нажмите эту кнопку"
            )
        
        if message_id:
            return self.edit_message(message_id, text, reply_markup=keyboard, chat_id=chat_id)
        else:
            result = self.send_message(text, reply_markup=keyboard, chat_id=chat_id)
            return result is not None
    
    def send_validation_result(self, total: int, valid: int, invalid: int, chat_id: str = None, message_id: int = None) -> bool:
        """Отправляет результаты проверки валидности токенов"""
        # Кнопка "Назад"
        keyboard = {
            "inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "menu"}]]
        }
        
        # Эмодзи в зависимости от результата
        if invalid == 0:
            emoji = "✅"
            result_text = "Все токены валидны!"
        elif valid == 0:
            emoji = "❌"
            result_text = "Все токены невалидны!"
        else:
            emoji = "⚠️"
            result_text = "Проверка завершена"
        
        text = (
            f"{emoji} <b>{result_text}</b>\n\n"
            f"📊 <b>Результаты проверки:</b>\n"
            f"🔍 Проверено: {total}\n"
            f"✅ Валидных: {valid}\n"
            f"❌ Невалидных: {invalid}\n\n"
        )
        
        if invalid > 0:
            text += f"🗑️ Невалидные токены удалены из базы\n\n"
        
        if valid > 0:
            text += f"📦 Валидных токенов готово к отправке: <b>{valid}</b>"
        else:
            text += f"⚠️ Нет валидных токенов для отправки"
        
        if message_id:
            return self.edit_message(message_id, text, reply_markup=keyboard, chat_id=chat_id)
        else:
            result = self.send_message(text, reply_markup=keyboard, chat_id=chat_id)
            return result is not None
    
    def send_tokens_sent_notification(self, count: int, invalid_count: int = 0, chat_id: str = None) -> bool:
        """Отправляет уведомление об успешной отправке токенов"""
        text = (
            f"✅ <b>Токены отправлены!</b>\n\n"
            f"📤 Отправлено: {count} шт.\n"
        )
        
        if invalid_count > 0:
            text += f"🗑️ Удалено невалидных: {invalid_count} шт.\n"
        
        result = self.send_message(text, chat_id=chat_id)
        return result is not None

    def send_sales_status(self, summary: Dict, chat_id: str = None, message_id: int = None) -> bool:
        """Отправляет статус продаж."""
        if not summary.get("enabled"):
            text = "🧾 <b>Продажи</b>\n\n⚠️ Модуль продаж выключен."
            keyboard = {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "menu"}]]}
        else:
            active = summary.get("active", 0)
            completed = summary.get("completed", 0)
            failed = summary.get("failed", 0)
            canceled = summary.get("canceled", 0)

            text = (
                "🧾 <b>Продажи</b>\n\n"
                f"🔄 В работе: <b>{active}</b>\n"
                f"✅ Завершено: <b>{completed}</b>\n"
            )
            if failed:
                text += f"❌ Ошибки: <b>{failed}</b>\n"
            if canceled:
                text += f"↩️ Отменено: <b>{canceled}</b>\n"

            recent = summary.get("recent", [])
            if recent:
                text += "\n<b>Последние:</b>\n"
                for item in recent:
                    sid = item.get("submission_id", "?")
                    short_id = sid.split("-")[-1] if "-" in sid else sid[-8:]
                    status = item.get("status", "?")
                    count = item.get("accepted_count", 0)

                    wf = item.get("workflow") or {}
                    stage = wf.get("stage", "")

                    status_emoji = {
                        "PENDING": "⏳",
                        "TSKUPKA_PRICE_PENDING": "💰",
                        "TOKENBUYROBOT_PRICE_PENDING": "💰",
                        "TOKENBUYROBOT_COMPLETION_PENDING": "⏳",
                        "COMPLETED": "✅",
                        "WORKFLOW_FAILED": "❌",
                        "CANCELED": "↩️",
                    }.get(status, "❓")

                    prices = []
                    if wf.get("tskupka_price") is not None:
                        prices.append(f"tsk: {wf['tskupka_price']}")
                    if wf.get("tokenbuyrobot_price") is not None:
                        prices.append(f"tbr: {wf['tokenbuyrobot_price']}")
                    price_str = " | ".join(prices)

                    status_label = {
                        "PENDING": "подготовка",
                        "TSKUPKA_PRICE_PENDING": "ждём цену tskupka",
                        "TOKENBUYROBOT_PRICE_PENDING": "ждём цену tbr",
                        "TOKENBUYROBOT_COMPLETION_PENDING": "ждём tbr",
                        "COMPLETED": "завершена",
                        "WORKFLOW_FAILED": "ошибка",
                        "CANCELED": "отменена",
                    }.get(status, status)

                    text += f"{status_emoji} <code>{short_id}</code> {count} шт. — {status_label}"
                    if price_str:
                        text += f"\n     {price_str}"
                    text += "\n"

            keyboard_rows = []
            # Кнопка отмены для активных заявок
            active_subs = [
                s for s in recent
                if str(s.get("status", "")).upper() not in {"COMPLETED", "CANCELED", "WORKFLOW_FAILED"}
            ]
            for item in active_subs[:2]:
                sid = item.get("submission_id")
                short_id = sid.split("-")[-1] if sid and "-" in sid else (sid or "?")[-8:]
                keyboard_rows.append([
                    {"text": f"↩️ Отменить {short_id}", "callback_data": f"sales_cancel:{sid}"},
                ])

            keyboard_rows.append([{"text": "🔄 Обновить", "callback_data": "sales_status"}])
            keyboard_rows.append([{"text": "◀️ Назад", "callback_data": "menu"}])
            keyboard = {"inline_keyboard": keyboard_rows}

        if message_id:
            return self.edit_message(message_id, text, reply_markup=keyboard, chat_id=chat_id)
        result = self.send_message(text, reply_markup=keyboard, chat_id=chat_id)
        return result is not None

    def send_sale_action_result(self, title: str, message: str, success: bool = True,
                                chat_id: str = None, message_id: int = None) -> bool:
        """Показывает результат действия с заявкой."""
        emoji = "✅" if success else "❌"
        keyboard = {
            "inline_keyboard": [
                [{"text": "🧾 К продажам", "callback_data": "sales_status"}],
                [{"text": "◀️ В меню", "callback_data": "menu"}],
            ]
        }
        text = f"{emoji} <b>{title}</b>\n\n{message}"

        if message_id:
            return self.edit_message(message_id, text, reply_markup=keyboard, chat_id=chat_id)
        return self.send_message(text, reply_markup=keyboard, chat_id=chat_id) is not None
    
    def send_insufficient_tokens_error(self, current: int, required: int, chat_id: str = None) -> bool:
        """Отправляет ошибку о недостаточном количестве токенов"""
        text = (
            f"❌ <b>Недостаточно токенов для отправки</b>\n\n"
            f"📦 Готовых токенов: {current} шт.\n"
            f"📊 Минимум требуется: {required} шт.\n\n"
            f"⏳ Дождитесь обработки новых токенов и попробуйте снова."
        )
        
        result = self.send_message(text, chat_id=chat_id)
        return result is not None
    
    def register_command_handler(self, command: str, handler: Callable):
        """Регистрирует обработчик команды"""
        self.command_handlers[command] = handler
        logger.info(f"✅ Зарегистрирован обработчик для команды: {command}")

    def register_token_upload_handler(self, handler: Callable):
        """
        Регистрирует обработчик ручной загрузки токенов.

        Args:
            handler: функция (chat_id: str, raw_text: str) -> (added, duplicates, errors, total)
        """
        self.token_upload_handler = handler
        logger.info("✅ Зарегистрирован обработчик ручной загрузки токенов")

    def send_upload_prompt(self, chat_id: str = None, message_id: int = None) -> bool:
        """
        Переводит чат в режим ожидания токенов.
        Пользователь может прислать .txt файл или сообщение со списком токенов.
        """
        target_chat = str(chat_id or self.chat_id)
        self.pending_uploads.add(target_chat)

        keyboard = {"inline_keyboard": [[{"text": "◀️ Отмена", "callback_data": "upload_cancel"}]]}

        text = (
            "📥 <b>Загрузка токенов</b>\n\n"
            "Пришлите:\n"
            "• <b>.txt файл</b> со списком токенов, либо\n"
            "• <b>сообщение</b> с токенами (каждый с новой строки)\n\n"
            "Поддерживается формат <code>login:pass:token</code> — "
            "будет взят последний сегмент.\n\n"
            "Для отмены — команда /cancel или кнопка ниже."
        )

        if message_id:
            return self.edit_message(message_id, text, reply_markup=keyboard, chat_id=target_chat)
        result = self.send_message(text, reply_markup=keyboard, chat_id=target_chat)
        return result is not None

    def cancel_upload(self, chat_id: str) -> bool:
        """Отменяет режим ожидания токенов для чата"""
        target_chat = str(chat_id)
        was_pending = target_chat in self.pending_uploads
        self.pending_uploads.discard(target_chat)
        return was_pending

    def download_file(self, file_id: str) -> Optional[bytes]:
        """Скачивает файл с серверов Telegram по file_id"""
        try:
            info = self._send_request("getFile", data={"file_id": file_id})
            if not info or not info.get('ok'):
                return None
            file_path = info['result'].get('file_path')
            if not file_path:
                return None
            response = requests.get(f"{self.file_base_url}/{file_path}", timeout=30)
            if response.status_code == 200:
                return response.content
            logger.error(f"❌ Не удалось скачать файл: {response.status_code}")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка скачивания файла: {e}")
            return None

    def send_upload_result(self, chat_id: str, added: int, duplicates: int,
                           errors: int, total: int) -> bool:
        """Отправляет сводку по результату ручной загрузки"""
        keyboard = {"inline_keyboard": [[{"text": "◀️ В меню", "callback_data": "menu"}]]}
        text = (
            "✅ <b>Загрузка завершена</b>\n\n"
            f"📦 Всего получено: <b>{total}</b>\n"
            f"➕ Принято: <b>{added}</b>\n"
            f"⚠️ Ошибок: <b>{errors}</b>"
        )
        return self.send_message(text, reply_markup=keyboard, chat_id=chat_id) is not None
    
    def get_updates(self, timeout: int = 30) -> List[Dict]:
        """Получает обновления от Telegram (long polling)"""
        params = {
            "offset": self.last_update_id + 1,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"]
        }
        
        try:
            response = requests.get(f"{self.base_url}/getUpdates", params=params, timeout=timeout + 5)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    return data.get('result', [])
        except Exception as e:
            logger.error(f"❌ Ошибка получения обновлений: {e}")
        
        return []
    
    def answer_callback_query(self, callback_query_id: str, text: str = None, show_alert: bool = False) -> bool:
        """Отвечает на callback query (нажатие кнопки)"""
        data = {"callback_query_id": callback_query_id}
        
        if text:
            data["text"] = text
            data["show_alert"] = show_alert
        
        result = self._send_request("answerCallbackQuery", data=data)
        return result and result.get('ok')

    def _dispatch_command_handler(self, command: str, chat_id: str = None,
                                  message_id: int = None, payload: str = None) -> bool:
        """Вызывает обработчик команды с учетом поддерживаемых аргументов."""
        handler = self.command_handlers.get(command)
        if not handler:
            return False

        import inspect
        sig = inspect.signature(handler)
        params = list(sig.parameters.keys())

        if len(params) >= 3:
            handler(chat_id, message_id, payload)
        elif len(params) == 2:
            handler(chat_id, message_id)
        elif len(params) == 1:
            handler(chat_id)
        else:
            handler()
        return True
    
    def process_update(self, update: Dict):
        """Обрабатывает одно обновление"""
        update_id = update.get('update_id', 0)
        self.last_update_id = max(self.last_update_id, update_id)
        
        # Обработка callback query (нажатия на кнопки)
        if 'callback_query' in update:
            callback_query = update['callback_query']
            callback_data = callback_query.get('data')
            callback_id = callback_query.get('id')
            from_user = callback_query.get('from', {})
            user_chat_id = str(from_user.get('id', ''))
            message = callback_query.get('message', {})
            message_id = message.get('message_id')
            
            logger.info(f"📱 Получена команда: {callback_data} от пользователя {user_chat_id}")
            
            # Отвечаем на callback query
            self.answer_callback_query(callback_id)
            
            if callback_data == 'upload_cancel':
                self.cancel_upload(user_chat_id)
                self.send_main_menu(chat_id=user_chat_id, message_id=message_id)
                return

            if callback_data == 'upload_tokens':
                self.send_upload_prompt(chat_id=user_chat_id, message_id=message_id)
                return

            if callback_data == 'menu':
                self.send_main_menu(chat_id=user_chat_id, message_id=message_id)
                return
            
            command = callback_data
            payload = None
            if callback_data and ':' in callback_data:
                command, payload = callback_data.split(':', 1)

            # Вызываем обработчик команды
            if command in self.command_handlers:
                try:
                    self._dispatch_command_handler(command, user_chat_id, message_id, payload)
                except Exception as e:
                    logger.error(f"❌ Ошибка выполнения команды {callback_data}: {e}")
                    self.send_error("Command Handler", str(e))
            else:
                logger.warning(f"⚠️ Нет обработчика для команды: {callback_data}")
        
        # Обработка текстовых команд
        elif 'message' in update:
            message = update['message']
            text = message.get('text', '') or ''
            document = message.get('document')
            from_user = message.get('from', {})
            user_chat_id = str(from_user.get('id', ''))

            logger.info(f"📱 Получено сообщение от {user_chat_id}: "
                        f"{'[document] ' + (document.get('file_name') or '') if document else text}")

            # === Ручная загрузка токенов: документ (.txt) ===
            if document and user_chat_id in self.pending_uploads:
                file_name = document.get('file_name') or 'upload.txt'
                mime = document.get('mime_type') or ''
                if not (file_name.lower().endswith('.txt') or mime.startswith('text/')):
                    self.send_message(
                        "⚠️ Нужен текстовый файл (.txt). Попробуйте ещё раз или /cancel.",
                        chat_id=user_chat_id
                    )
                    return

                data_bytes = self.download_file(document['file_id'])
                if data_bytes is None:
                    self.send_message("❌ Не удалось скачать файл.", chat_id=user_chat_id)
                    return
                raw_text = data_bytes.decode('utf-8', errors='ignore')
                self.cancel_upload(user_chat_id)
                if self.token_upload_handler:
                    try:
                        self.token_upload_handler(user_chat_id, raw_text)
                    except Exception as e:
                        logger.error(f"❌ Ошибка обработки загрузки: {e}")
                        self.send_error("Token Upload", str(e))
                else:
                    self.send_message("⚠️ Обработчик загрузки не настроен.", chat_id=user_chat_id)
                return

            # === Ручная загрузка токенов: текстовое сообщение ===
            if text and not text.startswith('/') and user_chat_id in self.pending_uploads:
                self.cancel_upload(user_chat_id)
                if self.token_upload_handler:
                    try:
                        self.token_upload_handler(user_chat_id, text)
                    except Exception as e:
                        logger.error(f"❌ Ошибка обработки загрузки: {e}")
                        self.send_error("Token Upload", str(e))
                else:
                    self.send_message("⚠️ Обработчик загрузки не настроен.", chat_id=user_chat_id)
                return

            if text.startswith('/'):
                parts = text[1:].split(maxsplit=1)
                command = parts[0].split('@')[0]
                payload = parts[1].strip() if len(parts) > 1 else None
                logger.info(f"📱 Получена команда: /{command}")

                if command == 'cancel':
                    if self.cancel_upload(user_chat_id):
                        self.send_message("↩️ Загрузка отменена.", chat_id=user_chat_id)
                    else:
                        self.send_message("Нет активных операций.", chat_id=user_chat_id)
                    return

                if command == 'start' or command == 'menu':
                    self.send_main_menu(chat_id=user_chat_id)
                elif command in self.command_handlers:
                    try:
                        self._dispatch_command_handler(command, user_chat_id, None, payload)
                    except Exception as e:
                        logger.error(f"❌ Ошибка выполнения команды /{command}: {e}")
                        self.send_error("Command Handler", str(e))
    
    def start_polling(self):
        """Запускает polling в отдельном потоке"""
        if self.is_polling:
            logger.warning("⚠️ Polling уже запущен")
            return
        
        self.is_polling = True
        self.polling_thread = threading.Thread(target=self._polling_loop, daemon=True, name="TelegramPolling")
        self.polling_thread.start()
        logger.info("✅ Telegram polling запущен")
    
    def stop_polling(self):
        """Останавливает polling"""
        self.is_polling = False
        if self.polling_thread:
            self.polling_thread.join(timeout=5)
        logger.info("⏹️ Telegram polling остановлен")
    
    def _polling_loop(self):
        """Основной цикл polling"""
        logger.info("🔄 Запуск цикла polling...")
        
        while self.is_polling:
            try:
                updates = self.get_updates()
                
                for update in updates:
                    self.process_update(update)
                
            except Exception as e:
                logger.error(f"❌ Ошибка в цикле polling: {e}")
                time.sleep(5)
        
        logger.info("⏹️ Цикл polling завершен")
    
    def test_connection(self) -> bool:
        """Проверяет подключение к боту"""
        result = self._send_request("getMe")
        
        if result and result.get('ok'):
            bot_info = result.get('result', {})
            bot_name = bot_info.get('username', 'Unknown')
            logger.info(f"✅ Подключение к боту успешно: @{bot_name}")
            return True
        
        logger.error("❌ Не удалось подключиться к боту")
        return False
