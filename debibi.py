
"""
Debibi Accounting POC (SQLite + PySide6)

Scope implemented:
- Journal Item List Base (Card/Deck-ish list with date section headers)
  - Used for Expense List and Account Transaction List
- Balance Sheet Overview (Assets/Liabilities list with section headers)
- Expense Journal Detail (simple expense entry input)
- General Journal Detail (advanced journal input)
- Balance Sheet Account Detail (manage user-managed ASSET/LIAB accounts)

Notes:
- UI hides account_code/entry_uuid/line_no/dc in list views as required.
- DB schema and data format follow the provided design.
- For simplicity, "sticky header" is represented as section header rows (not pinned).
"""

from __future__ import annotations

import sys
import os
import json
import math
import sqlite3
import uuid
import datetime as dt
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Any

from PySide6.QtCore import Qt, QSize, QDate
from PySide6.QtGui import QAction, QFont
from PySide6.QtCore import (
    Qt, QSize, QDate, QByteArray, QBuffer, QIODevice, QPointF, QRectF, QSizeF
)
from PySide6.QtGui import (
    QAction, QFont, QPixmap, QImage, QPainter
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QStackedWidget, QTabWidget, QToolButton, QMessageBox,
    QDialog, QFormLayout, QLineEdit, QTextEdit, QDateEdit, QComboBox, QDoubleSpinBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QCheckBox, QMenuBar,
    QSizePolicy, QFileDialog
)


# -------------------------
# Humanization / Mapping
# -------------------------

ACCOUNT_TYPE_LABEL = {
    "ASSET": "Assets",
    "LIAB": "Liabilities",
    "EQUITY": "Equity",
    "INCOME": "Income",
    "EXPENSE": "Expenses",
}

ENTRY_TYPE_LABEL = {
    "EXPENSE": "Expense",
    "GENERAL": "Journal",
}

# Minimal icon mapping (text-based)
EXPENSE_ICON_BY_CODE = {
    "5000000000": "❓",
    "5000000001": "🍽",
    "5000000002": "👕",
    "5000000003": "🎮",
    "5000000004": "🚌",
    "5000000005": "🏠",
    "5000000006": "📱",
    "5000000007": "🧻",
    "5000000008": "🩺",
    "5000000009": "🧾",
    "5000000010": "🧩",
}

# Optional PDF rendering (for attachment thumbnails)
try:
    from PySide6.QtPdf import QPdfDocument
    PDF_RENDER_AVAILABLE = True
except Exception:
    PDF_RENDER_AVAILABLE = False

def bs_icon(account_code: str, account_type: str) -> str:
    if account_code == "0000000001":
        return "💵"
    if account_type == "ASSET":
        return "🏦"
    if account_type == "LIAB":
        return "💳"
    return "•"

def fmt_money(amount: float, ccy: str) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}{ccy} {abs(amount):,.2f}"

def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()

def qdate_to_iso(d: QDate) -> str:
    return f"{d.year():04d}-{d.month():02d}-{d.day():02d}"

def iso_to_qdate(s: str) -> QDate:
    y, m, d = map(int, s.split("-"))
    return QDate(y, m, d)

def new_uuid() -> str:
    return str(uuid.uuid4())


# -------------------------
# Attachments / MIME helpers
# -------------------------

ATTACH_MAX_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_MIME = {"image/jpeg", "image/png", "application/pdf"}

def guess_mime_from_path(path: str) -> Optional[str]:
    ext = path.lower().rsplit(".", 1)
    if len(ext) < 2:
        return None
    ext = ext[1]
    if ext in ("jpg", "jpeg"):
        return "image/jpeg"
    if ext == "png":
        return "image/png"
    if ext == "pdf":
        return "application/pdf"
    return None

