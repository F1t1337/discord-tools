import requests
import time
import random
import concurrent.futures
import logging
import base64
import os
import threading
from typing import List, Dict, Optional, Tuple

# Конфигурация
TOKENS_FILE = "tokens.txt"
PROXIES_FILE = "proxies.txt"
MAX_TOKENS_WORKERS = 20  # Фактическое ограничение на потоки
MAX_MESSAGES_TO_DELETE = 20  # Максимум сообщений для удаления
BASE_DELAY = 1.0
MIN_DELAY = 0.8
TIMEOUT = 10
TEST_URL = "https://discord.com/api/v9/users/@me"
UPDATE_INTERVAL = 0.5  # Интервал обновления дисплея (секунд)

# Настройка логирования
logging.basicConfig(
    level=logging.WARNING,
    format="%(message)s",
    handlers=[
        logging.FileHandler("discord_cleaner.log", encoding='utf-8'),
    ]
)
logger = logging.getLogger(__name__)


class ProgressTracker:
    """Класс для отслеживания прогресса всех потоков"""

    def __init__(self, max_workers: int):
        self.stats = {}
        self.max_workers = max_workers
        self.start_time = time.time()
        self.completed_tokens = 0
        self.total_tokens = 0
        self.lock = threading.Lock()

    def add_token(self, token: str, username: str):
        with self.lock:
            short_token = token[:6] + "..." + token[-4:] if len(token) > 10 else token
            self.stats[short_token] = {
                'username': username[:15],
                'processed_channels': 0,
                'deleted_messages': 0,
                'closed_channels': 0,
                'status': '🟡 Инициализация',
                'active': True
            }

    def update_status(self, token: str, status: str,
                      processed_channels: int = None,
                      deleted_messages: int = None,
                      closed_channels: int = None):
        with self.lock:
            short_token = token[:6] + "..." + token[-4:] if len(token) > 10 else token
            if short_token in self.stats:
                if processed_channels is not None:
                    self.stats[short_token]['processed_channels'] = processed_channels
                if deleted_messages is not None:
                    self.stats[short_token]['deleted_messages'] = deleted_messages
                if closed_channels is not None:
                    self.stats[short_token]['closed_channels'] = closed_channels
                self.stats[short_token]['status'] = status

    def mark_completed(self, token: str):
        with self.lock:
            short_token = token[:6] + "..." + token[-4:] if len(token) > 10 else token
            if short_token in self.stats:
                self.stats[short_token]['active'] = False
                self.completed_tokens += 1

    def set_total_tokens(self, total: int):
        with self.lock:
            self.total_tokens = total

    def get_stats(self):
        with self.lock:
            return self.stats.copy(), self.completed_tokens, self.total_tokens, time.time() - self.start_time


def clear_console():
    """Очищает консоль"""
    os.system('cls' if os.name == 'nt' else 'clear')


