import sys
import os
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.database import Database
from modules.lzt_monitor import LZTMonitor

API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzUxMiJ9.eyJzdWIiOjI2MTU0MjYsImlzcyI6Imx6dCIsImlhdCI6MTc2OTE5ODQ5NSwianRpIjoiOTE4MzM5Iiwic2NvcGUiOiJiYXNpYyByZWFkIHBvc3QgY29udmVyc2F0ZSBwYXltZW50IGludm9pY2UgY2hhdGJveCBtYXJrZXQiLCJleHAiOjE5MjY4Nzg0OTV9.h-4coumbxAbNnGdRzQhPmb8UOYxxICVm_kB-vrZlXhq_uEl44dPkH9gQi1KldJq5s2u-mez7sAStCcJHzws06Onxw2iSdtieU3KSuCyixLcryt3MXwxXTsBb45lLSrzOG5kJno_25jo4yMbPzB3SzAUIK0r05IDKVN3zjKeFijc"


def test_basic_operations():
    """Тест 1: Базовые операции с токенами"""
    print("\n" + "="*60)
    print("ТЕСТ 1: Базовые операции с токенами")
    print("="*60)
    
    db = Database("test_tokens.db")
    
    # Добавляем токены
    print("\n📝 Добавление тестовых токенов...")
    token1 = "MTAx.test.token_123_" + str(int(time.time()))
    token2 = "MTAy.test.token_456_" + str(int(time.time()))
    
    id1 = db.add_token(token1, lzt_item_id=12345, price=25.0)
    id2 = db.add_token(token2, lzt_item_id=12346, price=30.0)
    
    print(f"✅ Токен 1 добавлен, ID: {id1}")
    print(f"✅ Токен 2 добавлен, ID: {id2}")
    
    # Проверяем дубликат
    print("\n🔄 Попытка добавить дубликат...")
    duplicate_id = db.add_token(token1, lzt_item_id=12345, price=25.0)
    if duplicate_id is None:
        print("✅ Дубликат правильно отклонен")
    
    # Получаем информацию о токене
    print("\n📊 Информация о токене:")
    info = db.get_token_info(token1)
    print(f"  Status: {info['status']}")
    print(f"  Price: {info['price']} ₽")
    print(f"  LZT ID: {info['lzt_item_id']}")
    
    # Обновляем статус
    print("\n🔄 Обновление статусов...")
    db.update_token_status(token1, "validated", username="TestUser#1234")
    db.update_token_status(token2, "invalid", error="Token locked")
    
    info1 = db.get_token_info(token1)
    info2 = db.get_token_info(token2)
    
    print(f"✅ Токен 1: {info1['status']} - {info1['username']}")
    print(f"❌ Токен 2: {info2['status']} - {info2['error']}")
    
    # Подсчет по статусам
    print("\n📈 Статистика по статусам:")
    counts = db.count_tokens_by_status()
    for status, count in counts.items():
        print(f"  {status}: {count}")


def test_real_tokens_workflow():
    """Тест 2: Реальный workflow с токенами из LZT"""
    print("\n" + "="*60)
    print("ТЕСТ 2: Workflow с реальными токенами из LZT")
    print("="*60)
    
    db = Database("test_tokens.db")
    monitor = LZTMonitor(api_token=API_TOKEN)
    
    # Получаем токены из LZT
    print("\n📦 Получение токенов из LZT...")
    accounts = monitor.get_purchased_accounts(category_id=22)
    
    if not accounts:
        print("❌ Нет купленных аккаунтов")
        return
    
    # Берем первые 5
    count = min(5, len(accounts))
    print(f"Обрабатываем {count} токенов...\n")
    
    for i, acc in enumerate(accounts[:count], 1):
        item_id = acc.get('item_id')
        price = acc.get('price', 0)
        
        # Получаем токен
        token = monitor.extract_token_from_account(item_id)
        
        if not token:
            print(f"⚠️ {i}. Не удалось получить токен для {item_id}")
            continue
        
        # Добавляем в БД
        token_db_id = db.add_token(token, lzt_item_id=item_id, price=price)
        
        if token_db_id:
            print(f"✅ {i}. Токен добавлен в БД: {token[:20]}... (ID: {token_db_id})")
        else:
            print(f"⚠️ {i}. Токен уже есть в БД: {token[:20]}...")
    
    # Получаем все новые токены
    print("\n📋 Токены со статусом 'new':")
    new_tokens = db.get_tokens_by_status('new')
    print(f"  Найдено: {len(new_tokens)} токенов")
    
    for token_data in new_tokens[:3]:
        print(f"  - {token_data['token'][:20]}... | Price: {token_data['price']} ₽")


