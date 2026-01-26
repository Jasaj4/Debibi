
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
import sqlite3
import uuid
import datetime as dt
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Any

from PySide6.QtCore import Qt, QSize, QDate
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QStackedWidget, QTabWidget, QToolButton, QMessageBox,
    QDialog, QFormLayout, QLineEdit, QTextEdit, QDateEdit, QComboBox, QDoubleSpinBox,
    QTableWidget, QAbstractItemView, QHeaderView, QCheckBox, QMenuBar,
    QSizePolicy
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

    def list_user_managed_bs_accounts(self) -> List[sqlite3.Row]:
        return self.list_accounts("is_user_managed=1 AND account_type IN ('ASSET','LIAB')")

    def next_user_managed_code(self, account_type: str) -> str:
        if account_type == "ASSET":
            pattern = "1?????????"
            base_floor = 1000000000
        elif account_type == "LIAB":
            pattern = "2?????????"
            base_floor = 2000000000
        else:
            raise ValueError("account_type must be ASSET or LIAB")

        row = self.conn.execute(
            """SELECT printf('%010d', COALESCE(MAX(CAST(account_code AS INTEGER)), ?) + 1) AS next_code
               FROM gl_account
               WHERE account_type=? AND account_code GLOB ?""",
            (base_floor, account_type, pattern),
        ).fetchone()
        return row["next_code"]

    def upsert_user_managed_account(self, account_code: str, account_name: str, account_type: str, is_active: int):
        self.conn.execute(
            """INSERT OR IGNORE INTO gl_account(account_code, account_name, account_type, is_pl, is_active, is_user_managed)
               VALUES(?, ?, ?, 0, ?, 1)""",
            (account_code, account_name, account_type, is_active),
        )
        self.conn.execute(
            """UPDATE gl_account SET account_name=?, is_active=?
               WHERE account_code=? AND is_user_managed=1 AND account_type IN ('ASSET','LIAB')""",
            (account_name, is_active, account_code),
        )
        self.conn.commit()

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
        self.resize(780, 520)

        root = QVBoxLayout(self)

        self.view_mode = not self.is_new

        form = QFormLayout()
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDate(QDate.currentDate())
        self.store = QLineEdit()
        self.note = QTextEdit()
        self.note.setFixedHeight(70)

        self.currency = QLineEdit()
        self.currency.setPlaceholderText(self.dom)
        self.currency.setText(self.dom)
        self.currency.textChanged.connect(self.on_currency_changed)

        self.payment = QComboBox()
        self.payment_map: Dict[str, str] = {}
        self._load_payment_accounts()

        form.addRow("Date", self.date)
        form.addRow("Store", self.store)
        form.addRow("Note", self.note)
        form.addRow("Currency", self.currency)
        form.addRow("Payment account", self.payment)
        root.addLayout(form)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Category", "Amount (domestic)", "Original amount", ""])
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

    def _load_payment_accounts(self):
        rows = self.repo.list_asset_accounts()
        self.payment.clear()
        self.payment_map.clear()
        for r in rows:
            self.payment.addItem(r["account_name"])
            self.payment_map[r["account_name"]] = r["account_code"]
        if "Cash" in self.payment_map:
            self.payment.setCurrentText("Cash")

    def on_currency_changed(self, ccy: str):
        ccy = ccy.strip().upper() if ccy else ""
        is_foreign = (ccy != "" and ccy != self.dom)
        self.table.setColumnHidden(2, not is_foreign)

    def set_view_mode(self):
        self.view_mode = True
        for w in [self.date, self.store, self.note, self.currency, self.payment]:
            w.setEnabled(False)
        self.table.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_add_line.setEnabled(False)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(True)

    def set_edit_mode(self):
        self.view_mode = False
        for w in [self.date, self.store, self.note, self.currency, self.payment]:
            w.setEnabled(True)
        self.table.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_add_line.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(False)
        self.btn_cancel.setText("Cancel")

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

        items = self.repo.get_entry_items(self.entry_uuid)
        if items:
            self.currency.setText(items[0]["currency_original"])

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
    def __init__(self, repo: Repo, entry_uuid: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.dom = self.repo.get_domestic_currency()
        self.entry_uuid = entry_uuid
        self.is_new = entry_uuid is None

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
        form.addRow("Type", self.entry_type)
        form.addRow("Date", self.date)
        form.addRow("Title", self.title)
        form.addRow("Note", self.note)
        root.addLayout(form)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Account", "D/C", "Amount (domestic)", "Currency", "Original amount", "Item note", ""])
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

        if self.is_new:
            self.btn_edit.setVisible(False)
            self.btn_delete.setVisible(False)
            self.set_edit_mode()
            self.add_line()
            self.add_line()
        else:
            self.load_entry()
            self.set_view_mode()

    def set_view_mode(self):
        self.view_mode = True
        for w in [self.entry_type, self.date, self.title, self.note]:
            w.setEnabled(False)
        self.table.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_add_line.setEnabled(False)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(True)

    def set_edit_mode(self):
        self.view_mode = False
        for w in [self.entry_type, self.date, self.title, self.note]:
            w.setEnabled(True)
        self.table.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_add_line.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.btn_edit.setEnabled(False)
        self.btn_cancel.setText("Cancel")

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


class BalanceSheetAccountDetailDialog(QDialog):
    def __init__(self, repo: Repo, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.setWindowTitle("Balance Sheet Account Detail")
        self.resize(720, 460)

        root = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setSpacing(6)
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

            icon = QLabel(bs_icon(r["account_code"], t))
            icon.setFixedWidth(28)
            icon.setAlignment(Qt.AlignCenter)

            name = QLineEdit(r["account_name"])
            active = QCheckBox("Active")
            active.setChecked(int(r["is_active"]) == 1)

            save = QPushButton("Save")
            save.clicked.connect(lambda _, i=item, n=name, a=active: self.save_row(i, n, a))

            lay.addWidget(icon)
            lay.addWidget(name, 1)
            lay.addWidget(active)
            lay.addWidget(save)

            self.list.setItemWidget(item, w)
            item.setSizeHint(QSize(10, 52))

    def save_row(self, item: QListWidgetItem, name_edit: QLineEdit, active_chk: QCheckBox):
        data = item.data(Qt.UserRole) or {}
        if data.get("kind") != "row":
            return
        account_code = data["account_code"]
        account_type = data["account_type"]
        name = name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Account name is required.")
            return
        is_active = 1 if active_chk.isChecked() else 0
        try:
            self.repo.upsert_user_managed_account(account_code, name, account_type, is_active)
            QMessageBox.information(self, "Saved", "Account saved.")
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def add_account(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Balance Sheet Account")
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        typ = QComboBox()
        typ.addItems(["ASSET", "LIAB"])
        name = QLineEdit()
        form.addRow("Type", typ)
        form.addRow("Name", name)
        lay.addLayout(form)

        btns = QHBoxLayout()
        b_cancel = QPushButton("Cancel")
        b_save = QPushButton("Save")
        b_save.setDefault(True)
        btns.addStretch(1)
        btns.addWidget(b_cancel)
        btns.addWidget(b_save)
        lay.addLayout(btns)

        b_cancel.clicked.connect(dlg.reject)

        def _save():
            t = typ.currentText()
            n = name.text().strip()
            if not n:
                QMessageBox.warning(dlg, "Validation", "Name is required.")
                return
            code = self.repo.next_user_managed_code(t)
            try:
                self.repo.upsert_user_managed_account(code, n, t, 1)
                dlg.accept()
            except Exception as e:
                QMessageBox.critical(dlg, "Save failed", str(e))

        b_save.clicked.connect(_save)

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
        self.setWindowTitle("Debibi")
        self.resize(430, 760)

        menubar = QMenuBar(self)
        self.setMenuBar(menubar)
        m = menubar.addMenu("Actions")

        act_new_expense = QAction("New Expense Entry", self)
        act_new_general = QAction("New Journal Entry", self)
        act_manage_accounts = QAction("Manage BS Accounts", self)
        act_refresh = QAction("Refresh", self)

        m.addAction(act_new_expense)
        m.addAction(act_new_general)
        m.addSeparator()
        m.addAction(act_manage_accounts)
        m.addSeparator()
        m.addAction(act_refresh)

        act_new_expense.triggered.connect(self.new_expense)
        act_new_general.triggered.connect(self.new_general)
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