def display_progress(progress_tracker: ProgressTracker):
    """Отображает прогресс в консоли"""
    stats, completed, total, elapsed_time = progress_tracker.get_stats()

    # Рассчитываем общую статистику
    total_channels = sum(s['processed_channels'] for s in stats.values())
    total_messages = sum(s['deleted_messages'] for s in stats.values())
    total_closed = sum(s['closed_channels'] for s in stats.values())
    active_count = sum(1 for s in stats.values() if s['active'])

    # Рассчитываем прогресс
    progress_percent = (completed / total * 100) if total > 0 else 0
    progress_bar_length = 40
    filled = int(progress_bar_length * progress_percent / 100)
    progress_bar = '█' * filled + '░' * (progress_bar_length - filled)

    # Форматируем время
    elapsed_str = time.strftime('%H:%M:%S', time.gmtime(elapsed_time))

    # Отображаем
    print("\n" + "═" * 80)
    print(
        f"📊 DISCORD CLEANER PRO | Активных: {active_count}/{progress_tracker.max_workers} | Завершено: {completed}/{total}")
    print(f"⏱️  Время: {elapsed_str} | 📈 Прогресс: {progress_percent:.1f}%")
    print(f"{progress_bar} {progress_percent:.1f}%")
    print("─" * 80)
    print(f"{'Токен':<12} {'Пользователь':<15} {'Каналы':<12} {'Сообщения':<12} {'Закрыто':<10} {'Статус':<15}")
    print("─" * 80)

    # Сортируем по активности (активные сверху)
    sorted_stats = sorted(stats.items(), key=lambda x: (not x[1]['active'], x[0]))

    for token_short, data in sorted_stats:
        channels_str = f"📁 {data['processed_channels']}"
        messages_str = f"🗑️ {data['deleted_messages']}"
        closed_str = f"🚫 {data['closed_channels']}"

        # Добавляем индикатор активности
        active_indicator = "▶️" if data['active'] else "✅"

        print(f"{active_indicator} {token_short:<10} {data['username']:<15} "
              f"{channels_str:<12} {messages_str:<12} {closed_str:<10} {data['status']:<15}")

    print("─" * 80)
    print(f"📈 ИТОГО: 📁 {total_channels} каналов | 🗑️ {total_messages} сообщений | 🚫 {total_closed} закрыто")
    print("═" * 80)


def display_worker(progress_tracker: ProgressTracker, stop_event: threading.Event):
    """Поток для отображения прогресса"""
    while not stop_event.is_set():
        clear_console()
        display_progress(progress_tracker)

        # Проверяем все ли завершены
        _, completed, total, _ = progress_tracker.get_stats()
        if completed >= total and total > 0:
            break

        # Ждем 5 секунд до следующего обновления
        stop_event.wait(UPDATE_INTERVAL)


class ProxyManager:
    def __init__(self):
        self._valid_proxies = None
        self.test_proxies_once()

    def load_proxies(self) -> List[str]:
        """Загружает прокси из файла"""
        try:
            with open(PROXIES_FILE, "r") as f:
                proxies = []
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        proxies.append(line)
                return proxies
        except Exception as e:
            return []

    def parse_proxy(self, proxy_str: str) -> Dict:
        """Парсит строку прокси в словарь с параметрами"""
        proxy_dict = {
            "original": proxy_str,
            "http": None,
            "https": None,
            "auth": None
        }

        try:
            proxy_str = proxy_str.replace('http://', '').replace('https://', '')

            if '@' in proxy_str:
                auth_part, server_part = proxy_str.split('@', 1)
                if ':' in auth_part:
                    login, password = auth_part.split(':', 1)
                    proxy_dict['auth'] = (login, password)

                if ':' in server_part:
                    host, port = server_part.split(':', 1)
                    proxy_url = f"http://{host}:{port}"
                    proxy_dict['http'] = proxy_url
                    proxy_dict['https'] = proxy_url
            else:
                parts = proxy_str.split(':')
                if len(parts) == 2:
                    host, port = parts
                    proxy_url = f"http://{host}:{port}"
                    proxy_dict['http'] = proxy_url
                    proxy_dict['https'] = proxy_url
                elif len(parts) == 4:
                    host, port, login, password = parts
                    proxy_dict['auth'] = (login, password)
                    proxy_url = f"http://{host}:{port}"
                    proxy_dict['http'] = proxy_url
                    proxy_dict['https'] = proxy_url

        except Exception:
            pass

        return proxy_dict

    def test_proxy(self, proxy_dict: Dict) -> Tuple[bool, Optional[int]]:
        """Тестирует прокси"""
        proxies = {}
        if proxy_dict['http']:
            proxies = {"http": proxy_dict['http'], "https": proxy_dict['http']}

        try:
            start = time.time()
            session = requests.Session()
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })

            if proxy_dict['auth']:
                login, password = proxy_dict['auth']
                auth_str = f"{login}:{password}"
                encoded_auth = base64.b64encode(auth_str.encode()).decode()
                session.headers['Proxy-Authorization'] = f'Basic {encoded_auth}'

            response = session.get(
                TEST_URL,
                proxies=proxies,
                timeout=5
            )

            if response.status_code == 200:
                ping = int((time.time() - start) * 1000)
                return True, ping

        except:
            pass

        return False, None

    def test_proxies_once(self):
        """Тестирует все прокси один раз"""
        if self._valid_proxies is not None:
            return

        proxy_strings = self.load_proxies()
        if not proxy_strings:
            self._valid_proxies = []
            return

        valid_proxies = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(self.test_proxy, self.parse_proxy(proxy)): proxy for proxy in proxy_strings}
            for future in concurrent.futures.as_completed(futures):
                proxy_str = futures[future]
                try:
                    is_valid, ping = future.result()
                    if is_valid:
                        proxy_dict = self.parse_proxy(proxy_str)
                        valid_proxies.append((proxy_dict, ping))
                except:
                    pass

        valid_proxies.sort(key=lambda x: x[1])
        self._valid_proxies = [p[0] for p in valid_proxies]

    def get_random_proxy(self) -> Optional[Dict]:
        """Возвращает случайный валидный прокси"""
        if not self._valid_proxies:
            return None

        proxy_dict = random.choice(self._valid_proxies)
        proxies = {}

        if proxy_dict['http']:
            proxies = {"http": proxy_dict['http'], "https": proxy_dict['http']}

        return {
            'proxies': proxies,
            'auth': proxy_dict.get('auth')
        }


