# Debibi (Desktop Prototype)

An ADHD-friendly personal finance prototype that turns expense tracking into a light, low-pressure experience. The current build focuses on a local, offline PySide6 desktop UI with a double-entry SQLite backbone. It implements the minimum workflow for recording expenses and viewing balances while leaving space for future “raise your Debibi” gamification and AI-powered input.


## Overview (Background & Goal)
- Motivation: student/young-adult debt stress and the difficulty ADHD users face with heavy finance apps. Debibi aims to lower friction, hide intimidating codes, and keep engagement gentle.
- Approach: simple mobile-like desktop UI, receipts-as-feed metaphor, and emotionally neutral visuals. Data model follows trustworthy double-entry accounting so later insights stay consistent.
- Scope for this prototype: manual journal entry, basic lists, balance overview, and user-managed asset/liability accounts. Everything runs locally; no network or bank links.

## How to Run
Prerequisites: Python 3.10+ and pip.

```bash
# From the repo root
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install PySide6

# Launch the app
python debibi.py
```

Notes:
- The app creates/uses a local `debibi.db` in the working directory. On first run it seeds sample accounts and a few example entries.
- Everything is offline; quit the app to close the DB.

## Main Features & Usage
- Feed tab: quick manual capture.
  - “Record expenses manually” opens **Expense Journal Detail** (guided: categories + one payment account, auto-balances debit/credit).
  - “Record other transactions manually” opens **General Journal Detail** (advanced free-form journal lines with balance check).
- Attachments: both journal dialogs support one attachment per entry (JPG/PNG/PDF up to 10MB). Use **Add / Replace** or **Remove**; images/PDFs show inline preview (PDF requires QtPdf).
- Import from JSON: use **Actions → Import JSON Entry** to load a JSON file that follows the schema in `dev/JSON Schema.json`; the importer creates an EXPENSE entry and opens it for review/edit before saving.
- Insight tab: read-only drill-downs.
  - **Expense List** (card list by date); tap to open **General Journal Detail** for the entry.
  - **Balance Sheet Overview** (assets/liabilities with section headers); tap an account to see **Account Transaction List**, then drill into an entry.
- Manage BS Accounts: add/rename/activate/deactivate user-managed asset/liability accounts (codes auto-generated).
- Debibi tab: placeholder for future avatar/chat.
- JSON schema for AI ingestion: `dev/JSON Schema.json` defines the expected expense payload when an LLM/OCR front end is added.

## Importing Expenses via JSON (LLM/API hook)
1) Generate a JSON payload that matches `dev/JSON Schema.json` (account names must match active accounts; defaults to domestic currency when omitted).
2) In the app, go to **Actions → Import JSON Entry** and pick the file.
3) The entry is created as EXPENSE and immediately opened in **General Journal Detail** so you can review/edit, attach a receipt, and save.

Example payload:

```json
{
  "date": "2026-01-25",
  "store": "TESCO",
  "note": "Parsed by OCR/LLM",
  "payment_account": "Cash",
  "currency_original": "GBP",
  "lines": [
    {"expense_category": "Food and dining", "note": "Milk", "amount_domestic": 2.15, "amount_original": 2.15},
    {"expense_category": "Clothing and personal care", "note": "T-shirt", "amount_domestic": 10, "amount_original": 10}
  ]
}
```


## Development Status
- Done: core schema creation and seeding; manual expense & general journal dialogs with validation and balance check; attachment storage + preview; JSON expense import; expense list + account transaction list cards; balance sheet overview; user-managed BS account maintenance; sample icons; domestic currency handling; basic navigation stack.
- In progress / Not yet: Debibi avatar + chat; gamified quests/XP/moods; automated OCR/voice capture; richer charts; polished mobile visual language (fonts/colors), real sticky headers, settings UI, tests/CI.
