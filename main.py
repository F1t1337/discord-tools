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


from modules.configuration import PROJECT_ROOT, load_env_file, resolve_env_placeholders, load_config as read_config
from modules.access_control import dashboard_settings, telegram_owner_ids


def load_config(config_path=None) -> dict:
    try:
        return read_config(config_path)
    except (OSError, ValueError) as exc:
        print(f"Ошибка конфигурации: {type(exc).__name__}")
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

    try:
        telegram_owner_ids(config.get('telegram', {}).get('allowed_user_ids'), tg_chat)
        if '--dashboard' in sys.argv or config.get('dashboard', {}).get('enabled'):
            dashboard_settings(config)
    except ValueError as exc:
        errors.append(str(exc))

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
    print(f"💾 База данных:          {config['database']['path']}")
    print(f"🔐 Прокси:               {'Включено' if config['proxy']['enabled'] else 'Выключено'}")
    
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
        host = dashboard_config.get('host', '127.0.0.1')
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
        print("\n⚠️  Зависимости панели не установлены")
        print("💡 Установите: pip install -r requirements.txt")
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
    pipeline = TokenPipeline(config, config_path=str(PROJECT_ROOT / 'config.json'))
    
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
