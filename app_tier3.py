"""
Who owes what? - Front-end prototype (Streamlit)
Tier 3 (Pushed): repayment plans, recorded refunds, unequal shares,
natural-language expense entry, closing a settled group.

Run with:  streamlit run app.py
Requires:  pip install streamlit
"""

import os
import re
import uuid
import secrets
import hashlib
import sqlite3

import streamlit as st

st.set_page_config(page_title="Who owes what?", page_icon="💸", layout="centered")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whoowes.db")


# =========================================================
# 1) DATABASE
# =========================================================
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS managers (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        salt TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        security_question TEXT,
        security_answer_salt TEXT,
        security_answer_hash TEXT
    );
    CREATE TABLE IF NOT EXISTS groups (
        id TEXT PRIMARY KEY,
        manager_id TEXT NOT NULL,
        name TEXT NOT NULL,
        share_token TEXT UNIQUE NOT NULL
    );
    CREATE TABLE IF NOT EXISTS people (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS expenses (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        payer_id TEXT NOT NULL,
        amount REAL NOT NULL,
        label TEXT
    );
    CREATE TABLE IF NOT EXISTS expense_beneficiaries (
        expense_id TEXT NOT NULL,
        person_id TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS repayments (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        from_person_id TEXT NOT NULL,
        to_person_id TEXT NOT NULL,
        amount REAL NOT NULL
    );
    """)
    conn.commit()

    # ---- Migrations for databases created by earlier tiers ----
    manager_cols = [r["name"] for r in conn.execute("PRAGMA table_info(managers)").fetchall()]
    for col in ["security_question", "security_answer_salt", "security_answer_hash"]:
        if col not in manager_cols:
            conn.execute(f"ALTER TABLE managers ADD COLUMN {col} TEXT")

    group_cols = [r["name"] for r in conn.execute("PRAGMA table_info(groups)").fetchall()]
    if "closed" not in group_cols:
        conn.execute("ALTER TABLE groups ADD COLUMN closed INTEGER NOT NULL DEFAULT 0")

    benef_cols = [r["name"] for r in conn.execute("PRAGMA table_info(expense_beneficiaries)").fetchall()]
    if "weight" not in benef_cols:
        # Existing rows predate weighted shares - they were equal splits, so weight 1 for all is correct.
        conn.execute("ALTER TABLE expense_beneficiaries ADD COLUMN weight REAL NOT NULL DEFAULT 1")

    conn.commit()
    conn.close()


SECURITY_QUESTIONS = [
    "What city were you born in?",
    "What was the name of your first pet?",
    "What's your mother's maiden name?",
    "What was the name of your first school?",
]


# ---- Auth helpers ----
def hash_password(password: str, salt: bytes = None):
    if salt is None:
        salt = secrets.token_bytes(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return salt.hex(), pwd_hash.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    _, computed = hash_password(password, salt)
    return computed == hash_hex


def create_manager(username: str, password: str, security_question: str, security_answer: str):
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM managers WHERE LOWER(username) = LOWER(?)", (username,)
    ).fetchone()
    if existing:
        conn.close()
        return None, "This username is already taken."
    salt, pwd_hash = hash_password(password)
    ans_salt, ans_hash = hash_password(normalized(security_answer))
    manager_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO managers "
        "(id, username, salt, password_hash, security_question, security_answer_salt, security_answer_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (manager_id, username, salt, pwd_hash, security_question, ans_salt, ans_hash),
    )
    conn.commit()
    conn.close()
    return manager_id, None


def get_manager_by_username(username: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM managers WHERE LOWER(username) = LOWER(?)", (username,)
    ).fetchone()
    conn.close()
    return row


def reset_password(username: str, security_answer: str, new_password: str):
    row = get_manager_by_username(username)
    if row is None:
        return False, "No account with this username."
    if not row["security_answer_hash"]:
        return False, "This account was created before password recovery existed, so it has no security answer on file."
    if not verify_password(normalized(security_answer), row["security_answer_salt"], row["security_answer_hash"]):
        return False, "That answer doesn't match what's on file."
    salt, pwd_hash = hash_password(new_password)
    conn = get_conn()
    conn.execute("UPDATE managers SET salt = ?, password_hash = ? WHERE id = ?", (salt, pwd_hash, row["id"]))
    conn.commit()
    conn.close()
    return True, None


def authenticate(username: str, password: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM managers WHERE LOWER(username) = LOWER(?)", (username,)
    ).fetchone()
    conn.close()
    if row is None or not verify_password(password, row["salt"], row["password_hash"]):
        return None
    return row["id"]


# ---- Group helpers ----
def create_group(manager_id: str, name: str) -> str:
    conn = get_conn()
    group_id = uuid.uuid4().hex
    share_token = secrets.token_urlsafe(10)
    conn.execute(
        "INSERT INTO groups (id, manager_id, name, share_token, closed) VALUES (?, ?, ?, ?, 0)",
        (group_id, manager_id, name, share_token),
    )
    conn.commit()
    conn.close()
    return group_id


def group_name_taken(manager_id: str, name: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM groups WHERE manager_id = ? AND LOWER(name) = LOWER(?)", (manager_id, name)
    ).fetchone()
    conn.close()
    return row is not None


def get_groups_for_manager(manager_id: str):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM groups WHERE manager_id = ? ORDER BY name", (manager_id,)).fetchall()
    conn.close()
    return rows


def get_group_by_id(group_id: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    conn.close()
    return row


def get_group_by_share_token(token: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM groups WHERE share_token = ?", (token,)).fetchone()
    conn.close()
    return row


def delete_group(group_id: str):
    conn = get_conn()
    conn.execute(
        "DELETE FROM expense_beneficiaries WHERE expense_id IN "
        "(SELECT id FROM expenses WHERE group_id = ?)", (group_id,)
    )
    conn.execute("DELETE FROM expenses WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM repayments WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM people WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    conn.commit()
    conn.close()


def close_group(group_id: str):
    conn = get_conn()
    conn.execute("UPDATE groups SET closed = 1 WHERE id = ?", (group_id,))
    conn.commit()
    conn.close()


# ---- People helpers ----
def get_people(group_id: str):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM people WHERE group_id = ? ORDER BY name", (group_id,)).fetchall()
    conn.close()
    return rows


def add_person(group_id: str, name: str):
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM people WHERE group_id = ? AND LOWER(name) = LOWER(?)", (group_id, name)
    ).fetchone()
    if existing:
        conn.close()
        return None, f"'{name}' is already in the group."
    person_id = uuid.uuid4().hex
    conn.execute("INSERT INTO people (id, group_id, name) VALUES (?, ?, ?)", (person_id, group_id, name))
    conn.commit()
    conn.close()
    return person_id, None


def person_in_expenses(person_id: str) -> bool:
    conn = get_conn()
    as_payer = conn.execute("SELECT COUNT(*) c FROM expenses WHERE payer_id = ?", (person_id,)).fetchone()["c"]
    as_benef = conn.execute(
        "SELECT COUNT(*) c FROM expense_beneficiaries WHERE person_id = ?", (person_id,)
    ).fetchone()["c"]
    conn.close()
    return (as_payer + as_benef) > 0


def remove_person(person_id: str):
    # Design decision (Tier 2, "TO DECIDE"): block removal rather than silently
    # reassigning or archiving, so nobody's balance shifts without a deliberate edit.
    if person_in_expenses(person_id):
        return False, "This person appears in at least one expense and can't be removed. Edit or delete those expenses first."
    conn = get_conn()
    conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
    conn.commit()
    conn.close()
    return True, None


# ---- Expense helpers ----
def get_expenses(group_id: str):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM expenses WHERE group_id = ? ORDER BY rowid", (group_id,)).fetchall()
    expenses = []
    for r in rows:
        benef_rows = conn.execute(
            "SELECT person_id, weight FROM expense_beneficiaries WHERE expense_id = ?", (r["id"],)
        ).fetchall()
        expenses.append({
            "id": r["id"],
            "payer_id": r["payer_id"],
            "amount": r["amount"],
            "label": r["label"] or "",
            # beneficiaries: {person_id: weight} - weight 1 for a normal equal share
            "beneficiaries": {b["person_id"]: b["weight"] for b in benef_rows},
        })
    conn.close()
    return expenses


def add_expense(group_id: str, payer_id: str, amount: float, label: str, beneficiary_weights: dict):
    conn = get_conn()
    expense_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO expenses (id, group_id, payer_id, amount, label) VALUES (?, ?, ?, ?, ?)",
        (expense_id, group_id, payer_id, amount, label),
    )
    conn.executemany(
        "INSERT INTO expense_beneficiaries (expense_id, person_id, weight) VALUES (?, ?, ?)",
        [(expense_id, pid, w) for pid, w in beneficiary_weights.items()],
    )
    conn.commit()
    conn.close()


def update_expense(expense_id: str, payer_id: str, amount: float, label: str, beneficiary_weights: dict):
    conn = get_conn()
    conn.execute(
        "UPDATE expenses SET payer_id = ?, amount = ?, label = ? WHERE id = ?",
        (payer_id, amount, label, expense_id),
    )
    conn.execute("DELETE FROM expense_beneficiaries WHERE expense_id = ?", (expense_id,))
    conn.executemany(
        "INSERT INTO expense_beneficiaries (expense_id, person_id, weight) VALUES (?, ?, ?)",
        [(expense_id, pid, w) for pid, w in beneficiary_weights.items()],
    )
    conn.commit()
    conn.close()


def delete_expense(expense_id: str):
    conn = get_conn()
    conn.execute("DELETE FROM expense_beneficiaries WHERE expense_id = ?", (expense_id,))
    conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()


# ---- Repayment helpers ----
def get_repayments(group_id: str):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM repayments WHERE group_id = ? ORDER BY rowid", (group_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_repayment(group_id: str, from_person_id: str, to_person_id: str, amount: float):
    conn = get_conn()
    repayment_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO repayments (id, group_id, from_person_id, to_person_id, amount) VALUES (?, ?, ?, ?, ?)",
        (repayment_id, group_id, from_person_id, to_person_id, amount),
    )
    conn.commit()
    conn.close()


def delete_repayment(repayment_id: str):
    conn = get_conn()
    conn.execute("DELETE FROM repayments WHERE id = ?", (repayment_id,))
    conn.commit()
    conn.close()


# =========================================================
# 2) BUSINESS LOGIC
# =========================================================
def compute_shares(amount: float, beneficiary_weights: dict) -> dict:
    """
    Splits `amount` among beneficiaries proportionally to their weight
    (weight 1 each = equal split; a child at half-weight gets half a
    full adult share, etc). Works in cents with the "largest remainder"
    method so the shares always sum exactly to the amount, regardless
    of how uneven the weights are.
    """
    cents_total = round(amount * 100)
    total_weight = sum(beneficiary_weights.values())
    if total_weight <= 0:
        # Guard against a corrupt/zero-weight expense; treat as equal split.
        beneficiary_weights = {pid: 1 for pid in beneficiary_weights}
        total_weight = len(beneficiary_weights)

    raw = {pid: cents_total * w / total_weight for pid, w in beneficiary_weights.items()}
    base = {pid: int(v) for pid, v in raw.items()}  # floor
    remainder = cents_total - sum(base.values())

    # Largest fractional remainder gets the leftover cents, one each.
    order = sorted(beneficiary_weights.keys(), key=lambda pid: (raw[pid] - base[pid]), reverse=True)
    for i in range(remainder):
        base[order[i % len(order)]] += 1

    return {pid: base[pid] / 100 for pid in beneficiary_weights}


def compute_totals(people_rows, expenses: list, repayments: list):
    """
    Returns (paid, share, expense_balance, outstanding_balance, total_spent).
    - paid / share / expense_balance come purely from logged expenses.
    - outstanding_balance additionally accounts for recorded repayments:
      a person who has sent money moves toward zero, a person who has
      received money moves toward zero too. The group's balances always
      sum to zero either way.
    """
    ids = [p["id"] for p in people_rows]
    paid = {pid: 0.0 for pid in ids}
    share = {pid: 0.0 for pid in ids}
    for exp in expenses:
        paid[exp["payer_id"]] = paid.get(exp["payer_id"], 0.0) + exp["amount"]
        for pid, amt in compute_shares(exp["amount"], exp["beneficiaries"]).items():
            share[pid] = share.get(pid, 0.0) + amt

    expense_balance = {pid: round(paid[pid] - share[pid], 2) for pid in ids}

    outstanding = dict(expense_balance)
    for r in repayments:
        outstanding[r["from_person_id"]] = outstanding.get(r["from_person_id"], 0.0) + r["amount"]
        outstanding[r["to_person_id"]] = outstanding.get(r["to_person_id"], 0.0) - r["amount"]
    outstanding = {pid: round(v, 2) for pid, v in outstanding.items()}

    total_spent = sum(paid.values())
    return paid, share, expense_balance, outstanding, total_spent


def simplify_debts(balance: dict, name_by_id: dict):
    """
    Greedy algorithm: turns individual balances into a minimal list of
    "who pays whom" transactions to settle everything. Never routes
    money through an uninvolved third party, never repeats the same
    pair twice, and uses at most (n-1) transactions for n people.
    """
    creditors = [[pid, b] for pid, b in balance.items() if b > 0.005]
    debtors = [[pid, -b] for pid, b in balance.items() if b < -0.005]
    creditors.sort(key=lambda x: -x[1])
    debtors.sort(key=lambda x: -x[1])

    transactions = []
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        debtor_id, damt = debtors[i]
        creditor_id, camt = creditors[j]
        amt = round(min(damt, camt), 2)
        transactions.append({
            "from_id": debtor_id, "to_id": creditor_id, "amount": amt,
            "from_name": name_by_id[debtor_id], "to_name": name_by_id[creditor_id],
        })
        debtors[i][1] -= amt
        creditors[j][1] -= amt
        if debtors[i][1] < 0.01:
            i += 1
        if creditors[j][1] < 0.01:
            j += 1
    return transactions


def normalized(name: str) -> str:
    return name.strip().lower()


# =========================================================
# 3) NATURAL LANGUAGE EXPENSE PARSER
# =========================================================
AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*(?:€|eur|euros?)?|€\s*(\d+(?:[.,]\d{1,2})?)", re.IGNORECASE)
VERB_RE = re.compile(r"\b(paid|spent)\b", re.IGNORECASE)
EXCEPT_RE = re.compile(r"\b(everyone|everybody|all)\b.*?\b(except|but)\b\s+(.+)", re.IGNORECASE)
EVERYONE_RE = re.compile(r"\b(everyone|everybody|all)\b", re.IGNORECASE)
FOR_RE = re.compile(r"\bfor\b\s+(.+)", re.IGNORECASE)


def _split_names(fragment: str):
    fragment = re.sub(r"\band\b", ",", fragment, flags=re.IGNORECASE)
    return [n.strip(" .") for n in fragment.split(",") if n.strip(" .")]


def _match_person(name_guess: str, name_by_norm: dict):
    key = normalized(name_guess)
    if key in name_by_norm:
        return name_by_norm[key]
    # allow a loose prefix match (e.g. "Lea" matching "Léa" typed without the accent)
    for norm_name, pid in name_by_norm.items():
        if norm_name.startswith(key) or key.startswith(norm_name):
            return pid
    return None


def parse_expense_sentence(sentence: str, people_rows):
    """
    Rule-based parser - deliberately conservative. Returns a dict:
      {"ok": True, "payer_id":, "payer_name":, "amount":, "label":, "beneficiary_ids": [...]}
    or
      {"ok": False, "error": "<specific, actionable message>"}
    Never invents a person who isn't already in the group.
    """
    text = sentence.strip()
    if not text:
        return {"ok": False, "error": "Type a sentence first."}

    name_by_norm = {normalized(p["name"]): p["id"] for p in people_rows}
    all_ids = [p["id"] for p in people_rows]

    verb_match = VERB_RE.search(text)
    if not verb_match:
        return {"ok": False, "error": "I couldn't tell who paid — try including the word 'paid', e.g. \"Karim paid €45 for everyone\"."}

    payer_fragment = text[:verb_match.start()].strip()
    rest = text[verb_match.end():].strip()

    if not payer_fragment:
        return {"ok": False, "error": "I couldn't find who paid — put their name at the start of the sentence."}

    payer_id = _match_person(payer_fragment, name_by_norm)
    if payer_id is None:
        return {
            "ok": False,
            "error": f"I don't see '{payer_fragment}' in this group. Add them first (People tab), or check the spelling."
        }

    amount_match = AMOUNT_RE.search(rest)
    if not amount_match:
        return {"ok": False, "error": "I couldn't find an amount — include something like '€45' or '45 euros'."}
    amount_str = (amount_match.group(1) or amount_match.group(2) or "").replace(",", ".")
    try:
        amount = float(amount_str)
    except ValueError:
        amount = None
    if not amount or amount <= 0:
        return {"ok": False, "error": "I couldn't find a valid amount — it needs to be a positive number."}

    # ---- Beneficiaries ----
    beneficiary_ids = None
    except_match = EXCEPT_RE.search(rest)
    if except_match:
        excluded_names = _split_names(except_match.group(3))
        excluded_ids = []
        for n in excluded_names:
            pid = _match_person(n, name_by_norm)
            if pid is None:
                return {"ok": False, "error": f"I don't see '{n}' in this group, so I can't exclude them. Check the spelling."}
            excluded_ids.append(pid)
        beneficiary_ids = [pid for pid in all_ids if pid not in excluded_ids]
    elif EVERYONE_RE.search(rest):
        beneficiary_ids = list(all_ids)
    else:
        for_match = FOR_RE.search(rest)
        if for_match:
            names = _split_names(for_match.group(1))
            found_ids = []
            for n in names:
                pid = _match_person(n, name_by_norm)
                if pid is None:
                    return {"ok": False, "error": f"I don't see '{n}' in this group. Add them first, or check the spelling."}
                found_ids.append(pid)
            if found_ids:
                beneficiary_ids = found_ids

    if not beneficiary_ids:
        return {
            "ok": False,
            "error": "I couldn't tell who this was for — add \"for everyone\", \"for everyone except X\", or list names, e.g. \"for Karim and Léa\"."
        }

    # ---- Label: whatever's between the amount and the beneficiary clause ----
    label_zone = rest[amount_match.end():]
    label_zone = EXCEPT_RE.sub("", label_zone)
    label_zone = FOR_RE.sub("", label_zone)
    label = re.sub(r"^(at|on|in|for|the)\b\s*", "", label_zone.strip(), flags=re.IGNORECASE)
    label = re.sub(r"\s*\b(for|at|on|in|the)\b\s*$", "", label, flags=re.IGNORECASE).strip(" .")

    payer_name = next(p["name"] for p in people_rows if p["id"] == payer_id)
    return {
        "ok": True,
        "payer_id": payer_id,
        "payer_name": payer_name,
        "amount": round(amount, 2),
        "label": label,
        "beneficiary_ids": beneficiary_ids,
    }


# =========================================================
# 4) NAVIGATION HELPERS
# =========================================================
def go(page, **kwargs):
    st.session_state.page = page
    for k, v in kwargs.items():
        st.session_state[k] = v
    st.rerun()


def ensure_session_defaults():
    defaults = {
        "manager_id": None,
        "manager_username": None,
        "page": "home",
        "current_group": None,
        "editing_expense_id": None,
        "new_group_people": [],
        "confirm_delete_group": False,
        "confirm_close_group": False,
        "reset_username": None,
        "reset_question": None,
        "prefill_expense": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# =========================================================
# 5) LOGIN / SIGN UP SCREEN
# =========================================================
def render_login():
    st.title("💸 Who owes what?")
    st.caption("A group's accounts, without spreadsheets or migraines.")

    tab_login, tab_signup, tab_forgot = st.tabs(["Log in", "Sign up", "Forgot password?"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", type="primary", use_container_width=True)
            if submitted:
                manager_id = authenticate(username.strip(), password)
                if manager_id:
                    go("home", manager_id=manager_id, manager_username=username.strip())
                else:
                    st.error("Incorrect username or password.")

    with tab_signup:
        with st.form("signup_form"):
            new_username = st.text_input("Choose a username")
            new_password = st.text_input("Choose a password", type="password")
            security_question = st.selectbox("Security question (used to recover your account)", SECURITY_QUESTIONS)
            security_answer = st.text_input("Your answer")
            submitted = st.form_submit_button("Create account", type="primary", use_container_width=True)
            if submitted:
                if not new_username.strip() or not new_password or not security_answer.strip():
                    st.warning("Fill in all fields, including the security answer.")
                elif len(new_password) < 4:
                    st.warning("Password must be at least 4 characters.")
                else:
                    manager_id, error = create_manager(
                        new_username.strip(), new_password, security_question, security_answer.strip()
                    )
                    if error:
                        st.error(error)
                    else:
                        go("home", manager_id=manager_id, manager_username=new_username.strip())

    with tab_forgot:
        if st.session_state.reset_username is None:
            with st.form("forgot_lookup_form"):
                lookup_username = st.text_input("Your username")
                submitted = st.form_submit_button("Continue", use_container_width=True)
                if submitted:
                    row = get_manager_by_username(lookup_username.strip())
                    if row is None:
                        st.error("No account with this username.")
                    elif not row["security_question"]:
                        st.error("This account was created before password recovery existed, so it has no security question on file.")
                    else:
                        st.session_state.reset_username = lookup_username.strip()
                        st.session_state.reset_question = row["security_question"]
                        st.rerun()
        else:
            st.caption(f"Account: **{st.session_state.reset_username}**")
            with st.form("forgot_reset_form"):
                st.markdown(f"**{st.session_state.reset_question}**")
                answer = st.text_input("Your answer")
                new_password = st.text_input("New password", type="password")
                confirm_password = st.text_input("Confirm new password", type="password")
                submitted = st.form_submit_button("Reset password", type="primary", use_container_width=True)
                if submitted:
                    if not answer.strip() or not new_password:
                        st.warning("Fill in all fields.")
                    elif len(new_password) < 4:
                        st.warning("Password must be at least 4 characters.")
                    elif new_password != confirm_password:
                        st.warning("Passwords don't match.")
                    else:
                        ok, error = reset_password(st.session_state.reset_username, answer.strip(), new_password)
                        if ok:
                            st.success("Password reset. You can log in with your new password now.")
                            st.session_state.reset_username = None
                            st.session_state.reset_question = None
                        else:
                            st.error(error)
            if st.button("Start over"):
                st.session_state.reset_username = None
                st.session_state.reset_question = None
                st.rerun()


# =========================================================
# 6) SCREEN 1 - HOME / GROUP LIST (manager only)
# =========================================================
def render_home():
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("💸 Who owes what?")
        st.caption(f"Logged in as **{st.session_state.manager_username}**")
    with col2:
        if st.button("Log out"):
            go("home", manager_id=None, manager_username=None, page="home")

    groups = get_groups_for_manager(st.session_state.manager_id)

    if not groups:
        st.info("No groups yet. Create your first group to get started.")
    else:
        st.subheader("Your groups")
        for g in groups:
            people_count = len(get_people(g["id"]))
            expense_count = len(get_expenses(g["id"]))
            status = " · 🔒 closed" if g["closed"] else ""
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"**{g['name']}**  \n{people_count} people · {expense_count} expenses{status}")
            with col2:
                if st.button("Open", key=f"open_{g['id']}"):
                    go("group_detail", current_group=g["id"])
            st.divider()

    if st.button("➕ New group", type="primary", use_container_width=True):
        go("create_group", new_group_people=[])


# =========================================================
# 7) SCREEN 2 - CREATE GROUP
# =========================================================
def render_create_group():
    st.title("➕ New group")

    if st.button("← Back"):
        go("home")

    group_name = st.text_input("Group name", placeholder="Etretat Weekend")

    st.subheader("Participants")
    st.caption("Add at least 2 people by their first name.")

    with st.form("add_person_form", clear_on_submit=True):
        new_person = st.text_input("First name", label_visibility="collapsed", placeholder="First name")
        submitted = st.form_submit_button("Add")
        if submitted:
            candidate = new_person.strip()
            if not candidate:
                st.warning("Enter a first name.")
            elif normalized(candidate) in [normalized(p) for p in st.session_state.new_group_people]:
                st.error(f"'{candidate}' is already in the group. Distinguish first names (e.g. Karim B.).")
            else:
                st.session_state.new_group_people.append(candidate)
                st.rerun()

    if st.session_state.new_group_people:
        for i, person in enumerate(st.session_state.new_group_people):
            col1, col2 = st.columns([4, 1])
            col1.write(f"• {person}")
            if col2.button("✕", key=f"remove_person_{i}"):
                st.session_state.new_group_people.pop(i)
                st.rerun()

    st.divider()

    can_create = bool(group_name.strip()) and len(st.session_state.new_group_people) >= 2
    if group_name.strip() and group_name_taken(st.session_state.manager_id, group_name.strip()):
        st.error("You already have a group with this name.")
        can_create = False
    elif len(st.session_state.new_group_people) < 2:
        st.caption("⚠️ A group must contain at least two people.")

    if st.button("Create group", type="primary", disabled=not can_create, use_container_width=True):
        group_id = create_group(st.session_state.manager_id, group_name.strip())
        for name in st.session_state.new_group_people:
            add_person(group_id, name)
        go("group_detail", current_group=group_id, new_group_people=[])


# =========================================================
# 8) SCREEN 3 - GROUP DETAIL
# =========================================================
def render_group_detail():
    group = get_group_by_id(st.session_state.current_group)

    if group is None or group["manager_id"] != st.session_state.manager_id:
        st.error("Group not found.")
        if st.button("← Back to home"):
            go("home")
        return

    is_closed = bool(group["closed"])
    people = get_people(group["id"])
    expenses = get_expenses(group["id"])
    repayments = get_repayments(group["id"])
    name_by_id = {p["id"]: p["name"] for p in people}

    col1, col2 = st.columns([4, 1])
    with col1:
        st.title(group["name"] + (" 🔒" if is_closed else ""))
        st.caption(", ".join(p["name"] for p in people) if people else "No one in this group yet.")
    with col2:
        if st.button("← Groups"):
            go("home")

    if is_closed:
        st.info("This group is closed. Everyone is settled up, and nothing can be changed anymore — it's still fully viewable, including via the share link.")

    with st.expander("🔗 Share this group (view-only, no account needed)"):
        share_path = f"?share={group['share_token']}"
        st.code(share_path, language=None)
        st.caption(
            "Append this to your app's URL and send it to the group. They'll see live "
            "expenses, balances and the settlement plan, with no login and no edit access."
        )

    paid, share, expense_balance, outstanding, total_spent = compute_totals(people, expenses, repayments)

    tab_expenses, tab_totals, tab_settle, tab_people = st.tabs(
        ["📋 Expenses", "🧮 Totals", "🤝 Settle up", "👥 People"]
    )

    # ---- Expenses tab ----
    with tab_expenses:
        if not is_closed and len(people) >= 2:
            with st.expander("✨ Quick add via sentence"):
                st.caption('e.g. "Karim paid €45 at the restaurant for everyone except Léa"')
                nl_text = st.text_input("Describe the expense", key="nl_input", label_visibility="collapsed")
                if st.button("Parse"):
                    result = parse_expense_sentence(nl_text, people)
                    if result["ok"]:
                        st.session_state.prefill_expense = result
                        go("add_expense", editing_expense_id=None)
                    else:
                        st.error(result["error"])

        if len(people) < 2:
            st.info("Add at least two people (People tab) before logging an expense.")
        elif not expenses:
            st.info("No expenses recorded yet.")
        else:
            for exp in expenses:
                label = exp["label"] or "(no label)"
                payer_name = name_by_id.get(exp["payer_id"], "?")
                weights = exp["beneficiaries"]
                uneven = len(set(weights.values())) > 1
                if set(weights.keys()) == set(p["id"] for p in people) and not uneven:
                    benef_desc = "everyone, equally"
                else:
                    parts = [f"{name_by_id.get(pid, '?')}" + (f" (×{w:g})" if uneven else "")
                             for pid, w in weights.items()]
                    benef_desc = ", ".join(parts)
                with st.container(border=True):
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        st.markdown(f"**{label}** — {exp['amount']:.2f} €")
                        st.caption(f"Paid by {payer_name} · for {benef_desc}")
                    with c2:
                        if not is_closed:
                            if st.button("Edit", key=f"edit_{exp['id']}"):
                                go("add_expense", editing_expense_id=exp["id"], prefill_expense=None)
                            if st.button("Delete", key=f"delete_{exp['id']}"):
                                delete_expense(exp["id"])
                                st.rerun()

        if not is_closed:
            st.write("")
            if st.button("➕ Add an expense", type="primary", use_container_width=True, disabled=len(people) < 2):
                go("add_expense", editing_expense_id=None, prefill_expense=None)

    # ---- Totals tab ----
    with tab_totals:
        st.metric("Total spent by the group", f"{total_spent:.2f} €")

        if not people:
            st.info("No one in this group yet.")
        else:
            st.subheader("By person")
            for p in people:
                pid = p["id"]
                b = outstanding[pid]
                if b > 0.005:
                    status = f"🟢 Is owed {b:.2f} €"
                elif b < -0.005:
                    status = f"🔴 Owes {abs(b):.2f} €"
                else:
                    status = "⚪ Settled up"
                note = ""
                if round(expense_balance[pid], 2) != b:
                    note = f"  \n<sub>from expenses: {expense_balance[pid]:.2f} € · adjusted for repayments below</sub>"
                st.markdown(
                    f"**{p['name']}** — paid {paid[pid]:.2f} € · share is {share[pid]:.2f} €  \n{status}{note}",
                    unsafe_allow_html=True,
                )

    # ---- Settle up tab ----
    with tab_settle:
        st.subheader("Suggested repayment plan")
        st.caption("As few transfers as possible. Recorded repayments below are already subtracted.")
        transactions = simplify_debts(outstanding, name_by_id)
        if not transactions:
            st.success("Everyone is settled up ✅")
        else:
            for t in transactions:
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.markdown(f"- **{t['from_name']}** gives **{t['to_name']}** {t['amount']:.2f} €")
                with c2:
                    if not is_closed:
                        if st.button("Record", key=f"record_{t['from_id']}_{t['to_id']}"):
                            add_repayment(group["id"], t["from_id"], t["to_id"], t["amount"])
                            st.rerun()

        if not is_closed:
            st.divider()
            st.subheader("Record a repayment manually")
            with st.form("manual_repayment_form", clear_on_submit=True):
                names = [p["name"] for p in people]
                id_by_name = {p["name"]: p["id"] for p in people}
                r_from = st.selectbox("Who's paying?", names, key="repay_from")
                r_to = st.selectbox("Who's receiving?", names, key="repay_to")
                r_amount = st.number_input("Amount (€)", min_value=0.01, step=0.5, format="%.2f")
                submitted = st.form_submit_button("Record repayment", type="primary")
                if submitted:
                    if r_from == r_to:
                        st.warning("Pick two different people.")
                    else:
                        add_repayment(group["id"], id_by_name[r_from], id_by_name[r_to], round(r_amount, 2))
                        st.rerun()

        if repayments:
            st.divider()
            st.subheader("Repayment history")
            for r in repayments:
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.markdown(
                        f"- {name_by_id.get(r['from_person_id'], '?')} → "
                        f"{name_by_id.get(r['to_person_id'], '?')}: {r['amount']:.2f} €"
                    )
                with c2:
                    if not is_closed:
                        if st.button("Undo", key=f"undo_{r['id']}"):
                            delete_repayment(r["id"])
                            st.rerun()

        st.divider()
        all_settled = all(abs(v) < 0.005 for v in outstanding.values()) if people else True
        if is_closed:
            st.caption("🔒 This group is closed.")
        elif all_settled:
            with st.expander("✅ Close this group"):
                st.caption("Everyone is settled up. Closing keeps everything viewable but locks out further changes.")
                if not st.session_state.confirm_close_group:
                    if st.button("Close group…"):
                        st.session_state.confirm_close_group = True
                        st.rerun()
                else:
                    st.warning("Close this group? You won't be able to add or edit anything afterward.")
                    c1, c2 = st.columns(2)
                    if c1.button("Yes, close it", type="primary"):
                        close_group(group["id"])
                        go("group_detail", confirm_close_group=False)
                    if c2.button("Cancel"):
                        st.session_state.confirm_close_group = False
                        st.rerun()
        else:
            st.caption("Balances must all be zero before this group can be closed.")

    # ---- People tab ----
    with tab_people:
        if is_closed:
            for p in people:
                st.write(f"• {p['name']}")
        else:
            st.caption("You can add someone at any time, even if the group already has expenses.")
            with st.form("add_existing_group_person", clear_on_submit=True):
                new_name = st.text_input("First name", label_visibility="collapsed", placeholder="First name")
                submitted = st.form_submit_button("Add to group")
                if submitted:
                    candidate = new_name.strip()
                    if not candidate:
                        st.warning("Enter a first name.")
                    else:
                        _, error = add_person(group["id"], candidate)
                        if error:
                            st.error(error)
                        else:
                            st.rerun()

            st.write("")
            for p in people:
                col1, col2 = st.columns([4, 1])
                col1.write(f"• {p['name']}")
                if col2.button("Remove", key=f"remove_{p['id']}"):
                    ok, error = remove_person(p["id"])
                    if ok:
                        st.rerun()
                    else:
                        st.error(error)

    if not is_closed:
        st.divider()
        with st.expander("⚠️ Delete this group"):
            st.warning("This permanently deletes the group and all its expenses. This can't be undone.")
            if not st.session_state.confirm_delete_group:
                if st.button("Delete group…"):
                    st.session_state.confirm_delete_group = True
                    st.rerun()
            else:
                st.error(f"Are you sure you want to delete '{group['name']}' and all its expenses?")
                c1, c2 = st.columns(2)
                if c1.button("Yes, delete permanently", type="primary"):
                    delete_group(group["id"])
                    go("home", confirm_delete_group=False)
                if c2.button("Cancel"):
                    st.session_state.confirm_delete_group = False
                    st.rerun()


# =========================================================
# 9) SCREEN 4 - ADD / EDIT AN EXPENSE
# =========================================================
def render_add_expense():
    group = get_group_by_id(st.session_state.current_group)

    if group is None or group["manager_id"] != st.session_state.manager_id or group["closed"]:
        st.error("Group not found or closed.")
        if st.button("← Back to home"):
            go("home")
        return

    people = get_people(group["id"])
    name_by_id = {p["id"]: p["name"] for p in people}
    id_by_name = {p["name"]: p["id"] for p in people}
    names = [p["name"] for p in people]

    editing_id = st.session_state.editing_expense_id
    existing = None
    if editing_id:
        existing = next((e for e in get_expenses(group["id"]) if e["id"] == editing_id), None)

    prefill = st.session_state.prefill_expense

    st.title("✏️ Edit expense" if existing else "➕ New expense")
    if prefill:
        st.info("Parsed from your sentence — check it over, then save.")

    if existing:
        default_payer_index = names.index(name_by_id[existing["payer_id"]])
    elif prefill:
        default_payer_index = names.index(prefill["payer_name"])
    else:
        default_payer_index = 0
    payer_name = st.selectbox("Who paid?", names, index=default_payer_index)

    if existing:
        default_amount = existing["amount"]
    elif prefill:
        default_amount = prefill["amount"]
    else:
        default_amount = 0.01
    amount = st.number_input("Amount (€)", min_value=0.01, step=0.5, format="%.2f", value=default_amount)

    if existing:
        default_label = existing["label"]
    elif prefill:
        default_label = prefill["label"]
    else:
        default_label = ""
    label = st.text_input("Label (optional)", value=default_label, placeholder="Groceries, restaurant, gas...")

    if existing:
        default_beneficiary_names = [name_by_id[pid] for pid in existing["beneficiaries"]]
    elif prefill:
        default_beneficiary_names = [name_by_id[pid] for pid in prefill["beneficiary_ids"]]
    else:
        default_beneficiary_names = names
    beneficiary_names = st.multiselect("For whom?", names, default=default_beneficiary_names)

    existing_weights_by_name = {}
    if existing:
        existing_weights_by_name = {name_by_id[pid]: w for pid, w in existing["beneficiaries"].items()}
    uneven_default = len(set(existing_weights_by_name.values())) > 1 if existing_weights_by_name else False
    uneven = st.checkbox("Split unevenly (e.g. a child counts for half, a double room counts double)", value=uneven_default)

    weights_by_name = {}
    if beneficiary_names:
        if uneven:
            st.caption("Weight 1 = a normal full share. 0.5 = half a share. 2 = double.")
            for n in beneficiary_names:
                default_w = existing_weights_by_name.get(n, 1.0)
                weights_by_name[n] = st.number_input(
                    f"{n}'s weight", min_value=0.1, step=0.1, value=float(default_w), key=f"weight_{n}"
                )
        else:
            weights_by_name = {n: 1.0 for n in beneficiary_names}

    st.divider()

    error = None
    if not payer_name:
        error = "You must choose a payer."
    elif amount <= 0:
        error = "The amount must be strictly positive."
    elif not beneficiary_names:
        error = "You need at least one beneficiary."

    if error:
        st.warning(error)

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Cancel", use_container_width=True):
            go("group_detail", editing_expense_id=None, prefill_expense=None)
    with col2:
        if existing and st.button("Delete", use_container_width=True):
            delete_expense(existing["id"])
            go("group_detail", editing_expense_id=None, prefill_expense=None)
    with col3:
        if st.button("Save", type="primary", disabled=bool(error), use_container_width=True):
            payer_id = id_by_name[payer_name]
            beneficiary_weights = {id_by_name[n]: weights_by_name[n] for n in beneficiary_names}
            if existing:
                update_expense(existing["id"], payer_id, round(amount, 2), label.strip(), beneficiary_weights)
            else:
                add_expense(group["id"], payer_id, round(amount, 2), label.strip(), beneficiary_weights)
            go("group_detail", editing_expense_id=None, prefill_expense=None)


# =========================================================
# 10) PUBLIC READ-ONLY VIEW (share link, no login)
# =========================================================
def render_shared_view(token: str):
    group = get_group_by_share_token(token)

    if group is None:
        st.title("💸 Who owes what?")
        st.error("This link doesn't match any group. Ask the group manager for a fresh link.")
        return

    is_closed = bool(group["closed"])
    people = get_people(group["id"])
    expenses = get_expenses(group["id"])
    repayments = get_repayments(group["id"])
    name_by_id = {p["id"]: p["name"] for p in people}

    st.title(f"💸 {group['name']}" + (" 🔒" if is_closed else ""))
    st.caption(
        ("🔒 Closed group · " if is_closed else "👀 Read-only view · ")
        + (", ".join(p["name"] for p in people) or "no one yet")
    )

    paid, share, expense_balance, outstanding, total_spent = compute_totals(people, expenses, repayments)

    tab_expenses, tab_totals, tab_settle = st.tabs(["📋 Expenses", "🧮 Totals", "🤝 Settle up"])

    with tab_expenses:
        if not expenses:
            st.info("No expenses recorded yet.")
        else:
            for exp in expenses:
                label = exp["label"] or "(no label)"
                payer_name = name_by_id.get(exp["payer_id"], "?")
                weights = exp["beneficiaries"]
                uneven = len(set(weights.values())) > 1
                if set(weights.keys()) == set(p["id"] for p in people) and not uneven:
                    benef_desc = "everyone, equally"
                else:
                    parts = [f"{name_by_id.get(pid, '?')}" + (f" (×{w:g})" if uneven else "")
                             for pid, w in weights.items()]
                    benef_desc = ", ".join(parts)
                with st.container(border=True):
                    st.markdown(f"**{label}** — {exp['amount']:.2f} €")
                    st.caption(f"Paid by {payer_name} · for {benef_desc}")

    with tab_totals:
        st.metric("Total spent by the group", f"{total_spent:.2f} €")
        if people:
            st.subheader("By person")
            for p in people:
                pid = p["id"]
                b = outstanding[pid]
                if b > 0.005:
                    status = f"🟢 Is owed {b:.2f} €"
                elif b < -0.005:
                    status = f"🔴 Owes {abs(b):.2f} €"
                else:
                    status = "⚪ Settled up"
                st.markdown(
                    f"**{p['name']}** — paid {paid[pid]:.2f} € · share is {share[pid]:.2f} €  \n{status}"
                )

    with tab_settle:
        transactions = simplify_debts(outstanding, name_by_id)
        if not transactions:
            st.success("Everyone is settled up ✅")
        else:
            for t in transactions:
                st.markdown(f"- **{t['from_name']}** gives **{t['to_name']}** {t['amount']:.2f} €")
        if repayments:
            st.divider()
            st.subheader("Repayment history")
            for r in repayments:
                st.markdown(
                    f"- {name_by_id.get(r['from_person_id'], '?')} → "
                    f"{name_by_id.get(r['to_person_id'], '?')}: {r['amount']:.2f} €"
                )

    st.divider()
    if st.button("Manage your own groups"):
        st.query_params.clear()
        st.rerun()


# =========================================================
# 11) ROUTING
# =========================================================
init_db()
ensure_session_defaults()

_share_token = st.query_params.get("share")

if _share_token:
    render_shared_view(_share_token)
elif st.session_state.manager_id is None:
    render_login()
else:
    PAGES = {
        "home": render_home,
        "create_group": render_create_group,
        "group_detail": render_group_detail,
        "add_expense": render_add_expense,
    }
    PAGES[st.session_state.page]()