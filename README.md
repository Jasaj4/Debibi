# Debibi (Desktop Prototype)
> **Concept credit**  
> The original product concept of Debibi was developed collaboratively with
> Chit Hei Ng, Xilai Wang, and Junwen Huang for the Gillmore Hackathon 2025,
> where the idea was awarded **1st place**.  
>  
> This repository contains a **fully independent, individual implementation**
> created specifically for the MISDI Individual Programming Assignment.
> All code, system design, and AI-assisted development documented here were
> produced solely by the author after the hackathon.

> **Status**: Local-first desktop prototype (proof of concept).
> This build intentionally excludes bank/credit-card sync, budgeting or debt advice,
> server/cloud infrastructure, mobile or production-grade web UI, and character/gamification systems.
> The focus is on validating the core ledger model, AI-assisted input flow and AI chat-bot.

An ADHD-friendly personal finance prototype that turns expense tracking into a light, low-pressure experience. The current build focuses on a local, offline PySide6 desktop UI with a double-entry SQLite backbone. It now includes an AI “Feed Debibi” flow that sends receipts/text to Debibi and auto-creates expense entries.

## Overview (Background & Goal)

- Motivation: student/young-adult debt stress and the difficulty ADHD users face with heavy finance apps. Debibi aims to lower friction, hide intimidating codes, and keep engagement gentle.
- Approach: simple mobile-like desktop UI, receipts-as-feed metaphor, and emotionally neutral visuals.
- Accounting core: ERP-style double-entry ledger (journal header + line items). This guarantees internal consistency (assets = liabilities + equity) and allows future features (cashflow, net worth, anomaly detection) without rework.
- Scope for this prototype: manual journal entry, basic lists, balance overview, and user-managed asset/liability accounts. Everything runs locally; no network or bank links.

### Design Principles (ADHD-friendly by design)

- **Low activation energy**: minimal required fields, defaults everywhere
- **Deferred correctness**: drafts first, accounting correctness enforced automatically
- **No forced routines**: no daily reminders, streaks, or penalties
- **Cognitive shielding**: account codes, debits/credits hidden unless needed

## How to Run

Prerequisites: Python 3.10+ and pip.

```bash
# From the repo root
python -m venv .venv

source .venv/bin/activate

# if you are on Windows: 
# .venv\Scripts\Activate

pip install --upgrade pip
pip install -e .

# Set your Gemini AI key in .env (create if missing)
echo "GEMINI_API_KEY=your-key-here" >> .env
# Optional: override model name
echo "GEMINI_MODEL=gemini-2.5-flash" >> .env

# Launch the app
python debibi.py
```

Notes:

- The app creates/uses a local `debibi.db` in the working directory. On first run it seeds sample accounts and a few example entries.
- All data is offline; quit the app to close the DB.
- Debibi’s AI needs network access and a valid `GEMINI_API_KEY`. If it’s missing, AI buttons show an error and fall back to manual entry.

Sample `.env` file:

```dotenv
GEMINI_API_KEY=XXXXXXXXXXXXXXXXXXXXXXX
GEMINI_MODEL=gemini-2.5-flash
```

## Main Features & Usage

All-in-one guide across the three tabs. Everything is saved locally, so you can reopen, edit, or delete later.

### Feed (Capture without thinking)

The fastest path from “I spent money” to a balanced journal entry.

- **Feed Debibi automatically**
  - Camera capture: one tap to snap a receipt. Debibi “chews” it and spits out an expense draft, with the image/PDF tucked in as proof.
  - Pick image/file: hand Debibi a JPG/PNG/PDF (≤10 MB) and he drafts the expense for you, great for a e-receipts.
  - Paste text: drop in the receipt words or your note; Debibi takes care of the remaining work.
  - If Debibi is asleep/offline: he pops a gentle alert and you can keep going with manual entry.
- **Manual entry**
  - Expense Journal (simple): date, store, payment account, category/amount. Debits/credits balance automatically; supports one image/PDF attachment.
  - General Journal (advanced): Designed for finance-savvy users; Take advantage of the full flexibility and reliability of double-entry bookkeeping. Record any type of transaction, including inter-account transfers, by selecting account and debit/credit entries per line, just like in an ERP system.

### Debibi (Reflect, not judge)

A conversational layer for lightweight reflection, not budgeting enforcement.

- Ask questions about your money from Debibi, using your last 30 days (daily) and 6 months (monthly) expense trends as context.
- Debibi only sees totals grouped by day/month (no raw line items), keeping the context lightweight and privacy-friendly.
- “How are my spends this month?” works fine; prior turns are included in context.
- Chat history lives in memory only. Clears on ❌ or app exit. If Debibi’s AI isn’t configured, you’re notified on send.

### Insight (See structure, not noise)

Traditional finance views built on the same ledger, surfaced only when you want them.

- **My Expenses**: A list showing what I spent money on and how much; tap to open a journal entry for edit/delete.
- **My Accounts**: A list showing how much money I have in each account and where I have debts or credit card balances; tap an account → its transactions → the entry. Use the ⚙️ to add/rename/deactivate accounts.
- **Expense Trend**: stacked bars by category; set From/To and switch day/month granularity; toggle categories via legend.
- **Assets Trend**: net-assets line; optionally show assets and liabilities lines via checkboxes; same date/granularity filters.

### JSON & debugging

- Direct JSON import: Actions Menu → Import JSON Entry to load a file matching `JSON Schema.json`; opens an expense draft for review/edit.
- When Debibi can’t digest a “snack” (JSON parse/validation fails), the raw payload is saved to `log/yyyymmdd_hhmmss_xxx.json` so you can inspect and try again later.

## Tech Rationale

- **PySide6**: native desktop feel and flexible custom UI without web stack overhead
- **SQLite**: transparent, inspectable, zero-setup ledger storage
- **Local-first**: predictable behavior, no auth, no background sync
- **LLM as assistant, not authority**: AI drafts entries; users always review
