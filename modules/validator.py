import requests
import time
import random
import logging
import concurrent.futures
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class TokenValidator:
    """Класс для валидации Discord токенов (упрощенная версия)"""

    DISCORD_API_URL = "https://discord.com/api/v9/users/@me"

    def __init__(self, max_workers: int = 20, timeout: int = 10, max_retries: int = 2):
        """
        Инициализация валидатора токенов

        Args:
            max_workers: Максимальное количество потоков для проверки
            timeout: Таймаут запроса в секундах
            max_retries: Количество попыток при ошибке
        """
        self.max_workers = max_workers
        self.timeout = timeout
        self.max_retries = max_retries

    def validate_token(self, token: str) -> Dict:
        """
        Проверяет токен на валидность

        Args:
            token: Discord токен

        Returns:
            {
                'token': str,
                'valid': bool,
                'username': str or None,
                'error': str or None,
                'checked_at': timestamp
            }
        """
        result = {
            'token': token,
            'valid': False,
            'username': None,
            'error': None,
            'checked_at': datetime.now().timestamp()
        }

        # Быстрая проверка формата
        if not token or len(token) < 50:
            result['error'] = 'Invalid format'
            return result

        # Проверка через Discord API
        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        for attempt in range(self.max_retries):
            try:
                response = requests.get(
                    self.DISCORD_API_URL,
                    headers=headers,
                    timeout=self.timeout
                )

                # Rate limit
                if response.status_code == 429:
                    retry_after = response.json().get('retry_after', 1)
                    time.sleep(retry_after)
                    continue

                # Успех
                if response.status_code == 200:
                    data = response.json()
                    result['valid'] = True
                    result['username'] = f"{data.get('username')}#{data.get('discriminator', '0000')}"
                    logger.info(f"✅ Валидный токен: {result['username']}")
                    return result

                # Невалидный токен
                elif response.status_code == 401:
                    result['error'] = 'Invalid token'
                    logger.warning(f"❌ Невалидный токен")
                    return result

                # Заблокирован
                elif response.status_code == 403:
                    result['error'] = 'Token locked'
                    logger.warning(f"🔒 Токен заблокирован")
                    return result

                else:
                    result['error'] = f'HTTP {response.status_code}'
                    return result

            except requests.exceptions.Timeout:
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                    continue
                result['error'] = 'Timeout'

            except Exception as e:
                result['error'] = str(e)
                logger.error(f"❌ Ошибка: {e}")

        return result

    def validate_tokens_batch(self, tokens: List[str], callback=None) -> List[Dict]:
        """
        Проверяет несколько токенов параллельно

        Args:
            tokens: Список токенов
            callback: Функция обратного вызова (принимает result)

        Returns:
            Список результатов
        """
        results = []

        logger.info(f"🔍 Проверка {len(tokens)} токенов...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_token = {
                executor.submit(self.validate_token, token): token
                for token in tokens
            }

            for future in concurrent.futures.as_completed(future_to_token):
                try:
                    result = future.result()
                    results.append(result)

                    if callback:
                        callback(result)

                    # Небольшая задержка
                    time.sleep(random.uniform(0.2, 0.5))

                except Exception as e:
                    logger.error(f"❌ Ошибка валидации: {e}")

        # Статистика
        valid = sum(1 for r in results if r['valid'])
        logger.info(f"📊 Результат: ✅ {valid}/{len(results)} валидных")

        return results


class ValidationStats:
    """Класс для статистики валидации"""

    def __init__(self):
        self.total = 0
        self.valid = 0
        self.invalid = 0
        self.locked = 0
        self.errors = 0
        self.start_time = time.time()

    def update(self, result: Dict):
        """Обновляет статистику"""
        self.total += 1

        if result['valid']:
            self.valid += 1
        elif 'locked' in result.get('error', '').lower():
            self.locked += 1
        elif 'invalid' in result.get('error', '').lower():
            self.invalid += 1
        else:
            self.errors += 1

    def print_stats(self):
        """Выводит статистику"""
        elapsed = time.time() - self.start_time
        success_rate = (self.valid / self.total * 100) if self.total > 0 else 0
        speed = self.total / elapsed if elapsed > 0 else 0

        print("\n" + "=" * 60)
        print("📊 СТАТИСТИКА ВАЛИДАЦИИ")
        print("=" * 60)
        print(f"Всего проверено:     {self.total}")
        print(f"✅ Валидные:         {self.valid} ({success_rate:.1f}%)")
        print(f"❌ Невалидные:       {self.invalid}")
        print(f"🔒 Заблокированные:  {self.locked}")
        print(f"⚠️ Ошибки:           {self.errors}")
        print(f"⏱️ Время:            {elapsed:.1f}с")
        print(f"⚡ Скорость:         {speed:.1f} токенов/сек")
        print("=" * 60)


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Тестовые токены
    test_tokens = [
        "valid_token_here",
        "invalid_token_123",
    ]

    validator = TokenValidator(max_workers=10)
    stats = ValidationStats()


    def on_result(result):
        stats.update(result)
        status = "✅" if result['valid'] else "❌"
        info = result.get('username') or result.get('error')
        print(f"{status} {info}")


    results = validator.validate_tokens_batch(test_tokens, callback=on_result)
    stats.print_stats()