"""
Google Sheets logger for Discord Token sales tracking.

Columns:
  A: Дата/Время
  B: ID заявки
  C: Потрачено, ₽
  D: TBR, ₽
  E: TSK, ₽
  F: Прибыль, ₽

Оформление:
  - Заголовок: тёмно-синий фон, белый жирный текст, заморожен
  - Ширины столбцов подогнаны под содержимое
  - Числа хранятся как float (формат #,##0.00), не как строки
  - Строка в процессе: светло-жёлтый фон
  - Завершено с прибылью: светло-зелёный фон
  - Завершено с убытком: светло-красный фон
  - Ошибка: красный фон
"""

import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ── Цветовая схема ────────────────────────────────────────────────────────────

def _rgb(hex_color: str) -> dict:
    """'#RRGGBB' → {red, green, blue} 0..1"""
    h = hex_color.lstrip("#")
    return {
        "red":   int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue":  int(h[4:6], 16) / 255,
    }


C_HEADER_BG = _rgb("#1A237E")   # тёмно-синий
C_HEADER_FG = _rgb("#FFFFFF")   # белый
C_PENDING   = _rgb("#FFF9C4")   # светло-жёлтый  (в процессе)
C_PROFIT    = _rgb("#C8E6C9")   # светло-зелёный (прибыль)
C_LOSS      = _rgb("#FFCDD2")   # светло-красный (убыток)
C_FAILED    = _rgb("#EF9A9A")   # красный        (ошибка)
C_WHITE     = _rgb("#FFFFFF")

HEADERS = [
    "Дата/Время",
    "ID заявки",
    "Потрачено, ₽",
    "TokenBuyRobot, ₽",
    "Tskupka, ₽",
    "Прибыль, ₽",
]

# Ширина столбцов в пикселях
COL_WIDTHS_PX = [160, 230, 120, 140, 120, 120]


