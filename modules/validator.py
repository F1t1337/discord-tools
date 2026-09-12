from modules.discord_transport import DiscordTransport, ProxyUnavailable, DiscordUnavailable
from typing import Tuple, Optional


class ValidationUnavailable(RuntimeError):
    """The remote service did not provide a definitive validation result."""


class TokenValidator:
    """Валидатор Discord токенов"""

    DISCORD_API_URL = "https://discord.com/api/v9/users/@me"

    def __init__(self, timeout: int = 10, max_retries: int = 3, transport=None):
        self.timeout = timeout
        self.max_retries = max_retries
        self.transport = transport or DiscordTransport(None)

    def validate_token(self, token: str, strict: bool = False, stage='validator') -> Tuple[bool, Optional[str]]:
        if not token or len(token) < 50:
            return False, None
        try:
            response = self.transport.request(token, 'GET', self.DISCORD_API_URL,
                timeout=self.timeout, max_retries=self.max_retries, stage=stage)
            if response.status_code == 200:
                return True, response.json().get('username')
            if response.status_code in (401, 403):
                return False, None
            raise DiscordUnavailable('Проверка временно недоступна')
        except (ProxyUnavailable, DiscordUnavailable, ValueError, TypeError):
            if strict:
                raise ValidationUnavailable('Проверка временно недоступна') from None
            return False, None

    def close(self):
        # Transport sessions are scoped to individual requests.
        return None


if __name__ == "__main__":
    validator = TokenValidator()

    test_token = "your_token_here"
    is_valid, username = validator.validate_token(test_token)

    if is_valid:
        print(f"Валидный токен: {username}")
    else:
        print("Невалидный токен")

    validator.close()