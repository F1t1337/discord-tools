"""
Упрощенный модуль проверки Discord токенов
Проверяет только: валидность и проспам/непроспам
НЕ УДАЛЯЕТ токены - только предоставляет информацию
"""
import requests
import time
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

logger = logging.getLogger(__name__)


class SimpleDiscordChecker:
    """Упрощенный чекер Discord токенов - только валид/невалид и проспам/непроспам"""
    
    # Системные каналы Discord (исключаемые из проверки)
    EXCLUDED_CHANNELS = [
        'clyde',
        'discord',
        'system',
        'nitro',
        'hypesquad',
        'partner',
        'verification',
    ]
    
    def __init__(self, threads: int = 10):
        self.threads = threads
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        })
    
    def check_token_validity(self, token: str) -> tuple[bool, Optional[Dict]]:
        """Проверяет валидность токена и возвращает данные пользователя"""
        try:
            headers = {'Authorization': token}
            response = self.session.get(
                'https://discord.com/api/v9/users/@me', 
                headers=headers, 
                timeout=10
            )
            
            if response.status_code == 200:
                return True, response.json()
            return False, None
        except Exception as e:
            logger.debug(f"Ошибка проверки валидности: {e}")
            return False, None
    
    def get_dm_channels(self, token: str) -> List[Dict]:
        """Получает список DM каналов"""
        try:
            headers = {'Authorization': token}
            response = self.session.get(
                'https://discord.com/api/v9/users/@me/channels', 
                headers=headers, 
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            logger.debug(f"Ошибка получения DM каналов: {e}")
            return []
    
    def _is_valid_dm(self, dm: Dict) -> bool:
        """Проверяет что DM канал не системный"""
        recipients = dm.get('recipients', [])
        if not recipients:
            return False
        
        # Проверяем username получателя
        for recipient in recipients:
            username = recipient.get('username', '').lower()
            if any(excluded in username for excluded in self.EXCLUDED_CHANNELS):
                return False
        
        return True
    
    def get_channel_messages(self, token: str, channel_id: str, limit: int = 10) -> List[Dict]:
        """Получает последние сообщения из канала"""
        try:
            headers = {'Authorization': token}
            response = self.session.get(
                f'https://discord.com/api/v9/channels/{channel_id}/messages?limit={limit}',
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            logger.debug(f"Ошибка получения сообщений: {e}")
            return []
    
    def _message_has_spam(self, msg: Dict) -> bool:
        """Проверяет содержит ли сообщение спам (ссылки/вложения)"""
        content = msg.get('content', '').lower()
        attachments = msg.get('attachments', [])
        embeds = msg.get('embeds', [])
        
        # Проверка на ссылки
        has_links = any(
            link in content 
            for link in ['http://', 'https://', 'www.', '.com', '.ru', '.net', '.org', '.gg', '.xyz', '.io', '.me']
        )
        
        # Проверка на вложения или embeds
        has_attachments = len(attachments) > 0
        has_embeds = len(embeds) > 0
        
        return has_links or has_attachments or has_embeds
    
    def check_spam_status(self, token: str, user_id: str) -> str:
        """
        Проверяет проспам/непроспам через анализ последних сообщений
        
        Returns:
            'spam' - найдены ссылки/вложения в исходящих сообщениях
            'non_spam' - чистые сообщения
        """
        try:
            # Получаем DM каналы
            dm_channels = self.get_dm_channels(token)
            
            # Фильтруем валидные DM
            valid_dms = [dm for dm in dm_channels if self._is_valid_dm(dm)]
            
            if not valid_dms:
                # Нет валидных DM - считаем непроспамом
                return 'non_spam'
            
            # Проверяем последние сообщения в первых 5 DM
            spam_detected = False
            
            for dm in valid_dms[:5]:
                channel_id = dm.get('id')
                if not channel_id:
                    continue
                
                try:
                    # Получаем последние 10 сообщений
                    messages = self.get_channel_messages(token, channel_id, limit=10)
                    
                    # Проверяем ИСХОДЯЩИЕ сообщения от нашего пользователя
                    for msg in messages:
                        msg_author_id = msg.get('author', {}).get('id')
                        
                        # Пропускаем входящие сообщения
                        if msg_author_id != user_id:
                            continue
                        
                        # Проверяем наше сообщение на спам
                        if self._message_has_spam(msg):
                            spam_detected = True
                            break
                    
                    if spam_detected:
                        break
                        
                    time.sleep(0.3)  # Задержка между запросами
                    
                except Exception as e:
                    logger.debug(f"Ошибка проверки канала {channel_id}: {e}")
                    continue
            
            return 'spam' if spam_detected else 'non_spam'
            
        except Exception as e:
            logger.warning(f"⚠️ Ошибка проверки проспама: {e}")
            # При ошибке считаем непроспамом (лучше пропустить чем удалить)
            return 'non_spam'
    
    def check_single_token(self, token: str) -> Dict:
        """
        Проверяет один токен
        
        Returns:
            {
                'token': str,
                'valid': bool,
                'username': str or None,
                'user_id': str or None,
                'spam_status': 'spam' | 'non_spam' | None,
                'error': str or None
            }
        """
        result = {
            'token': token,
            'valid': False,
            'username': None,
            'user_id': None,
            'spam_status': None,
            'error': None,
        }
        
        # Проверка валидности
        is_valid, user_data = self.check_token_validity(token)
        
        if not is_valid:
            result['error'] = 'Token invalid'
            return result
        
        # Токен валиден
        result['valid'] = True
        result['username'] = user_data.get('username', 'Unknown')
        result['user_id'] = user_data.get('id')
        
        # Проверка проспама
        try:
            result['spam_status'] = self.check_spam_status(token, result['user_id'])
        except Exception as e:
            logger.warning(f"⚠️ Ошибка проверки проспама для {result['username']}: {e}")
            result['spam_status'] = 'non_spam'  # При ошибке считаем непроспамом
        
        return result
    
    def check_tokens(self, tokens: List[str], progress_callback=None) -> Dict:
        """
        Проверяет список токенов с многопоточностью
        
        Args:
            tokens: список токенов для проверки
            progress_callback: функция callback(current, total, result)
        
        Returns:
            {
                'total': int,
                'valid': int,
                'invalid': int,
                'spam': int,
                'non_spam': int,
                'results': List[Dict],
                'elapsed_time': float
            }
        """
        start_time = time.time()
        results = []
        
        stats = {
            'total': len(tokens),
            'valid': 0,
            'invalid': 0,
            'spam': 0,
            'non_spam': 0,
        }
        
        logger.info(f"🔍 Начало проверки {len(tokens)} токенов...")
        
        # Многопоточная проверка
        with ThreadPoolExecutor(max_workers=self.threads) as executor:
            futures = {executor.submit(self.check_single_token, token): i for i, token in enumerate(tokens)}
            
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                    
                    # Обновляем статистику
                    if result['valid']:
                        stats['valid'] += 1
                        
                        if result['spam_status'] == 'spam':
                            stats['spam'] += 1
                        elif result['spam_status'] == 'non_spam':
                            stats['non_spam'] += 1
                    else:
                        stats['invalid'] += 1
                    
                    # Вызываем callback если есть
                    if progress_callback:
                        progress_callback(len(results), stats['total'], result)
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки токена: {e}")
        
        elapsed_time = time.time() - start_time
        
        logger.info(f"✅ Проверка завершена за {elapsed_time:.1f}с")
        logger.info(f"📊 Результат: валидных={stats['valid']}, невалидных={stats['invalid']}, проспам={stats['spam']}, непроспам={stats['non_spam']}")
        
        return {
            **stats,
            'results': results,
            'elapsed_time': elapsed_time
        }


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    checker = SimpleDiscordChecker(threads=10)
    
    # Тестовые токены
    test_tokens = [
        "your_token_here_1",
        "your_token_here_2",
    ]
    
    # Проверка с callback
    def progress(current, total, result):
        print(f"[{current}/{total}] {result['username']}: valid={result['valid']}, spam={result['spam_status']}")
    
    stats = checker.check_tokens(test_tokens, progress_callback=progress)
    
    print(f"\n📊 Итоговая статистика:")
    print(f"  Всего: {stats['total']}")
    print(f"  Валидных: {stats['valid']}")
    print(f"  Невалидных: {stats['invalid']}")
    print(f"  Проспам: {stats['spam']}")
    print(f"  Непроспам: {stats['non_spam']}")