def test_statistics():
    """Тест 3: Работа со статистикой"""
    print("\n" + "="*60)
    print("ТЕСТ 3: Статистика")
    print("="*60)
    
    db = Database("test_tokens.db")
    
    # Обновляем статистику
    print("\n💰 Обновление статистики за сегодня...")
    db.update_statistics(
        tokens_bought=10,
        tokens_valid=7,
        tokens_cleaned=5,
        tokens_sent=4,
        money_spent=250.0
    )
    
    # Получаем статистику за сегодня
    print("\n📊 Статистика за сегодня:")
    today = db.get_today_statistics()
    print(f"  📥 Куплено токенов:     {today['tokens_bought']}")
    print(f"  ✅ Валидных:            {today['tokens_valid']}")
    print(f"  🧹 Очищенных:           {today['tokens_cleaned']}")
    print(f"  📤 Отправлено:          {today['tokens_sent']}")
    print(f"  💵 Потрачено:           {today['money_spent']} ₽")
    print(f"  📈 Success Rate:        {today['success_rate']:.1f}%")
    
    # Еще раз обновляем
    print("\n🔄 Добавляем еще данных...")
    db.update_statistics(
        tokens_bought=5,
        tokens_valid=3,
        money_spent=125.0
    )
    
    # Проверяем обновление
    today = db.get_today_statistics()
    print(f"  📥 Теперь куплено:      {today['tokens_bought']}")
    print(f"  ✅ Теперь валидных:     {today['tokens_valid']}")
    print(f"  💵 Теперь потрачено:    {today['money_spent']} ₽")
    
    # Общая статистика
    print("\n🌍 Общая статистика за все время:")
    total = db.get_total_statistics()
    if total and total.get('total_bought'):
        print(f"  📥 Всего куплено:       {int(total['total_bought'])}")
        print(f"  ✅ Всего валидных:      {int(total['total_valid'])}")
        print(f"  📤 Всего отправлено:    {int(total['total_sent'])}")
        print(f"  💵 Всего потрачено:     {total['total_money_spent']:.2f} ₽")
        print(f"  📈 Средний Success:     {total['avg_success_rate']:.1f}%")


def test_logs():
    """Тест 4: Работа с логами"""
    print("\n" + "="*60)
    print("ТЕСТ 4: Логи")
    print("="*60)
    
    db = Database("test_tokens.db")
    
    # Добавляем логи
    print("\n📝 Добавление логов...")
    db.add_log("INFO", "lzt_monitor", "Найдена новая покупка")
    db.add_log("INFO", "validator", "Токен успешно проверен")
    db.add_log("WARNING", "cleaner", "Не удалось удалить некоторые сообщения")
    db.add_log("ERROR", "telegram", "Ошибка отправки в Telegram")
    db.add_log("INFO", "database", "Статистика обновлена")
    
    print("✅ Добавлено 5 логов")
    
    # Получаем все логи
    print("\n📋 Последние 10 логов:")
    logs = db.get_recent_logs(limit=10)
    for log in logs:
        level_icon = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌"}.get(log['level'], "📝")
        print(f"  {level_icon} [{log['module']}] {log['message']}")
    
    # Фильтр по уровню
    print("\n❌ Только ошибки:")
    errors = db.get_recent_logs(limit=10, level="ERROR")
    for log in errors:
        print(f"  [{log['module']}] {log['message']}")


def test_ready_tokens():
    """Тест 5: Получение готовых токенов"""
    print("\n" + "="*60)
    print("ТЕСТ 5: Готовые токены для отправки")
    print("="*60)
    
    db = Database("test_tokens.db")
    
    # Создаем несколько готовых токенов
    print("\n📝 Создание готовых токенов...")
    for i in range(3):
        token = f"READY.token.{i}_{int(time.time())}"
        token_id = db.add_token(token, price=25.0)
        if token_id:
            db.update_token_status(token, "ready", username=f"ReadyUser{i}")
            print(f"  ✅ Готовый токен {i+1} создан")
    
    # Получаем готовые токены
    print("\n📤 Получение готовых токенов для отправки...")
    ready = db.get_ready_tokens(limit=50)
    
    print(f"Найдено готовых токенов: {len(ready)}")
    for token_data in ready[:5]:
        print(f"  - {token_data['username']}: {token_data['token'][:20]}...")


def main():
    print("\n" + "🔥"*30)
    print("ТЕСТИРОВАНИЕ DATABASE MODULE")
    print("🔥"*30)
    
    try:
        # Тест 1: Базовые операции
        test_basic_operations()
        
        # Тест 2: Реальные токены из LZT
        test_real_tokens_workflow()
        
        # Тест 3: Статистика
        test_statistics()
        
        # Тест 4: Логи
        test_logs()
        
        # Тест 5: Готовые токены
        test_ready_tokens()
        
        print("\n" + "="*60)
        print("✅ ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ!")
        print("="*60)
        print("\n💡 Database готова к использованию!")
        print("📁 База данных: test_tokens.db")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()