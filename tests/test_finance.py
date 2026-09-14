"""Accounting, batch boundaries and polling with synthetic records only."""
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from modules.database import Database
from modules.finance import Finance, money_minor
from modules.purchase_task import PurchaseTaskManager
from modules.lzt_monitor import PurchaseError
from modules.tskupka import TskupkaService, TskupkaError


class FinanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Database(str(Path(directory.name) / 'test.db'))
        self.finance = Finance(self.db)
        self.now = datetime(2026, 9, 14, 10, tzinfo=ZoneInfo('Europe/Saratov')).timestamp()
        clock = patch('modules.finance.time.time', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('External HTTP prohibited'))
        guard.start()
        self.addCleanup(guard.stop)

    def batch(self, token='synthetic-A', item='item-A', price='10.25'):
        ident = self.finance.start_purchase(1)
        self.finance.record_purchase(ident, item, price)
        self.db.add_token(token, price=float(price), purchase_id=ident)
        self.db.update_token_status(token, 'ready')
        self.finance.finish_purchase(ident, 'done')
        return ident

    def submission(self, purchase_id, token='synthetic-A'):
        export_id = self.db.commit_export([token], [], purchase_id)['export_id']
        service = TskupkaService(self.db, 'synthetic-key')
        service.client.create = Mock(return_value={'task_id': 91})
        service.submit(export_id)
        return service, export_id

    def test_decimal_arithmetic_and_receipt_idempotence(self):
        ident = self.batch(price='0.10')
        self.assertFalse(self.finance.record_purchase(ident, 'item-A', '0.10'))
        self.finance.record_purchase(ident, 'item-B', '0.20')
        report = self.finance.report('day', '2026-09-14')
        self.assertEqual(report['spent_minor'], 30)
        self.assertEqual(report['profit_minor'], -30)
        self.assertEqual(money_minor('1.005'), 101)
        for bad in (True, None, {}, 'NaN', '-1', 'Infinity'):
            with self.assertRaises(ValueError): money_minor(bad)

    def test_poll_every_ten_seconds_survives_restart_and_credits_price_once(self):
        ident = self.batch()
        service, export_id = self.submission(ident)
        service.client.status = Mock()
        self.now += 9
        service.poll_once()
        service.client.status.assert_not_called()
        restarted = TskupkaService(Database(self.db.db_path), 'synthetic-key')
        restarted.client.status = Mock(side_effect=[
            {'id': 91, 'status': 'completed', 'price_result': None, 'amount_paid': 999},
            {'id': 91, 'status': 'completed', 'price_result': '30.15', 'amount_paid': 999},
        ])
        self.now += 1
        restarted.poll_once()
        self.assertEqual(self.finance.report('day', '2026-09-14')['earned_minor'], 0)
        self.now += 9
        restarted.poll_once()
        self.assertEqual(restarted.client.status.call_count, 1)
        self.now += 1
        restarted.poll_once()
        report = self.finance.report('day', '2026-09-14')
        self.assertEqual((report['earned_minor'], report['profit_minor']), (3015, 1990))
        self.assertEqual(report['items'][0]['profit_minor'], 1990)
        self.now += 100
        restarted.poll_once()
        self.assertEqual(restarted.client.status.call_count, 2)
        self.finance.receive_price(export_id, 50)
        self.assertEqual(self.finance.report('day', '2026-09-14')['earned_minor'], 3015)

    def test_zero_price_is_a_result_not_pending(self):
        _, export_id = self.submission(self.batch())
        self.finance.receive_price(export_id, 0)
        self.assertEqual(self.finance.report('day', '2026-09-14')['awaiting_price'], 0)
        self.assertEqual(self.finance.report('day', '2026-09-14')['items'][0]['profit_minor'], -1025)

    def test_poll_lease_and_failure_retry(self):
        service, export_id = self.submission(self.batch())
        self.now += 10
        self.assertTrue(self.finance.claim_poll(export_id))
        self.assertFalse(Finance(Database(self.db.db_path)).claim_poll(export_id))
        self.finance.release_poll(export_id)
        self.now += 10
        service.client.status = Mock(side_effect=TskupkaError('Unavailable'))
        service.poll_once()
        self.assertEqual(service.client.status.call_count, 1)
        service.poll_once()
        self.assertEqual(service.client.status.call_count, 1)
        self.now += 10
        service.poll_once()
        self.assertEqual(service.client.status.call_count, 2)

    def test_unknown_price_structure_is_not_treated_as_zero_or_amount_paid(self):
        service, export_id = self.submission(self.batch())
        service.client.status = Mock(return_value={'id': 91, 'status': 'completed',
                                                  'price_result': {'unknown': 123}, 'amount_paid': 999})
        self.now += 10
        service.poll_once()
        row = self.db.get_tskupka_task(export_id)
        self.assertIsNone(row['price_minor'])
        self.assertIn('price_result', row['error'])

    def test_calendar_periods_and_cross_midnight_income(self):
        self.now = datetime(2026, 9, 13, 23, 59, tzinfo=ZoneInfo('Europe/Saratov')).timestamp()
        _, export_id = self.submission(self.batch())
        self.now += 120
        self.finance.receive_price(export_id, 20)
        yesterday = self.finance.report('day', '2026-09-13')
        today = self.finance.report('day', '2026-09-14')
        # Доход относится к дате закупки (13.09), хотя price_result пришёл 14.09.
        self.assertEqual((yesterday['spent_minor'], yesterday['earned_minor']), (1025, 2000))
        self.assertEqual((today['spent_minor'], today['earned_minor']), (0, 0))
        self.assertEqual(yesterday['items'][0]['spent_minor'], 1025)
        self.assertEqual(today['items'], [])
        week = self.finance.report('week', '2026-09-14')
        self.assertEqual((week['start'], week['end']), ('2026-09-14', '2026-09-20'))
        month = self.finance.report('month', '2026-09-14')
        self.assertEqual((month['start'], month['end'], month['profit_minor']), ('2026-09-01', '2026-09-30', 975))
        self.assertEqual(self.finance.report('month', '2024-02-20')['end'], '2024-02-29')

    def test_orphaned_running_batch_is_finalized_and_exportable(self):
        ident = self.batch()  # готовый токен, закупка завершена
        with self.db.get_connection() as conn:
            conn.execute("UPDATE purchase_batches SET status='running' WHERE id=?", (ident,))
        # Пока закупка «running», выгрузка заблокирована.
        with self.assertRaises(RuntimeError):
            self.finance.export_candidates(ident)
        # После перезапуска осиротевшую закупку закрываем — выгрузка разблокирована.
        self.assertEqual(self.finance.finalize_orphaned_batches(), 1)
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute('SELECT status FROM purchase_batches WHERE id=?', (ident,)).fetchone()[0], 'interrupted')
        self.assertEqual(len(self.finance.export_candidates(ident)), 1)

    def test_purchase_must_finish_and_process_all_records_before_single_export(self):
        ident = self.batch()
        self.db.add_token('synthetic-B', purchase_id=ident)
        with self.assertRaises(RuntimeError): self.finance.export_candidates(ident)
        self.db.update_token_status('synthetic-B', 'invalid')
        self.assertEqual(len(self.finance.export_candidates(ident)), 1)
        self.submission(ident)
        with self.assertRaises(RuntimeError): self.finance.export_candidates(ident)
        with self.assertRaises(RuntimeError): self.db.commit_export(['synthetic-A'], [], ident)

    def test_different_purchases_cannot_be_mixed_or_partially_exported(self):
        a = self.batch()
        b = self.batch('synthetic-B', 'item-B')
        with self.assertRaises(RuntimeError): self.db.commit_export(['synthetic-A', 'synthetic-B'], [], a)
        self.assertEqual([r['token'] for r in self.finance.export_candidates(a)], ['synthetic-A'])
        self.assertEqual([r['token'] for r in self.finance.export_candidates(b)], ['synthetic-B'])
        self.assertEqual(self.finance.export_candidates(None), [])

    def test_expense_survives_intake_failure(self):
        lzt = SimpleNamespace(get_balance=Mock(return_value=100),
                              search_accounts=Mock(return_value=([{'item_id': 44, 'price': 10}], 1)),
                              fast_buy=Mock(return_value=('synthetic-intake-failed', {})))
        manager = PurchaseTaskManager(lzt, self.db, Mock(side_effect=RuntimeError('Intake failed')))
        manager.start(20, 0, 1, 1)
        manager._thread.join(3)
        self.assertEqual(manager.snapshot()['bought'], 1)
        self.assertEqual(self.finance.report('day', '2026-09-14')['spent_minor'], 1000)

    def test_paid_purchase_without_token_still_counts_as_expense(self):
        lzt = SimpleNamespace(get_balance=Mock(return_value=100),
                              search_accounts=Mock(return_value=([{'item_id': 44, 'price': 10}], 1)),
                              fast_buy=Mock(side_effect=PurchaseError('Missing token', purchased=True)))
        manager = PurchaseTaskManager(lzt, self.db, Mock())
        manager.start(20, 0, 1, 1)
        manager._thread.join(3)
        self.assertEqual(manager.snapshot()['bought'], 1)
        self.assertEqual(self.finance.report('day', '2026-09-14')['spent_minor'], 1000)

    def test_migration_snapshots_legacy_daily_totals_once(self):
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO statistics(date, money_spent) VALUES ('2026-09-14', 37.70)")
            conn.execute('DROP TABLE legacy_expenses')
            conn.execute('PRAGMA user_version=5')
        migrated = Database(self.db.db_path)
        report = Finance(migrated).report('day', '2026-09-14')
        self.assertEqual((report['spent_minor'], report['legacy_spent_minor']), (3770, 3770))
        self.batch()
        reopened = Finance(Database(self.db.db_path)).report('day', '2026-09-14')
        self.assertEqual(reopened['spent_minor'], 4795)
        self.assertEqual(reopened['legacy_spent_minor'], 3770)
