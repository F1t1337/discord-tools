"""Задача покупки аккаунтов с LZT Market.

Одна активная задача на процесс. Задача ищет аккаунты по фильтрам, покупает их
через fast-buy (с отбраковкой мёртвых на стороне LZT) и передаёт купленные токены
в существующий конвейер обработки (валидация → очистка → финальная валидация).
Запросы к LZT не проксируются — проксируется только Discord в конвейере.
"""
import logging
import threading
import time
from typing import Dict, Optional

from modules.lzt_monitor import PurchaseError
from modules.finance import Finance

logger = logging.getLogger(__name__)


class PurchaseTaskManager:
    # Ограничение на число страниц выдачи при оценке (защита от лишних запросов).
    MAX_ESTIMATE_PAGES = 20
    # Пауза между покупками, чтобы не упереться в лимиты маркета.
    BUY_DELAY = 0.3

    def __init__(self, lzt_monitor, db, intake_cb, set_cleaner_workers_cb=None):
        """
        Args:
            lzt_monitor: объект LZTMonitor (поиск, покупка, баланс).
            db: объект Database (для живой статистики по конвейеру).
            intake_cb: callable(token, item_id, price, seller_username) — постановка
                       купленного аккаунта в конвейер обработки.
            set_cleaner_workers_cb: callable(int) — задать число потоков очистки.
        """
        self.lzt = lzt_monitor
        self.db = db
        self.intake_cb = intake_cb
        self.set_cleaner_workers_cb = set_cleaner_workers_cb
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._state = self._idle_state()

    @staticmethod
    def _idle_state() -> Dict:
        return {
            'status': 'idle',        # idle | running | stopping | done | error
            'pmax': None, 'chat_min': None, 'requested': 0, 'cleaner_workers': None,
            'balance_start': None, 'balance': None, 'spent': 0.0,
            'bought': 0, 'checked': 0, 'skipped_dead': 0, 'errors': 0,
            'started_at': None, 'finished_at': None, 'error': None, 'message': None,
            'purchase_id': None,
        }

    # ---------- потокобезопасные помощники ----------

    def _bump(self, **deltas):
        with self._lock:
            for key, value in deltas.items():
                self._state[key] = (self._state.get(key) or 0) + value

    def _set(self, **fields):
        with self._lock:
            self._state.update(fields)

    def _get(self, key):
        with self._lock:
            return self._state.get(key)

    def is_active(self) -> bool:
        return self._get('status') in ('running', 'stopping')

    # ---------- оценка ----------

    def estimate(self, pmax: float, chat_min: int) -> Dict:
        """Считает: сколько подходящих аккаунтов, их стоимость, баланс, макс. к покупке."""
        balance = self.lzt.get_balance()
        prices = []
        total = 0
        for page in range(1, self.MAX_ESTIMATE_PAGES + 1):
            items, total = self.lzt.search_accounts(pmax, chat_min, page=page)
            if not items:
                break
            for item in items:
                try:
                    price = float(item.get('price') or 0)
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    prices.append(price)
            if total and len(prices) >= total:
                break
        prices.sort()
        total_cost = round(sum(prices), 2)
        max_affordable = 0
        if balance is not None:
            running = 0.0
            for price in prices:
                if running + price > balance:
                    break
                running += price
                max_affordable += 1
        return {
            'count': total or len(prices),
            'collected': len(prices),
            'truncated': bool(total and total > len(prices)),
            'total_cost': total_cost,
            'balance': balance,
            'max_affordable': max_affordable,
            'pmax': pmax,
            'chat_min': chat_min,
        }

    # ---------- запуск / остановка ----------

    def start(self, pmax: float, chat_min: int, count: int, cleaner_workers: int) -> Dict:
        with self._lock:
            if self._state['status'] in ('running', 'stopping'):
                raise RuntimeError('Задача покупки уже выполняется')
            self._stop.clear()
            balance = self.lzt.get_balance()
            purchase_id = Finance(self.db).start_purchase(count)
            self._state = self._idle_state()
            self._state.update(
                status='running', pmax=pmax, chat_min=chat_min, requested=count,
                cleaner_workers=cleaner_workers, balance_start=balance, balance=balance,
                started_at=time.time(), message='Поиск аккаунтов…')
            self._state['purchase_id'] = purchase_id
        if self.set_cleaner_workers_cb:
            try:
                self.set_cleaner_workers_cb(cleaner_workers)
            except Exception:
                logger.warning('Не удалось задать число потоков очистки для задачи')
        self._thread = threading.Thread(target=self._run, name='PurchaseTask', daemon=True)
        try:
            self._thread.start()
        except Exception:
            self._set(status='error', error='StartFailed', finished_at=time.time())
            Finance(self.db).finish_purchase(self._get('purchase_id'), 'error')
            raise
        return self.snapshot()

    def stop(self) -> bool:
        with self._lock:
            if self._state['status'] == 'running':
                self._state['status'] = 'stopping'
                self._state['message'] = 'Останавливаем покупку…'
        self._stop.set()
        return True

    # ---------- рабочий поток ----------

    def _affordable(self, price: float) -> bool:
        """Проверяет, что покупка не превысит баланс; при нехватке обновляет баланс."""
        with self._lock:
            balance = self._state['balance']
            spent = self._state['spent']
        if balance is None:
            return True  # баланс неизвестен — не блокируем (LZT сам отклонит при нехватке)
        if spent + price <= balance:
            return True
        fresh = self.lzt.get_balance()
        if fresh is None:
            return False
        self._set(balance=fresh)
        return spent + price <= fresh

    def _run(self):
        pmax = self._get('pmax')
        chat_min = self._get('chat_min')
        requested = self._get('requested')
        seen = set()
        page = 1
        try:
            while not self._stop.is_set() and self._get('bought') < requested:
                items, total = self.lzt.search_accounts(pmax, chat_min, page=page)
                if not items:
                    break
                for item in items:
                    if self._stop.is_set() or self._get('bought') >= requested:
                        break
                    item_id = item.get('item_id')
                    if item_id is None or item_id in seen:
                        continue
                    seen.add(item_id)
                    try:
                        price = float(item.get('price') or 0)
                    except (TypeError, ValueError):
                        continue
                    if price <= 0 or price > pmax:
                        continue
                    if not self._affordable(price):
                        self._set(message='Недостаточно баланса для следующей покупки')
                        self._stop.set()
                        break

                    self._bump(checked=1)
                    try:
                        token, raw = self.lzt.fast_buy(item_id, price)
                    except PurchaseError as exc:
                        if exc.purchased:
                            Finance(self.db).record_purchase(self._get('purchase_id'), item_id, price)
                            self._bump(bought=1, spent=price, errors=1)
                            continue
                        if exc.out_of_balance:
                            self._set(message='Недостаточно баланса для покупки')
                            self._stop.set()
                            break
                        self._bump(skipped_dead=1)
                        continue
                    except Exception:
                        self._bump(errors=1)
                        continue

                    seller = ''
                    if isinstance(raw, dict) and isinstance(raw.get('seller'), dict):
                        seller = raw['seller'].get('username', '')
                    # The expense exists even if intake or cleaning subsequently fails.
                    Finance(self.db).record_purchase(self._get('purchase_id'), item_id, price)
                    with self._lock:
                        self._state['bought'] += 1
                        self._state['spent'] = round(self._state['spent'] + price, 2)
                        self._state['message'] = f"Куплено {self._state['bought']} из {requested}"
                    try:
                        self.intake_cb(token=token, item_id=item_id, price=price,
                                       seller_username=seller or 'lzt_market', purchase_id=self._get('purchase_id'))
                    except Exception:
                        logger.error('Не удалось поставить купленный токен в обработку')
                        self._bump(errors=1)
                        continue

                    time.sleep(self.BUY_DELAY)

                page += 1
                if total and len(seen) >= total:
                    break  # перебрали всю выдачу
        except Exception as exc:
            logger.error('Ошибка задачи покупки: %s', type(exc).__name__)
            self._set(error=type(exc).__name__)
        finally:
            with self._lock:
                bought = self._state['bought']
                requested_final = self._state['requested']
                if self._state['error']:
                    self._state['status'] = 'error'
                    if not self._state['message']:
                        self._state['message'] = 'Задача прервана из-за ошибки'
                else:
                    self._state['status'] = 'done'
                    if bought >= requested_final:
                        self._state['message'] = f'Готово: куплено {bought}'
                    else:
                        self._state['message'] = (
                            f'Завершено: куплено {bought} из {requested_final} '
                            f'(часть аккаунтов отбракована или закончилась выдача)')
                self._state['finished_at'] = time.time()
            Finance(self.db).finish_purchase(self._get('purchase_id'), self._get('status'))

    # ---------- статус ----------

    def snapshot(self) -> Dict:
        with self._lock:
            state = dict(self._state)
        try:
            counts = self.db.count_tokens_by_status() if self.db is not None else {}
        except Exception:
            counts = {}
        state['pipeline'] = {name: counts.get(name, 0) for name in
                             ('new', 'validated', 'cleaning', 'cleaned', 'ready', 'invalid', 'sent')}
        return state