def pixmap_from_image_bytes(data: bytes, max_size: QSize) -> Optional[QPixmap]:
    img = QImage.fromData(data)
    if img.isNull():
        return None
    if max_size.width() > 0 and max_size.height() > 0:
        img = img.scaled(max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return QPixmap.fromImage(img)

def pixmap_from_pdf_bytes(data: bytes, max_size: QSize) -> Optional[QPixmap]:
    if not PDF_RENDER_AVAILABLE:
        return None
    doc = QPdfDocument()
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.ReadOnly)
    err = doc.load(buf)
    try:
        ok_value = QPdfDocument.Error.NoError  # Qt 6.5+
    except AttributeError:
        ok_value = getattr(QPdfDocument, "NoError", 0)  # older enum style
    if err != ok_value or doc.pageCount() == 0:
        return None
    page_size = doc.pagePointSize(0)
    img = QImage(page_size.toSize(), QImage.Format_ARGB32)
    img.fill(Qt.white)
    painter = QPainter(img)
    doc.render(painter, 0, QRectF(QPointF(0, 0), QSizeF(page_size)))
    painter.end()
    if max_size.width() > 0 and max_size.height() > 0:
        img = img.scaled(max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return QPixmap.fromImage(img)


# -------------------------
# Database / Repository
# -------------------------

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS gl_account (
  account_code    TEXT PRIMARY KEY NOT NULL,
  account_name    TEXT NOT NULL UNIQUE,
  account_type    TEXT NOT NULL,
  is_pl           INTEGER NOT NULL,
  is_active       INTEGER NOT NULL,
  is_user_managed INTEGER NOT NULL,
  CHECK (account_type IN ('ASSET','LIAB','EQUITY','INCOME','EXPENSE')),
  CHECK (is_pl IN (0,1)),
  CHECK (is_active IN (0,1)),
  CHECK (is_user_managed IN (0,1)),
  CHECK (account_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]')
);

CREATE TABLE IF NOT EXISTS gl_entry (
  entry_uuid        TEXT PRIMARY KEY NOT NULL,
  modification_date TEXT NOT NULL,
  accounting_date   TEXT NOT NULL,
  entry_type        TEXT NOT NULL,
  entry_title       TEXT,
  entry_text        TEXT,
  CHECK(entry_type IN ('EXPENSE','GENERAL'))
);

CREATE TABLE IF NOT EXISTS gl_entry_item (
  entry_uuid        TEXT NOT NULL,
  line_no           INTEGER NOT NULL,
  account_code      TEXT NOT NULL,
  dc                TEXT NOT NULL,
  amount_domestic   NUMERIC NOT NULL,
  currency_original TEXT NOT NULL,
  amount_original   NUMERIC,
  item_text         TEXT,
  PRIMARY KEY (entry_uuid, line_no),
  FOREIGN KEY (entry_uuid) REFERENCES gl_entry(entry_uuid) ON DELETE CASCADE,
  FOREIGN KEY (account_code) REFERENCES gl_account(account_code),
  CHECK (dc IN ('D','C'))
);

CREATE TABLE IF NOT EXISTS gl_entry_attachment (
  entry_uuid  TEXT PRIMARY KEY NOT NULL,
  file_name   TEXT,
  mime_type   TEXT NOT NULL,
  file_blob   BLOB NOT NULL,
  FOREIGN KEY (entry_uuid) REFERENCES gl_entry(entry_uuid) ON DELETE CASCADE,
  CHECK (mime_type IN ('image/jpeg','image/png','application/pdf'))
);

CREATE TABLE IF NOT EXISTS user_setting (
  setting_key   TEXT PRIMARY KEY NOT NULL,
  setting_value TEXT NOT NULL
);
"""

MASTER_ACCOUNTS = [
    ("0000000001", "Cash", "ASSET", 0, 1, 0),
    ("1000000001", "Dummy Credit card", "LIAB", 0, 1, 1),
    ("3000000000", "Capital", "EQUITY", 0, 1, 0),
    ("4000000000", "Income", "INCOME", 1, 1, 0),
    ("5000000000", "Uncategorized", "EXPENSE", 1, 1, 0),
    ("5000000001", "Food and dining", "EXPENSE", 1, 1, 0),
    ("5000000002", "Clothing and personal care", "EXPENSE", 1, 1, 0),
    ("5000000003", "Entertainment and leisure", "EXPENSE", 1, 1, 0),
    ("5000000004", "Transportation", "EXPENSE", 1, 1, 0),
    ("5000000005", "Housing", "EXPENSE", 1, 1, 0),
    ("5000000006", "Utilities and communications", "EXPENSE", 1, 1, 0),
    ("5000000007", "Household supplies", "EXPENSE", 1, 1, 0),
    ("5000000008", "Healthcare", "EXPENSE", 1, 1, 0),
    ("5000000009", "Taxes and social security", "EXPENSE", 1, 1, 0),
    ("5000000010", "Other expenses", "EXPENSE", 1, 1, 0),
]

DEFAULT_SETTINGS = [
    ("USER_NAME", ""),
    ("CURRENCY_DOMESTIC", "GBP"),
]

class Repo:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")

    def close(self):
        self.conn.close()

    def init_db(self):
        self.conn.executescript(SCHEMA_SQL)
        for row in MASTER_ACCOUNTS:
            self.conn.execute(
                """INSERT OR IGNORE INTO gl_account
                   (account_code, account_name, account_type, is_pl, is_active, is_user_managed)
                   VALUES (?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        for k, v in DEFAULT_SETTINGS:
            self.conn.execute(
                "INSERT OR IGNORE INTO user_setting(setting_key, setting_value) VALUES (?,?)",
                (k, v),
            )
        self.conn.commit()

    def seed_sample_data_if_empty(self):
        c = self.conn.execute("SELECT COUNT(*) AS n FROM gl_entry").fetchone()["n"]
        if c > 0:
            return

        dom = self.get_domestic_currency()

        # Expense entry 1
        e1 = new_uuid()
        self.conn.execute(
            """INSERT INTO gl_entry(entry_uuid, modification_date, accounting_date, entry_type, entry_title, entry_text)
               VALUES (?,?,?,?,?,?)
            """,
            (e1, now_iso(), (dt.date.today() - dt.timedelta(days=2)).isoformat(), "EXPENSE", "Tesco", "Groceries"),
        )
        self.conn.executemany(
            """INSERT INTO gl_entry_item(entry_uuid,line_no,account_code,dc,amount_domestic,currency_original,amount_original,item_text)
               VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                (e1, 1, "5000000001", "D", 18.50, dom, 18.50, None),
                (e1, 2, "5000000007", "D", 6.20, dom, 6.20, None),
                (e1, 3, "0000000001", "C", 24.70, dom, 24.70, None),
            ],
        )

        # Expense entry 2 (foreign currency)
        e2 = new_uuid()
        self.conn.execute(
            """INSERT INTO gl_entry(entry_uuid, modification_date, accounting_date, entry_type, entry_title, entry_text)
               VALUES (?,?,?,?,?,?)
            """,
            (e2, now_iso(), (dt.date.today() - dt.timedelta(days=1)).isoformat(), "EXPENSE", "Amazon US", "Foreign purchase"),
        )
        self.conn.executemany(
            """INSERT INTO gl_entry_item(entry_uuid,line_no,account_code,dc,amount_domestic,currency_original,amount_original,item_text)
               VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                (e2, 1, "5000000002", "D", 30.00, "USD", 38.00, None),
                (e2, 2, "0000000001", "C", 30.00, "USD", 38.00, None),
            ],
        )

        # General entry (pay credit card with cash)
        e3 = new_uuid()
        self.conn.execute(
            """INSERT INTO gl_entry(entry_uuid, modification_date, accounting_date, entry_type, entry_title, entry_text)
               VALUES (?,?,?,?,?,?)
            """,
            (e3, now_iso(), dt.date.today().isoformat(), "GENERAL", "Card Payment", "Pay credit card"),
        )
        self.conn.executemany(
            """INSERT INTO gl_entry_item(entry_uuid,line_no,account_code,dc,amount_domestic,currency_original,amount_original,item_text)
               VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                (e3, 1, "1000000001", "D", 50.00, dom, 50.00, "Credit card decrease"),
                (e3, 2, "0000000001", "C", 50.00, dom, 50.00, "Cash decrease"),
            ],
        )

        self.conn.commit()

    def get_domestic_currency(self) -> str:
        row = self.conn.execute(
            "SELECT setting_value FROM user_setting WHERE setting_key='CURRENCY_DOMESTIC'"
        ).fetchone()
        return row["setting_value"] if row else "GBP"

    # --- Attachments
    def get_attachment(self, entry_uuid: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT entry_uuid, file_name, mime_type, file_blob FROM gl_entry_attachment WHERE entry_uuid=?",
            (entry_uuid,),
        ).fetchone()

    def upsert_attachment(self, entry_uuid: str, file_name: Optional[str], mime_type: str, blob: bytes):
        self.conn.execute(
            """INSERT INTO gl_entry_attachment(entry_uuid, file_name, mime_type, file_blob)
               VALUES(?,?,?,?)
               ON CONFLICT(entry_uuid) DO UPDATE SET
                 file_name=excluded.file_name,
                 mime_type=excluded.mime_type,
                 file_blob=excluded.file_blob""",
            (entry_uuid, file_name, mime_type, sqlite3.Binary(blob)),
        )
        self.conn.commit()

    def delete_attachment(self, entry_uuid: str):
        self.conn.execute("DELETE FROM gl_entry_attachment WHERE entry_uuid=?", (entry_uuid,))
        self.conn.commit()

    # --- Account master queries
    def list_accounts(self, where_sql: str = "", params: Tuple[Any, ...] = ()) -> List[sqlite3.Row]:
        sql = """SELECT account_code, account_name, account_type, is_pl, is_active, is_user_managed
                 FROM gl_account WHERE 1=1 """
        if where_sql:
            sql += " AND " + where_sql
        sql += " ORDER BY account_code"
        return list(self.conn.execute(sql, params).fetchall())

    def list_expense_categories(self) -> List[sqlite3.Row]:
        return self.list_accounts("is_active=1 AND account_type='EXPENSE'")

    def list_asset_accounts(self) -> List[sqlite3.Row]:
        return self.list_accounts("is_active=1 AND account_type='ASSET'")

    def list_payment_accounts(self) -> List[sqlite3.Row]:
        """Payment accounts can be ASSET (cash/bank) or LIAB (credit card)."""
        return self.list_accounts("is_active=1 AND account_type IN ('ASSET','LIAB')")

    def list_user_managed_bs_accounts(self) -> List[sqlite3.Row]:
        return list(
            self.conn.execute(
                """SELECT account_code, account_name, account_type, is_active
                       FROM gl_account
                      WHERE is_user_managed=1
                        AND account_type IN ('ASSET','LIAB')
                   ORDER BY account_type, account_name"""
            ).fetchall()
        )

    def find_account_by_name(
        self,
        account_name: str,
        account_type: Optional[str] = None,
        account_types: Optional[List[str]] = None,
        active_required: bool = True,
    ) -> Optional[sqlite3.Row]:
        """Return account row matched by name (case-insensitive)."""
        if not account_name:
            return None
        sql = """SELECT account_code, account_name, account_type, is_active
                   FROM gl_account
                  WHERE account_name = ? COLLATE NOCASE"""
        params: List[Any] = [account_name]
        if account_type:
            sql += " AND account_type = ?"
            params.append(account_type)
        elif account_types:
            placeholders = ",".join("?" * len(account_types))
            sql += f" AND account_type IN ({placeholders})"
            params.extend(account_types)
        row = self.conn.execute(sql, params).fetchone()
        if not row:
            return None
        if active_required and int(row["is_active"]) != 1:
            return None
        return row

    def find_payment_account_by_name(self, account_name: str) -> Optional[sqlite3.Row]:
        """Find active ASSET or LIAB account by name."""
        return self.find_account_by_name(account_name, account_types=["ASSET", "LIAB"])

    def next_user_managed_code(self, account_type: str) -> str:
        if account_type == "ASSET":
            pattern = "1?????????"
            base_floor = 1000000000
        elif account_type == "LIAB":
            pattern = "2?????????"
            base_floor = 2000000000
        else:
            raise ValueError("account_type must be ASSET or LIAB")

        # Use prefix-based scan (no account_type filter) to avoid collisions
        # when legacy data has incorrect type/code combinations.
        row = self.conn.execute(
            """SELECT printf('%010d', COALESCE(MAX(CAST(account_code AS INTEGER)), ?) + 1) AS next_code
                   FROM gl_account
                  WHERE account_code GLOB ?""",
            (base_floor, pattern),
        ).fetchone()
        return row["next_code"]

    def create_user_managed_account(self, account_name: str, account_type: str, is_active: int = 1) -> str:
        if account_type not in ("ASSET", "LIAB"):
            raise ValueError("account_type must be ASSET or LIAB")
        code = self.next_user_managed_code(account_type)
        self.conn.execute(
            """INSERT INTO gl_account(account_code, account_name, account_type, is_pl, is_active, is_user_managed)
                   VALUES(?, ?, ?, 0, ?, 1)""",
            (code, account_name, account_type, is_active),
        )
        self.conn.commit()
        return code

    def update_user_managed_account(self, account_code: str, account_name: str, is_active: int):
        cur = self.conn.execute(
            """UPDATE gl_account
                      SET account_name=?, is_active=?
                    WHERE account_code=?
                      AND is_user_managed=1
                      AND account_type IN ('ASSET','LIAB')""",
            (account_name, is_active, account_code),
        )
        if cur.rowcount == 0:
            raise ValueError("Account not found or not user managed")
        self.conn.commit()

    def get_user_managed_account(self, account_code: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            """SELECT account_code, account_name, account_type, is_active
                   FROM gl_account
                  WHERE account_code=?
                    AND is_user_managed=1
                    AND account_type IN ('ASSET','LIAB')""",
            (account_code,),
        ).fetchone()

    # --- Entry queries
    def get_entry_header(self, entry_uuid: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM gl_entry WHERE entry_uuid=?", (entry_uuid,)).fetchone()

    def get_entry_items(self, entry_uuid: str) -> List[sqlite3.Row]:
        return list(
            self.conn.execute(
                """SELECT ei.*, a.account_name, a.account_type, a.is_pl
                   FROM gl_entry_item ei
                   JOIN gl_account a ON a.account_code=ei.account_code
                   WHERE ei.entry_uuid=?
                   ORDER BY ei.line_no""",
                (entry_uuid,),
            ).fetchall()
        )

    def delete_entry(self, entry_uuid: str):
        self.conn.execute("DELETE FROM gl_entry_item WHERE entry_uuid=?", (entry_uuid,))
        self.conn.execute("DELETE FROM gl_entry WHERE entry_uuid=?", (entry_uuid,))
        self.conn.commit()

    def save_entry_full_replace(
        self,
        entry_uuid: str,
        accounting_date: str,
        entry_type: str,
        entry_title: Optional[str],
        entry_text: Optional[str],
        items: List[Dict[str, Any]],
        is_new: bool,
    ):
        if not accounting_date or len(accounting_date) != 10:
            raise ValueError("accounting_date must be ISO date YYYY-MM-DD")
        if not items:
            raise ValueError("At least one item is required")

        for it in items:
            ac = it["account_code"]
            row = self.conn.execute("SELECT is_active FROM gl_account WHERE account_code=?", (ac,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown account_code: {ac}")
            if row["is_active"] != 1:
                raise ValueError(f"Inactive account_code: {ac}")
            if it["dc"] not in ("D", "C"):
                raise ValueError("dc must be D or C")

        bal = 0.0
        for it in items:
            amt = float(it["amount_domestic"])
            bal += amt if it["dc"] == "D" else -amt
        if abs(bal) > 1e-6:
            raise ValueError(f"Debit/Credit not balanced (domestic). diff={bal:.6f}")

        mod_date = now_iso()

        if is_new:
            self.conn.execute(
                """INSERT INTO gl_entry(entry_uuid, modification_date, accounting_date, entry_type, entry_title, entry_text)
                   VALUES(?,?,?,?,?,?)""",
                (entry_uuid, mod_date, accounting_date, entry_type, entry_title, entry_text),
            )
        else:
            self.conn.execute(
                """UPDATE gl_entry
                   SET modification_date=?, accounting_date=?, entry_type=?, entry_title=?, entry_text=?
                   WHERE entry_uuid=?""",
                (mod_date, accounting_date, entry_type, entry_title, entry_text, entry_uuid),
            )

        self.conn.execute("DELETE FROM gl_entry_item WHERE entry_uuid=?", (entry_uuid,))
        for idx, it in enumerate(items, start=1):
            self.conn.execute(
                """INSERT INTO gl_entry_item(entry_uuid,line_no,account_code,dc,amount_domestic,currency_original,amount_original,item_text)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    entry_uuid,
                    idx,
                    it["account_code"],
                    it["dc"],
                    it["amount_domestic"],
                    it["currency_original"],
                    it.get("amount_original"),
                    it.get("item_text"),
                ),
            )
        self.conn.commit()

    # --- List queries for UI
    def list_journal_items_base(
        self,
        account_code: Optional[str] = None,
        account_type: Optional[str] = None,
    ) -> List[sqlite3.Row]:
        sql = """
        SELECT
          e.accounting_date,
          e.entry_uuid,
          e.entry_title,
          ei.account_code,
          a.account_type,
          ei.amount_domestic,
          ei.line_no
        FROM gl_entry_item ei
        JOIN gl_entry e   ON e.entry_uuid = ei.entry_uuid
        JOIN gl_account a ON a.account_code = ei.account_code
        WHERE a.is_active=1
        """
        params: List[Any] = []
        if account_code:
            sql += " AND ei.account_code = ?"
            params.append(account_code)
        if account_type:
            sql += " AND a.account_type = ?"
            params.append(account_type)

        sql += " ORDER BY e.accounting_date DESC, e.entry_uuid DESC, ei.line_no ASC"
        return list(self.conn.execute(sql, params).fetchall())

    def list_expense_list(self) -> List[sqlite3.Row]:
        return self.list_journal_items_base(account_type="EXPENSE")

    def list_account_transactions(self, account_code: str) -> List[sqlite3.Row]:
        return self.list_journal_items_base(account_code=account_code)

    def list_balance_sheet_overview(self) -> List[sqlite3.Row]:
        sql = """
        SELECT
          a.account_type,
          a.account_code,
          a.account_name,
          COALESCE(SUM(CASE WHEN ei.dc='D' THEN ei.amount_domestic ELSE -ei.amount_domestic END), 0) AS balance_domestic
        FROM gl_account a
        LEFT JOIN gl_entry_item ei ON ei.account_code = a.account_code
        LEFT JOIN gl_entry e ON e.entry_uuid = ei.entry_uuid
        WHERE a.is_active=1
          AND a.account_type IN ('ASSET','LIAB')
        GROUP BY a.account_type, a.account_code, a.account_name
        ORDER BY
          CASE a.account_type WHEN 'ASSET' THEN 1 WHEN 'LIAB' THEN 2 ELSE 9 END,
          a.account_code
        """
        return list(self.conn.execute(sql).fetchall())


