# Who owes what?

A group's shared expenses — no spreadsheets, no account required for
everyone, no ads. Built across four progressive tiers as a Streamlit
prototype.

This project was developed during a hackathon based on the
organizer's problem statement and provided context. The
implementation was created through AI-assisted coding, with me
leading the requirements, prompting, testing, and iterative
refinement.

## Files

| File            | Tier(s) | What it is                                                          |
|------------------|---------|----------------------------------------------------------------------|
| `app_tier1.py`   | 1       | Single-session prototype: create a group, log expenses, see totals. |
| `app_tier2.py`   | 2       | Adds persistence (SQLite), manager login, and a view-only share link.|
| `app_tier3.py`   | 1–3     | Adds repayment plans, unequal shares, natural-language entry, and closing groups. |
| `app_tier4.py`   | 1–4     | Full version: everything above plus multi-currency, recurring expenses, copy-paste summaries, reminder messages, and AI receipt scanning. |

## Running

```bash
pip install -r requirements.txt
streamlit run app_tier4.py     # or app_tier1.py / app_tier2.py / app_tier3.py
```

`app_tier2.py`, `app_tier3.py`, and `app_tier4.py` create a `whoowes.db`
file next to themselves on first run — that's where accounts/groups/
expenses live. It's excluded from git via `.gitignore` since it can
contain real account data once you use it.

## Tier 4 receipt scanning

Optional. Needs your own Anthropic API key (from
[console.anthropic.com](https://console.anthropic.com)), entered in
the app itself each session — it's never written to disk or the
database.

## Running multiple tiers side by side

Since Streamlit defaults every app to port 8501, run each on its own
port to compare them at once:

```bash
streamlit run app_tier1.py --server.port 8501
streamlit run app_tier2.py --server.port 8502
streamlit run app_tier3.py --server.port 8503
streamlit run app_tier4.py --server.port 8504
```