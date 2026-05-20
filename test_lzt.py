import logging
import sys
import os

# Добавляем путь к модулям
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.lzt_monitor import LZTMonitor

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('lzt_test.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)


def test_connection():
    """Тест 1: Проверка подключения к API"""
    print("\n" + "=" * 60)
    print("ТЕСТ 1: Проверка подключения к LZT API")
    print("=" * 60)

    API_TOKEN = os.environ.get("LZT_API_TOKEN", "")

    monitor = LZTMonitor(api_token=API_TOKEN)

    # Проверяем баланс
    balance = monitor.get_balance()

    if balance is not None:
        print(f"✅ Подключение успешно!")
        print(f"💰 Ваш баланс: {balance} ₽")
        return True
    else:
        print("❌ Ошибка подключения к API")
        return False


def test_get_purchases():
    """Тест 2: Получение списка купленных аккаунтов"""
    print("\n" + "=" * 60)
    print("ТЕСТ 2: Получение купленных аккаунтов Discord")
    print("=" * 60)

    API_TOKEN = os.environ.get("LZT_API_TOKEN", "")

    monitor = LZTMonitor(api_token=API_TOKEN)

    # Получаем купленные аккаунты Discord (category_id=22)
    accounts = monitor.get_purchased_accounts(category_id=22)

    if accounts:
        print(f"✅ Найдено купленных аккаунтов: {len(accounts)}")
        print("\nПоследние 5 покупок:")
        print("-" * 60)

        for i, acc in enumerate(accounts[:5], 1):
            print(f"\n{i}. ID: {acc.get('item_id', 'N/A')}")
            print(f"   Название: {acc.get('title', 'N/A')}")
            print(f"   Цена: {acc.get('price', 'N/A')} ₽")
            print(f"   Дата покупки: {acc.get('buy_date', 'N/A')}")

        return accounts
    else:
        print("⚠️ Купленных аккаунтов не найдено или ошибка API")
        return []


def test_account_details(item_id=None):
    """Тест 3: Получение детальной информации об аккаунте"""
    print("\n" + "=" * 60)
    print("ТЕСТ 3: Детальная информация об аккаунте")
    print("=" * 60)

    API_TOKEN = os.environ.get("LZT_API_TOKEN", "")

    monitor = LZTMonitor(api_token=API_TOKEN)

    # Если ID не передан, берем из последних покупок
    if not item_id:
        accounts = monitor.get_purchased_accounts(category_id=22)
        if accounts:
            item_id = accounts[0].get('item_id')
        else:
            print("❌ Нет купленных аккаунтов для проверки")
            return None

    print(f"Получаем детали для аккаунта ID: {item_id}")

    details = monitor.get_account_details(item_id)

    if details:
        print("✅ Информация получена!")
        print("\nДоступные поля в ответе:")
        print("-" * 60)

        # Выводим все ключи для анализа структуры
        for key in details.keys():
            value = details[key]
            if isinstance(value, str) and len(value) > 100:
                print(f"   {key}: {value[:100]}... (обрезано)")
            else:
                print(f"   {key}: {value}")

        return details
    else:
        print("❌ Не удалось получить детали аккаунта")
        return None


def test_account_goods(item_id=None):
    """Тест 4: Получение данных для входа (goods)"""
    print("\n" + "=" * 60)
    print("ТЕСТ 4: Получение данных для входа (goods)")
    print("=" * 60)

    API_TOKEN = os.environ.get("LZT_API_TOKEN", "")

    monitor = LZTMonitor(api_token=API_TOKEN)

    # Если ID не передан, берем из последних покупок
    if not item_id:
        accounts = monitor.get_purchased_accounts(category_id=22)
        if accounts:
            item_id = accounts[0].get('item_id')
        else:
            print("❌ Нет купленных аккаунтов для проверки")
            return None

    print(f"Получаем данные для входа (goods) для ID: {item_id}")

    goods = monitor.get_account_goods(item_id)

    if goods:
        print("✅ Данные получены!")
        print("\nДоступные поля в goods:")
        print("-" * 60)

        # Выводим все поля
        for key, value in goods.items():
            if isinstance(value, str) and len(value) > 100:
                print(f"   {key}: {value[:100]}... (длина: {len(value)})")
            else:
                print(f"   {key}: {value}")

        return goods
    else:
        print("❌ Не удалось получить данные для входа")
        return None


def test_token_extraction(item_id=None):
    """Тест 5: Извлечение токена из аккаунта"""
    print("\n" + "=" * 60)
    print("ТЕСТ 5: Извлечение Discord токена")
    print("=" * 60)

    API_TOKEN = os.environ.get("LZT_API_TOKEN", "")

    monitor = LZTMonitor(api_token=API_TOKEN)

    # Если ID не передан, берем из последних покупок
    if not item_id:
        accounts = monitor.get_purchased_accounts(category_id=22)
        if accounts:
            item_id = accounts[0].get('item_id')
        else:
            print("❌ Нет купленных аккаунтов для проверки")
            return None

    print(f"Извлекаем токен для аккаунта ID: {item_id}")

    token = monitor.extract_token_from_account(item_id)

    if token:
        print(f"✅ Токен успешно извлечен!")
        print(f"   Токен: {token[:20]}...{token[-10:]} (длина: {len(token)})")
        return token
    else:
        print("❌ Не удалось извлечь токен")
        print("⚠️ ВАЖНО: Проверьте вывод Теста 4 - там должны быть данные аккаунта")
        return None


def main():
    """Запуск всех тестов"""
    print("\n" + "🔥" * 30)
    print("ТЕСТИРОВАНИЕ LZT MONITOR")
    print("🔥" * 30)

    # Тест 1: Подключение
    if not test_connection():
        print("\n❌ Тесты прерваны - нет подключения к API")
        return

    # Тест 2: Получение покупок
    accounts = test_get_purchases()

    if not accounts:
        print("\n⚠️ Дальнейшие тесты пропущены - нет купленных аккаунтов")
        print("💡 Купите хотя бы один Discord аккаунт на LZT для тестирования")
        return

    # Тест 3: Детали аккаунта
    details = test_account_details()

    # Тест 4: Данные для входа (goods)
    goods = test_account_goods()

    # Тест 5: Извлечение токена
    test_token_extraction()

    print("\n" + "=" * 60)
    print("✅ ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ!")
    print("=" * 60)
    print("\n📋 Следующие шаги:")
    print("1. Проверьте вывод Теста 4 - там данные для входа в аккаунт")
    print("2. Если токен извлечен успешно - модуль готов к работе!")
    print("3. Если нет - найдите поле с токеном и сообщите мне")


if __name__ == "__main__":
    main()