# -------------------------
# JSON Expense Import Core
# -------------------------

class JsonExpenseImportError(Exception):
    pass


@dataclass
class JsonExpenseImportResult:
    entry_uuid: str
    accounting_date: str
    currency_original: str
    total_amount_domestic: float
    line_count: int


class JsonExpenseImportService:
    """Reusable core for importing expense entries from JSON (LLM/API)."""

    def __init__(self, repo: Repo):
        self.repo = repo

    # Public API
    def import_file(self, path: str) -> JsonExpenseImportResult:
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            raise JsonExpenseImportError(f"Failed to read JSON file: {e}") from e
        return self.import_payload(payload)

    def import_payload(self, payload: Any) -> JsonExpenseImportResult:
        data = self._normalize_top(payload)
        norm_lines = [
            self._normalize_line(idx, line, data["currency_original"])
            for idx, line in enumerate(data["lines"], start=1)
        ]
        items, total_dom, total_org = self._build_items(data, norm_lines)
        entry_uuid = new_uuid()
        try:
            self.repo.save_entry_full_replace(
                entry_uuid=entry_uuid,
                accounting_date=data["accounting_date"],
                entry_type="EXPENSE",
                entry_title=data["entry_title"],
                entry_text=data["entry_text"],
                items=items,
                is_new=True,
            )
        except Exception as e:
            raise JsonExpenseImportError(str(e)) from e

        return JsonExpenseImportResult(
            entry_uuid=entry_uuid,
            accounting_date=data["accounting_date"],
            currency_original=data["currency_original"],
            total_amount_domestic=total_dom,
            line_count=len(norm_lines),
        )

    # Normalization / validation helpers
    def _normalize_top(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise JsonExpenseImportError("Top-level JSON must be an object.")

        allowed_keys = {"date", "store", "note", "payment_account", "currency_original", "lines"}
        extra_keys = set(payload.keys()) - allowed_keys
        if extra_keys:
            raise JsonExpenseImportError(f"Unexpected fields: {', '.join(sorted(extra_keys))}")

        if "payment_account" not in payload:
            raise JsonExpenseImportError("payment_account is required.")
        if "lines" not in payload:
            raise JsonExpenseImportError("lines is required.")

        date_raw = payload.get("date")
        if date_raw in (None, ""):
            accounting_date = dt.date.today().isoformat()
        elif isinstance(date_raw, str) and self._is_valid_iso_date(date_raw):
            accounting_date = date_raw
        else:
            raise JsonExpenseImportError("date must be YYYY-MM-DD or null.")

        store = payload.get("store")
        if store is not None:
            if not isinstance(store, str):
                raise JsonExpenseImportError("store must be a string or null.")
            if len(store) > 200:
                raise JsonExpenseImportError("store must be 200 characters or less.")
            store = store.strip() or None

        note = payload.get("note")
        if note is not None:
            if not isinstance(note, str):
                raise JsonExpenseImportError("note must be a string or null.")
            if len(note) > 500:
                raise JsonExpenseImportError("note must be 500 characters or less.")
            note = note.strip() or None

        pay_name = payload.get("payment_account")
        if not isinstance(pay_name, str) or not pay_name.strip():
            raise JsonExpenseImportError("payment_account must be a non-empty string.")
        pay_row = self.repo.find_payment_account_by_name(pay_name.strip())
        if not pay_row:
            raise JsonExpenseImportError(f"payment_account not found/active ASSET or LIAB account: {pay_name}")

        currency = payload.get("currency_original")
        if currency in (None, ""):
            currency = self.repo.get_domestic_currency()
        elif isinstance(currency, str):
            currency = currency.strip().upper()
            if not self._is_valid_currency(currency):
                raise JsonExpenseImportError("currency_original must be a 3-letter code or null.")
        else:
            raise JsonExpenseImportError("currency_original must be a string or null.")

        lines = payload.get("lines")
        if not isinstance(lines, list):
            raise JsonExpenseImportError("lines must be an array.")
        if not (1 <= len(lines) <= 500):
            raise JsonExpenseImportError("lines must contain between 1 and 500 items.")

        return {
            "accounting_date": accounting_date,
            "entry_title": store,
            "entry_text": note,
            "payment_account_code": pay_row["account_code"],
            "payment_account_name": pay_row["account_name"],
            "currency_original": currency,
            "lines": lines,
        }

    def _normalize_line(self, idx: int, line: Any, currency: str) -> Dict[str, Any]:
        if not isinstance(line, dict):
            raise JsonExpenseImportError(f"lines[{idx}] must be an object.")
        allowed_keys = {"expense_category", "note", "amount_domestic", "amount_original"}
        extra_keys = set(line.keys()) - allowed_keys
        if extra_keys:
            raise JsonExpenseImportError(f"lines[{idx}] unexpected fields: {', '.join(sorted(extra_keys))}")

        cat_name = line.get("expense_category")
        if not isinstance(cat_name, str) or not cat_name.strip():
            raise JsonExpenseImportError(f"lines[{idx}].expense_category must be a non-empty string.")
        cat_row = self.repo.find_account_by_name(cat_name.strip(), account_type="EXPENSE")
        if not cat_row:
            raise JsonExpenseImportError(f"lines[{idx}].expense_category not found/active EXPENSE account: {cat_name}")

        ln_note = line.get("note")
        if ln_note is not None:
            if not isinstance(ln_note, str):
                raise JsonExpenseImportError(f"lines[{idx}].note must be a string or null.")
            if len(ln_note) > 500:
                raise JsonExpenseImportError(f"lines[{idx}].note must be 500 characters or less.")
            ln_note = ln_note.strip() or None

        amt_dom = self._parse_positive_number(line.get("amount_domestic"), f"lines[{idx}].amount_domestic")
        amt_org_raw = line.get("amount_original")
        amt_org = self._parse_positive_number(
            amt_org_raw if amt_org_raw is not None else amt_dom,
            f"lines[{idx}].amount_original"
        )

        return {
            "account_code": cat_row["account_code"],
            "account_name": cat_row["account_name"],
            "amount_domestic": amt_dom,
            "amount_original": amt_org,
            "item_text": ln_note,
            "dc": "D",
            "currency_original": currency,
        }

    def _build_items(
        self,
        data: Dict[str, Any],
        lines: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], float, float]:
        items: List[Dict[str, Any]] = []
        total_dom = 0.0
        total_org = 0.0

        for ln in lines:
            items.append(
                {
                    "account_code": ln["account_code"],
                    "dc": "D",
                    "amount_domestic": ln["amount_domestic"],
                    "currency_original": data["currency_original"],
                    "amount_original": ln["amount_original"],
                    "item_text": ln["item_text"],
                }
            )
            total_dom += float(ln["amount_domestic"])
            total_org += float(ln["amount_original"])

        if total_dom <= 0:
            raise JsonExpenseImportError("Total amount_domestic must be greater than zero.")

        items.append(
            {
                "account_code": data["payment_account_code"],
                "dc": "C",
                "amount_domestic": total_dom,
                "currency_original": data["currency_original"],
                "amount_original": total_org,
                "item_text": None,
            }
        )

        # Final guard against imbalance (should not happen)
        diff = sum(it["amount_domestic"] if it["dc"] == "D" else -it["amount_domestic"] for it in items)
        if abs(diff) > 1e-6:
            raise JsonExpenseImportError(f"Debit/Credit not balanced after build. diff={diff:.6f}")

        return items, total_dom, total_org

    @staticmethod
    def _parse_positive_number(value: Any, field_name: str) -> float:
        try:
            num = float(value)
        except Exception:
            raise JsonExpenseImportError(f"{field_name} must be a positive number.") from None
        if not math.isfinite(num) or num <= 0:
            raise JsonExpenseImportError(f"{field_name} must be a positive number.")
        return num

    @staticmethod
    def _is_valid_iso_date(value: str) -> bool:
        try:
            dt.date.fromisoformat(value)
            return True
        except Exception:
            return False

    @staticmethod
    def _is_valid_currency(value: str) -> bool:
        return len(value) == 3 and value.isalpha() and value.isupper()


