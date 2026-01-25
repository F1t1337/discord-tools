import requests
import time
from typing import Tuple, Optional


class TokenValidator:
    """Валидатор Discord токенов"""

    DISCORD_API_URL = "https://discord.com/api/v9/users/@me"

    def __init__(self, timeout: int = 10, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def validate_token(self, token: str) -> Tuple[bool, Optional[str]]:
        """
        Проверяет валидность токена и возвращает username.

        Returns:
            (True, username) — токен валиден
            (False, None) — токен невалиден
        """
        if not token or len(token) < 50:
            return False, None

        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        for attempt in range(self.max_retries):
            try:
                response = self.session.get(
                    self.DISCORD_API_URL,
                    headers=headers,
                    timeout=self.timeout
                )

                if response.status_code == 200:
                    data = response.json()
                    username = data.get('username')
                    return True, username

                if response.status_code == 429:
                    retry_after = response.json().get('retry_after', 1)
                    time.sleep(retry_after)
                    continue

                if response.status_code in (401, 403):
                    return False, None

                if attempt < self.max_retries - 1:
                    time.sleep(1)
                    continue

                return False, None

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                    continue
                return False, None

            except Exception:
                return False, None

        return False, None

    def close(self):
        self.session.close()


if __name__ == "__main__":
    validator = TokenValidator()

    test_token = "your_token_here"
    is_valid, username = validator.validate_token(test_token)

    if is_valid:
        print(f"Валидный токен: {username}")
    else:
        print("Невалидный токен")

    validator.close()