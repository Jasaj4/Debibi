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
- Insight tab: read-only drill-downs.
  - **Expense List** (card list by date); tap to open **General Journal Detail** for the entry.
  - **Balance Sheet Overview** (assets/liabilities with section headers); tap an account to see **Account Transaction List**, then drill into an entry.
- Manage BS Accounts: add/rename/activate/deactivate user-managed asset/liability accounts (codes auto-generated).
- Debibi tab: placeholder for future avatar/chat.
- JSON schema for AI ingestion: `JSON Schema.json` defines the expected expense payload if an LLM/OCR front end is added later.


## Development Status
- Done: core schema creation and seeding; manual expense & general journal dialogs with validation and balance check; expense list + account transaction list cards; balance sheet overview; user-managed BS account maintenance; sample icons; domestic currency handling; basic navigation stack.
- In progress / Not yet: Debibi avatar + chat; gamified quests/XP/moods; AI-driven input (OCR/voice/LLM) and auto-categorization; charts (Expense Trend, Assets Trend); polished mobile visual language (fonts/colors), real sticky headers, settings UI, tests/CI.