# -------------------------
# UI: Reusable list widgets
# -------------------------

class SectionHeaderItem(QListWidgetItem):
    def __init__(self, text: str):
        super().__init__(text)
        f = QFont()
        f.setBold(True)
        self.setFont(f)
        self.setFlags(Qt.ItemIsEnabled)  # not selectable
        self.setData(Qt.UserRole, {"kind": "section"})


class CardItemWidget(QWidget):
    def __init__(self, icon_text: str, title: str, amount_text: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        icon = QLabel(icon_text)
        icon.setFixedWidth(28)
        icon.setAlignment(Qt.AlignCenter)

        mid = QLabel(title or "")
        mid.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        right = QLabel(amount_text)
        right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right.setFixedWidth(120)

        lay.addWidget(icon)
        lay.addWidget(mid)
        lay.addWidget(right)


class CardRowItem(QListWidgetItem):
    def __init__(self, payload: Dict[str, Any]):
        super().__init__("")
        self.setSizeHint(QSize(10, 44))
        self.setData(Qt.UserRole, payload)


class JournalCardList(QWidget):
    def __init__(self, repo: Repo, mode: str, account_code: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.mode = mode  # 'expense' or 'account'
        self.account_code = account_code
        self.dom = self.repo.get_domestic_currency()

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setSpacing(6)
        self.list.itemClicked.connect(self.on_item_clicked)
        v.addWidget(self.list)

        self.on_open_entry = None  # callback(entry_uuid)
        self.refresh()

    def refresh(self):
        self.list.clear()
        if self.mode == "expense":
            rows = self.repo.list_expense_list()
        elif self.mode == "account":
            rows = self.repo.list_account_transactions(self.account_code or "")
        else:
            rows = []

        last_date = None
        for r in rows:
            d = r["accounting_date"]
            if d != last_date:
                self.list.addItem(SectionHeaderItem(d))
                last_date = d

            entry_uuid = r["entry_uuid"]
            store = r["entry_title"] or ""
            amt = float(r["amount_domestic"])
            amt_text = fmt_money(amt, self.dom)

            account_code = r["account_code"]
            account_type = r["account_type"]

            icon = EXPENSE_ICON_BY_CODE.get(account_code, "🧾") if self.mode == "expense" else bs_icon(account_code, account_type)

            payload = {
                "kind": "row",
                "entry_uuid": entry_uuid,
            }
            item = CardRowItem(payload)
            self.list.addItem(item)
            self.list.setItemWidget(item, CardItemWidget(icon, store, amt_text))

    def on_item_clicked(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole) or {}
        if data.get("kind") == "section":
            return
        if data.get("kind") == "row" and self.on_open_entry:
            self.on_open_entry(data["entry_uuid"])


class BalanceSheetOverviewWidget(QWidget):
    def __init__(self, repo: Repo, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.dom = self.repo.get_domestic_currency()

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        self.list = QListWidget()
        self.list.setSpacing(6)
        self.list.itemClicked.connect(self.on_item_clicked)
        v.addWidget(self.list)

        self.on_open_account = None  # callback(account_code, account_name)
        self.refresh()

    def refresh(self):
        self.list.clear()
        rows = self.repo.list_balance_sheet_overview()
        last_type = None
        for r in rows:
            t = r["account_type"]
            if t != last_type:
                self.list.addItem(SectionHeaderItem(ACCOUNT_TYPE_LABEL.get(t, t)))
                last_type = t

            account_code = r["account_code"]
            name = r["account_name"]
            bal = float(r["balance_domestic"])
            bal_text = fmt_money(bal, self.dom)

            payload = {
                "kind": "row",
                "account_code": account_code,
                "account_name": name,
            }
            item = CardRowItem(payload)
            self.list.addItem(item)
            self.list.setItemWidget(item, CardItemWidget(bs_icon(account_code, t), name, bal_text))

    def on_item_clicked(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole) or {}
        if data.get("kind") == "section":
            return
        if data.get("kind") == "row" and self.on_open_account:
            self.on_open_account(data["account_code"], data["account_name"])


# -------------------------
# Dialogs
# -------------------------

class ExpenseJournalDetailDialog(QDialog):
    def __init__(self, repo: Repo, entry_uuid: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.dom = self.repo.get_domestic_currency()
        self.entry_uuid = entry_uuid
        self.is_new = entry_uuid is None

        self.setWindowTitle("Expense Journal Detail" if self.is_new else "Expense Journal Detail (View/Edit)")
        self.resize(400, 620)  # compact, dialog-like width slightly smaller than main window

        root = QVBoxLayout(self)

        self.view_mode = not self.is_new

        form = QFormLayout()
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDate(QDate.currentDate())
        self.store = QLineEdit()
        self.note = QTextEdit()
        self.note.setFixedHeight(70)
        self.note_add_btn = QPushButton("Add note")
        self.note_add_btn.clicked.connect(self._show_note_field)
        self.note_shown_with_empty = False
        self.note.textChanged.connect(self._update_note_visibility)
        note_wrap = QVBoxLayout()
        note_wrap.setContentsMargins(0, 0, 0, 0)
        note_wrap.addWidget(self.note)
        note_wrap.addWidget(self.note_add_btn, alignment=Qt.AlignLeft)
        note_wrap_widget = QWidget()
        note_wrap_widget.setLayout(note_wrap)
        self.attach_data: Optional[bytes] = None
        self.attach_mime: Optional[str] = None
        self.attach_name: Optional[str] = None
        self.attach_deleted: bool = False
        self.attach_existing_present: bool = False

        self.currency = QLineEdit()
        self.currency.setPlaceholderText(self.dom)
        self.currency.setText(self.dom)
        self.currency.textChanged.connect(self.on_currency_changed)

        self.payment = QComboBox()
        self.payment_map: Dict[str, str] = {}
        self._load_payment_accounts()

        form.addRow("Date", self.date)
        form.addRow("Store", self.store)
        form.addRow("Currency", self.currency)
        form.addRow("Payment account", self.payment)
        self._build_attachment_ui(form)
        form.addRow("Note", note_wrap_widget)
        root.addLayout(form)

        self.table = QTableWidget(0, 4)
        dom_label = f"Amount ({self.dom})"
        self.table.setHorizontalHeaderLabels(["Category", dom_label, "", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        root.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.btn_edit = QPushButton("Edit")
        self.btn_delete = QPushButton("Delete")
        self.btn_add_line = QPushButton("Add line")
        self.btn_save = QPushButton("Save")
        self.btn_cancel = QPushButton("Close" if self.view_mode else "Cancel")
        self.btn_save.setDefault(True)

        btn_row.addWidget(self.btn_edit)
        btn_row.addWidget(self.btn_delete)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_add_line)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_save)
        root.addLayout(btn_row)

        self.btn_edit.clicked.connect(self.set_edit_mode)
        self.btn_add_line.clicked.connect(self.add_line)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_delete.clicked.connect(self.on_delete)

        self.cat_map: Dict[str, str] = {}
        self._load_categories()
        self._refresh_original_amount_header(self.currency.text())
        self._update_attachment_preview()
        self._update_note_visibility()

        if self.is_new:
            self.btn_edit.setVisible(False)
            self.btn_delete.setVisible(False)
            self.set_edit_mode()
            self.add_line()
        else:
            self.load_entry()
            self.set_view_mode()

    def _load_categories(self):
        self.cat_map.clear()
        for r in self.repo.list_expense_categories():
            self.cat_map[r["account_name"]] = r["account_code"]

    def _build_attachment_ui(self, form: QFormLayout):
        self.attach_preview = QLabel("No attachment")
        self.attach_preview.setAlignment(Qt.AlignLeft)
        self.attach_preview.setMinimumSize(240, 160)
        self.attach_preview.setMaximumSize(320, 220)
        self.attach_preview.setStyleSheet("border: 1px solid #ccc; background: #fafafa;")

        self.attach_name_lbl = QLabel("None")
        self.attach_name_lbl.setStyleSheet("color: #666;")

        self.attach_add_btn = QPushButton("Add / Replace")
        self.attach_remove_btn = QPushButton("Remove")
        self.attach_add_btn.clicked.connect(self.on_select_attachment)
        self.attach_remove_btn.clicked.connect(self.on_remove_attachment)

        btns = QHBoxLayout()
        btns.addWidget(self.attach_add_btn)
        btns.addWidget(self.attach_remove_btn)
        btns.addStretch(1)

        container = QVBoxLayout()
        container.setContentsMargins(0, 0, 0, 0)
        container.addWidget(self.attach_preview)
        container.addWidget(self.attach_name_lbl)
        container.addLayout(btns)

        wrap = QWidget()
        wrap.setLayout(container)
        wrap.setContentsMargins(0, 0, 0, 0)
        form.addRow("Attachment", wrap)

    def _show_note_field(self):
        if self.view_mode:
            return
        self.note_shown_with_empty = True
        self._update_note_visibility()
        self.note.setFocus()

    def _update_note_visibility(self):
        has_text = bool(self.note.toPlainText().strip())
        if has_text:
            self.note_shown_with_empty = False
        show_note = has_text or self.note_shown_with_empty
        self.note.setVisible(show_note)
        self.note_add_btn.setVisible(not show_note)

    def _refresh_original_amount_header(self, ccy: str):
        ccy = (ccy or "").strip().upper()
        label = f"Amount ({ccy})" if ccy else "Amount"
        item = self.table.horizontalHeaderItem(2)
        if item:
            item.setText(label)
        else:
            self.table.setHorizontalHeaderItem(2, QTableWidgetItem(label))

    def _update_attachment_preview(self):
        max_size = QSize(300, 200)
        pixmap: Optional[QPixmap] = None
        if self.attach_data and self.attach_mime:
            if self.attach_mime in ("image/jpeg", "image/png"):
                pixmap = pixmap_from_image_bytes(self.attach_data, max_size)
            elif self.attach_mime == "application/pdf":
                pixmap = pixmap_from_pdf_bytes(self.attach_data, max_size)
        if pixmap:
            self.attach_preview.setPixmap(pixmap)
            self.attach_preview.setScaledContents(False)
        else:
            self.attach_preview.setPixmap(QPixmap())
            self.attach_preview.setText("No attachment" if not self.attach_deleted else "Will remove on save")

        name_text = self.attach_name if self.attach_name else "None"
        if self.attach_deleted:
            name_text += " (removed)"
        self.attach_name_lbl.setText(name_text)
        self.attach_remove_btn.setEnabled(
            not self.view_mode and (self.attach_data is not None or self.attach_existing_present or self.attach_deleted)
        )

    def on_select_attachment(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select attachment",
            "",
            "Images/PDF (*.jpg *.jpeg *.png *.pdf)"
        )
        if not path:
            return
        mime = guess_mime_from_path(path)
        if mime not in ALLOWED_MIME:
            QMessageBox.warning(self, "Invalid file", "Only JPG, PNG, or PDF files are allowed.")
            return
        size = os.path.getsize(path)
        if size > ATTACH_MAX_BYTES:
            QMessageBox.warning(self, "File too large", "File must be 10MB or smaller.")
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read file: {e}")
            return

        self.attach_data = data
        self.attach_mime = mime
        self.attach_name = os.path.basename(path)
        self.attach_deleted = False
        # existing attachment will be replaced on save
        self._update_attachment_preview()

    def on_remove_attachment(self):
        self.attach_data = None
        self.attach_mime = None
        self.attach_name = None
        self.attach_deleted = True
        self.attach_existing_present = False
        self._update_attachment_preview()

    def _refresh_original_amount_header(self, ccy: str):
        ccy = (ccy or "").strip().upper()
        label = f"Amount ({ccy})" if ccy else "Amount"
        item = self.table.horizontalHeaderItem(2)
        if item:
            item.setText(label)
        else:
            self.table.setHorizontalHeaderItem(2, QTableWidgetItem(label))

    def _load_payment_accounts(self):
        rows = self.repo.list_payment_accounts()
        self.payment.clear()
        self.payment_map.clear()
        for r in rows:
            self.payment.addItem(r["account_name"])
            self.payment_map[r["account_name"]] = r["account_code"]
        if "Cash" in self.payment_map:
            self.payment.setCurrentText("Cash")

    def on_currency_changed(self, ccy: str):
        ccy = ccy.strip().upper() if ccy else ""
        self._refresh_original_amount_header(ccy or self.dom)
        is_foreign = (ccy != "" and ccy != self.dom)
        self.table.setColumnHidden(2, not is_foreign)

    def set_view_mode(self):
        self.view_mode = True
        for w in [self.date, self.store, self.note, self.currency, self.payment, self.note_add_btn]:
            w.setEnabled(False)
        self.table.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_add_line.setEnabled(False)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(True)
        self.attach_add_btn.setEnabled(False)
        self.attach_remove_btn.setEnabled(False)
        self._update_attachment_preview()
        self.note_shown_with_empty = False
        self._update_note_visibility()

    def set_edit_mode(self):
        self.view_mode = False
        for w in [self.date, self.store, self.note, self.currency, self.payment, self.note_add_btn]:
            w.setEnabled(True)
        self.table.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_add_line.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(False)
        self.btn_cancel.setText("Cancel")
        self.attach_add_btn.setEnabled(True)
        self.attach_remove_btn.setEnabled(True)
        self._update_attachment_preview()
        self._update_note_visibility()

    def _update_attachment_preview(self):
        max_size = QSize(300, 200)
        has_attachment = (self.attach_data is not None or self.attach_existing_present) and not self.attach_deleted
        pixmap: Optional[QPixmap] = None
        if has_attachment and self.attach_data and self.attach_mime:
            if self.attach_mime in ("image/jpeg", "image/png"):
                pixmap = pixmap_from_image_bytes(self.attach_data, max_size)
            elif self.attach_mime == "application/pdf":
                pixmap = pixmap_from_pdf_bytes(self.attach_data, max_size)

        self.attach_preview.setVisible(has_attachment)
        if has_attachment:
            if pixmap:
                self.attach_preview.setPixmap(pixmap)
                self.attach_preview.setScaledContents(False)
                self.attach_preview.setText("")
            else:
                self.attach_preview.setPixmap(QPixmap())
                self.attach_preview.setText("Preview not available")
        else:
            self.attach_preview.setPixmap(QPixmap())
            self.attach_preview.setText("")

        name_text = self.attach_name if self.attach_name else "None"
        if self.attach_deleted:
            name_text += " (removed)"
        self.attach_name_lbl.setText(name_text)
        self.attach_remove_btn.setEnabled(
            not self.view_mode and (self.attach_data is not None or self.attach_existing_present or self.attach_deleted)
        )

    def on_select_attachment(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select attachment",
            "",
            "Images/PDF (*.jpg *.jpeg *.png *.pdf)"
        )
        if not path:
            return
        mime = guess_mime_from_path(path)
        if mime not in ALLOWED_MIME:
            QMessageBox.warning(self, "Invalid file", "Only JPG, PNG, or PDF files are allowed.")
            return
        size = os.path.getsize(path)
        if size > ATTACH_MAX_BYTES:
            QMessageBox.warning(self, "File too large", "File must be 10MB or smaller.")
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read file: {e}")
            return

        self.attach_data = data
        self.attach_mime = mime
        self.attach_name = os.path.basename(path)
        self.attach_deleted = False
        self._update_attachment_preview()

    def on_remove_attachment(self):
        self.attach_data = None
        self.attach_mime = None
        self.attach_name = None
        self.attach_deleted = True
        self.attach_existing_present = False
        self._update_attachment_preview()

    def add_line(self):
        row = self.table.rowCount()
        self.table.insertRow(row)

        cat = QComboBox()
        for name in self.cat_map.keys():
            cat.addItem(name)
        self.table.setCellWidget(row, 0, cat)

        sp = QDoubleSpinBox()
        sp.setRange(0, 10_000_000)
        sp.setDecimals(2)
        sp.setSingleStep(1.0)
        self.table.setCellWidget(row, 1, sp)

        sp2 = QDoubleSpinBox()
        sp2.setRange(0, 10_000_000)
        sp2.setDecimals(2)
        sp2.setSingleStep(1.0)
        self.table.setCellWidget(row, 2, sp2)

        rm = QPushButton("Remove")
        rm.clicked.connect(lambda _, b=rm: self.remove_line_by_button(b))
        self.table.setCellWidget(row, 3, rm)

        self.on_currency_changed(self.currency.text())

    def remove_line_by_button(self, btn: QPushButton):
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 3) is btn:
                self.table.removeRow(r)
                return

    def load_entry(self):
        h = self.repo.get_entry_header(self.entry_uuid)
        if not h or h["entry_type"] != "EXPENSE":
            QMessageBox.critical(self, "Error", "Entry not found or not an EXPENSE entry.")
            self.reject()
            return

        self.date.setDate(iso_to_qdate(h["accounting_date"]))
        self.store.setText(h["entry_title"] or "")
        self.note.setPlainText(h["entry_text"] or "")
        self._update_note_visibility()

        items = self.repo.get_entry_items(self.entry_uuid)
        if items:
            self.currency.setText(items[0]["currency_original"])

        att = self.repo.get_attachment(self.entry_uuid)
        if att:
            self.attach_data = att["file_blob"]
            self.attach_mime = att["mime_type"]
            self.attach_name = att["file_name"]
            self.attach_deleted = False
            self.attach_existing_present = True
        else:
            self.attach_data = None
            self.attach_mime = None
            self.attach_name = None
            self.attach_deleted = False
            self.attach_existing_present = False
        self._update_attachment_preview()

        pay_code = None
        for it in items:
            if it["account_type"] == "ASSET" and it["dc"] == "C":
                pay_code = it["account_code"]
                break
        if pay_code:
            for name, code in self.payment_map.items():
                if code == pay_code:
                    self.payment.setCurrentText(name)
                    break

        self.table.setRowCount(0)
        for it in items:
            if it["account_type"] == "EXPENSE" and it["dc"] == "D":
                self.add_line()
                row = self.table.rowCount() - 1
                cat = self.table.cellWidget(row, 0)
                sp = self.table.cellWidget(row, 1)
                sp2 = self.table.cellWidget(row, 2)
                assert isinstance(cat, QComboBox) and isinstance(sp, QDoubleSpinBox) and isinstance(sp2, QDoubleSpinBox)
                cat.setCurrentText(it["account_name"])
                sp.setValue(float(it["amount_domestic"]))
                if it["amount_original"] is not None:
                    sp2.setValue(float(it["amount_original"]))
        self.on_currency_changed(self.currency.text())

    def _collect_items(self) -> List[Dict[str, Any]]:
        ccy_raw = self.currency.text().strip().upper()
        ccy = ccy_raw or self.dom
        is_foreign = (ccy != self.dom)

        items: List[Dict[str, Any]] = []
        total_dom = 0.0
        total_org = 0.0

        for r in range(self.table.rowCount()):
            cat = self.table.cellWidget(r, 0)
            sp = self.table.cellWidget(r, 1)
            sp2 = self.table.cellWidget(r, 2)
            if not (isinstance(cat, QComboBox) and isinstance(sp, QDoubleSpinBox) and isinstance(sp2, QDoubleSpinBox)):
                continue
            name = cat.currentText()
            code = self.cat_map.get(name)
            if not code:
                raise ValueError("Invalid expense category selection")
            amt_dom = float(sp.value())
            if amt_dom <= 0:
                continue
            if is_foreign:
                amt_org = float(sp2.value())
                if amt_org <= 0:
                    raise ValueError("Original amount is required when currency is foreign")
            else:
                amt_org = amt_dom
            items.append({
                "account_code": code,
                "dc": "D",
                "amount_domestic": amt_dom,
                "currency_original": ccy,
                "amount_original": amt_org,
                "item_text": None,
            })
            total_dom += amt_dom
            total_org += amt_org

        if not items:
            raise ValueError("Add at least one expense line with amount > 0")

        pay_name = self.payment.currentText()
        pay_code = self.payment_map.get(pay_name)
        if not pay_code:
            raise ValueError("Payment account is required")

        items.append({
            "account_code": pay_code,
            "dc": "C",
            "amount_domestic": total_dom,
            "currency_original": ccy,
            "amount_original": total_org,
            "item_text": None,
        })
        return items

    def _save_attachment(self):
        if not self.entry_uuid:
            return
        if self.attach_data and self.attach_mime:
            self.repo.upsert_attachment(self.entry_uuid, self.attach_name, self.attach_mime, self.attach_data)
            self.attach_existing_present = True
            self.attach_deleted = False
        elif self.attach_deleted or self.attach_existing_present:
            self.repo.delete_attachment(self.entry_uuid)
            self.attach_existing_present = False

    def on_save(self):
        try:
            accounting_date = qdate_to_iso(self.date.date())
            entry_title = self.store.text().strip() or None
            entry_text = self.note.toPlainText().strip() or None

            if self.is_new:
                self.entry_uuid = new_uuid()

            items = self._collect_items()

            self.repo.save_entry_full_replace(
                entry_uuid=self.entry_uuid,
                accounting_date=accounting_date,
                entry_type="EXPENSE",
                entry_title=entry_title,
                entry_text=entry_text,
                items=items,
                is_new=self.is_new,
            )
            self.is_new = False
            self._save_attachment()
            QMessageBox.information(self, "Saved", "Entry saved.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def on_delete(self):
        if self.is_new or not self.entry_uuid:
            return
        if QMessageBox.question(self, "Delete", "Delete this entry?") != QMessageBox.Yes:
            return
        try:
            self.repo.delete_entry(self.entry_uuid)
            QMessageBox.information(self, "Deleted", "Entry deleted.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Delete failed", str(e))


class GeneralJournalDetailDialog(QDialog):
    def __init__(self, repo: Repo, entry_uuid: Optional[str] = None, parent=None, start_edit_mode: bool = False):
        super().__init__(parent)
        self.repo = repo
        self.dom = self.repo.get_domestic_currency()
        self.entry_uuid = entry_uuid
        self.is_new = entry_uuid is None
        self.start_edit_mode = start_edit_mode

        self.setWindowTitle("General Journal Detail" if self.is_new else "General Journal Detail (View/Edit)")
        self.resize(860, 560)

        root = QVBoxLayout(self)
        self.view_mode = not self.is_new

        form = QFormLayout()
        self.entry_type = QComboBox()
        self.entry_type.addItems(["EXPENSE", "GENERAL"])
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDate(QDate.currentDate())
        self.title = QLineEdit()
        self.note = QTextEdit()
        self.note.setFixedHeight(70)
        self.note_add_btn = QPushButton("Add note")
        self.note_add_btn.clicked.connect(self._show_note_field)
        self.note_shown_with_empty = False
        self.note.textChanged.connect(self._update_note_visibility)
        note_wrap = QVBoxLayout()
        note_wrap.setContentsMargins(0, 0, 0, 0)
        note_wrap.addWidget(self.note)
        note_wrap.addWidget(self.note_add_btn, alignment=Qt.AlignLeft)
        note_wrap_widget = QWidget()
        note_wrap_widget.setLayout(note_wrap)
        self.attach_data: Optional[bytes] = None
        self.attach_mime: Optional[str] = None
        self.attach_name: Optional[str] = None
        self.attach_deleted: bool = False
        self.attach_existing_present: bool = False
        form.addRow("Type", self.entry_type)
        form.addRow("Date", self.date)
        form.addRow("Title (Vendor)", self.title)
        self._build_attachment_ui(form)
        form.addRow("Note", note_wrap_widget)
        root.addLayout(form)

        self.table = QTableWidget(0, 7)
        dom_label = f"Amount ({self.dom})"
        self.table.setHorizontalHeaderLabels(["Account", "D/C", dom_label, "Currency", "Original amount", "Item note", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in [1, 2, 3, 4, 6]:
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        root.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.btn_edit = QPushButton("Edit")
        self.btn_delete = QPushButton("Delete")
        self.btn_add_line = QPushButton("Add line")
        self.btn_save = QPushButton("Save")
        self.btn_cancel = QPushButton("Close" if self.view_mode else "Cancel")
        self.btn_save.setDefault(True)

        btn_row.addWidget(self.btn_edit)
        btn_row.addWidget(self.btn_delete)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_add_line)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_save)
        root.addLayout(btn_row)

        self.btn_edit.clicked.connect(self.set_edit_mode)
        self.btn_add_line.clicked.connect(self.add_line)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_delete.clicked.connect(self.on_delete)

        self.accounts = self.repo.list_accounts("is_active=1")
        self.account_map = {r["account_name"]: r["account_code"] for r in self.accounts}
        self._update_attachment_preview()
        self._update_note_visibility()

        if self.is_new:
            self.btn_edit.setVisible(False)
            self.btn_delete.setVisible(False)
            self.set_edit_mode()
            self.add_line()
            self.add_line()
        else:
            self.load_entry()
            if self.start_edit_mode:
                self.set_edit_mode()
            else:
                self.set_view_mode()

    def _build_attachment_ui(self, form: QFormLayout):
        self.attach_preview = QLabel("No attachment")
        self.attach_preview.setAlignment(Qt.AlignLeft)
        self.attach_preview.setMinimumSize(240, 160)
        self.attach_preview.setMaximumSize(320, 220)
        self.attach_preview.setStyleSheet("border: 1px solid #ccc; background: #fafafa;")

        self.attach_name_lbl = QLabel("None")
        self.attach_name_lbl.setStyleSheet("color: #666;")

        self.attach_add_btn = QPushButton("Add / Replace")
        self.attach_remove_btn = QPushButton("Remove")
        self.attach_add_btn.clicked.connect(self.on_select_attachment)
        self.attach_remove_btn.clicked.connect(self.on_remove_attachment)

        btns = QHBoxLayout()
        btns.addWidget(self.attach_add_btn)
        btns.addWidget(self.attach_remove_btn)
        btns.addStretch(1)

        container = QVBoxLayout()
        container.setContentsMargins(0, 0, 0, 0)
        container.addWidget(self.attach_preview)
        container.addWidget(self.attach_name_lbl)
        container.addLayout(btns)

        wrap = QWidget()
        wrap.setLayout(container)
        wrap.setContentsMargins(0, 0, 0, 0)
        form.addRow("Attachment", wrap)

    def _show_note_field(self):
        if self.view_mode:
            return
        self.note_shown_with_empty = True
        self._update_note_visibility()
        self.note.setFocus()

    def _update_note_visibility(self):
        has_text = bool(self.note.toPlainText().strip())
        if has_text:
            self.note_shown_with_empty = False
        show_note = has_text or self.note_shown_with_empty
        self.note.setVisible(show_note)
        self.note_add_btn.setVisible(not show_note)

    def set_view_mode(self):
        self.view_mode = True
        for w in [self.entry_type, self.date, self.title, self.note, self.note_add_btn]:
            w.setEnabled(False)
        self.table.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_add_line.setEnabled(False)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(True)
        self.attach_add_btn.setEnabled(False)
        self.attach_remove_btn.setEnabled(False)
        self._update_attachment_preview()
        self.note_shown_with_empty = False
        self._update_note_visibility()

    def set_edit_mode(self):
        self.view_mode = False
        for w in [self.entry_type, self.date, self.title, self.note, self.note_add_btn]:
            w.setEnabled(True)
        self.table.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_add_line.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(False)
        self.btn_cancel.setText("Cancel")
        self.attach_add_btn.setEnabled(True)
        self.attach_remove_btn.setEnabled(True)
        self._update_attachment_preview()
        self._update_note_visibility()

    def _update_attachment_preview(self):
        max_size = QSize(300, 200)
        has_attachment = (self.attach_data is not None or self.attach_existing_present) and not self.attach_deleted
        pixmap: Optional[QPixmap] = None
        if has_attachment and self.attach_data and self.attach_mime:
            if self.attach_mime in ("image/jpeg", "image/png"):
                pixmap = pixmap_from_image_bytes(self.attach_data, max_size)
            elif self.attach_mime == "application/pdf":
                pixmap = pixmap_from_pdf_bytes(self.attach_data, max_size)

        self.attach_preview.setVisible(has_attachment)
        if has_attachment:
            if pixmap:
                self.attach_preview.setPixmap(pixmap)
                self.attach_preview.setScaledContents(False)
                self.attach_preview.setText("")
            else:
                self.attach_preview.setPixmap(QPixmap())
                self.attach_preview.setText("Preview not available")
        else:
            self.attach_preview.setPixmap(QPixmap())
            self.attach_preview.setText("")

        name_text = self.attach_name if self.attach_name else "None"
        if self.attach_deleted:
            name_text += " (removed)"
        self.attach_name_lbl.setText(name_text)
        self.attach_remove_btn.setEnabled(
            not self.view_mode and (self.attach_data is not None or self.attach_existing_present or self.attach_deleted)
        )

    def on_select_attachment(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select attachment",
            "",
            "Images/PDF (*.jpg *.jpeg *.png *.pdf)"
        )
        if not path:
            return
        mime = guess_mime_from_path(path)
        if mime not in ALLOWED_MIME:
            QMessageBox.warning(self, "Invalid file", "Only JPG, PNG, or PDF files are allowed.")
            return
        size = os.path.getsize(path)
        if size > ATTACH_MAX_BYTES:
            QMessageBox.warning(self, "File too large", "File must be 10MB or smaller.")
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read file: {e}")
            return

        self.attach_data = data
        self.attach_mime = mime
        self.attach_name = os.path.basename(path)
        self.attach_deleted = False
        self._update_attachment_preview()

    def on_remove_attachment(self):
        self.attach_data = None
        self.attach_mime = None
        self.attach_name = None
        self.attach_deleted = True
        self.attach_existing_present = False
        self._update_attachment_preview()

    def add_line(self):
        row = self.table.rowCount()
        self.table.insertRow(row)

        acc = QComboBox()
        for r in self.accounts:
            acc.addItem(r["account_name"])
        self.table.setCellWidget(row, 0, acc)

        dc = QComboBox()
        dc.addItems(["D", "C"])
        self.table.setCellWidget(row, 1, dc)

        amt = QDoubleSpinBox()
        amt.setRange(0, 10_000_000)
        amt.setDecimals(2)
        amt.setSingleStep(1.0)
        self.table.setCellWidget(row, 2, amt)

        ccy = QComboBox()
        for c in [self.dom, "USD", "EUR", "JPY", "CNY"]:
            if ccy.findText(c) < 0:
                ccy.addItem(c)
        ccy.setCurrentText(self.dom)
        self.table.setCellWidget(row, 3, ccy)

        org = QDoubleSpinBox()
        org.setRange(0, 10_000_000)
        org.setDecimals(2)
        org.setSingleStep(1.0)
        self.table.setCellWidget(row, 4, org)

        note = QLineEdit()
        self.table.setCellWidget(row, 5, note)

        rm = QPushButton("Remove")
        rm.clicked.connect(lambda _, b=rm: self.remove_line_by_button(b))
        self.table.setCellWidget(row, 6, rm)

    def remove_line_by_button(self, btn: QPushButton):
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 6) is btn:
                self.table.removeRow(r)
                return

    def load_entry(self):
        h = self.repo.get_entry_header(self.entry_uuid)
        if not h:
            QMessageBox.critical(self, "Error", "Entry not found.")
            self.reject()
            return
        self.entry_type.setCurrentText(h["entry_type"])
        self.date.setDate(iso_to_qdate(h["accounting_date"]))
        self.title.setText(h["entry_title"] or "")
        self.note.setPlainText(h["entry_text"] or "")
        self._update_note_visibility()

        att = self.repo.get_attachment(self.entry_uuid)
        if att:
            self.attach_data = att["file_blob"]
            self.attach_mime = att["mime_type"]
            self.attach_name = att["file_name"]
            self.attach_deleted = False
            self.attach_existing_present = True
        else:
            self.attach_data = None
            self.attach_mime = None
            self.attach_name = None
            self.attach_deleted = False
            self.attach_existing_present = False
        self._update_attachment_preview()

        items = self.repo.get_entry_items(self.entry_uuid)
        self.table.setRowCount(0)
        for it in items:
            self.add_line()
            row = self.table.rowCount() - 1
            acc = self.table.cellWidget(row, 0)
            dc = self.table.cellWidget(row, 1)
            amt = self.table.cellWidget(row, 2)
            ccy = self.table.cellWidget(row, 3)
            org = self.table.cellWidget(row, 4)
            note = self.table.cellWidget(row, 5)
            assert isinstance(acc, QComboBox) and isinstance(dc, QComboBox)
            assert isinstance(amt, QDoubleSpinBox) and isinstance(ccy, QComboBox) and isinstance(org, QDoubleSpinBox)
            assert isinstance(note, QLineEdit)

            acc.setCurrentText(it["account_name"])
            dc.setCurrentText(it["dc"])
            amt.setValue(float(it["amount_domestic"]))
            ccy.setCurrentText(it["currency_original"])
            if it["amount_original"] is not None:
                org.setValue(float(it["amount_original"]))
            note.setText(it["item_text"] or "")

    def _collect_items(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for r in range(self.table.rowCount()):
            acc = self.table.cellWidget(r, 0)
            dc = self.table.cellWidget(r, 1)
            amt = self.table.cellWidget(r, 2)
            ccy = self.table.cellWidget(r, 3)
            org = self.table.cellWidget(r, 4)
            note = self.table.cellWidget(r, 5)
            if not all([
                isinstance(acc, QComboBox), isinstance(dc, QComboBox), isinstance(amt, QDoubleSpinBox),
                isinstance(ccy, QComboBox), isinstance(org, QDoubleSpinBox), isinstance(note, QLineEdit)
            ]):
                continue
            name = acc.currentText()
            account_code = self.account_map.get(name)
            if not account_code:
                raise ValueError("Invalid account selection")

            amt_dom = float(amt.value())
            if amt_dom <= 0:
                continue

            cur = ccy.currentText()
            if cur == self.dom:
                amt_org = float(org.value()) if org.value() > 0 else amt_dom
            else:
                amt_org = float(org.value())
                if amt_org <= 0:
                    raise ValueError("Original amount is required for foreign currency lines")

            items.append({
                "account_code": account_code,
                "dc": dc.currentText(),
                "amount_domestic": amt_dom,
                "currency_original": cur,
                "amount_original": amt_org,
                "item_text": note.text().strip() or None,
            })
        if not items:
            raise ValueError("Add at least one line with amount > 0")
        return items

    def _save_attachment(self):
        if not self.entry_uuid:
            return
        if self.attach_data and self.attach_mime:
            self.repo.upsert_attachment(self.entry_uuid, self.attach_name, self.attach_mime, self.attach_data)
            self.attach_existing_present = True
            self.attach_deleted = False
        elif self.attach_deleted or self.attach_existing_present:
            self.repo.delete_attachment(self.entry_uuid)
            self.attach_existing_present = False

    def on_save(self):
        try:
            accounting_date = qdate_to_iso(self.date.date())
            entry_type = self.entry_type.currentText()
            title = self.title.text().strip() or None
            note = self.note.toPlainText().strip() or None

            if self.is_new:
                self.entry_uuid = new_uuid()

            items = self._collect_items()

            self.repo.save_entry_full_replace(
                entry_uuid=self.entry_uuid,
                accounting_date=accounting_date,
                entry_type=entry_type,
                entry_title=title,
                entry_text=note,
                items=items,
                is_new=self.is_new,
            )
            self.is_new = False
            self._save_attachment()
            QMessageBox.information(self, "Saved", "Entry saved.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def on_delete(self):
        if self.is_new or not self.entry_uuid:
            return
        if QMessageBox.question(self, "Delete", "Delete this entry?") != QMessageBox.Yes:
            return
        try:
            self.repo.delete_entry(self.entry_uuid)
            QMessageBox.information(self, "Deleted", "Entry deleted.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Delete failed", str(e))


class BalanceSheetAccountEditDialog(QDialog):
    """Add/Edit dialog for user-managed BS accounts (ASSET/LIAB)."""

    def __init__(self, repo: Repo, account_code: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.account_code = account_code
        self.is_new = account_code is None

        self.setWindowTitle("Add Balance Sheet Account" if self.is_new else "Balance Sheet Account Detail")
        self.resize(420, 220)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.typ = QComboBox()
        self.typ.addItems(["ASSET", "LIAB"])
        self.name = QLineEdit()
        self.active = QCheckBox("Active")
        self.active.setChecked(True)

        form.addRow("Type", self.typ)
        form.addRow("Name", self.name)
        form.addRow("", self.active)
        root.addLayout(form)

        btns = QHBoxLayout()
        self.btn_edit = QPushButton("Edit")
        self.btn_save = QPushButton("Save")
        self.btn_cancel = QPushButton("Close" if not self.is_new else "Cancel")
        self.btn_save.setDefault(True)

        if not self.is_new:
            btns.addWidget(self.btn_edit)
        btns.addStretch(1)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_save)
        root.addLayout(btns)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self.on_save)
        if not self.is_new:
            self.btn_edit.clicked.connect(self.enable_edit_mode)
        else:
            self.btn_edit.setVisible(False)

        if self.is_new:
            self.set_edit_mode(True)
        else:
            self.load_account()
            self.set_edit_mode(False)

    def set_edit_mode(self, editable: bool):
        self.view_mode = not editable
        self.name.setEnabled(editable)
        self.active.setEnabled(editable)
        # account_type is fixed after creation (ties to code prefix)
        self.typ.setEnabled(self.is_new and editable)
        self.btn_save.setEnabled(editable)
        if not self.is_new:
            self.btn_edit.setEnabled(not editable)
            self.btn_cancel.setText("Close" if not editable else "Cancel")

    def enable_edit_mode(self):
        self.set_edit_mode(True)

    def load_account(self):
        row = self.repo.get_user_managed_account(self.account_code)
        if not row:
            QMessageBox.critical(self, "Error", "Account not found or not user managed.")
            self.reject()
            return
        self.typ.setCurrentText(row["account_type"])
        self.name.setText(row["account_name"])
        self.active.setChecked(int(row["is_active"]) == 1)

    def on_save(self):
        nm = self.name.text().strip()
        if not nm:
            QMessageBox.warning(self, "Validation", "Account name is required.")
            return
        is_active = 1 if self.active.isChecked() else 0
        try:
            if self.is_new:
                t = self.typ.currentText()
                self.account_code = self.repo.create_user_managed_account(nm, t, is_active)
            else:
                self.repo.update_user_managed_account(self.account_code, nm, is_active)
            QMessageBox.information(self, "Saved", "Account saved.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))


class BalanceSheetAccountDetailDialog(QDialog):
    """List + entry point for Balance Sheet Account Detail management."""

    def __init__(self, repo: Repo, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.setWindowTitle("Balance Sheet Account Detail")
        self.resize(720, 460)

        root = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setSpacing(6)
        self.list.itemDoubleClicked.connect(self.on_item_activated)
        root.addWidget(self.list)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Add account")
        self.btn_refresh = QPushButton("Refresh")
        self.btn_close = QPushButton("Close")
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_refresh)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

        self.btn_add.clicked.connect(self.add_account)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_close.clicked.connect(self.accept)

        self.refresh()

    def refresh(self):
        self.list.clear()
        rows = self.repo.list_user_managed_bs_accounts()
        last_type = None
        for r in rows:
            t = r["account_type"]
            if t != last_type:
                self.list.addItem(SectionHeaderItem(ACCOUNT_TYPE_LABEL.get(t, t)))
                last_type = t

            payload = {
                "kind": "row",
                "account_code": r["account_code"],
                "account_name": r["account_name"],
                "account_type": t,
                "is_active": int(r["is_active"]),
            }
            item = CardRowItem(payload)
            self.list.addItem(item)

            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(12, 8, 12, 8)
            lay.setSpacing(12)

            icon = QLabel(bs_icon(r["account_code"], t))
            icon.setFixedWidth(28)
            icon.setAlignment(Qt.AlignCenter)

            text_col = QVBoxLayout()
            name_lbl = QLabel(r["account_name"])
            type_lbl = QLabel(ACCOUNT_TYPE_LABEL.get(t, t))
            type_lbl.setStyleSheet("color: #666; font-size: 12px;")
            text_col.addWidget(name_lbl)
            text_col.addWidget(type_lbl)

            active_lbl = QLabel("Active" if r["is_active"] else "Inactive")
            if r["is_active"]:
                active_lbl.setStyleSheet("color: #0a7a0a;")
            else:
                active_lbl.setStyleSheet("color: #a00;")

            edit_btn = QPushButton("Edit")
            edit_btn.clicked.connect(lambda _, code=r["account_code"]: self.edit_account(code))

            lay.addWidget(icon)
            lay.addLayout(text_col, 1)
            lay.addWidget(active_lbl)
            lay.addWidget(edit_btn)

            self.list.setItemWidget(item, w)
            item.setSizeHint(QSize(10, 64))

    def on_item_activated(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole) or {}
        if data.get("kind") != "row":
            return
        self.edit_account(data["account_code"])

    def edit_account(self, account_code: str):
        dlg = BalanceSheetAccountEditDialog(self.repo, account_code, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def add_account(self):
        dlg = BalanceSheetAccountEditDialog(self.repo, account_code=None, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()


# -------------------------
# Insight UI
# -------------------------

class InsightHome(QWidget):
    def __init__(self, repo: Repo, parent=None):
        super().__init__(parent)
        self.repo = repo

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        seg = QHBoxLayout()
        self.btn_expense = QPushButton("Expense List")
        self.btn_bs = QPushButton("Balance Sheet")
        self.btn_expense.setCheckable(True)
        self.btn_bs.setCheckable(True)
        self.btn_expense.setChecked(True)
        seg.addWidget(self.btn_expense)
        seg.addWidget(self.btn_bs)
        seg.addStretch(1)
        root.addLayout(seg)

        nav = QHBoxLayout()
        self.back = QToolButton()
        self.back.setText("<")
        self.back.clicked.connect(self.go_back)
        self.back.setEnabled(False)
        self.title = QLabel("Expense List")
        f = self.title.font()
        f.setPointSize(f.pointSize() + 2)
        f.setBold(True)
        self.title.setFont(f)
        nav.addWidget(self.back)
        nav.addWidget(self.title)
        nav.addStretch(1)
        root.addLayout(nav)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.page_expense = JournalCardList(repo, mode="expense")
        self.page_bs = BalanceSheetOverviewWidget(repo)
        self.stack.addWidget(self.page_expense)
        self.stack.addWidget(self.page_bs)

        self.nav_stack: List[Tuple[int, str]] = []

        self.page_expense.on_open_entry = self.open_entry_general
        self.page_bs.on_open_account = self.open_account_transactions

        self.btn_expense.clicked.connect(lambda: self.switch_root(0))
        self.btn_bs.clicked.connect(lambda: self.switch_root(1))
        self._set_segment_checked(0)

    def _set_segment_checked(self, idx: int):
        self.btn_expense.setChecked(idx == 0)
        self.btn_bs.setChecked(idx == 1)

    def switch_root(self, idx: int):
        self.nav_stack.clear()
        self.back.setEnabled(False)
        self.stack.setCurrentIndex(idx)
        self._set_segment_checked(idx)
        self.title.setText("Expense List" if idx == 0 else "Balance Sheet")
        self.refresh_current()

    def refresh_current(self):
        w = self.stack.currentWidget()
        if isinstance(w, JournalCardList):
            w.refresh()
        elif isinstance(w, BalanceSheetOverviewWidget):
            w.refresh()

    def go_back(self):
        if not self.nav_stack:
            return
        idx, title = self.nav_stack.pop()
        self.stack.setCurrentIndex(idx)
        self.title.setText(title)
        self.back.setEnabled(len(self.nav_stack) > 0)
        self.refresh_current()

    def open_entry_general(self, entry_uuid: str):
        dlg = GeneralJournalDetailDialog(self.repo, entry_uuid=entry_uuid, parent=self)
        if dlg.exec():
            self.refresh_all()

    def open_account_transactions(self, account_code: str, account_name: str):
        cur_idx = self.stack.currentIndex()
        cur_title = self.title.text()
        self.nav_stack.append((cur_idx, cur_title))
        self.back.setEnabled(True)

        page = JournalCardList(self.repo, mode="account", account_code=account_code)
        page.on_open_entry = self.open_entry_general
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.title.setText(account_name)

    def refresh_all(self):
        self.page_expense.refresh()
        self.page_bs.refresh()
        self.refresh_current()


class MainWindow(QMainWindow):
    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo
        self.importer = JsonExpenseImportService(repo)
        self.setWindowTitle("Debibi")
        self.resize(430, 760)

        menubar = QMenuBar(self)
        self.setMenuBar(menubar)
        m = menubar.addMenu("Actions")

        act_new_expense = QAction("New Expense Entry", self)
        act_new_general = QAction("New Journal Entry", self)
        act_import_json = QAction("Import JSON Entry", self)
        act_manage_accounts = QAction("Manage BS Accounts", self)
        act_refresh = QAction("Refresh", self)

        m.addAction(act_new_expense)
        m.addAction(act_new_general)
        m.addAction(act_import_json)
        m.addSeparator()
        m.addAction(act_manage_accounts)
        m.addSeparator()
        m.addAction(act_refresh)

        act_new_expense.triggered.connect(self.new_expense)
        act_new_general.triggered.connect(self.new_general)
        act_import_json.triggered.connect(self.import_json_entry)
        act_manage_accounts.triggered.connect(self.manage_accounts)
        act_refresh.triggered.connect(self.refresh_all)

        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.South)

        feed = QWidget()
        feed_l = QVBoxLayout(feed)
        feed_l.setContentsMargins(16, 16, 16, 16)
        feed_l.setSpacing(12)
        feed_l.addStretch(1)

        feed_title = QLabel("Feed Debibi")
        feed_title.setAlignment(Qt.AlignCenter)
        f_title = feed_title.font()
        f_title.setPointSize(f_title.pointSize() + 2)
        f_title.setBold(True)
        feed_title.setFont(f_title)

        btn_manual_expense = QPushButton("Record expenses manually")
        btn_manual_expense.setMinimumHeight(120)
        btn_manual_expense.clicked.connect(self.new_expense)

        btn_manual_advanced = QPushButton("Record other transactions manually")
        btn_manual_advanced.setMinimumHeight(40)
        btn_manual_advanced.clicked.connect(self.new_general)

        feed_l.addWidget(feed_title)
        feed_l.addSpacing(6)
        feed_l.addWidget(btn_manual_expense)
        feed_l.addWidget(btn_manual_advanced)
        feed_l.addStretch(1)

        debibi = QWidget()
        debibi_l = QVBoxLayout(debibi)
        debibi_l.addStretch(1)
        debibi_l.addWidget(QLabel("Debibi (not implemented yet)"), alignment=Qt.AlignCenter)
        debibi_l.addStretch(1)

        self.insight = InsightHome(repo)

        tabs.addTab(feed, "Feed")
        tabs.addTab(debibi, "Debibi")
        tabs.addTab(self.insight, "Insight")
        tabs.setCurrentIndex(2)

        self.setCentralWidget(tabs)

    def refresh_all(self):
        self.insight.refresh_all()

    def new_expense(self):
        dlg = ExpenseJournalDetailDialog(self.repo, entry_uuid=None, parent=self)
        if dlg.exec():
            self.refresh_all()

    def new_general(self):
        dlg = GeneralJournalDetailDialog(self.repo, entry_uuid=None, parent=self)
        if dlg.exec():
            self.refresh_all()

    def import_json_entry(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import JSON entry",
            "",
            "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            result = self.importer.import_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Import failed", str(e))
            return

        dlg = GeneralJournalDetailDialog(
            self.repo,
            entry_uuid=result.entry_uuid,
            parent=self,
            start_edit_mode=True,
        )
        dlg.exec()
        self.refresh_all()

    def manage_accounts(self):
        dlg = BalanceSheetAccountDetailDialog(self.repo, parent=self)
        dlg.exec()
        self.refresh_all()


def main():
    db_path = "debibi.db"
    repo = Repo(db_path)
    repo.init_db()
    repo.seed_sample_data_if_empty()

    app = QApplication(sys.argv)
    w = MainWindow(repo)
    w.show()
    rc = app.exec()
    repo.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
