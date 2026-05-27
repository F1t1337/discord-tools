"""
Google Sheets logger for Discord Token sales tracking.
Updates the row incrementally as each workflow stage completes.

Columns:
  A: Дата/Время
  B: ID заявки
  C: Потрачено (сумма покупки токенов)
  D: TBR продажа (final_payment)
  E: TSK продажа
  F: Прибыль
"""

import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class GoogleSheetsLogger:
    """Логирование продаж в Google Sheets через Service Account.
    Обновляет строку инкрементально по мере поступления данных.
    """

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    # Индексы столбцов (1-based)
    COL_DATETIME = 1
    COL_SUBMISSION_ID = 2
    COL_TOKENS_COST = 3
    COL_TBR_PRICE = 4
    COL_TSK_PRICE = 5
    COL_PROFIT = 6

    def __init__(self, config: dict):
        self.enabled = bool(config.get("enabled", False))
        self.credentials_path = config.get("credentials_path", "data/credentials.json")
        self.spreadsheet_id = config.get("spreadsheet_id", "")
        self.sheet_name = config.get("sheet_name", "Sales")
        self._sheet = None

        if self.enabled:
            self._init_sheet()

    # ── init ───────────────────────────────────────────────────

    def _init_sheet(self):
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            creds = Credentials.from_service_account_file(
                self.credentials_path, scopes=self.SCOPES
            )
            client = gspread.authorize(creds)
            spreadsheet = client.open_by_key(self.spreadsheet_id)
            self._sheet = spreadsheet.worksheet(self.sheet_name)
            logger.info("✅ Google Sheets подключены: %s / %s", self.spreadsheet_id, self.sheet_name)
        except Exception as e:
            logger.error("❌ Google Sheets ошибка инициализации: %s", e)
            self._sheet = None

    # ── public API ─────────────────────────────────────────────

    def create_row(self, submission_id: str, tokens_cost: float) -> Optional[int]:
        """
        Создаёт начальную строку с плейсхолдерами.
        Возвращает номер строки (1-based) для последующих обновлений.
        """
        if not self.enabled or not self._sheet:
            return None

        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            submission_id or "",
            self._fmt(tokens_cost),
            "⏳ ожидание TBR",
            "⏳ ожидание TSK",
            "⏳",
        ]
        try:
            result = self._sheet.append_row(row, value_input_option="USER_ENTERED")
            row_num = self._parse_row_num(result)
            logger.info("📊 Sheets: создана строка %s для %s", row_num, submission_id)
            return row_num
        except Exception as e:
            logger.error("❌ Sheets create_row: %s", e)
            return None

    def update_tbr_expected(self, row: int, price: float):
        """TBR вернул ожидаемую цену."""
        self._update(row, self.COL_TBR_PRICE, f"🔄 {self._fmt(price)} ₽ (ожид.)")

    def update_tbr_confirmed(self, row: int):
        """TBR заявка подтверждена, ждём завершения."""
        self._update(row, self.COL_TBR_PRICE, "⏳ TBR завершается...")

    def update_tbr_final(self, row: int, price: float):
        """TBR завершён, финальная цена получена."""
        self._update(row, self.COL_TBR_PRICE, f"{self._fmt(price)} ₽ ✅")

    def update_recleaning(self, row: int, count: int):
        """Токены отправлены на повторную очистку."""
        self._update(row, self.COL_TSK_PRICE, f"🧹 очистка {count} шт...")

    def update_tsk_submitted(self, row: int):
        """Токены отправлены в tskupka."""
        self._update(row, self.COL_TSK_PRICE, "⏳ проверка TSK...")

    def update_complete(self, row: int, tsk_price: float, profit: float):
        """Круг завершён. Записываем TSK цену и прибыль."""
        self._update(row, self.COL_TSK_PRICE, f"{self._fmt(tsk_price)} ₽ ✅")
        self._update(row, self.COL_PROFIT, self._fmt(profit))

    def update_failed(self, row: int, stage: str):
        """Ошибка на каком-то этапе."""
        self._update(row, self.COL_PROFIT, f"❌ ошибка ({stage})")

    # ── internal ───────────────────────────────────────────────

    def _update(self, row: Optional[int], col: int, value):
        if not self.enabled or not self._sheet or not row:
            return
        try:
            self._sheet.update_cell(row, col, value)
            logger.debug("📊 Sheets: строка %s, столбец %s = %s", row, col, value)
        except Exception as e:
            logger.error("❌ Sheets update_cell(%s, %s): %s", row, col, e)

    @staticmethod
    def _fmt(value) -> str:
        if value is None:
            return "—"
        try:
            return str(round(float(value), 2))
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _parse_row_num(append_result) -> Optional[int]:
        """Извлекает номер строки из ответа append_row."""
        try:
            updated_range = (
                append_result.get("updates", {}).get("updatedRange", "")
                if isinstance(append_result, dict)
                else ""
            )
            # Формат: 'SheetName!A5:F5'
            match = re.search(r":?[A-Z]+(\d+)$", updated_range)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return None