class RateLimiter:
    def __init__(self):
        self.last_request = 0
        self.rate_limit_reset = 0
        self.remaining_requests = 1

    def wait(self):
        current_time = time.time()

        if current_time < self.rate_limit_reset:
            wait_time = self.rate_limit_reset - current_time + 0.1
            time.sleep(wait_time)
            current_time = time.time()

        elapsed = current_time - self.last_request
        wait_time = max(0, MIN_DELAY - elapsed)

        if wait_time > 0:
            time.sleep(wait_time)

        self.last_request = time.time()

    def update_from_headers(self, headers: Dict):
        """Обновляет лимиты из заголовков ответа"""
        try:
            remaining = headers.get('X-RateLimit-Remaining')
            reset = headers.get('X-RateLimit-Reset-After')

            if remaining and reset:
                self.remaining_requests = int(remaining)
                self.rate_limit_reset = time.time() + float(reset)

                if self.remaining_requests <= 2:
                    extra_delay = random.uniform(0.5, 1.5)
                    time.sleep(extra_delay)

        except:
            pass


class DiscordAPI:
    def __init__(self, proxy_manager: ProxyManager, progress_tracker: ProgressTracker):
        self.rate_limiter = RateLimiter()
        self.proxy_manager = proxy_manager
        self.progress = progress_tracker

    def create_session_with_proxy(self, proxy_info: Dict):
        """Создает сессию с настройками прокси"""
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        if proxy_info and 'proxies' in proxy_info:
            session.proxies = proxy_info['proxies']

            if proxy_info.get('auth'):
                login, password = proxy_info['auth']
                auth_str = f"{login}:{password}"
                encoded_auth = base64.b64encode(auth_str.encode()).decode()
                session.headers['Proxy-Authorization'] = f'Basic {encoded_auth}'

        return session

    def safe_request(self, method: str, url: str, headers: Dict, max_retries: int = 3) -> Optional[requests.Response]:
        for attempt in range(max_retries):
            self.rate_limiter.wait()
            proxy_info = self.proxy_manager.get_random_proxy()

            try:
                session = self.create_session_with_proxy(proxy_info)

                response = session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=TIMEOUT
                )

                self.rate_limiter.update_from_headers(response.headers)

                if response.status_code == 429:
                    try:
                        data = response.json()
                        retry_after = float(data.get('retry_after', random.uniform(1, 3)))
                        time.sleep(retry_after + 0.1)
                        continue
                    except:
                        time.sleep(random.uniform(2, 5))
                        continue

                return response

            except:
                if attempt == max_retries - 1:
                    return None
                time.sleep(2 ** attempt)

        return None