class GoogleSheetsLogger:
    """Логирование продаж в Google Sheets. Строка обновляется инкрементально."""

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    COL_DATETIME      = 1
    COL_SUBMISSION_ID = 2
    COL_TOKENS_COST   = 3
    COL_TBR_PRICE     = 4
    COL_TSK_PRICE     = 5
    COL_PROFIT        = 6
    LAST_COL          = 6

    # Столбцы с числовыми значениями (0-based для API)
    NUMERIC_COLS = [2, 3, 4, 5]   # C, D, E, F

    def __init__(self, config: dict):
        self.enabled = bool(config.get("enabled", False))
        self.credentials_path = config.get("credentials_path", "data/credentials.json")
        self.spreadsheet_id = config.get("spreadsheet_id", "")
        self.sheet_name = config.get("sheet_name", "Sales")
        self._sheet = None
        self._spreadsheet = None
        self._sheet_id = None

        if self.enabled:
            self._init_sheet()

    # ── init ───────────────────────────────────────────────────────────────────

    def _init_sheet(self):
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            creds = Credentials.from_service_account_file(
                self.credentials_path, scopes=self.SCOPES
            )
            client = gspread.authorize(creds)
            self._spreadsheet = client.open_by_key(self.spreadsheet_id)
            self._sheet = self._spreadsheet.worksheet(self.sheet_name)
            self._sheet_id = self._sheet.id
            logger.info(
                "✅ Google Sheets подключены: %s / %s",
                self.spreadsheet_id, self.sheet_name,
            )
            self._setup_sheet()
        except Exception as e:
            logger.error("❌ Google Sheets инициализация: %s", e)
            self._sheet = None

    def _setup_sheet(self):
        """Создаёт/обновляет заголовок, ширины столбцов, заморозку."""
        try:
            # Заголовок — вставляем только если строка 1 пуста или другая
            first_row = self._sheet.row_values(1)
            if not first_row or first_row[0] != HEADERS[0]:
                self._sheet.insert_row(
                    HEADERS, index=1, value_input_option="USER_ENTERED"
                )
                logger.info("📊 Sheets: заголовок создан")

            # Форматирование заголовка
            header_rng = f"A1:{self._letter(self.LAST_COL)}1"
            self._sheet.format(header_rng, {
                "backgroundColor": C_HEADER_BG,
                "textFormat": {
                    "bold": True,
                    "fontSize": 10,
                    "foregroundColor": C_HEADER_FG,
                },
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
            })

            # Заморозка строки 1 + ширины столбцов (один batch_update)
            requests = [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": self._sheet_id,
                            "gridProperties": {"frozenRowCount": 1},
                        },
                        "fields": "gridProperties.frozenRowCount",
                    }
                }
            ]
            for i, px in enumerate(COL_WIDTHS_PX):
                requests.append({
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": self._sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": i,
                            "endIndex": i + 1,
                        },
                        "properties": {"pixelSize": px},
                        "fields": "pixelSize",
                    }
                })

            self._spreadsheet.batch_update({"requests": requests})
            logger.info("📊 Sheets: форматирование листа настроено")

        except Exception as e:
            logger.warning("⚠️ Sheets _setup_sheet: %s", e)

    # ── public API ─────────────────────────────────────────────────────────────

    def create_row(self, submission_id: str, tokens_cost: float) -> Optional[int]:
        """Добавляет строку с плейсхолдерами. Возвращает номер строки."""
        if not self.enabled or not self._sheet:
            return None

        row = [
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            submission_id or "",
            self._num(tokens_cost),
            "⏳",
            "⏳",
            "",
        ]
        try:
            result = self._sheet.append_row(row, value_input_option="USER_ENTERED")
            row_num = self._parse_row_num(result)
            if row_num:
                self._format_row(row_num, C_PENDING)
                self._apply_number_format(row_num)
            logger.info("📊 Sheets: строка %s → %s", row_num, submission_id)
            return row_num
        except Exception as e:
            logger.error("❌ Sheets create_row: %s", e)
            return None

    def update_tbr_expected(self, row: int, price: float):
        """TBR вернул ожидаемую цену."""
        self._update(row, self.COL_TBR_PRICE, f"🔄 {self._fmt(price)}")

    def update_tbr_confirmed(self, row: int):
        """TBR подтверждён, ждём завершения."""
        self._update(row, self.COL_TBR_PRICE, "⏳ завершается...")

    def update_tbr_final(self, row: int, price: float):
        """TBR завершён — пишем итоговое число."""
        self._update(row, self.COL_TBR_PRICE, self._num(price))

    def update_recleaning(self, row: int, count: int):
        """Повторная очистка токенов."""
        self._update(row, self.COL_TSK_PRICE, f"🧹 очистка {count} шт.")

    def update_tsk_submitted(self, row: int):
        """Токены отправлены в tskupka."""
        self._update(row, self.COL_TSK_PRICE, "⏳ ожидание...")

    def update_complete(self, row: int, tsk_price: float, profit: float):
        """Круг завершён: пишем числа, красим строку."""
        self._update(row, self.COL_TSK_PRICE, self._num(tsk_price))
        self._update(row, self.COL_PROFIT, self._num(profit))
        self._format_row(row, C_PROFIT if profit >= 0 else C_LOSS)

    def update_failed(self, row: int, stage: str):
        """Ошибка на этапе stage."""
        self._update(row, self.COL_PROFIT, f"❌ {stage}")
        self._format_row(row, C_FAILED)

    # ── internal ───────────────────────────────────────────────────────────────

    def _update(self, row: Optional[int], col: int, value):
        if not self.enabled or not self._sheet or not row:
            return
        try:
            self._sheet.update_cell(row, col, value)
        except Exception as e:
            logger.error("❌ Sheets update_cell(%s,%s): %s", row, col, e)

    def _format_row(self, row: int, bg_color: dict):
        """Закрашивает строку целиком."""
        if not self.enabled or not self._sheet or not row:
            return
        try:
            rng = f"A{row}:{self._letter(self.LAST_COL)}{row}"
            self._sheet.format(rng, {"backgroundColor": bg_color})
        except Exception as e:
            logger.warning("⚠️ Sheets _format_row(%s): %s", row, e)

    def _apply_number_format(self, row: int):
        """Применяет числовой формат #,##0.00 к столбцам C–F."""
        if not self.enabled or not self._spreadsheet or not row:
            return
        try:
            requests = [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": self._sheet_id,
                            "startRowIndex": row - 1,
                            "endRowIndex": row,
                            "startColumnIndex": ci,
                            "endColumnIndex": ci + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {
                                    "type": "NUMBER",
                                    "pattern": "#,##0.00",
                                }
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
                for ci in self.NUMERIC_COLS
            ]
            self._spreadsheet.batch_update({"requests": requests})
        except Exception as e:
            logger.warning("⚠️ Sheets _apply_number_format(%s): %s", row, e)

    @staticmethod
    def _num(value) -> float:
        """Конвертирует в float для записи в ячейку."""
        if value is None:
            return 0.0
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _fmt(value) -> str:
        """Форматирует число как строку для промежуточных статусов."""
        if value is None:
            return "—"
        try:
            return f"{round(float(value), 2):,.2f}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _letter(col: int) -> str:
        """Конвертирует номер столбца в букву: 1→A, 6→F."""
        result = ""
        while col:
            col, rem = divmod(col - 1, 26)
            result = chr(65 + rem) + result
        return result

    @staticmethod
    def _parse_row_num(append_result) -> Optional[int]:
        """Извлекает номер строки из ответа append_row."""
        try:
            updated_range = (
                append_result.get("updates", {}).get("updatedRange", "")
                if isinstance(append_result, dict)
                else ""
            )
            match = re.search(r":?[A-Z]+(\d+)$", updated_range)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return None
