"""
One-off tool: reset a manager's password directly in whoowes.db.
Use this only when "Forgot password?" can't help (e.g. the account
predates the security-question feature).

Run with:  python reset_password.py
Must be in the same folder as whoowes.db.
"""

import os
import sqlite3
import hashlib
import secrets
import getpass

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whoowes.db")


def hash_password(password: str):
    salt = secrets.token_bytes(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return salt.hex(), pwd_hash.hex()


def main():
    if not os.path.exists(DB_PATH):
        print(f"Couldn't find whoowes.db next to this script (looked in: {DB_PATH}).")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    username = input("Username to reset: ").strip()
    row = conn.execute(
        "SELECT id, username FROM managers WHERE LOWER(username) = LOWER(?)", (username,)
    ).fetchone()

    if row is None:
        print(f"No account found with username '{username}'.")
        conn.close()
        return

    print(f"Found account: {row['username']}")
    new_password = getpass.getpass("New password (input hidden): ")
    confirm = getpass.getpass("Confirm new password: ")

    if new_password != confirm:
        print("Passwords didn't match. Nothing was changed.")
        conn.close()
        return
    if len(new_password) < 4:
        print("Password must be at least 4 characters. Nothing was changed.")
        conn.close()
        return

    salt, pwd_hash = hash_password(new_password)
    conn.execute(
        "UPDATE managers SET salt = ?, password_hash = ? WHERE id = ?",
        (salt, pwd_hash, row["id"]),
    )
    conn.commit()
    conn.close()
    print(f"Password reset for '{row['username']}'. You can log in with it now.")


if __name__ == "__main__":
    main()