def check_message_has_links_or_attachments(msg: Dict) -> bool:
    """Проверяет, содержит ли сообщение ссылки или вложения"""
    if not isinstance(msg, dict):
        return False

    content = msg.get('content', '').lower()
    attachments = msg.get('attachments', [])
    embeds = msg.get('embeds', [])

    has_links = any(
        link in content for link in ['http://', 'https://', 'www.', '.com', '.ru', '.net', '.org', '.gg', '.xyz'])

    has_attachments = len(attachments) > 0
    has_embeds = len(embeds) > 0

    return has_links or has_attachments or has_embeds


def process_token(api: DiscordAPI, token: str, progress_tracker: ProgressTracker):
    headers = {"Authorization": token, "Content-Type": "application/json"}

    # Проверка токена
    time.sleep(random.uniform(1, 2))
    response = api.safe_request("GET", TEST_URL, headers)
    if not response or response.status_code != 200:
        progress_tracker.mark_completed(token)
        return

    try:
        user_data = response.json()
        user_id = user_data.get('id')
        username = user_data.get('username', 'unknown')
        discriminator = user_data.get('discriminator', '0000')

        if not user_id:
            progress_tracker.mark_completed(token)
            return

        progress_tracker.add_token(token, f"{username}#{discriminator}")
        progress_tracker.update_status(token, "🔍 Получение каналов")

        # Получаем каналы
        response = api.safe_request("GET", "https://discord.com/api/v9/users/@me/channels", headers)
        if not response or response.status_code != 200:
            progress_tracker.update_status(token, "❌ Ошибка каналов")
            progress_tracker.mark_completed(token)
            return

        channels = response.json()
        if not channels:
            progress_tracker.update_status(token, "✅ Нет каналов")
            progress_tracker.mark_completed(token)
            return

        progress_tracker.update_status(token, f"📊 {len(channels)} каналов")
        random.shuffle(channels)

        processed_channels = 0
        deleted_messages = 0
        closed_channels = 0

        for i, channel in enumerate(channels):
            if not isinstance(channel, dict):
                continue

            channel_id = channel.get('id')
            if not channel_id:
                continue

            # Задержка между каналами
            if i > 0:
                channel_delay = random.uniform(MIN_DELAY, BASE_DELAY * 2)
                time.sleep(channel_delay)

            # Обновляем статус
            progress_tracker.update_status(token, f"📂 Канал {processed_channels + 1}",
                                           processed_channels=processed_channels,
                                           deleted_messages=deleted_messages,
                                           closed_channels=closed_channels)

            # Проверяем последние 5 сообщений
            response = api.safe_request(
                "GET",
                f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=5",
                headers
            )

            if not response or response.status_code != 200:
                processed_channels += 1
                continue

            recent_messages = response.json()
            if not recent_messages:
                processed_channels += 1
                continue

            # Проверяем сообщения
            need_to_close = False
            for msg in recent_messages:
                if not isinstance(msg, dict):
                    continue

                has_links_or_attachments = check_message_has_links_or_attachments(msg)
                msg_author_id = msg.get('author', {}).get('id')
                is_own_message = msg_author_id == user_id

                if has_links_or_attachments and not is_own_message:
                    need_to_close = True
                    break

            # Закрываем канал если нужно
            if need_to_close:
                progress_tracker.update_status(token, f"🚫 Закрытие канала")

                close_response = api.safe_request(
                    "DELETE",
                    f"https://discord.com/api/v9/channels/{channel_id}?silent=false",
                    headers
                )

                if close_response and close_response.status_code in [200, 204]:
                    closed_channels += 1
                    progress_tracker.update_status(token, f"✅ Канал закрыт",
                                                   processed_channels=processed_channels + 1,
                                                   deleted_messages=deleted_messages,
                                                   closed_channels=closed_channels)
                else:
                    progress_tracker.update_status(token, f"❌ Ошибка закрытия",
                                                   processed_channels=processed_channels + 1,
                                                   deleted_messages=deleted_messages,
                                                   closed_channels=closed_channels)

                processed_channels += 1
                continue

            # Получаем все сообщения (максимум 20)
            all_user_messages = []
            before_id = None
            has_more_messages = True

            while has_more_messages and len(all_user_messages) < MAX_MESSAGES_TO_DELETE:
                url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=100"
                if before_id:
                    url += f"&before={before_id}"

                response = api.safe_request("GET", url, headers)
                if not response or response.status_code != 200:
                    break

                messages = response.json()
                if not messages:
                    has_more_messages = False
                    break

                for msg in messages:
                    if isinstance(msg, dict) and msg.get('author', {}).get('id') == user_id and msg.get('id'):
                        if check_message_has_links_or_attachments(msg):
                            all_user_messages.append(msg)
                            # Останавливаемся если набрали 20 сообщений
                            if len(all_user_messages) >= MAX_MESSAGES_TO_DELETE:
                                has_more_messages = False
                                break

                if len(messages) > 0:
                    before_id = messages[-1]['id']
                else:
                    has_more_messages = False

                if len(messages) == 100 and has_more_messages:
                    time.sleep(random.uniform(0.5, 1.0))

            if not all_user_messages:
                processed_channels += 1
                continue

            # Удаляем сообщения
            for j, msg in enumerate(all_user_messages):
                delete_response = api.safe_request(
                    "DELETE",
                    f"https://discord.com/api/v9/channels/{channel_id}/messages/{msg['id']}",
                    headers
                )
                if delete_response and delete_response.status_code == 204:
                    deleted_messages += 1

                    if j % 5 == 0:
                        progress_tracker.update_status(token, f"🗑️ Удаление {j + 1}/{len(all_user_messages)}",
                                                       processed_channels=processed_channels,
                                                       deleted_messages=deleted_messages,
                                                       closed_channels=closed_channels)

                if j < len(all_user_messages) - 1:
                    progressive_delay = BASE_DELAY * (1 + (j * 0.05))
                    time.sleep(min(progressive_delay, 2.0))

            processed_channels += 1

        # Финальный статус
        progress_tracker.update_status(token, f"✅ Завершено",
                                       processed_channels=processed_channels,
                                       deleted_messages=deleted_messages,
                                       closed_channels=closed_channels)
        progress_tracker.mark_completed(token)

    except Exception:
        progress_tracker.mark_completed(token)


