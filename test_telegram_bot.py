import sys
import os
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.telegram_bot import TelegramBot
from modules.database import Database

# ВАЖНО: Замените на свои данные!
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def test_connection():
    """Тест 1: Проверка подключения к боту"""
    print("\n" + "="*60)
    print("ТЕСТ 1: Проверка подключения к боту")
    print("="*60)
    
    bot = TelegramBot(bot_token=BOT_TOKEN, chat_id=CHAT_ID)
    
    print("Проверяем подключение...")
    
    if bot.test_connection():
        print("✅ Подключение успешно!")
        return True
    else:
        print("❌ Ошибка подключения")
        print("\n💡 Проверьте:")
        print("  1. BOT_TOKEN правильный (получите у @BotFather)")
        print("  2. CHAT_ID правильный (узнайте у @userinfobot)")
        print("  3. Вы написали боту хоть раз (/start)")
        return False


def test_send_message():
    """Тест 2: Отправка простого сообщения"""
    print("\n" + "="*60)
    print("ТЕСТ 2: Отправка текстового сообщения")
    print("="*60)
    
    bot = TelegramBot(bot_token=BOT_TOKEN, chat_id=CHAT_ID)
    
    print("Отправляем тестовое сообщение...")
    
    success = bot.send_message(
        "🧪 <b>Тестовое сообщение</b>\n\n"
        "Это тест отправки сообщения из Discord Token Manager.\n\n"
        "✅ Если вы видите это сообщение - бот работает!"
    )
    
    if success:
        print("✅ Сообщение отправлено!")
    else:
        print("❌ Ошибка отправки")
    
    return success


def test_send_tokens_file():
    """Тест 3: Отправка файла с токенами"""
    print("\n" + "="*60)
    print("ТЕСТ 3: Отправка файла с токенами")
    print("="*60)
    
    bot = TelegramBot(bot_token=BOT_TOKEN, chat_id=CHAT_ID)
    
    # Создаем тестовые токены
    test_tokens = [
        "MTAx.test.token_1_example",
        "MTAy.test.token_2_example",
        "MTAz.test.token_3_example",
        "MTA0.test.token_4_example",
        "MTA1.test.token_5_example",
    ]
    
    print(f"Отправляем файл с {len(test_tokens)} токенами...")
    
    success = bot.send_tokens_file(test_tokens, filename="test_tokens.txt")
    
    if success:
        print("✅ Файл отправлен!")
    else:
        print("❌ Ошибка отправки файла")
    
    return success


def test_send_notifications():
    """Тест 4: Отправка различных уведомлений"""
    print("\n" + "="*60)
    print("ТЕСТ 4: Уведомления")
    print("="*60)
    
    bot = TelegramBot(bot_token=BOT_TOKEN, chat_id=CHAT_ID)
    
    # INFO уведомление
    print("\n📢 Отправка INFO уведомления...")
    bot.send_notification(
        title="Информация",
        message="Система работает нормально",
        level="INFO"
    )
    time.sleep(1)
    
    # SUCCESS уведомление
    print("✅ Отправка SUCCESS уведомления...")
    bot.send_notification(
        title="Успешная операция",
        message="5 токенов успешно обработаны",
        level="SUCCESS"
    )
    time.sleep(1)
    
    # WARNING уведомление
    print("⚠️ Отправка WARNING уведомления...")
    bot.send_notification(
        title="Предупреждение",
        message="Очередь токенов почти пуста",
        level="WARNING"
    )
    time.sleep(1)
    
    # ERROR уведомление
    print("❌ Отправка ERROR уведомления...")
    bot.send_notification(
        title="Ошибка",
        message="Не удалось подключиться к Discord API",
        level="ERROR"
    )
    
    print("\n✅ Все уведомления отправлены!")


def test_send_statistics():
    """Тест 5: Отправка статистики"""
    print("\n" + "="*60)
    print("ТЕСТ 5: Статистика")
    print("="*60)
    
    bot = TelegramBot(bot_token=BOT_TOKEN, chat_id=CHAT_ID)
    
    # Получаем статистику из БД
    db = Database("test_tokens.db")
    stats = db.get_today_statistics()
    
    print("Отправляем статистику за сегодня...")
    
    success = bot.send_statistics(stats)
    
    if success:
        print("✅ Статистика отправлена!")
    else:
        print("❌ Ошибка отправки статистики")
    
    return success


def test_balance_alert():
    """Тест 6: Уведомление о низком балансе"""
    print("\n" + "="*60)
    print("ТЕСТ 6: Уведомление о низком балансе")
    print("="*60)
    
    bot = TelegramBot(bot_token=BOT_TOKEN, chat_id=CHAT_ID)
    
    print("Отправляем уведомление о низком балансе...")
    
    success = bot.send_balance_alert(
        current_balance=75.50,
        min_balance=100.0
    )
    
    if success:
        print("✅ Уведомление отправлено!")
    else:
        print("❌ Ошибка отправки")
    
    return success


def test_new_purchase_notification():
    """Тест 7: Уведомление о новой покупке"""
    print("\n" + "="*60)
    print("ТЕСТ 7: Уведомление о новой покупке")
    print("="*60)
    
    bot = TelegramBot(bot_token=BOT_TOKEN, chat_id=CHAT_ID)
    
    print("Отправляем уведомление о покупке...")
    
    success = bot.send_new_purchase(
        item_id=212200065,
        price=25.0,
        username="TestUser#1234"
    )
    
    if success:
        print("✅ Уведомление отправлено!")
    else:
        print("❌ Ошибка отправки")
    
    return success




def main():
    print("\n" + "🔥"*30)
    print("ТЕСТИРОВАНИЕ TELEGRAM BOT MODULE")
    print("🔥"*30)
    
    print("\n⚠️ ВАЖНО: Перед запуском тестов:")
    print("1. Создайте бота через @BotFather")
    print("2. Получите BOT_TOKEN")
    print("3. Узнайте свой CHAT_ID через @userinfobot")
    print("4. Запишите их в TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID")
    print("5. Напишите боту /start")
    
    # Проверяем настройки
    if not BOT_TOKEN or not CHAT_ID:
        print("\n❌ ОШИБКА: Настройте BOT_TOKEN и CHAT_ID!")
        print("Заполните TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env.")
        return
    
    try:
        # Тест 1: Подключение
        if not test_connection():
            print("\n❌ Тесты прерваны - нет подключения к боту")
            return
        
        time.sleep(2)
        
        # Тест 2: Простое сообщение
        test_send_message()
        time.sleep(2)
        
        # Тест 3: Файл с токенами
        test_send_tokens_file()
        time.sleep(2)
        
        # Тест 4: Уведомления
        test_send_notifications()
        time.sleep(2)
        
        # Тест 5: Статистика
        test_send_statistics()
        time.sleep(2)
        
        # Тест 6: Низкий баланс
        test_balance_alert()
        time.sleep(2)
        
        # Тест 7: Новая покупка
        test_new_purchase_notification()
        time.sleep(2)
        
        
        print("\n" + "="*60)
        print("✅ ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ!")
        print("="*60)
        print("\n💡 Telegram Bot готов к использованию!")
        print("📱 Проверьте свой Telegram - там должны быть все сообщения")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
