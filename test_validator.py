import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.lzt_monitor import LZTMonitor
from modules.validator import TokenValidator, ValidationStats

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('validator_test.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzUxMiJ9.eyJzdWIiOjI2MTU0MjYsImlzcyI6Imx6dCIsImlhdCI6MTc2OTEyMjQzNSwianRpIjoiOTE3OTUwIiwic2NvcGUiOiJiYXNpYyByZWFkIHBvc3QgY29udmVyc2F0ZSBwYXltZW50IGludm9pY2UgY2hhdGJveCBtYXJrZXQiLCJleHAiOjE5MjY4MDI0MzV9.AFeSm1asGqoNbXtKUsMauLX5NljumPAcxjayGALPH3qinUWknODFSsvY5wUVpPku5-pZnuMWtYeXypy40RASSDZ4HrstgeWDmx8DFH11cDHUzPiOgl7r1YgrLSnwGreMA540GJQlC6fU2RolXGDwRp_uNuIZibh9RRzAYQlum3k"


def test_single_token():
    """Тест 1: Проверка одного токена"""
    print("\n" + "=" * 60)
    print("ТЕСТ 1: Проверка одного токена")
    print("=" * 60)

    monitor = LZTMonitor(api_token=API_TOKEN)
    accounts = monitor.get_purchased_accounts(category_id=22)

    if not accounts:
        print("❌ Нет купленных аккаунтов")
        return

    item_id = accounts[0].get('item_id')
    token = monitor.extract_token_from_account(item_id)

    if not token:
        print("❌ Не удалось получить токен")
        return

    print(f"Токен: {token[:20]}...{token[-10:]}")

    validator = TokenValidator()
    result = validator.validate_token(token)

    print("\n📋 Результат:")
    print(f"✅ Валидный:  {result['valid']}")
    print(f"👤 Username:  {result['username']}")
    print(f"❌ Ошибка:    {result['error']}")


def test_batch_validation():
    """Тест 2: Пакетная проверка токенов"""
    print("\n" + "=" * 60)
    print("ТЕСТ 2: Пакетная проверка токенов")
    print("=" * 60)

    monitor = LZTMonitor(api_token=API_TOKEN)
    accounts = monitor.get_purchased_accounts(category_id=22)

    if not accounts:
        print("❌ Нет купленных аккаунтов")
        return

    # Берем первые 10 токенов
    count = min(10, len(accounts))
    print(f"Получаем {count} токенов из LZT...\n")

    tokens = []
    for i, acc in enumerate(accounts[:count]):
        item_id = acc.get('item_id')
        token = monitor.extract_token_from_account(item_id)
        if token:
            tokens.append(token)
            print(f"  {i + 1}. {token[:20]}...")

    if not tokens:
        print("❌ Не удалось получить токены")
        return

    print(f"\n🔍 Проверяем {len(tokens)} токенов...\n")

    validator = TokenValidator(max_workers=5)
    stats = ValidationStats()

    def on_result(result):
        stats.update(result)
        status = "✅" if result['valid'] else "❌"
        info = result.get('username') or result.get('error')
        print(f"{status} {info}")

    validator.validate_tokens_batch(tokens, callback=on_result)
    stats.print_stats()


def test_mixed_tokens():
    """Тест 3: Смесь валидных и невалидных токенов"""
    print("\n" + "=" * 60)
    print("ТЕСТ 3: Смесь валидных и невалидных токенов")
    print("=" * 60)

    monitor = LZTMonitor(api_token=API_TOKEN)
    accounts = monitor.get_purchased_accounts(category_id=22)

    # Берем 2 валидных токена
    valid_tokens = []
    for acc in accounts[:2]:
        token = monitor.extract_token_from_account(acc.get('item_id'))
        if token:
            valid_tokens.append(token)

    # Добавляем невалидные
    mixed_tokens = valid_tokens + [
        "invalid_token_123456789",
        "MTAx.fake.token",
        "",
    ]

    print(f"Проверяем {len(mixed_tokens)} токенов (валидные + невалидные)...\n")

    validator = TokenValidator()
    stats = ValidationStats()

    def on_result(result):
        stats.update(result)
        status = "✅" if result['valid'] else "❌"
        info = result.get('username') or result.get('error')
        token_preview = result['token'][:20] if len(result['token']) > 20 else result['token']
        print(f"{status} {token_preview}... - {info}")

    validator.validate_tokens_batch(mixed_tokens, callback=on_result)
    stats.print_stats()


def main():
    print("\n" + "🔥" * 30)
    print("ТЕСТИРОВАНИЕ VALIDATOR MODULE")
    print("🔥" * 30)

    try:
        # Тест 1: Один токен
        test_single_token()

        # Тест 2: Пакетная проверка
        test_batch_validation()

        # Тест 3: Смешанные токены
        test_mixed_tokens()

        print("\n" + "=" * 60)
        print("✅ ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ!")
        print("=" * 60)
        print("\n💡 Validator готов к интеграции в pipeline!")

    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()