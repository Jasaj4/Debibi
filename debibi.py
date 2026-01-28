
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

import datetime as dt
import io
import json
import math
import os
import sqlite3
import sys
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import pypdfium2 as pdfium
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from pdf2image import convert_from_bytes
from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QDate, QIODevice, QPointF, QRectF, QSize, QSizeF, Qt, Signal, QThread, QObject
from PySide6.QtGui import QAction, QFont, QImage, QPainter, QPixmap, QColor, QIcon, QPen
from PySide6.QtMultimedia import QCamera, QImageCapture, QMediaCaptureSession, QMediaDevices
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtPdf import QPdfDocument
from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSet,
    QChart,
    QChartView,
    QLineSeries,
    QStackedBarSeries,
    QValueAxis,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QScroller,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
    QFrame,
)

CAMERA_AVAILABLE = True
PDF_RENDER_AVAILABLE = True


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

COLOR_PALETTE = [
    "#5b8ff9",
    "#5ad8a6",
    "#5d7092",
    "#f6bd16",
    "#6f5ef9",
    "#6dc8ec",
    "#945fb9",
    "#ff9845",
    "#1e9493",
    "#ff99c3",
]

def color_for_key(key: str) -> QColor:
    if not key:
        return QColor("#5b8ff9")
    idx = sum(ord(c) for c in key) % len(COLOR_PALETTE)
    return QColor(COLOR_PALETTE[idx])

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

def _pdf_to_png_bytes(data: bytes) -> Optional[bytes]:
    """Convert first page of a PDF to PNG bytes using available backends."""
    try:
        with pdfium.PdfDocument(data) as pdf:
            if len(pdf) < 1:
                return None
            page = pdf[0]
            pil_image = page.render(scale=2).to_pil()
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        pass

    try:
        images = convert_from_bytes(data, first_page=1, last_page=1, fmt="png")
        if images:
            buf = io.BytesIO()
            images[0].save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        pass

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            img = Image.open(io.BytesIO(data))
            img.load()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        pass

    return None

