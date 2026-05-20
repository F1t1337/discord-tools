import json
import logging
import os
import sys
import time
import threading
from pathlib import Path

# Добавляем путь к модулям
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.pipeline import TokenPipeline


def load_env_file(env_path: str = ".env"):
    """Загружает простые KEY=VALUE строки из .env без внешних зависимостей."""
    if not os.path.exists(env_path):
        return

    with open(env_path, 'r', encoding='utf-8') as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def resolve_env_placeholders(value):
    """Рекурсивно заменяет строки вида ${ENV_NAME} значениями окружения."""
    if isinstance(value, dict):
        return {k: resolve_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_placeholders(item) for item in value]
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


def load_config(config_path: str = "config.json") -> dict:
    """Загружает конфигурацию из JSON файла"""
    try:
        load_env_file()
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        config = resolve_env_placeholders(config)
        print(f"✅ Конфигурация загружена: {config_path}")
        return config
    except FileNotFoundError:
        print(f"❌ Файл конфигурации не найден: {config_path}")
        print("💡 Создайте файл config.json на основе примера")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка в файле конфигурации: {e}")
        sys.exit(1)


def setup_logging(config: dict):
    """Настраивает логирование"""
    # Создаем папку для логов
    log_file = config.get('logging', {}).get('file', 'data/logs/pipeline.log')
    log_dir = os.path.dirname(log_file)
    
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # Уровень логирования
    log_level = config.get('logging', {}).get('level', 'INFO')
    
    # Очищаем существующие handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Настройка логирования
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8', mode='a'),
            logging.StreamHandler()
        ],
        force=True
    )
    
    # Тестовое сообщение
    logger = logging.getLogger(__name__)
    logger.info("="*60)
    logger.info("СИСТЕМА ЗАПУЩЕНА")
    logger.info("="*60)
    
    print(f"✅ Логирование настроено: {log_file}")


def check_config(config: dict) -> bool:
    """Проверяет конфигурацию перед запуском"""
    errors = []
    
    # Проверка LZT
    if not config.get('lzt', {}).get('api_token'):
        errors.append("❌ LZT API token не настроен")
    
    # Проверка Telegram
    tg_token = config.get('telegram', {}).get('bot_token')
    tg_chat = config.get('telegram', {}).get('chat_id')
    
    if not tg_token or tg_token == "YOUR_BOT_TOKEN_HERE":
        errors.append("❌ Telegram bot_token не настроен")
    
    if not tg_chat or tg_chat == "YOUR_CHAT_ID_HERE":
        errors.append("❌ Telegram chat_id не настроен")

    # Проверка Sales API
    sales_config = config.get('sales', {})
    if sales_config.get('enabled') and sales_config.get('mode') == 'external_submit':
        sales_key_env = sales_config.get('api_key_env', 'SALES_API_KEY')
        if not os.environ.get(sales_key_env):
            errors.append(f"❌ Sales API key не настроен ({sales_key_env})")
    if sales_config.get('enabled') and sales_config.get('mode') == 'price_workflow':
        providers = sales_config.get('providers', {})
        for provider, default_env in {
            'tskupka': 'TSKUPKA_API_KEY',
            'tokenbuyrobot': 'TOKENBUYROBOT_API_KEY',
        }.items():
            key_env = providers.get(provider, {}).get('api_key_env', default_env)
            if not os.environ.get(key_env):
                errors.append(f"❌ {provider} API key не настроен ({key_env})")
    
    # Проверка папок
    db_path = config.get('database', {}).get('path', 'data/tokens.db')
    db_dir = os.path.dirname(db_path)
    
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
        print(f"✅ Создана папка для БД: {db_dir}")
    
    # Вывод ошибок
    if errors:
        print("\n" + "="*60)
        print("ОШИБКИ КОНФИГУРАЦИИ:")
        print("="*60)
        for error in errors:
            print(error)
        print("="*60)
        print("\n💡 Отредактируйте config.json перед запуском!")
        return False
    
    return True


