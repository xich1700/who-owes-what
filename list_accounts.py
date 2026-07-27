"""
Diagnostic: shows exactly which whoowes.db this script sees,
and every username stored in it.

Run with:  python list_accounts.py
Must be run from the same folder as the app you're troubleshooting.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whoowes.db")

print(f"Looking for database at:\n  {DB_PATH}\n")

if not os.path.exists(DB_PATH):
    print("No whoowes.db found in this folder.")
else:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT username FROM managers ORDER BY username").fetchall()
    conn.close()
    if not rows:
        print("whoowes.db exists but has no accounts in it.")
    else:
        print("Accounts found in this database:")
        for r in rows:
            print(f"  - {r['username']}")