def pixmap_from_pdf_bytes(data: bytes, max_size: QSize) -> Optional[QPixmap]:
    # Try QtPdf first (fastest when available)
    if PDF_RENDER_AVAILABLE:
        doc = QPdfDocument()
        buf = QBuffer()
        buf.setData(QByteArray(data))
        buf.open(QIODevice.ReadOnly)
        err = doc.load(buf)
        try:
            ok_value = QPdfDocument.Error.NoError  # Qt 6.5+
        except AttributeError:
            ok_value = getattr(QPdfDocument, "NoError", 0)  # older enum style
        if err == ok_value and doc.pageCount() > 0:
            page_size = doc.pagePointSize(0)
            img = QImage(page_size.toSize(), QImage.Format_ARGB32)
            img.fill(Qt.white)
            painter = QPainter(img)
            doc.render(painter, 0, QRectF(QPointF(0, 0), QSizeF(page_size)))
            painter.end()
            if max_size.width() > 0 and max_size.height() > 0:
                img = img.scaled(max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            return QPixmap.fromImage(img)

    # Fallback: convert to PNG via external libs
    png_bytes = _pdf_to_png_bytes(data)
    if png_bytes:
        return pixmap_from_image_bytes(png_bytes, max_size)
    return None


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
                   ORDER BY
                     CASE WHEN is_active=0 THEN 3 WHEN account_type='ASSET' THEN 1 ELSE 2 END,
                     account_name"""
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

    def list_expense_trend(
        self,
        granularity: str = "day",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[sqlite3.Row]:
        label_expr = "e.accounting_date" if granularity == "day" else "substr(e.accounting_date,1,7)"
        sql = f"""
        SELECT
          {label_expr} AS label,
          ei.account_code,
          a.account_name,
          SUM(CASE WHEN ei.dc='D' THEN ei.amount_domestic ELSE -ei.amount_domestic END) AS amount_domestic_sum
        FROM gl_entry_item ei
        JOIN gl_entry e   ON e.entry_uuid = ei.entry_uuid
        JOIN gl_account a ON a.account_code = ei.account_code
        WHERE a.is_active=1
          AND a.account_type='EXPENSE'
        """
        params: List[Any] = []
        if date_from:
            sql += " AND e.accounting_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND e.accounting_date <= ?"
            params.append(date_to)
        sql += " GROUP BY label, ei.account_code, a.account_name ORDER BY label ASC, ei.account_code ASC"
        return list(self.conn.execute(sql, params).fetchall())

    def _opening_balances(self, date_from: Optional[str]) -> Tuple[float, float]:
        if not date_from:
            return 0.0, 0.0
        sql = """
        SELECT
          a.account_type,
          SUM(CASE WHEN ei.dc='D' THEN ei.amount_domestic ELSE -ei.amount_domestic END) AS delta_amount
        FROM gl_entry_item ei
        JOIN gl_entry e   ON e.entry_uuid = ei.entry_uuid
        JOIN gl_account a ON a.account_code = ei.account_code
        WHERE a.is_active=1
          AND a.account_type IN ('ASSET','LIAB')
          AND e.accounting_date < ?
        GROUP BY a.account_type
        """
        asset_open = 0.0
        liab_open = 0.0
        for r in self.conn.execute(sql, (date_from,)).fetchall():
            if r["account_type"] == "ASSET":
                asset_open = float(r["delta_amount"] or 0.0)
            elif r["account_type"] == "LIAB":
                liab_open = float(r["delta_amount"] or 0.0)
        return asset_open, liab_open

    def list_assets_trend(
        self,
        granularity: str = "day",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        label_expr = "e.accounting_date" if granularity == "day" else "substr(e.accounting_date,1,7)"
        sql = f"""
        SELECT
          {label_expr} AS label,
          a.account_type,
          SUM(CASE WHEN ei.dc='D' THEN ei.amount_domestic ELSE -ei.amount_domestic END) AS delta_amount
        FROM gl_entry_item ei
        JOIN gl_entry e   ON e.entry_uuid = ei.entry_uuid
        JOIN gl_account a ON a.account_code = ei.account_code
        WHERE a.is_active=1
          AND a.account_type IN ('ASSET','LIAB')
        """
        params: List[Any] = []
        if date_from:
            sql += " AND e.accounting_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND e.accounting_date <= ?"
            params.append(date_to)
        sql += " GROUP BY label, a.account_type ORDER BY label ASC, a.account_type ASC"
        rows = list(self.conn.execute(sql, params).fetchall())

        buckets: Dict[str, Dict[str, float]] = {}
        for r in rows:
            label = r["label"]
            acc_type = r["account_type"]
            delta = float(r["delta_amount"] or 0.0)
            if label not in buckets:
                buckets[label] = {"ASSET": 0.0, "LIAB": 0.0}
            buckets[label][acc_type] = delta

        labels = sorted(buckets.keys())
        asset_open, liab_open = self._opening_balances(date_from)
        asset_bal = asset_open
        liab_bal = liab_open

        result: List[Dict[str, Any]] = []
        for label in labels:
            asset_bal += buckets[label].get("ASSET", 0.0)
            liab_bal += buckets[label].get("LIAB", 0.0)
            net = asset_bal - liab_bal
            result.append(
                {
                    "label": label,
                    "asset_balance": asset_bal,
                    "liab_balance": liab_bal,
                    "net_assets": net,
                }
            )
        return result


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

        amt_dom = self._parse_nonzero_number(line.get("amount_domestic"), f"lines[{idx}].amount_domestic")
        amt_org_raw = line.get("amount_original")
        amt_org = self._parse_nonzero_number(
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

        if abs(total_dom) <= 1e-9:
            raise JsonExpenseImportError("Total amount_domestic must not be zero.")

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
    def _parse_nonzero_number(value: Any, field_name: str) -> float:
        try:
            num = float(value)
        except Exception:
            raise JsonExpenseImportError(f"{field_name} must be a non-zero number.") from None
        if not math.isfinite(num) or abs(num) < 1e-9:
            raise JsonExpenseImportError(f"{field_name} must be a non-zero number.")
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
# Gemini / AI Import helpers
# -------------------------


class GeminiClientError(Exception):
    pass


class PromptBuilder:
    """Builds system prompt with live schema and enum values."""

    def __init__(self, repo: Repo, schema_path: str):
        self.repo = repo
        self.schema_path = schema_path
        self._schema_template: Optional[str] = None

    def _load_schema_template(self) -> str:
        if self._schema_template is None:
            with open(self.schema_path, "r", encoding="utf-8") as f:
                raw = f.read()
            try:
                # Minify schema to single line to reduce prompt noise
                schema_obj = json.loads(raw)
                self._schema_template = json.dumps(schema_obj, separators=(",", ":"), ensure_ascii=False)
            except json.JSONDecodeError:
                self._schema_template = raw
        return self._schema_template

    def build_prompt(self) -> str:
        schema = self._load_schema_template()
        pay_names = [r["account_name"] for r in self.repo.list_payment_accounts()]
        exp_names = [r["account_name"] for r in self.repo.list_expense_categories()]
        dom = self.repo.get_domestic_currency() or "GBP"

        schema = schema.replace('"{PAYMENT_ACCOUNT_NAME_ENUM}"', json.dumps(pay_names))
        schema = schema.replace('"{EXPENSE_ACCOUNT_NAME_ENUM}"', json.dumps(exp_names))
        schema = schema.replace("{USER_DOMESTIC_CURRENCY}", dom)

        prompt = (
            "You are a personal accounting professional. Output one JSON object ONLY.\n"
            "- Follow this JSON Schema strictly (no extra fields, no comments):\n"
            f"{schema}\n"
            f"- Use these payment accounts exactly as written: {', '.join(pay_names)}\n"
            f"- Use these expense categories exactly as written: {', '.join(exp_names)}\n"
            f"- Domestic currency: {dom}\n"
            "- If date is absent, set null.\n"
            "- If store/notes unreadable, set null.\n"
            "- Amounts must be non-zero numbers (positive or negative); sum(lines.amount_domestic) must equal the payment line.\n"
            "- Prefer grouping identical expense_category lines into one item by summing their amounts (one line per expense category when possible).\n"
            "- If the receipt shows discounts/coupons/savings/rounding/tax and the item sum differs from the receipt total, add ONE extra line with expense_category \"Other expenses\" and a NEGATIVE amount (both domestic and original) so the sums tie; if the item sum already matches the receipt total, do NOT add a savings line.\n"
            "- Output must be the full JSON object conforming to the schema (no missing required fields), plain JSON only (no markdown fences)."
        )
        return prompt


class GeminiClient:
    """Thin wrapper over google genai client with JSON-only contract."""

    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        if not self.api_key:
            raise GeminiClientError("GEMINI_API_KEY not set (.env or environment).")
        if genai is None:
            raise GeminiClientError("google-genai package not installed. pip install google-genai")
        if genai_types is None:
            raise GeminiClientError("google-genai types missing. Verify installation.")
        self.client = genai.Client(api_key=self.api_key)

    def _upload_temp(self, data: bytes, file_name: str, mime_type: str):
        # genai client expects a file path; use temp file to avoid keeping artifacts
        suffix = ""
        if file_name and "." in file_name:
            suffix = "." + file_name.rsplit(".", 1)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            cfg = {}
            if file_name:
                cfg["display_name"] = file_name
            if mime_type:
                cfg["mime_type"] = mime_type
            uploaded = self.client.files.upload(
                file=tmp_path,
                config=cfg if cfg else None,
            )
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return uploaded

    @staticmethod
    def _strip_fences(text: str) -> str:
        if text.startswith("```"):
            text = text.strip()
            if text.startswith("```"):
                text = text.split("```", 2)[1] if "```" in text[3:] else text[3:]
            text = text.strip()
        return text

    def _parse_json_text(self, text: str) -> Any:
        cleaned = self._strip_fences(text)
        return json.loads(cleaned)

    def generate_json(
        self,
        system_prompt: str,
        user_text: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        mime_type: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> Any:
        if not user_text and not file_bytes:
            raise GeminiClientError("Either text or file_bytes must be provided.")

        uploaded = None
        contents: List[Any] = []
        if file_bytes:
            uploaded = self._upload_temp(file_bytes, file_name or "attachment", mime_type or "")
            contents.append(uploaded)
            contents.append("\n\nExtract all receipt text first, then map to schema.")
        if user_text:
            contents.append(user_text)

        def _call() -> str:
            config = genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=2048,
            )
            resp = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
            text = getattr(resp, "text", None)
            if not text and hasattr(resp, "candidates"):
                for cand in resp.candidates:
                    parts = getattr(cand, "content", None)
                    if not parts:
                        continue
                    for p in getattr(parts, "parts", []):
                        if hasattr(p, "text"):
                            text = p.text
                            break
                    if text:
                        break
            if not text:
                raise GeminiClientError("Gemini returned empty response.")
            return text

        first_text = _call()
        try:
            return self._parse_json_text(first_text)
        except Exception as e:
            # one retry for JSON parse failure
            retry_text = _call()
            try:
                return self._parse_json_text(retry_text)
            except Exception as e2:
                raise GeminiClientError(f"Failed to parse JSON from Gemini. Raw response:\n{retry_text}") from e2


class GeminiWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, client: GeminiClient, prompt: str, user_text: Optional[str], file_bytes: Optional[bytes], mime_type: Optional[str], file_name: Optional[str]):
        super().__init__()
        self.client = client
        self.prompt = prompt
        self.user_text = user_text
        self.file_bytes = file_bytes
        self.mime_type = mime_type
        self.file_name = file_name

    def run(self):
        try:
            payload = self.client.generate_json(
                system_prompt=self.prompt,
                user_text=self.user_text,
                file_bytes=self.file_bytes,
                mime_type=self.mime_type,
                file_name=self.file_name,
            )
            self.finished.emit(payload)
        except Exception as e:
            self.failed.emit(str(e))


class BusyOverlay(QWidget):
    """Simple full-screen overlay with Debibi loading image."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: rgba(0,0,0,0.35);")
        self.setVisible(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignCenter)

        self.img = QLabel()
        pix = QPixmap(os.path.join("assets", "debibi_loading.png"))
        if not pix.isNull():
            self.img.setPixmap(pix.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.img.setAlignment(Qt.AlignCenter)

        self.msg = QLabel("Debibi chewing on your receipt…")
        self.msg.setStyleSheet("color: white; font-size: 16px;")
        self.msg.setAlignment(Qt.AlignCenter)

        lay.addWidget(self.img)
        lay.addSpacing(12)
        lay.addWidget(self.msg)

    def show_message(self, text: str):
        self.msg.setText(text)
        if self.parent():
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()

    def hide_overlay(self):
        self.hide()


class FreeTextImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Text to Debibi")
        self.resize(480, 320)
        v = QVBoxLayout(self)
        lbl = QLabel("Paste any receipt or expense text below:")
        self.text = QTextEdit()
        btns = QHBoxLayout()
        ok = QPushButton("Feed")
        cancel = QPushButton("Cancel")
        ok.setDefault(True)
        ok.setAutoDefault(True)
        cancel.setAutoDefault(False)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        v.addWidget(lbl)
        v.addWidget(self.text, 1)
        v.addLayout(btns)

    def get_text(self) -> str:
        return self.text.toPlainText().strip()


class CameraCaptureDialog(QDialog):
    """Lightweight camera picker. Falls back to file chooser if camera is unavailable."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Camera Capture")
        self.resize(520, 420)
        self.captured_bytes: Optional[bytes] = None
        self.captured_mime: Optional[str] = None
        self.captured_name: Optional[str] = None

        if not CAMERA_AVAILABLE or not QMediaDevices.videoInputs():
            QMessageBox.warning(self, "Camera unavailable", "No camera devices detected. Please pick an image file instead.")
            self._fallback_file()
            return

        self.view = QVideoWidget()
        self.session = QMediaCaptureSession()
        self.camera = QCamera(QMediaDevices.videoInputs()[0])
        self.capture = QImageCapture()
        self.session.setCamera(self.camera)
        self.session.setVideoOutput(self.view)
        self.session.setImageCapture(self.capture)
        self.capture.imageCaptured.connect(self._on_captured)

        btns = QHBoxLayout()
        self.btn_capture = QPushButton("Shoot")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_capture.setDefault(True)
        self.btn_capture.setAutoDefault(True)
        self.btn_cancel.setAutoDefault(False)
        btns.addStretch(1)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_capture)

        v = QVBoxLayout(self)
        v.addWidget(self.view, 1)
        v.addLayout(btns)

        self.btn_capture.clicked.connect(self._capture)
        self.btn_cancel.clicked.connect(self.reject)
        self.camera.start()

    def _fallback_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select image",
            "",
            "Images (*.jpg *.jpeg *.png)"
        )
        if not path:
            self.reject()
            return
        mime = guess_mime_from_path(path)
        if mime not in ("image/jpeg", "image/png"):
            QMessageBox.warning(self, "Invalid file", "Only JPG/PNG allowed.")
            self.reject()
            return
        size = os.path.getsize(path)
        if size > ATTACH_MAX_BYTES:
            QMessageBox.warning(self, "File too large", "File must be 10MB or smaller.")
            self.reject()
            return
        with open(path, "rb") as f:
            self.captured_bytes = f.read()
        self.captured_mime = mime
        self.captured_name = os.path.basename(path)
        self.accept()

    def _capture(self):
        if not CAMERA_AVAILABLE:
            self.reject()
            return
        self.capture.captureToFile("")  # triggers imageCaptured

    def _on_captured(self, _id, image: QImage):
        buf = QBuffer()
        buf.open(QIODevice.ReadWrite)
        image.save(buf, "JPG")
        self.captured_bytes = bytes(buf.data())
        self.captured_mime = "image/jpeg"
        self.captured_name = "camera.jpg"
        self.accept()


class AiImportController(QObject):
    """Coordinates UI actions -> Gemini -> import service."""

    def __init__(
        self,
        repo: Repo,
        importer: JsonExpenseImportService,
        prompt_builder: PromptBuilder,
        gemini_client: GeminiClient,
        overlay: BusyOverlay,
        open_entry: Callable[[str, bool], None],
        refresh: Callable[[], None],
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.importer = importer
        self.prompt_builder = prompt_builder
        self.gemini_client = gemini_client
        self.overlay = overlay
        self.open_entry = open_entry
        self.refresh = refresh
        self.parent_widget = parent
        self._running_threads: List[QThread] = []
        self._job_ctx: Optional[Dict[str, Any]] = None
        self.log_dir = os.path.join(os.path.dirname(__file__), "log")

    def import_from_text(self):
        dlg = FreeTextImportDialog(self.parent_widget)
        if dlg.exec() != QDialog.Accepted:
            return
        text = dlg.get_text()
        if not text:
            QMessageBox.warning(self.parent_widget, "Validation", "テキストを入力してください。")
            return
        self._start_worker(source="text", user_text=text)

    def import_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self.parent_widget,
            "ファイルから取り込む",
            "",
            "Images/PDF (*.jpg *.jpeg *.png *.pdf)"
        )
        if not path:
            return
        mime = guess_mime_from_path(path)
        if mime not in ALLOWED_MIME:
            QMessageBox.warning(self.parent_widget, "Invalid file", "Only JPG, PNG, or PDF files are allowed.")
            return
        size = os.path.getsize(path)
        if size > ATTACH_MAX_BYTES:
            QMessageBox.warning(self.parent_widget, "File too large", "File must be 10MB or smaller.")
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            QMessageBox.critical(self.parent_widget, "Error", f"Failed to read file: {e}")
            return
        self._start_worker(source="file", file_bytes=data, mime_type=mime, file_name=os.path.basename(path))

    def import_from_camera(self):
        dlg = CameraCaptureDialog(self.parent_widget)
        if dlg.exec() != QDialog.Accepted:
            return
        if not dlg.captured_bytes or not dlg.captured_mime:
            QMessageBox.warning(self.parent_widget, "Camera error", "No image captured.")
            return
        self._start_worker(
            source="camera",
            file_bytes=dlg.captured_bytes,
            mime_type=dlg.captured_mime,
            file_name=dlg.captured_name or "camera.jpg",
        )

    def _start_worker(
        self,
        source: str,
        user_text: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        mime_type: Optional[str] = None,
        file_name: Optional[str] = None,
    ):
        if self._job_ctx:
            QMessageBox.information(self.parent_widget, "Busy", "Another import is running. Please wait.")
            return
        try:
            prompt = self.prompt_builder.build_prompt()
        except Exception as e:
            QMessageBox.critical(self.parent_widget, "Prompt error", str(e))
            return

        try:
            worker = GeminiWorker(self.gemini_client, prompt, user_text, file_bytes, mime_type, file_name)
        except Exception as e:
            QMessageBox.critical(self.parent_widget, "Gemini error", str(e))
            return

        thread = QThread()
        worker.moveToThread(thread)
        self._job_ctx = {
            "thread": thread,
            "worker": worker,
            "file_bytes": file_bytes,
            "mime_type": mime_type,
            "file_name": file_name,
        }
        worker.finished.connect(self._on_worker_success, Qt.QueuedConnection)
        worker.failed.connect(self._on_worker_failed, Qt.QueuedConnection)
        thread.started.connect(worker.run)
        self.overlay.show_message("Debibi chewing on your receipt…")
        self._running_threads.append(thread)
        thread.start()

    def _cleanup_thread(self):
        ctx = self._job_ctx or {}
        thread: Optional[QThread] = ctx.get("thread")
        worker: Optional[GeminiWorker] = ctx.get("worker")
        if thread:
            thread.quit()
            thread.wait()
        if worker:
            worker.deleteLater()
        if thread:
            thread.deleteLater()
            if thread in self._running_threads:
                self._running_threads.remove(thread)
        self._job_ctx = None

    def _on_worker_failed(self, message: str):
        self.overlay.hide_overlay()
        # Capture raw response if the parse step failed
        raw_start = "Failed to parse JSON from Gemini. Raw response:"
        if message.startswith(raw_start):
            raw = message[len(raw_start):].strip()
            self._save_failed_payload(raw)
        QMessageBox.critical(self.parent_widget, "LLM error", message)
        self._cleanup_thread()

    def _on_worker_success(self, payload: Any):
        ctx = self._job_ctx or {}
        file_bytes = ctx.get("file_bytes")
        mime_type = ctx.get("mime_type")
        file_name = ctx.get("file_name")
        try:
            result = self.importer.import_payload(payload)
            if file_bytes and mime_type:
                try:
                    self.repo.upsert_attachment(result.entry_uuid, file_name, mime_type, file_bytes)
                except Exception as e:
                    QMessageBox.warning(self.parent_widget, "Attachment warning", f"Import succeeded but attachment failed: {e}")
            self.open_entry(result.entry_uuid, True)
            self.refresh()
        except Exception as e:
            self._save_failed_payload(payload)
            QMessageBox.critical(self.parent_widget, "Validation failed", str(e))
        finally:
            self.overlay.hide_overlay()
            self._cleanup_thread()

    def _save_failed_payload(self, payload: Any):
        """Persist LLM payload for debugging when import fails."""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = os.path.join(self.log_dir, f"{ts}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            # Swallow logging errors; don't block the UI
            pass


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
        self.setBackground(QColor("#e0e0e0"))
        self.setForeground(QColor("#2d2018"))
        self.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setSizeHint(QSize(10, 32))
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


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event and event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class AttachmentViewerDialog(QDialog):
    """Simple resizable window that scales an attachment pixmap to fill the client area."""

    def __init__(self, pixmap: QPixmap, title: str, file_bytes: Optional[bytes], default_name: Optional[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title or "Attachment")
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self._orig_pixmap = pixmap
        self._file_bytes = file_bytes or b""
        self._default_name = default_name or "attachment"

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setMinimumSize(0, 0)
        # Allow the dialog to shrink freely; ignore pixmap size hints.
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._save_attachment)
        self.save_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        top = QHBoxLayout()
        top.setContentsMargins(8, 8, 8, 0)
        top.addStretch(1)
        top.addWidget(self.save_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.label)

        self.setMinimumSize(320, 240)
        self.resize(900, 700)
        self._apply_scaled_pixmap()

    def _apply_scaled_pixmap(self):
        if not self._orig_pixmap or self._orig_pixmap.isNull():
            self.label.setPixmap(QPixmap())
            self.label.setText("Preview unavailable")
            return
        target = self.label.size()
        if target.width() < 2 or target.height() < 2:
            target = QSize(10, 10)
        scaled = self._orig_pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.label.setPixmap(scaled)
        self.label.setText("")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_scaled_pixmap()

    def _save_attachment(self):
        if not self._file_bytes:
            QMessageBox.warning(self, "Attachment", "No attachment data to save.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save attachment", self._default_name)
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(self._file_bytes)
            QMessageBox.information(self, "Saved", "Attachment saved.")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))


# -------------------------
# Reusable header components
# -------------------------


class NoteFieldSection(QObject):
    """Shared note field with lazy reveal behaviour used by journal dialogs."""

    def __init__(self, owner: QWidget):
        super().__init__(owner)
        self.owner = owner
        self.note = QTextEdit()
        self.note.setFixedHeight(70)
        self.add_btn = QPushButton("Add note")
        self.add_btn.clicked.connect(self._show_note_field)
        self.note_shown_with_empty = False
        self._view_mode = False
        self.note.textChanged.connect(self.update_visibility)
        self.update_visibility()

    def wrap_widget(self) -> QWidget:
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.note)
        lay.addWidget(self.add_btn, alignment=Qt.AlignLeft)
        widget = QWidget()
        widget.setLayout(lay)
        return widget

    def _show_note_field(self):
        if self._view_mode:
            return
        self.note_shown_with_empty = True
        self.update_visibility()
        self.note.setFocus()

    def update_visibility(self):
        has_text = bool(self.note.toPlainText().strip())
        if has_text:
            self.note_shown_with_empty = False
        show_note = has_text or self.note_shown_with_empty
        self.note.setVisible(show_note)
        self.add_btn.setVisible(not show_note)

    def set_view_mode(self, view_mode: bool):
        self._view_mode = view_mode
        self.note.setEnabled(not view_mode)
        self.add_btn.setEnabled(not view_mode)
        if view_mode:
            self.note_shown_with_empty = False
        self.update_visibility()

    def set_edit_mode(self):
        self.set_view_mode(False)

    def set_text(self, text: str):
        self.note.setPlainText(text or "")
        self.update_visibility()

    def text(self) -> str:
        return self.note.toPlainText()


class AttachmentSection(QObject):
    """Shared attachment picker/preview used by journal dialogs."""

    def __init__(self, owner: QWidget, form: QFormLayout, label: str = "Attachment"):
        super().__init__(owner)
        self.owner = owner
        self.attach_data: Optional[bytes] = None
        self.attach_mime: Optional[str] = None
        self.attach_name: Optional[str] = None
        self.attach_deleted: bool = False
        self.attach_existing_present: bool = False
        self.view_mode: bool = False

        self.preview = ClickableLabel("No attachment")
        self.preview.setAlignment(Qt.AlignLeft)
        self.preview.setMinimumSize(240, 160)
        self.preview.setMaximumSize(320, 220)
        self.preview.setStyleSheet("border: 1px solid #ccc; background: #fafafa;")
        self.preview.clicked.connect(self.on_attachment_clicked)

        self.name_lbl = QLabel("None")
        self.name_lbl.setStyleSheet("color: #666;")

        self.add_btn = QPushButton("Add / Replace")
        self.remove_btn = QPushButton("Remove")
        for btn in (self.add_btn, self.remove_btn):
            btn.setStyleSheet("margin: 0; padding: 5px;")
        self.add_btn.clicked.connect(self.on_select_attachment)
        self.remove_btn.clicked.connect(self.on_remove_attachment)

        btns = QHBoxLayout()
        btns.addWidget(self.add_btn)
        btns.addWidget(self.remove_btn)
        btns.addStretch(1)

        container = QVBoxLayout()
        container.setContentsMargins(0, 0, 0, 0)
        container.addWidget(self.preview)
        container.addWidget(self.name_lbl)
        container.addLayout(btns)

        wrap = QWidget()
        wrap.setLayout(container)
        wrap.setContentsMargins(0, 0, 0, 0)
        form.addRow(label, wrap)

        self.update_preview()

    # --- state helpers
    def has_attachment(self) -> bool:
        return (self.attach_data is not None or self.attach_existing_present) and not self.attach_deleted

    def set_view_mode(self, view_mode: bool):
        self.view_mode = view_mode
        self.add_btn.setEnabled(not view_mode)
        self.update_preview()

    def load_existing(self, att_row: Optional[sqlite3.Row]):
        if att_row:
            self.attach_data = att_row["file_blob"]
            self.attach_mime = att_row["mime_type"]
            self.attach_name = att_row["file_name"]
            self.attach_deleted = False
            self.attach_existing_present = True
        else:
            self.attach_data = None
            self.attach_mime = None
            self.attach_name = None
            self.attach_deleted = False
            self.attach_existing_present = False
        self.update_preview()

    def save(self, repo: Repo, entry_uuid: Optional[str]):
        if not entry_uuid:
            return
        if self.attach_data and self.attach_mime:
            repo.upsert_attachment(entry_uuid, self.attach_name, self.attach_mime, self.attach_data)
            self.attach_existing_present = True
            self.attach_deleted = False
        elif self.attach_deleted or self.attach_existing_present:
            repo.delete_attachment(entry_uuid)
            self.attach_existing_present = False

    # --- UI operations
    def _attachment_pixmap_full(self) -> Optional[QPixmap]:
        if not self.attach_data or not self.attach_mime:
            return None
        if self.attach_mime in ("image/jpeg", "image/png"):
            return pixmap_from_image_bytes(self.attach_data, QSize(0, 0))
        return None

    def on_attachment_clicked(self):
        if not self.has_attachment():
            return
        if not self.attach_data:
            QMessageBox.warning(self.owner, "Attachment", "Attachment is not loaded in memory.")
            return
        if self.attach_mime == "application/pdf":
            self._download_attachment()
        else:
            self._open_attachment_viewer()

    def _open_attachment_viewer(self):
        pixmap = self._attachment_pixmap_full()
        if not pixmap or pixmap.isNull():
            QMessageBox.information(self.owner, "Attachment", "Preview is not available for this file.")
            return
        dlg = AttachmentViewerDialog(pixmap, self.attach_name or "Attachment", self.attach_data, self.attach_name, self.owner)
        dlg.show()

    def _download_attachment(self):
        if not self.attach_data:
            QMessageBox.warning(self.owner, "Attachment", "Attachment data is missing.")
            return
        default = self.attach_name or "attachment"
        if self.attach_mime == "application/pdf" and not default.lower().endswith(".pdf"):
            default += ".pdf"
        path, _ = QFileDialog.getSaveFileName(self.owner, "Save attachment", default)
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(self.attach_data)
            QMessageBox.information(self.owner, "Saved", "Attachment saved.")
        except Exception as e:
            QMessageBox.critical(self.owner, "Save failed", str(e))

    def on_select_attachment(self):
        path, _ = QFileDialog.getOpenFileName(
            self.owner,
            "Select attachment",
            "",
            "Images/PDF (*.jpg *.jpeg *.png *.pdf)"
        )
        if not path:
            return
        mime = guess_mime_from_path(path)
        if mime not in ALLOWED_MIME:
            QMessageBox.warning(self.owner, "Invalid file", "Only JPG, PNG, or PDF files are allowed.")
            return
        size = os.path.getsize(path)
        if size > ATTACH_MAX_BYTES:
            QMessageBox.warning(self.owner, "File too large", "File must be 10MB or smaller.")
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            QMessageBox.critical(self.owner, "Error", f"Failed to read file: {e}")
            return

        self.attach_data = data
        self.attach_mime = mime
        self.attach_name = os.path.basename(path)
        self.attach_deleted = False
        self.update_preview()

    def on_remove_attachment(self):
        self.attach_data = None
        self.attach_mime = None
        self.attach_name = None
        self.attach_deleted = True
        self.attach_existing_present = False
        self.update_preview()

    def update_preview(self):
        max_size = QSize(300, 200)
        has_attachment = self.has_attachment()
        pixmap: Optional[QPixmap] = None
        if has_attachment and self.attach_data and self.attach_mime:
            if self.attach_mime in ("image/jpeg", "image/png"):
                pixmap = pixmap_from_image_bytes(self.attach_data, max_size)
            elif self.attach_mime == "application/pdf":
                pixmap = pixmap_from_pdf_bytes(self.attach_data, max_size)

        self.preview.setVisible(has_attachment)
        if has_attachment:
            if pixmap:
                self.preview.setPixmap(pixmap)
                self.preview.setScaledContents(False)
                self.preview.setText("")
            else:
                self.preview.setPixmap(QPixmap())
                msg = "Preview not available"
                if self.attach_mime == "application/pdf":
                    msg = "PDF attached - click to download"
                self.preview.setText(msg)
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("")

        self.preview.setCursor(Qt.PointingHandCursor if has_attachment else Qt.ArrowCursor)
        if has_attachment:
            if self.attach_mime == "application/pdf":
                self.preview.setToolTip("Click to download the PDF attachment")
            else:
                self.preview.setToolTip("Click to view the attachment in a larger window")
        else:
            self.preview.setToolTip("")

        name_text = self.attach_name if self.attach_name else "None"
        if self.attach_deleted:
            name_text += " (removed)"
        self.name_lbl.setText(name_text)
        self.remove_btn.setEnabled(
            not self.view_mode and (self.attach_data is not None or self.attach_existing_present or self.attach_deleted)
        )


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
# Chart widgets (Insight)
# -------------------------

class ChartFilterBar(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        today = QDate.currentDate()
        default_from = today.addDays(-89)

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(default_from)
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(today)

        self.granularity = QComboBox()
        self.granularity.addItem("Day", "day")
        self.granularity.addItem("Month", "month")
        self._set_initial_granularity(default_from, today)

        self.refresh_btn = QPushButton("Refresh")

        layout.addWidget(QLabel("From"))
        layout.addWidget(self.date_from)
        layout.addWidget(QLabel("To"))
        layout.addWidget(self.date_to)
        layout.addWidget(QLabel("Granularity"))
        layout.addWidget(self.granularity)
        layout.addStretch(1)
        layout.addWidget(self.refresh_btn)

        self.date_from.dateChanged.connect(self._normalize_dates)
        self.date_to.dateChanged.connect(self._normalize_dates)
        self.granularity.currentIndexChanged.connect(self._emit_changed)
        self.refresh_btn.clicked.connect(self._emit_changed)

    def _set_initial_granularity(self, d_from: QDate, d_to: QDate):
        if d_from.daysTo(d_to) > 45:
            self.granularity.setCurrentIndex(1)  # Month
        else:
            self.granularity.setCurrentIndex(0)

    def _normalize_dates(self):
        if self.date_from.date() > self.date_to.date():
            self.date_from.setDate(self.date_to.date())
        self._emit_changed()

    def _emit_changed(self):
        self.changed.emit()

    def get_date_from(self) -> str:
        return qdate_to_iso(self.date_from.date())

    def get_date_to(self) -> str:
        return qdate_to_iso(self.date_to.date())

    def get_granularity(self) -> str:
        return self.granularity.currentData() or "day"


class ExpenseTrendChart(QWidget):
    def __init__(self, repo: Repo, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.dom = self.repo.get_domestic_currency()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.filters = ChartFilterBar()
        layout.addWidget(self.filters)

        self.placeholder = QLabel("No data")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet("color: #666;")

        self.chart_view = QChartView()
        self.chart_view.setRenderHint(QPainter.Antialiasing)
        layout.addWidget(self.chart_view, 1)
        layout.addWidget(self.placeholder, 1)
        self.placeholder.hide()

        self.filters.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        gran = self.filters.get_granularity()
        date_from = self.filters.get_date_from()
        date_to = self.filters.get_date_to()
        rows = self.repo.list_expense_trend(granularity=gran, date_from=date_from, date_to=date_to)

        if not rows:
            self.chart_view.hide()
            self.placeholder.setText("No expenses in this period")
            self.placeholder.show()
            return

        labels: List[str] = []
        label_index: Dict[str, int] = {}
        categories: Dict[str, str] = {}
        values: Dict[str, List[float]] = {}

        for r in rows:
            label = r["label"]
            if label not in label_index:
                label_index[label] = len(labels)
                labels.append(label)
            idx = label_index[label]

            code = r["account_code"]
            categories[code] = r["account_name"]
            if code not in values:
                values[code] = [0.0] * len(labels)
            # Ensure list length matches labels
            if len(values[code]) < len(labels):
                values[code].extend([0.0] * (len(labels) - len(values[code])))
            values[code][idx] += float(r["amount_domestic_sum"] or 0.0)

        for vals in values.values():
            if len(vals) < len(labels):
                vals.extend([0.0] * (len(labels) - len(vals)))

        series = QStackedBarSeries()
        max_val = 0.0
        for code, vals in values.items():
            bar = QBarSet(categories.get(code, code))
            col = color_for_key(code)
            bar.setColor(col)
            bar.setBorderColor(col.darker(115))
            for v in vals:
                bar.append(v)
                max_val = max(max_val, v)
            series.append(bar)

        chart = QChart()
        chart.addSeries(series)
        chart.setTitle(f"Expense Trend ({gran})")
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignBottom)
        chart.setAnimationOptions(QChart.SeriesAnimations)

        axis_x = QBarCategoryAxis()
        axis_x.append(labels)
        axis_y = QValueAxis()
        axis_y.setLabelFormat("%.0f")
        axis_y.applyNiceNumbers()
        axis_y.setTitleText(self.dom)
        chart.addAxis(axis_x, Qt.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_x)
        series.attachAxis(axis_y)

        for marker in chart.legend().markers(series):
            marker.clicked.connect(lambda _, m=marker: self._toggle_marker(m))

        self.chart_view.setChart(chart)
        self.placeholder.hide()
        self.chart_view.show()

    @staticmethod
    def _toggle_marker(marker):
        target = getattr(marker, "barset", None)
        barset = target() if callable(target) else marker.series()
        if not barset:
            return
        barset.setVisible(not barset.isVisible())
        marker.setVisible(True)
        color = marker.labelBrush().color()
        color.setAlpha(255 if barset.isVisible() else 80)
        marker.setLabelBrush(color)


class AssetsTrendChart(QWidget):
    def __init__(self, repo: Repo, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.dom = self.repo.get_domestic_currency()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.filters = ChartFilterBar()
        layout.addWidget(self.filters)

        toggles = QHBoxLayout()
        toggles.setContentsMargins(0, 0, 0, 0)
        toggles.setSpacing(12)
        toggles.addWidget(QLabel("Optional lines:"))
        self.chk_assets = QCheckBox("Assets")
        self.chk_liabs = QCheckBox("Liabilities")
        toggles.addWidget(self.chk_assets)
        toggles.addWidget(self.chk_liabs)
        toggles.addStretch(1)
        layout.addLayout(toggles)

        self.chart_view = QChartView()
        self.chart_view.setRenderHint(QPainter.Antialiasing)
        self.placeholder = QLabel("No data")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet("color: #666;")

        layout.addWidget(self.chart_view, 1)
        layout.addWidget(self.placeholder, 1)
        self.placeholder.hide()

        self.filters.changed.connect(self.refresh)
        self.chk_assets.stateChanged.connect(self.refresh)
        self.chk_liabs.stateChanged.connect(self.refresh)

        self.refresh()

    def refresh(self):
        gran = self.filters.get_granularity()
        date_from = self.filters.get_date_from()
        date_to = self.filters.get_date_to()
        rows = self.repo.list_assets_trend(granularity=gran, date_from=date_from, date_to=date_to)

        if not rows:
            self.chart_view.hide()
            self.placeholder.setText("No balance data in this period")
            self.placeholder.show()
            return

        labels = [r["label"] for r in rows]
        x_vals = list(range(len(labels)))

        net_series = QLineSeries()
        net_series.setName("Net assets")
        net_pen = QPen(color_for_key("NET"))
        net_pen.setWidth(2)
        net_series.setPen(net_pen)

        asset_series = QLineSeries()
        asset_series.setName("Assets")
        asset_series.setPen(QPen(color_for_key("ASSET"), 1.5))
        liab_series = QLineSeries()
        liab_series.setName("Liabilities")
        liab_series.setPen(QPen(color_for_key("LIAB"), 1.5))

        min_val = 0.0
        max_val = 0.0

        for i, r in enumerate(rows):
            net = float(r["net_assets"])
            net_series.append(float(i), net)
            min_val = min(min_val, net)
            max_val = max(max_val, net)
            if self.chk_assets.isChecked():
                a = float(r["asset_balance"])
                asset_series.append(float(i), a)
                min_val = min(min_val, a)
                max_val = max(max_val, a)
            if self.chk_liabs.isChecked():
                l = float(r["liab_balance"])
                liab_series.append(float(i), l)
                min_val = min(min_val, l)
                max_val = max(max_val, l)

        chart = QChart()
        chart.setTitle(f"Assets Trend ({gran})")
        chart.setAnimationOptions(QChart.SeriesAnimations)

        chart.addSeries(net_series)
        if self.chk_assets.isChecked():
            chart.addSeries(asset_series)
        if self.chk_liabs.isChecked():
            chart.addSeries(liab_series)

        axis_x = QBarCategoryAxis()
        axis_x.append(labels)
        axis_y = QValueAxis()
        axis_y.setLabelFormat("%.0f")
        if max_val == min_val:
            max_val += 1
        axis_y.setRange(min_val * 1.05, max_val * 1.05)
        axis_y.setTitleText(self.dom)

        chart.addAxis(axis_x, Qt.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignLeft)
        for s in chart.series():
            s.attachAxis(axis_x)
            s.attachAxis(axis_y)
            s.setPointsVisible(True)

        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignBottom)

        self.chart_view.setChart(chart)
        self.placeholder.hide()
        self.chart_view.show()

# -------------------------
# Dialogs
# -------------------------

class ExpenseJournalDetailDialog(QDialog):
    def __init__(self, repo: Repo, entry_uuid: Optional[str] = None, parent=None, start_edit_mode: bool = False):
        super().__init__(parent)
        self.repo = repo
        self.dom = self.repo.get_domestic_currency()
        self.entry_uuid = entry_uuid
        self.is_new = entry_uuid is None
        self.start_edit_mode = start_edit_mode

        self.setWindowTitle("Expense Journal Detail" if self.is_new else "Expense Journal Detail (View/Edit)")
        self.resize(400, 620)  # compact, dialog-like width slightly smaller than main window

        root = QVBoxLayout(self)

        self.view_mode = not self.is_new

        form = QFormLayout()
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDate(QDate.currentDate())
        self.store = QLineEdit()

        self.note_section = NoteFieldSection(self)
        note_wrap_widget = self.note_section.wrap_widget()

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
        self.attach_section = AttachmentSection(self, form)
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
        self.attach_section.set_view_mode(self.view_mode)
        self.note_section.set_view_mode(self.view_mode)

        if self.is_new:
            self.btn_edit.setVisible(False)
            self.btn_delete.setVisible(False)
            self.set_edit_mode()
            self.add_line()
        else:
            self.load_entry()
            if self.start_edit_mode:
                self.set_edit_mode()
            else:
                self.set_view_mode()

    def _load_categories(self):
        self.cat_map.clear()
        for r in self.repo.list_expense_categories():
            self.cat_map[r["account_name"]] = r["account_code"]

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
        for w in [self.date, self.store, self.currency, self.payment]:
            w.setEnabled(False)
        self.table.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_add_line.setEnabled(False)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(True)
        self.attach_section.set_view_mode(True)
        self.note_section.set_view_mode(True)

    def set_edit_mode(self):
        self.view_mode = False
        for w in [self.date, self.store, self.currency, self.payment]:
            w.setEnabled(True)
        self.table.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_add_line.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(False)
        self.btn_cancel.setText("Cancel")
        self.attach_section.set_view_mode(False)
        self.note_section.set_edit_mode()

    def add_line(self):
        row = self.table.rowCount()
        self.table.insertRow(row)

        cat = QComboBox()
        for name in self.cat_map.keys():
            cat.addItem(name)
        self.table.setCellWidget(row, 0, cat)

        sp = QDoubleSpinBox()
        sp.setRange(-10_000_000, 10_000_000)
        sp.setDecimals(2)
        sp.setSingleStep(1.0)
        self.table.setCellWidget(row, 1, sp)

        sp2 = QDoubleSpinBox()
        sp2.setRange(-10_000_000, 10_000_000)
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
        self.note_section.set_text(h["entry_text"] or "")

        items = self.repo.get_entry_items(self.entry_uuid)
        if items:
            self.currency.setText(items[0]["currency_original"])

        att = self.repo.get_attachment(self.entry_uuid)
        self.attach_section.load_existing(att)

        pay_code = None
        for it in items:
            if it["account_type"] in ("ASSET", "LIAB") and it["dc"] == "C":
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
            if abs(amt_dom) < 1e-9:
                continue  # allow negative; just skip true zero rows
            if is_foreign:
                amt_org = float(sp2.value())
                if abs(amt_org) < 1e-9:
                    raise ValueError("Original amount is required when currency is foreign and cannot be zero")
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
            raise ValueError("Add at least one expense line with non-zero amount")

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

    def on_save(self):
        try:
            accounting_date = qdate_to_iso(self.date.date())
            entry_title = self.store.text().strip() or None
            entry_text = self.note_section.text().strip() or None

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
            self.attach_section.save(self.repo, self.entry_uuid)
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
        self.note_section = NoteFieldSection(self)
        note_wrap_widget = self.note_section.wrap_widget()
        form.addRow("Type", self.entry_type)
        form.addRow("Date", self.date)
        form.addRow("Title (Vendor)", self.title)
        self.attach_section = AttachmentSection(self, form)
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
        self.attach_section.set_view_mode(self.view_mode)
        self.note_section.set_view_mode(self.view_mode)

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

    # Attachment click is handled inside AttachmentSection; keep stub for backward safety.
    def on_attachment_clicked(self):
        self.attach_section.on_attachment_clicked()

    def set_view_mode(self):
        self.view_mode = True
        for w in [self.entry_type, self.date, self.title]:
            w.setEnabled(False)
        self.table.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_add_line.setEnabled(False)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(True)
        self.attach_section.set_view_mode(True)
        self.note_section.set_view_mode(True)

    def set_edit_mode(self):
        self.view_mode = False
        for w in [self.entry_type, self.date, self.title]:
            w.setEnabled(True)
        self.table.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_add_line.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(False)
        self.btn_cancel.setText("Cancel")
        self.attach_section.set_view_mode(False)
        self.note_section.set_edit_mode()

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
        amt.setRange(-10_000_000, 10_000_000)
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
        org.setRange(-10_000_000, 10_000_000)
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
        self.note_section.set_text(h["entry_text"] or "")

        att = self.repo.get_attachment(self.entry_uuid)
        self.attach_section.load_existing(att)

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
            if abs(amt_dom) < 1e-9:
                continue

            cur = ccy.currentText()
            if cur == self.dom:
                amt_org = float(org.value()) if abs(org.value()) > 1e-9 else amt_dom
            else:
                amt_org = float(org.value())
                if abs(amt_org) < 1e-9:
                    raise ValueError("Original amount is required for foreign currency lines and cannot be zero")

            items.append({
                "account_code": account_code,
                "dc": dc.currentText(),
                "amount_domestic": amt_dom,
                "currency_original": cur,
                "amount_original": amt_org,
                "item_text": note.text().strip() or None,
            })
        if not items:
            raise ValueError("Add at least one line with non-zero amount")
        return items

    def on_save(self):
        try:
            accounting_date = qdate_to_iso(self.date.date())
            entry_type = self.entry_type.currentText()
            title = self.title.text().strip() or None
            note = self.note_section.text().strip() or None

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
            self.attach_section.save(self.repo, self.entry_uuid)
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
        current_section = None
        for r in rows:
            is_active = int(r["is_active"])
            if is_active == 0:
                section = "Inactive"
            else:
                section = ACCOUNT_TYPE_LABEL.get(r["account_type"], r["account_type"])

            if section != current_section:
                self.list.addItem(SectionHeaderItem(section))
                current_section = section

            payload = {
                "kind": "row",
                "account_code": r["account_code"],
                "account_name": r["account_name"],
                "account_type": r["account_type"],
                "is_active": is_active,
            }
            item = CardRowItem(payload)
            self.list.addItem(item)

            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(10, 8, 10, 8)
            lay.setSpacing(10)

            icon = QLabel(bs_icon(r["account_code"], r["account_type"]))
            icon.setFixedWidth(24)
            icon.setAlignment(Qt.AlignCenter)

            text_col = QVBoxLayout()
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setSpacing(2)
            name_lbl = QLabel(r["account_name"])
            name_lbl.setWordWrap(False)
            name_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            name_lbl.setMinimumWidth(200)
            type_lbl = QLabel(ACCOUNT_TYPE_LABEL.get(r["account_type"], r["account_type"]))
            type_lbl.setStyleSheet("color: #666; font-size: 12px;")
            text_col.addWidget(name_lbl)
            text_col.addWidget(type_lbl)

            active_lbl = QLabel("Active" if r["is_active"] else "Inactive")
            if r["is_active"]:
                active_lbl.setStyleSheet("color: #0a7a0a;")
            else:
                active_lbl.setStyleSheet("color: #a00;")
            active_lbl.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)

            edit_btn = QPushButton("Edit")
            edit_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            edit_btn.clicked.connect(lambda _, code=r["account_code"]: self.edit_account(code))

            lay.addWidget(icon)
            lay.addLayout(text_col, 1)
            lay.addStretch(1)

            right_col = QVBoxLayout()
            right_col.setContentsMargins(0, 0, 0, 0)
            right_col.setSpacing(6)
            right_col.addWidget(active_lbl, alignment=Qt.AlignRight | Qt.AlignVCenter)
            right_col.addWidget(edit_btn, alignment=Qt.AlignRight | Qt.AlignVCenter)
            lay.addLayout(right_col)

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

        seg_container = QWidget()
        seg_container.setObjectName("InsightSegmentContainer")
        seg = QHBoxLayout(seg_container)
        seg.setContentsMargins(0, 0, 0, 0)
        seg.setSpacing(8)
        self.btn_expense = QPushButton("My Expenses")
        self.btn_bs = QPushButton("My Accounts")
        self.btn_exp_trend = QPushButton("Expense Trend")
        self.btn_assets_trend = QPushButton("Assets Trend")
        for btn in (self.btn_expense, self.btn_bs, self.btn_exp_trend, self.btn_assets_trend):
            btn.setCheckable(True)
            btn.setMinimumHeight(36)
            btn.setMinimumWidth(120)
        segment_style = """
            QPushButton {
                background: #6e1d16;
                color: #fef6e4;
                border: 2px solid #6e1d16;
                border-radius: 14px;
                padding: 1px 14px;
                margin: 0;
                font-weight: 600;
            }
            QPushButton:checked {
                background: #f2c224;
                color: #3b1c0f;
                border-color: #e0ad1c;
            }
            QPushButton:hover:!checked {
                background: #843024;
            }
            QPushButton:!checked {
                opacity: 0.95;
            }
        """
        for btn in (self.btn_expense, self.btn_bs, self.btn_exp_trend, self.btn_assets_trend):
            btn.setStyleSheet(segment_style)
            seg.addWidget(btn)
        seg.addStretch(1)

        seg_scroll = QScrollArea()
        seg_scroll.setWidget(seg_container)
        seg_scroll.setWidgetResizable(False)
        seg_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        seg_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        seg_scroll.setFrameShape(QFrame.NoFrame)
        seg_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        seg_scroll.setContentsMargins(0, 0, 0, 0)
        seg_scroll.setViewportMargins(0, 0, 0, 0)
        seg_scroll.setFixedHeight(self.btn_expense.sizeHint().height() + 20)
        seg_scroll.setStyleSheet(
            """
            QScrollArea {
                background: #f2e4c7;
                border: none;
            }
            QScrollArea > QWidget {
                background: #f2e4c7;
            }
            """
        )
        seg_container.setStyleSheet("background: #f2e4c7;")
        QScroller.grabGesture(seg_scroll.viewport(), QScroller.LeftMouseButtonGesture)
        root.addWidget(seg_scroll)

        nav = QHBoxLayout()
        self.back = QToolButton()
        self.back.setText("<")
        self.back.clicked.connect(self.go_back)
        self.back.setEnabled(False)
        self.title = QLabel("My Expenses")
        f = self.title.font()
        f.setPointSize(f.pointSize() + 2)
        f.setBold(True)
        self.title.setFont(f)
        nav.addWidget(self.back)
        nav.addWidget(self.title)
        nav.addStretch(1)
        self.btn_manage_accounts = QToolButton()
        self.btn_manage_accounts.setText("⚙️")
        self.btn_manage_accounts.setCursor(Qt.PointingHandCursor)
        self.btn_manage_accounts.setToolTip("Manage accounts")
        self.btn_manage_accounts.setStyleSheet(
            """
            QToolButton {
                background: transparent;
                border: none;
                font-size: 16px;
                padding: 4px 6px;
            }
            QToolButton:hover {
                background: rgba(0, 0, 0, 0.08);
                border-radius: 10px;
            }
            """
        )
        self.btn_manage_accounts.clicked.connect(self._manage_accounts)
        nav.addWidget(self.btn_manage_accounts)
        root.addLayout(nav)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.page_expense = JournalCardList(repo, mode="expense")
        self.page_bs = BalanceSheetOverviewWidget(repo)
        self.page_exp_trend = ExpenseTrendChart(repo)
        self.page_assets_trend = AssetsTrendChart(repo)
        self.stack.addWidget(self.page_expense)
        self.stack.addWidget(self.page_bs)
        self.stack.addWidget(self.page_exp_trend)
        self.stack.addWidget(self.page_assets_trend)

        self.nav_stack: List[Tuple[int, str]] = []

        self.page_expense.on_open_entry = self.open_entry_general
        self.page_bs.on_open_account = self.open_account_transactions

        self.btn_expense.clicked.connect(lambda: self.switch_root(0))
        self.btn_bs.clicked.connect(lambda: self.switch_root(1))
        self.btn_exp_trend.clicked.connect(lambda: self.switch_root(2))
        self.btn_assets_trend.clicked.connect(lambda: self.switch_root(3))
        self._set_segment_checked(0)
        self._update_manage_button()

    def _set_segment_checked(self, idx: int):
        self.btn_expense.setChecked(idx == 0)
        self.btn_bs.setChecked(idx == 1)
        self.btn_exp_trend.setChecked(idx == 2)
        self.btn_assets_trend.setChecked(idx == 3)

    def switch_root(self, idx: int):
        self.nav_stack.clear()
        self.back.setEnabled(False)
        self.stack.setCurrentIndex(idx)
        self._set_segment_checked(idx)
        titles = ["My Expenses", "My Accounts", "Expense Trend", "Assets Trend"]
        self.title.setText(titles[idx] if 0 <= idx < len(titles) else "")
        self.refresh_current()
        self._update_manage_button()

    def refresh_current(self):
        w = self.stack.currentWidget()
        if isinstance(w, JournalCardList):
            w.refresh()
        elif isinstance(w, BalanceSheetOverviewWidget):
            w.refresh()
        elif isinstance(w, ExpenseTrendChart):
            w.refresh()
        elif isinstance(w, AssetsTrendChart):
            w.refresh()

    def go_back(self):
        if not self.nav_stack:
            return
        idx, title = self.nav_stack.pop()
        self.stack.setCurrentIndex(idx)
        self._set_segment_checked(idx)
        self.title.setText(title)
        self.back.setEnabled(len(self.nav_stack) > 0)
        self.refresh_current()
        self._update_manage_button()

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
        self._update_manage_button()

    def refresh_all(self):
        self.page_expense.refresh()
        self.page_bs.refresh()
        self.page_exp_trend.refresh()
        self.page_assets_trend.refresh()
        self.refresh_current()
        self._update_manage_button()

    def _manage_accounts(self):
        win = self.window()
        if hasattr(win, "manage_accounts"):
            win.manage_accounts()

    def _update_manage_button(self):
        show = self.stack.currentIndex() == 1 and not self.nav_stack
        self.btn_manage_accounts.setVisible(show)


class MainWindow(QMainWindow):
    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo
        self.importer = JsonExpenseImportService(repo)
        self.prompt_builder = PromptBuilder(repo, os.path.join(os.path.dirname(__file__), "dev", "JSON Schema.json"))
        self.busy_overlay = BusyOverlay(self)
        self.ai_controller: Optional[AiImportController] = None
        self.gemini_error: Optional[str] = None
        try:
            gemini_client = GeminiClient()
            self.ai_controller = AiImportController(
                repo=repo,
                importer=self.importer,
                prompt_builder=self.prompt_builder,
                gemini_client=gemini_client,
                overlay=self.busy_overlay,
                open_entry=self.open_expense_entry,
                refresh=self.refresh_all,
                parent=self,
            )
        except Exception as e:
            self.gemini_error = str(e)
        self.setWindowTitle("Debibi")
        self.resize(500, 820)

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
        tabs.setMinimumSize(480, 760)
        tabs.setStyleSheet(
            """
            QTabWidget::pane {
                border: none;
                border-radius: 18px;
                padding: 16px;
                background: #f2e4c7;
            }
            QTabBar {
                qproperty-drawBase: 0;
            }
            QTabBar::tab {
                min-height: 44px;
                min-width: 44px;
                padding: 10px 22px;
                margin: 0 6px;
                color: #fef6e4;
                background: #6e1d16;
                border: 2px solid #6e1d16;
                border-radius: 22px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: #f2c224;
                color: #3b1c0f;
                border-color: #e0ad1c;
            }
            QTabBar::tab:hover:!selected {
                background: #843024;
            }
            QTabBar::tab:!selected {
                opacity: 0.95;
            }
            #TabContainer {
                background: #f2e4c7;
            }
            """
        )

        feed = QWidget()
        feed_l = QVBoxLayout(feed)
        feed_l.setContentsMargins(16, 16, 16, 16)
        feed_l.setSpacing(12)

        feed_title = QLabel("Feed Debibi")
        feed_title.setAlignment(Qt.AlignCenter)
        f_title = feed_title.font()
        f_title.setPointSize(f_title.pointSize() + 2)
        f_title.setBold(True)
        feed_title.setFont(f_title)

        btn_camera = QPushButton("Feed Debibi with your camera")
        btn_camera.setMinimumHeight(120)
        btn_file = QPushButton("Feed Debibi image or file")
        btn_file.setMinimumHeight(120)
        btn_text = QPushButton("Feed Debibi any text")
        btn_text.setMinimumHeight(120)

        btn_manual_expense = QPushButton("Record expenses manually")
        btn_manual_expense.setMinimumHeight(48)
        btn_manual_expense.clicked.connect(self.new_expense)

        btn_manual_advanced = QPushButton("Record other transactions manually")
        btn_manual_advanced.setMinimumHeight(48)
        btn_manual_advanced.clicked.connect(self.new_general)

        feed_btn_style = """
            QPushButton {
                background: #6e1d16;
                color: #fef6e4;
                border: 2px solid #6e1d16;
                border-radius: 18px;
                padding: 12px 18px;
                font-weight: 700;
                font-size: 15px;
            }
            QPushButton:hover {
                background: #843024;
            }
            QPushButton:pressed {
                background: #f2c224;
                color: #3b1c0f;
                border-color: #e0ad1c;
            }
        """
        for btn in (btn_camera, btn_file, btn_text, btn_manual_expense, btn_manual_advanced):
            btn.setStyleSheet(feed_btn_style)

        btn_camera.clicked.connect(lambda: self._invoke_ai("camera"))
        btn_file.clicked.connect(lambda: self._invoke_ai("file"))
        btn_text.clicked.connect(lambda: self._invoke_ai("text"))

        feed_l.addWidget(feed_title)
        feed_l.addSpacing(6)
        feed_l.addWidget(btn_camera)
        feed_l.addWidget(btn_file)
        feed_l.addWidget(btn_text)
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
        tabs.setCurrentIndex(1)

        container = QWidget()
        container.setObjectName("TabContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(16, 16, 16, 16)
        container_layout.setSpacing(12)
        container_layout.addWidget(tabs)
        self.setCentralWidget(container)

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

    def open_expense_entry(self, entry_uuid: str, start_edit: bool = False):
        dlg = ExpenseJournalDetailDialog(self.repo, entry_uuid=entry_uuid, parent=self, start_edit_mode=start_edit)
        if dlg.exec():
            self.refresh_all()

    def _invoke_ai(self, mode: str):
        if not self.ai_controller:
            QMessageBox.critical(
                self,
                "Gemini not ready",
                self.gemini_error or "Gemini client is not configured. Set GEMINI_API_KEY and restart.",
            )
            return
        if mode == "camera":
            self.ai_controller.import_from_camera()
        elif mode == "file":
            self.ai_controller.import_from_file()
        elif mode == "text":
            self.ai_controller.import_from_text()

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
    icon_path = os.path.join("assets", "debibi_icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    app.setStyleSheet(
        """
        QMainWindow, QDialog, QMessageBox {
            background-color: #f2e4c7;
        }
        """
    )
    w = MainWindow(repo)
    w.show()
    rc = app.exec()
    repo.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
