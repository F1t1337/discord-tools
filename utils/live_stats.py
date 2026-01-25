import os
import time
import threading
from typing import Dict
from modules.database import Database


class PipelineLiveStats:
    """Красивая live-статистика для Pipeline"""
    
    def __init__(self, pipeline, config: Dict):
        self.pipeline = pipeline
        self.config = config
        self.db = Database(config['database']['path'])
        self.running = False
        self.thread = None
        self.start_time = time.time()
        
        # Статистика по токенам
        self.tokens_processing = {}  # {token: {'username': str, 'stage': str, 'status': str}}
        self.lock = threading.Lock()
    
    def clear_console(self):
        """Очищает консоль"""
        os.system('cls' if os.name == 'nt' else 'clear')
    
    def get_pipeline_stats(self):
        """Получает статистику из pipeline"""
        status = self.pipeline.get_status()
        counts = self.db.count_tokens_by_status()
        today_stats = self.db.get_today_statistics()
        
        return {
            'queues': status['queues'],
            'threads': status['threads'],
            'counts': counts,
            'today': today_stats
        }
    
    def get_cleaning_tokens(self):
        """Получает список токенов в процессе очистки"""
        cleaning_tokens = self.db.get_tokens_by_status('cleaning')
        return cleaning_tokens[:10]  # Показываем первые 10
    
    def display_header(self, elapsed_time):
        """Отображает заголовок"""
        elapsed_str = time.strftime('%H:%M:%S', time.gmtime(elapsed_time))
        
        print("\n" + "═" * 100)
        print("║" + " " * 30 + "🚀 DISCORD TOKEN MANAGER - LIVE STATS" + " " * 30 + "║")
        print("║" + " " * 35 + f"⏱️  Runtime: {elapsed_str}" + " " * 35 + "║")
        print("═" * 100)
    
    def display_queues(self, stats):
        """Отображает статус очередей"""
        queues = stats['queues']
        counts = stats['counts']
        
        print("\n📊 КОНВЕЙЕР ОБРАБОТКИ (токены в процессе)")
        print("─" * 100)
        
        # Показываем реальное количество токенов на каждом этапе
        # а не размер очередей (которые пустые из-за быстрой обработки)
        
        max_width = 40
        
        stages = [
            ("📥 Ожидают валидации", counts.get('new', 0), "🟦"),
            ("✅ Проходят валидацию", counts.get('validated', 0), "🟩"),
            ("🧹 Очищаются", counts.get('cleaning', 0), "🟨"),
            ("🔍 Финальная проверка", counts.get('cleaned', 0), "🟧"),
            ("📦 Готовы к отправке", counts.get('ready', 0), "🟪"),
        ]
        
        for name, count, color in stages:
            # Создаем прогресс бар (максимум 40)
            filled = min(count, max_width)
            bar = color * filled + "⬜" * (max_width - filled)
            
            # Добавляем индикатор если токенов больше 40
            overflow = f" (+{count - max_width})" if count > max_width else ""
            
            print(f"{name:<30} [{bar}] {count:>4}{overflow}")
        
        print("─" * 100)
        print(f"💡 Очереди обрабатываются быстро, показаны реальные статусы токенов в БД")
        print("─" * 100)
    
    def display_status_counts(self, stats):
        """Отображает количество токенов по статусам"""
        counts = stats['counts']
        
        print("\n📈 СТАТИСТИКА ТОКЕНОВ")
        print("─" * 100)
        
        statuses = [
            ("🆕 Новые", counts.get('new', 0)),
            ("✅ Валидные", counts.get('validated', 0)),
            ("🧹 Очищаются", counts.get('cleaning', 0)),
            ("✨ Очищены", counts.get('cleaned', 0)),
            ("📦 Готовы", counts.get('ready', 0)),
            ("📤 Отправлены", counts.get('sent', 0)),
            ("❌ Невалидные", counts.get('invalid', 0)),
        ]
        
        # Выводим в 2 колонки
        for i in range(0, len(statuses), 2):
            left = statuses[i]
            right = statuses[i+1] if i+1 < len(statuses) else ("", 0)
            
            print(f"  {left[0]:<20} {left[1]:>5}     {right[0]:<20} {right[1]:>5}")
        
        print("─" * 100)
    
    def display_cleaning_progress(self, cleaning_tokens):
        """Отображает прогресс очистки токенов"""
        if not cleaning_tokens:
            print("\n🧹 ПРОЦЕСС ОЧИСТКИ")
            print("─" * 100)
            print("  Нет токенов в процессе очистки...")
            print("─" * 100)
            return
        
        print("\n🧹 ПРОЦЕСС ОЧИСТКИ (активные токены)")
        print("─" * 100)
        print(f"{'№':<4} {'Username':<20} {'Item ID':<12} {'Цена':<10} {'Действие':<50}")
        print("─" * 100)
        
        for i, token_data in enumerate(cleaning_tokens, 1):
            username = token_data.get('username', 'Unknown')[:18] or 'Unknown'
            item_id = token_data.get('lzt_item_id', 'N/A')
            price = token_data.get('price', 0)
            
            # Получаем прогресс очистки из БД
            progress = token_data.get('cleaning_progress', '')
            
            if progress:
                # Убираем эмодзи из прогресса для чистоты
                status = progress
            else:
                # Анимированный статус по умолчанию
                animation = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
                anim_char = animation[int(time.time() * 3) % len(animation)]
                status = f"{anim_char} Очистка каналов..."
            
            # Обрезаем статус если он слишком длинный
            if len(status) > 48:
                status = status[:45] + "..."
            
            print(f"{i:<4} {username:<20} {item_id!s:<12} {price:>6.0f} ₽   {status:<50}")
        
        if len(cleaning_tokens) >= 10:
            print("  ... и еще больше")
        
        print("─" * 100)
    
    def display_today_stats(self, stats):
        """Отображает статистику за сегодня"""
        today = stats['today']
        
        print("\n💰 СТАТИСТИКА ЗА СЕГОДНЯ")
        print("─" * 100)
        
        stats_items = [
            ("📥 Куплено токенов", today.get('tokens_bought', 0)),
            ("✅ Валидных", today.get('tokens_valid', 0)),
            ("🧹 Очищенных", today.get('tokens_cleaned', 0)),
            ("📤 Отправлено", today.get('tokens_sent', 0)),
            ("💵 Потрачено", f"{today.get('money_spent', 0):.2f} ₽"),
            ("📈 Success Rate", f"{today.get('success_rate', 0):.1f}%"),
        ]
        
        # Выводим в 2 колонки
        for i in range(0, len(stats_items), 2):
            left = stats_items[i]
            right = stats_items[i+1] if i+1 < len(stats_items) else ("", "")
            
            print(f"  {left[0]:<25} {str(left[1]):>10}     {right[0]:<25} {str(right[1]):>10}")
        
        print("─" * 100)
    
    def display_threads_status(self, stats):
        """Отображает статус потоков"""
        threads = stats['threads']
        counts = stats['counts']
        
        print("\n🔧 СТАТУС ПОТОКОВ (реальная активность)")
        print("─" * 100)
        
        # Рассчитываем реальную активность на основе статусов токенов
        stages = [
            ("🔍 LZT Monitor", 1, 1, True),  # Всегда активен
            ("✅ Validator #1", 20, counts.get('new', 0) + counts.get('validated', 0), counts.get('new', 0) > 0),
            ("🧹 Cleaner", 20, counts.get('cleaning', 0), counts.get('cleaning', 0) > 0),
            ("✅ Validator #2", 20, counts.get('cleaned', 0), counts.get('cleaned', 0) > 0),
            ("📤 Telegram Sender", 1, 1, counts.get('ready', 0) >= 30),
            ("📊 Statistics", 1, 1, True),  # Всегда активен
        ]
        
        for name, max_threads, processing_count, is_working in stages:
            # Определяем сколько потоков реально работают
            active_threads = min(processing_count, max_threads) if is_working else 0
            
            # Статус
            if active_threads == 0:
                status = "🟡"  # Ожидание
                status_text = "ожидание"
            elif active_threads < max_threads:
                status = "🟢"  # Частично активен
                status_text = "работает"
            else:
                status = "🟢"  # Полностью активен
                status_text = "работает"
            
            # Прогресс бар
            if max_threads > 1:
                bar_width = 20
                filled = int((active_threads / max_threads) * bar_width)
                bar = "█" * filled + "░" * (bar_width - filled)
                activity = f"[{bar}] {active_threads}/{max_threads}"
            else:
                activity = "●" if is_working else "○"
            
            print(f"  {status} {name:<25} {activity:<30} {status_text}")
        
        print("─" * 100)
    
    def display(self):
        """Отображает всю статистику"""
        self.clear_console()
        
        elapsed_time = time.time() - self.start_time
        
        # Получаем статистику
        stats = self.get_pipeline_stats()
        cleaning_tokens = self.get_cleaning_tokens()
        
        # Отображаем
        self.display_header(elapsed_time)
        self.display_queues(stats)
        self.display_status_counts(stats)
        self.display_cleaning_progress(cleaning_tokens)
        self.display_today_stats(stats)
        self.display_threads_status(stats)
        
        # Футер
        print("\n" + "═" * 100)
        print("║" + " " * 35 + "💡 Нажмите Ctrl+C для остановки" + " " * 35 + "║")
        print("═" * 100)
    
    def _display_worker(self):
        """Поток отображения статистики"""
        while self.running:
            try:
                self.display()
                time.sleep(2)  # Обновление каждые 2 секунды
            except Exception as e:
                print(f"Ошибка отображения: {e}")
                time.sleep(2)
    
    def start(self):
        """Запускает отображение статистики"""
        if self.running:
            return
        
        self.running = True
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._display_worker, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Останавливает отображение статистики"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)


# Пример интеграции в main.py
if __name__ == "__main__":
    # В main.py после создания pipeline:
    
    # pipeline = TokenPipeline(config)
    # pipeline.start()
    
    # # Создаем и запускаем live stats
    # live_stats = PipelineLiveStats(pipeline, config)
    # live_stats.start()
    
    # try:
    #     while True:
    #         time.sleep(1)
    # except KeyboardInterrupt:
    #     live_stats.stop()
    #     pipeline.stop()
    pass