import sys
import os
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.lzt_monitor import LZTMonitor
from modules.validator import TokenValidator

API_TOKEN = os.environ.get("LZT_API_TOKEN", "")


def test_single_token():
    """Тест 1: Проверка одного токена"""
    print("\n" + "="*60)
    print("ТЕСТ 1: Проверка одного токена")
    print("="*60)
    
    # Получаем токен из LZT
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
    
    # Проверяем токен
    validator = TokenValidator()
    is_valid, username = validator.validate_token(token)
    
    print("\n📋 Результат:")
    if is_valid:
        print(f"✅ Токен валиден")
        print(f"👤 Username: {username}")
    else:
        print(f"❌ Токен невалиден")
    
    validator.close()


def test_multiple_tokens():
    """Тест 2: Проверка нескольких токенов"""
    print("\n" + "="*60)
    print("ТЕСТ 2: Проверка нескольких токенов")
    print("="*60)
    
    # Получаем токены из LZT
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
            print(f"  {i+1}. {token[:20]}...")
    
    if not tokens:
        print("❌ Не удалось получить токены")
        return
    
    print(f"\n🔍 Проверяем {len(tokens)} токенов...\n")
    
    # Проверяем токены
    validator = TokenValidator()
    
    valid_count = 0
    invalid_count = 0
    start_time = time.time()
    
    for i, token in enumerate(tokens, 1):
        is_valid, username = validator.validate_token(token)
        
        if is_valid:
            print(f"✅ {i}. {username}")
            valid_count += 1
        else:
            print(f"❌ {i}. Невалидный токен")
            invalid_count += 1
        
        # Небольшая задержка между проверками
        time.sleep(0.5)
    
    elapsed = time.time() - start_time
    
    # Статистика
    print("\n" + "="*60)
    print("📊 СТАТИСТИКА")
    print("="*60)
    print(f"Всего проверено:     {len(tokens)}")
    print(f"✅ Валидные:         {valid_count} ({valid_count/len(tokens)*100:.1f}%)")
    print(f"❌ Невалидные:       {invalid_count}")
    print(f"⏱️ Время:            {elapsed:.1f}с")
    print(f"⚡ Скорость:         {len(tokens)/elapsed:.1f} токенов/сек")
    print("="*60)
    
    validator.close()


def test_invalid_tokens():
    """Тест 3: Проверка заведомо невалидных токенов"""
    print("\n" + "="*60)
    print("ТЕСТ 3: Проверка невалидных токенов")
    print("="*60)
    
    invalid_tokens = [
        ("Короткий токен", "invalid123"),
        ("Пустой токен", ""),
        ("Фейковый токен", "MTAx.invalid.token_that_is_long_enough_to_pass_length_check_1234567890"),
    ]
    
    validator = TokenValidator()
    
    for name, token in invalid_tokens:
        print(f"\n{name}: {token[:30]}...")
        is_valid, username = validator.validate_token(token)
        
        if is_valid:
            print(f"  ✅ Валиден (неожиданно!): {username}")
        else:
            print(f"  ❌ Невалиден (ожидаемо)")
    
    validator.close()


def test_mixed_tokens():
    """Тест 4: Смесь валидных и невалидных токенов"""
    print("\n" + "="*60)
    print("ТЕСТ 4: Смесь валидных и невалидных токенов")
    print("="*60)
    
    # Получаем 2 валидных токена из LZT
    monitor = LZTMonitor(api_token=API_TOKEN)
    accounts = monitor.get_purchased_accounts(category_id=22)
    
    valid_tokens = []
    for acc in accounts[:2]:
        token = monitor.extract_token_from_account(acc.get('item_id'))
        if token:
            valid_tokens.append(token)
    
    # Создаем смесь
    mixed_tokens = valid_tokens + [
        "invalid_token_123456789_abcdefghijklmnopqrstuvwxyz_long_enough",
        "MTAx.fake.token_1234567890_abcdefghijklmnopqrstuvwxyz",
    ]
    
    print(f"Проверяем {len(mixed_tokens)} токенов (валидные + невалидные)...\n")
    
    validator = TokenValidator()
    
    valid_count = 0
    invalid_count = 0
    
    for i, token in enumerate(mixed_tokens, 1):
        is_valid, username = validator.validate_token(token)
        
        token_preview = token[:20] if len(token) > 20 else token
        
        if is_valid:
            print(f"✅ {i}. {token_preview}... → {username}")
            valid_count += 1
        else:
            print(f"❌ {i}. {token_preview}... → Невалиден")
            invalid_count += 1
        
        time.sleep(0.5)
    
    print(f"\n📊 Результат: ✅ {valid_count} валидных, ❌ {invalid_count} невалидных")
    
    validator.close()


def main():
    print("\n" + "🔥"*30)
    print("ТЕСТИРОВАНИЕ НОВОГО VALIDATOR MODULE")
    print("🔥"*30)
    
    try:
        # Тест 1: Один токен
        test_single_token()
        
        # Тест 2: Несколько токенов
        test_multiple_tokens()
        
        # Тест 3: Невалидные токены
        test_invalid_tokens()
        
        # Тест 4: Смешанные токены
        test_mixed_tokens()
        
        print("\n" + "="*60)
        print("✅ ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ!")
        print("="*60)
        print("\n💡 Validator готов к использованию!")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()