def main():
    # Загружаем токены
    try:
        with open(TOKENS_FILE, "r") as f:
            tokens = [line.strip() for line in f if line.strip()]
    except:
        print("❌ Ошибка загрузки токенов")
        return

    if not tokens:
        print("❌ Не найдено токенов для обработки")
        return

    # Создаем трекер прогресса
    progress_tracker = ProgressTracker(min(MAX_TOKENS_WORKERS, len(tokens)))
    progress_tracker.set_total_tokens(len(tokens))

    # Создаем менеджер прокси
    proxy_manager = ProxyManager()

    # Запускаем поток отображения прогресса
    import threading
    stop_event = threading.Event()
    display_thread = threading.Thread(target=display_worker, args=(progress_tracker, stop_event))
    display_thread.daemon = True
    display_thread.start()

    # Ограничиваем количество одновременных потоков
    max_workers = min(MAX_TOKENS_WORKERS, len(tokens))

    # Используем ThreadPoolExecutor с ограничением потоков
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []

        # Запускаем обработку токенов
        for token in tokens:
            api = DiscordAPI(proxy_manager, progress_tracker)
            future = executor.submit(process_token, api, token, progress_tracker)
            futures.append(future)

        # Ждем завершения всех задач
        concurrent.futures.wait(futures)

    # Останавливаем поток отображения
    stop_event.set()
    display_thread.join(timeout=2)

    # Финальный вывод
    clear_console()
    display_progress(progress_tracker)

    print("\n" + "⭐" * 40)
    print(" " * 10 + "ВСЕ ТОКЕНЫ УСПЕШНО ОБРАБОТАНЫ!")
    print("⭐" * 40)


if __name__ == "__main__":
    main()