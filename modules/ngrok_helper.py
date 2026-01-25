"""
Модуль для получения текущего ngrok URL
"""
import requests
import logging

logger = logging.getLogger(__name__)


class NgrokHelper:
    """Класс для работы с ngrok API"""
    
    def __init__(self, ngrok_api_url: str = "http://127.0.0.1:4040"):
        """
        Инициализация
        
        Args:
            ngrok_api_url: URL локального API ngrok (по умолчанию http://127.0.0.1:4040)
        """
        self.ngrok_api_url = ngrok_api_url
    
    def get_public_url(self) -> str:
        """
        Получает текущий публичный URL от ngrok
        
        Returns:
            Публичный URL (например: https://abc123.ngrok.io) или None если ngrok не запущен
        """
        try:
            response = requests.get(f"{self.ngrok_api_url}/api/tunnels", timeout=2)
            
            if response.status_code == 200:
                data = response.json()
                tunnels = data.get('tunnels', [])
                
                # Ищем HTTPS туннель
                for tunnel in tunnels:
                    if tunnel.get('proto') == 'https':
                        public_url = tunnel.get('public_url')
                        logger.info(f"✅ Получен ngrok URL: {public_url}")
                        return public_url
                
                # Если HTTPS не найден, берем первый доступный
                if tunnels:
                    public_url = tunnels[0].get('public_url')
                    logger.info(f"✅ Получен ngrok URL: {public_url}")
                    return public_url
                
                logger.warning("⚠️ Ngrok запущен, но туннели не найдены")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.debug(f"Ngrok API недоступен: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения ngrok URL: {e}")
            return None
    
    def is_running(self) -> bool:
        """
        Проверяет запущен ли ngrok
        
        Returns:
            True если ngrok запущен
        """
        try:
            response = requests.get(f"{self.ngrok_api_url}/api/tunnels", timeout=2)
            return response.status_code == 200
        except:
            return False
    
    def get_tunnel_info(self) -> dict:
        """
        Получает полную информацию о туннелях
        
        Returns:
            Словарь с информацией о туннелях
        """
        try:
            response = requests.get(f"{self.ngrok_api_url}/api/tunnels", timeout=2)
            
            if response.status_code == 200:
                return response.json()
            
            return {}
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения информации о туннелях: {e}")
            return {}