def print_banner():
    """Выводит красивый баннер"""
    banner = """
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║         DISCORD TOKEN MANAGER - АВТОМАТИЗАЦИЯ            ║
║                                                           ║
║  LZT Monitor → Validator → Cleaner → Database → Telegram ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)


def print_status_info(config: dict, use_dashboard: bool = False):
    """Выводит информацию о конфигурации"""
    print("\n" + "="*60)
    print("КОНФИГУРАЦИЯ СИСТЕМЫ")
    print("="*60)
    print(f"📊 LZT проверка каждые:  {config['lzt']['check_interval']}с")
    print(f"⚠️  Минимальный баланс:   {config['lzt']['min_balance_alert']} ₽")
    print(f"✅ Validator потоков:    {config['validator']['max_workers']}")
    print(f"🧹 Cleaner потоков:      {config['cleaner']['max_workers']}")
    print(f"📤 Отправка токенов:     {config['telegram']['min_tokens']}-{config['telegram']['max_tokens']} шт")
    print(f"💾 База данных:          {config['database']['path']}")
    print(f"🔐 Прокси:               {'Включено' if config['proxy']['enabled'] else 'Выключено'}")
    sales_config = config.get('sales', {})
    print(f"🧾 Продажа:              {sales_config.get('mode', 'local_queue')}")
    
    if use_dashboard:
        dashboard_port = config.get('dashboard', {}).get('port', 5000)
        print(f"🌐 Web Dashboard:        http://localhost:{dashboard_port}")
    
    print("="*60)


def start_dashboard_server(pipeline, config):
    """Запускает dashboard сервер в отдельном потоке"""
    try:
        import dashboard_api
        
        # Инициализируем dashboard с pipeline
        dashboard_api.init_dashboard(pipeline, config)
        
        # Получаем настройки
        dashboard_config = config.get('dashboard', {})
        host = dashboard_config.get('host', '0.0.0.0')
        port = dashboard_config.get('port', 5000)
        
        # Запускаем сервер в отдельном потоке
        dashboard_thread = threading.Thread(
            target=dashboard_api.run_dashboard,
            args=(host, port),
            daemon=True,
            name="Dashboard Server"
        )
        dashboard_thread.start()
        
        print(f"\n✅ Web Dashboard запущен на http://{host}:{port}")
        print(f"🌐 Откройте в браузере: http://localhost:{port}")
        
        return dashboard_thread
        
    except ImportError:
        print("\n⚠️  Flask не установлен - Dashboard отключен")
        print("💡 Установите: pip install flask flask-cors")
        return None
    except Exception as e:
        print(f"\n❌ Ошибка запуска Dashboard: {e}")
        return None


def main():
    """Главная функция запуска"""
    # Баннер
    print_banner()
    
    # Загружаем конфигурацию
    config = load_config()
    
    # Проверяем конфигурацию
    if not check_config(config):
        sys.exit(1)
    
    # Настраиваем логирование
    setup_logging(config)
    
    # Проверяем режим запуска
    use_dashboard = '--dashboard' in sys.argv or config.get('dashboard', {}).get('enabled', False)
    use_live_stats = '--live-stats' in sys.argv or (not use_dashboard and config.get('live_stats', {}).get('enabled', True))
    
    # Выводим информацию
    print_status_info(config, use_dashboard)
    
    # Создаем Pipeline
    print("\n🚀 Инициализация Pipeline...")
    pipeline = TokenPipeline(config)
    
    # Запускаем
    print("▶️  Запуск автоматической обработки...\n")
    pipeline.start()
    
    print("\n✅ Pipeline успешно запущен!")
    print("📱 Проверьте Telegram - должно прийти уведомление")
    
    # Запускаем Dashboard и/или Live Stats
    dashboard_thread = None
    live_stats = None
    
    if use_dashboard:
        print("\n🌐 Запуск Web Dashboard...")
        time.sleep(2)
        dashboard_thread = start_dashboard_server(pipeline, config)
        
        if dashboard_thread:
            print("\n✅ Dashboard запущен! Откройте браузер.")
            print(f"   URL: http://localhost:{config.get('dashboard', {}).get('port', 5000)}")
        else:
            print("\n⚠️  Dashboard не запущен")
    
    # Live Stats работают всегда (если не отключены явно)
    if use_live_stats or (use_dashboard and config.get('live_stats', {}).get('enabled', True)):
        print("\n💡 Запуск Live Statistics через 3 секунды...")
        print("   Ctrl+C - остановить систему\n")
        time.sleep(3)
        
        # Создаем и запускаем Live Stats
        try:
            from utils.live_stats import PipelineLiveStats
            live_stats = PipelineLiveStats(pipeline, config)
            live_stats.start()
        except ImportError:
            print("⚠️  Live Stats недоступен")
            if not use_dashboard:
                print("💡 Установите utils/live_stats.py\n")
    
    try:
        # Держим программу запущенной
        # Live Stats сам выводит информацию, не мешаем ему
        while True:
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n\n⏹️  Получен сигнал остановки...")
        
        # Останавливаем Live Stats если запущены
        if live_stats:
            live_stats.stop()
        
        # Останавливаем Pipeline
        pipeline.stop()
        
        print("✅ Pipeline остановлен успешно!")
        print("👋 До встречи!\n")
    
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        
        # Останавливаем все
        if live_stats:
            live_stats.stop()
        pipeline.stop()
        
        sys.exit(1)


if __name__ == "__main__":
    main()
