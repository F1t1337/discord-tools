"""
Модуль для получения Cloudflare Tunnel URL
"""
import requests
import logging
import re

logger = logging.getLogger(__name__)


class CloudflareHelper:
    """Класс для работы с Cloudflare Tunnel API"""
    
    # Возможные порты для Cloudflare metrics
    METRICS_PORTS = [20241,20242,43867, 60000, 60123]
    
    def __init__(self):
        """Инициализация"""
        self.metrics_url = None
        self._detect_metrics_port()
    
    def _detect_metrics_port(self):
        """Автоматически определяет на каком порту доступны metrics"""
        for port in self.METRICS_PORTS:
            try:
                url = f"http://127.0.0.1:{port}/metrics"
                response = requests.get(url, timeout=1)
                
                if response.status_code == 200:
                    self.metrics_url = url
                    logger.info(f"✅ Cloudflare metrics найдены на порту {port}")
                    return
            except:
                continue
        
        logger.debug("Cloudflare Tunnel metrics не найдены на стандартных портах")
    
    def get_public_url(self) -> str:
        """
        Получает текущий публичный URL от Cloudflare Tunnel
        
        Returns:
            Публичный URL (например: https://abc-def.trycloudflare.com) или None если не запущен
        """
        # Если порт не определен - пробуем снова
        if not self.metrics_url:
            self._detect_metrics_port()
        
        if not self.metrics_url:
            logger.debug("Cloudflare Tunnel не запущен")
            return None
        
        try:
            response = requests.get(self.metrics_url, timeout=2)
            
            if response.status_code == 200:
                text = response.text
                
                # Метод 1: Ищем userHostname в метриках
                # Формат: cloudflared_tunnel_user_hostnames_counts{userHostname="https://..."}
                pattern1 = r'userHostname="(https://[^"]+)"'
                matches1 = re.findall(pattern1, text)
                
                if matches1:
                    url = matches1[0]
                    logger.info(f"✅ Cloudflare Tunnel URL (метод 1): {url}")
                    return url
                
                # Метод 2: Прямой поиск trycloudflare.com URL
                pattern2 = r'(https://[a-z0-9\-]+\.trycloudflare\.com)'
                matches2 = re.findall(pattern2, text)
                
                if matches2:
                    # Берем первый уникальный URL
                    url = matches2[0]
                    logger.info(f"✅ Cloudflare Tunnel URL (метод 2): {url}")
                    return url
                
                # Метод 3: Поиск любого HTTPS URL в metrics
                pattern3 = r'"(https://[^"]+\.trycloudflare\.com[^"]*)"'
                matches3 = re.findall(pattern3, text)
                
                if matches3:
                    url = matches3[0]
                    logger.info(f"✅ Cloudflare Tunnel URL (метод 3): {url}")
                    return url
                
                logger.warning("⚠️ URL не найден в metrics (проверьте что туннель создан)")
                logger.debug(f"Первые 500 символов metrics: {text[:500]}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.debug(f"Cloudflare Tunnel metrics недоступны: {e}")
            # Сбрасываем URL чтобы попробовать переопределить порт в следующий раз
            self.metrics_url = None
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения Cloudflare URL: {e}")
            return None
    
    def is_running(self) -> bool:
        """
        Проверяет запущен ли Cloudflare Tunnel
        
        Returns:
            True если Cloudflare Tunnel запущен
        """
        # Проверяем порт если не определен
        if not self.metrics_url:
            self._detect_metrics_port()
        
        if not self.metrics_url:
            return False
        
        try:
            response = requests.get(self.metrics_url, timeout=2)
            return response.status_code == 200
        except:
            return False
    
    def get_tunnel_info(self) -> dict:
        """
        Получает полную информацию о туннеле
        
        Returns:
            Словарь с информацией о туннеле
        """
        try:
            if not self.is_running():
                return {'running': False, 'url': None, 'error': 'Tunnel not running'}
            
            response = requests.get(self.metrics_url, timeout=2)
            
            if response.status_code == 200:
                info = {
                    'running': True,
                    'url': self.get_public_url(),
                    'metrics_available': True,
                    'metrics_port': self.metrics_url.split(':')[-1].split('/')[0] if self.metrics_url else None
                }
                
                # Извлекаем дополнительную информацию из метрик
                text = response.text
                
                # Активные соединения
                connections_match = re.search(r'cloudflared_tunnel_total_requests\s+(\d+)', text)
                if connections_match:
                    info['total_requests'] = int(connections_match.group(1))
                
                # Время работы
                uptime_match = re.search(r'process_uptime_seconds\s+([\d.]+)', text)
                if uptime_match:
                    info['uptime_seconds'] = float(uptime_match.group(1))
                
                return info
            
            return {'running': False, 'url': None, 'metrics_available': False}
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения информации о туннеле: {e}")
            return {'running': False, 'url': None, 'error': str(e)}


# Алиас для совместимости
TunnelHelper = CloudflareHelper