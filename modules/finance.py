"""Persistent purchase-to-submission accounting, in integer kopecks."""
from datetime import date, datetime, time as day_time, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import time
from zoneinfo import ZoneInfo


def resolve_zone(timezone):
    """Возвращает объект таймзоны. Если системной базы IANA нет (не установлен пакет
    tzdata), не роняем панель, а используем фиксированный сдвиг рабочего региона
    (UTC+4, Europe/Saratov без перехода на летнее время)."""
    try:
        return ZoneInfo(timezone)
    except Exception:
        return dt_timezone(timedelta(hours=4))


def money_minor(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError('Сумма должна быть числом')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > Decimal('1000000000000'):
            raise ValueError('Некорректная сумма')
        return int((number * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError('Некорректная сумма') from None


def ensure_exportable(conn, purchase_id):
    row = conn.execute('SELECT status FROM purchase_batches WHERE id=?', (purchase_id,)).fetchone()
    if not row:
        raise ValueError('Закупка не найдена')
    if row['status'] in ('running', 'stopping'):
        raise RuntimeError('Сначала дождитесь завершения закупки')
    if conn.execute('SELECT 1 FROM export_batches WHERE purchase_id=?', (purchase_id,)).fetchone():
        raise RuntimeError('Для этой закупки уже создана сдача. Используйте файл в истории.')
    if conn.execute("SELECT 1 FROM tokens WHERE purchase_id=? AND status IN ('new','validated','cleaning','cleaned')", (purchase_id,)).fetchone():
        raise RuntimeError('Не все аккаунты закупки закончили обработку')


class Finance:
    def __init__(self, db):
        self.db = db

    def start_purchase(self, requested):
        with self.db.get_connection() as conn:
            return conn.execute("INSERT INTO purchase_batches(status, requested, started_at) VALUES ('running', ?, ?)",
                                (requested, time.time())).lastrowid

    def finish_purchase(self, purchase_id, status):
        with self.db.get_connection() as conn:
            conn.execute('UPDATE purchase_batches SET status=?, finished_at=? WHERE id=?',
                         (status, time.time(), purchase_id))

    def record_purchase(self, purchase_id, item_id, price):
        cost = money_minor(price)
        with self.db.get_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            inserted = conn.execute('''INSERT OR IGNORE INTO purchase_expenses
                (item_id, purchase_id, cost_minor, bought_at) VALUES (?, ?, ?, ?)''',
                (str(item_id), purchase_id, cost, time.time())).rowcount
            if inserted:
                # Preserve existing operational counters, atomically with the receipt.
                conn.execute('''INSERT INTO statistics(date, tokens_bought, money_spent) VALUES (?, 1, ?)
                    ON CONFLICT(date) DO UPDATE SET tokens_bought=tokens_bought+1,
                    money_spent=money_spent+excluded.money_spent''', (date.today().isoformat(), cost / 100))
        return bool(inserted)

    def export_candidates(self, purchase_id):
        with self.db.get_connection() as conn:
            if purchase_id is not None:
                ensure_exportable(conn, purchase_id)
            limit = ' LIMIT 1000' if purchase_id is None else ''
            return [dict(row) for row in conn.execute("SELECT * FROM tokens WHERE purchase_id IS ? AND status='ready' ORDER BY id" + limit, (purchase_id,))]

    def batches(self):
        with self.db.get_connection() as conn:
            rows = conn.execute('''SELECT p.*, e.id AS export_id,
                (SELECT COUNT(*) FROM purchase_expenses x WHERE x.purchase_id=p.id) AS bought,
                (SELECT COALESCE(SUM(cost_minor),0) FROM purchase_expenses x WHERE x.purchase_id=p.id) AS spent_minor,
                (SELECT COUNT(*) FROM tokens t WHERE t.purchase_id=p.id AND t.status='ready') AS ready,
                (SELECT COUNT(*) FROM tokens t WHERE t.purchase_id=p.id AND t.status IN ('new','validated','cleaning','cleaned')) AS pending
                FROM purchase_batches p LEFT JOIN export_batches e ON e.purchase_id=p.id
                WHERE e.id IS NULL AND (p.status IN ('running','stopping') OR EXISTS (
                    SELECT 1 FROM tokens q WHERE q.purchase_id=p.id AND q.status IN ('new','validated','cleaning','cleaned','ready')))
                ORDER BY p.id LIMIT 100''').fetchall()
            return [dict(row) for row in rows]

    def claim_poll(self, export_id, force=False):
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return bool(conn.execute('''UPDATE tskupka_tasks SET poll_lease_until=?
                WHERE export_id=? AND task_id IS NOT NULL AND poll_lease_until<=?
                AND (? OR (price_minor IS NULL AND next_poll_at<=?))''',
                (now+60, export_id, now, force, now)).rowcount)

    def release_poll(self, export_id):
        with self.db.get_connection() as conn:
            conn.execute('UPDATE tskupka_tasks SET poll_lease_until=0, next_poll_at=? WHERE export_id=?',
                         (time.time()+10, export_id))

    def due_tasks(self):
        now = time.time()
        with self.db.get_connection() as conn:
            return [r[0] for r in conn.execute('''SELECT export_id FROM tskupka_tasks
                WHERE task_id IS NOT NULL AND price_minor IS NULL AND next_poll_at<=? AND poll_lease_until<=?
                ORDER BY next_poll_at, export_id LIMIT 100''', (now, now))]

    def receive_price(self, export_id, value):
        if value is None:
            return
        amount = money_minor(value)
        with self.db.get_connection() as conn:
            # First observed result is immutable: repeat polling cannot double-credit.
            conn.execute('''UPDATE tskupka_tasks SET price_minor=?, price_at=?
                WHERE export_id=? AND price_minor IS NULL''', (amount, time.time(), export_id))

    def report(self, period, anchor=None, timezone='Europe/Saratov', offset=0):
        zone = resolve_zone(timezone)
        selected = date.fromisoformat(anchor) if anchor else datetime.now(zone).date()
        if period == 'day':
            start, end = selected, selected + timedelta(days=1)
        elif period == 'week':
            start = selected - timedelta(days=selected.weekday())
            end = start + timedelta(days=7)
        elif period == 'month':
            start = selected.replace(day=1)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            raise ValueError('Неверный период')
        since = datetime.combine(start, day_time(), zone).timestamp()
        until = datetime.combine(end, day_time(), zone).timestamp()
        days = {(start + timedelta(days=i)).isoformat(): {'spent_minor': 0, 'earned_minor': 0}
                for i in range((end - start).days)}
        with self.db.get_connection() as conn:
            for row in conn.execute('SELECT bought_at, cost_minor FROM purchase_expenses WHERE bought_at>=? AND bought_at<?', (since, until)):
                days[datetime.fromtimestamp(row['bought_at'], zone).date().isoformat()]['spent_minor'] += row['cost_minor']
            legacy = list(conn.execute('SELECT * FROM legacy_expenses WHERE date>=? AND date<?', (start.isoformat(), end.isoformat())))
            for row in legacy:
                days[row['date']]['spent_minor'] += row['cost_minor']
            # Доход относим к дате ЗАКУПКИ — расход и доход связки в одном периоде.
            # Для сдач без закупки даты покупки нет — берём дату получения price_result.
            for row in conn.execute('''SELECT t.price_minor AS earned,
                    COALESCE(p.started_at, t.price_at) AS anchor_at FROM tskupka_tasks t
                    LEFT JOIN export_batches e ON e.id=t.export_id
                    LEFT JOIN purchase_batches p ON p.id=e.purchase_id
                    WHERE t.price_minor IS NOT NULL
                    AND COALESCE(p.started_at, t.price_at)>=? AND COALESCE(p.started_at, t.price_at)<?''', (since, until)):
                days[datetime.fromtimestamp(row['anchor_at'], zone).date().isoformat()]['earned_minor'] += row['earned']
            # Связки перечисляем по дате закупки, чтобы список совпадал с итогами периода.
            base = '''FROM purchase_batches p LEFT JOIN export_batches e ON e.purchase_id=p.id
                LEFT JOIN tskupka_tasks t ON t.export_id=e.id WHERE
                (p.started_at>=? AND p.started_at<?)
                OR EXISTS(SELECT 1 FROM purchase_expenses x WHERE x.purchase_id=p.id AND x.bought_at>=? AND x.bought_at<?)'''
            params = (since, until, since, until)
            total = conn.execute('SELECT COUNT(*) ' + base, params).fetchone()[0]
            rows = conn.execute('''SELECT p.*, e.id AS export_id, e.created_at AS export_created_at, t.task_id, t.state AS submission_state,
                t.remote_status, t.price_minor AS earned_minor, t.price_at, t.error,
                (SELECT COALESCE(SUM(cost_minor),0) FROM purchase_expenses x WHERE x.purchase_id=p.id) AS spent_minor,
                (SELECT COUNT(*) FROM purchase_expenses x WHERE x.purchase_id=p.id) AS bought
                ''' + base + ' ORDER BY p.id DESC LIMIT 50 OFFSET ?', params + (offset,)).fetchall()
            unlinked = [dict(row) for row in conn.execute('''SELECT t.export_id, t.task_id, t.remote_status,
                t.price_minor AS earned_minor, t.price_at FROM tskupka_tasks t JOIN export_batches e ON e.id=t.export_id
                WHERE e.purchase_id IS NULL AND ((t.price_at>=? AND t.price_at<?) OR (e.created_at>=? AND e.created_at<?))
                ORDER BY e.id DESC LIMIT 100''', (since, until, since, until))]
            awaiting = conn.execute("SELECT COUNT(*) FROM tskupka_tasks WHERE task_id IS NOT NULL AND price_minor IS NULL").fetchone()[0]
        daily = [{'date': day, **amounts, 'profit_minor': amounts['earned_minor'] - amounts['spent_minor']} for day, amounts in days.items()]
        spent, earned = sum(r['spent_minor'] for r in daily), sum(r['earned_minor'] for r in daily)
        pairs = [{**dict(row), 'profit_minor': None if row['earned_minor'] is None else row['earned_minor'] - row['spent_minor']} for row in rows]
        return dict(period=period, date=selected.isoformat(), start=start.isoformat(), end=(end-timedelta(days=1)).isoformat(),
                    timezone=timezone, spent_minor=spent, earned_minor=earned, profit_minor=earned-spent,
                    daily=daily, items=pairs, total=total, offset=offset, limit=50, unlinked=unlinked,
                    awaiting_price=awaiting, legacy_spent_minor=sum(r['cost_minor'] for r in legacy))
