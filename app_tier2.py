"""
Who owes what? - Front-end prototype (Streamlit)
Tier 2 (Advanced): accounts persist over time, the manager logs in,
group members can view via a share link without an account.

Run with:  streamlit run app.py
Requires:  pip install streamlit   (streamlit >= 1.30 for st.query_params)
"""

import os
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
    """)
    conn.commit()

    # Migration: managers created before "forgot password" existed won't have
    # these columns yet if their .db predates this feature.
    existing_cols = [row["name"] for row in conn.execute("PRAGMA table_info(managers)").fetchall()]
    if "security_question" not in existing_cols:
        conn.execute("ALTER TABLE managers ADD COLUMN security_question TEXT")
    if "security_answer_salt" not in existing_cols:
        conn.execute("ALTER TABLE managers ADD COLUMN security_answer_salt TEXT")
    if "security_answer_hash" not in existing_cols:
        conn.execute("ALTER TABLE managers ADD COLUMN security_answer_hash TEXT")
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
        "INSERT INTO groups (id, manager_id, name, share_token) VALUES (?, ?, ?, ?)",
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
    conn.execute("DELETE FROM people WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
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
    # reassigning or archiving. The manager must first fix the expenses that
    # reference this person, so nobody's balance changes without a deliberate edit.
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
            "SELECT person_id FROM expense_beneficiaries WHERE expense_id = ?", (r["id"],)
        ).fetchall()
        expenses.append({
            "id": r["id"],
            "payer_id": r["payer_id"],
            "amount": r["amount"],
            "label": r["label"] or "",
            "beneficiary_ids": [b["person_id"] for b in benef_rows],
        })
    conn.close()
    return expenses


def add_expense(group_id: str, payer_id: str, amount: float, label: str, beneficiary_ids: list):
    conn = get_conn()
    expense_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO expenses (id, group_id, payer_id, amount, label) VALUES (?, ?, ?, ?, ?)",
        (expense_id, group_id, payer_id, amount, label),
    )
    conn.executemany(
        "INSERT INTO expense_beneficiaries (expense_id, person_id) VALUES (?, ?)",
        [(expense_id, pid) for pid in beneficiary_ids],
    )
    conn.commit()
    conn.close()


def update_expense(expense_id: str, payer_id: str, amount: float, label: str, beneficiary_ids: list):
    conn = get_conn()
    conn.execute(
        "UPDATE expenses SET payer_id = ?, amount = ?, label = ? WHERE id = ?",
        (payer_id, amount, label, expense_id),
    )
    conn.execute("DELETE FROM expense_beneficiaries WHERE expense_id = ?", (expense_id,))
    conn.executemany(
        "INSERT INTO expense_beneficiaries (expense_id, person_id) VALUES (?, ?)",
        [(expense_id, pid) for pid in beneficiary_ids],
    )
    conn.commit()
    conn.close()


def delete_expense(expense_id: str):
    conn = get_conn()
    conn.execute("DELETE FROM expense_beneficiaries WHERE expense_id = ?", (expense_id,))
    conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()


# =========================================================
# 2) BUSINESS LOGIC (operates on person ids, display maps to names)
# =========================================================
def compute_shares(amount: float, beneficiary_ids: list) -> dict:
    cents_total = round(amount * 100)
    n = len(beneficiary_ids)
    base = cents_total // n
    remainder = cents_total % n
    shares = {}
    for i, pid in enumerate(beneficiary_ids):
        cents = base + (1 if i < remainder else 0)
        shares[pid] = cents / 100
    return shares


def compute_totals(people_rows, expenses: list):
    ids = [p["id"] for p in people_rows]
    paid = {pid: 0.0 for pid in ids}
    share = {pid: 0.0 for pid in ids}
    for exp in expenses:
        paid[exp["payer_id"]] = paid.get(exp["payer_id"], 0.0) + exp["amount"]
        for pid, amt in compute_shares(exp["amount"], exp["beneficiary_ids"]).items():
            share[pid] = share.get(pid, 0.0) + amt
    balance = {pid: round(paid[pid] - share[pid], 2) for pid in ids}
    total_spent = sum(paid.values())
    return paid, share, balance, total_spent


def simplify_debts(balance: dict, name_by_id: dict):
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
        transactions.append((name_by_id[debtor_id], name_by_id[creditor_id], amt))
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
# 3) NAVIGATION HELPERS
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
        "reset_username": None,
        "reset_question": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# =========================================================
# 4) LOGIN / SIGN UP SCREEN
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
            # Step 1: look up the account and surface its security question.
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
            # Step 2: answer the question and set a new password.
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
# 5) SCREEN 1 - HOME / GROUP LIST (manager only)
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
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"**{g['name']}**  \n{people_count} people · {expense_count} expenses")
            with col2:
                if st.button("Open", key=f"open_{g['id']}"):
                    go("group_detail", current_group=g["id"])
            st.divider()

    if st.button("➕ New group", type="primary", use_container_width=True):
        go("create_group", new_group_people=[])


# =========================================================
# 6) SCREEN 2 - CREATE GROUP
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
# 7) SCREEN 3 - GROUP DETAIL (manager: expenses / totals / people)
# =========================================================
def render_group_detail():
    group = get_group_by_id(st.session_state.current_group)

    if group is None or group["manager_id"] != st.session_state.manager_id:
        st.error("Group not found.")
        if st.button("← Back to home"):
            go("home")
        return

    people = get_people(group["id"])
    expenses = get_expenses(group["id"])
    name_by_id = {p["id"]: p["name"] for p in people}

    col1, col2 = st.columns([4, 1])
    with col1:
        st.title(group["name"])
        st.caption(", ".join(p["name"] for p in people) if people else "No one in this group yet.")
    with col2:
        if st.button("← Groups"):
            go("home")

    with st.expander("🔗 Share this group (view-only, no account needed)"):
        share_path = f"?share={group['share_token']}"
        st.code(share_path, language=None)
        st.caption(
            "Append this to your app's URL (e.g. the address shown in your browser "
            "before the '?') and send it to the group. They'll see live expenses and "
            "balances, with no login and no edit access."
        )

    tab_expenses, tab_totals, tab_people = st.tabs(["📋 Expenses", "🧮 Totals", "👥 People"])

    # ---- Expenses tab ----
    with tab_expenses:
        if len(people) < 2:
            st.info("Add at least two people (People tab) before logging an expense.")
        elif not expenses:
            st.info("No expenses recorded yet.")
        else:
            for exp in expenses:
                label = exp["label"] or "(no label)"
                payer_name = name_by_id.get(exp["payer_id"], "?")
                benef_ids = set(exp["beneficiary_ids"])
                benef = "everyone" if benef_ids == set(p["id"] for p in people) \
                    else ", ".join(name_by_id.get(pid, "?") for pid in exp["beneficiary_ids"])
                with st.container(border=True):
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        st.markdown(f"**{label}** — {exp['amount']:.2f} €")
                        st.caption(f"Paid by {payer_name} · for {benef}")
                    with c2:
                        if st.button("Edit", key=f"edit_{exp['id']}"):
                            go("add_expense", editing_expense_id=exp["id"])
                        if st.button("Delete", key=f"delete_{exp['id']}"):
                            delete_expense(exp["id"])
                            st.rerun()

        st.write("")
        if st.button("➕ Add an expense", type="primary", use_container_width=True, disabled=len(people) < 2):
            go("add_expense", editing_expense_id=None)

    # ---- Totals tab ----
    with tab_totals:
        paid, share, balance, total_spent = compute_totals(people, expenses)

        st.metric("Total spent by the group", f"{total_spent:.2f} €")

        if not people:
            st.info("No one in this group yet.")
        else:
            st.subheader("By person")
            for p in people:
                pid = p["id"]
                b = balance[pid]
                if b > 0.005:
                    status = f"🟢 Is owed {b:.2f} €"
                elif b < -0.005:
                    status = f"🔴 Owes {abs(b):.2f} €"
                else:
                    status = "⚪ Settled up"
                st.markdown(
                    f"**{p['name']}** — paid {paid[pid]:.2f} € · share is {share[pid]:.2f} €  \n{status}"
                )

            st.subheader("Who owes what (settle with the fewest transfers)")
            transactions = simplify_debts(balance, name_by_id)
            if not transactions:
                st.success("Everyone is settled up ✅")
            else:
                for debtor, creditor, amt in transactions:
                    st.markdown(f"- **{debtor}** owes **{amt:.2f} €** to **{creditor}**")

    # ---- People tab ----
    with tab_people:
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
# 8) SCREEN 4 - ADD / EDIT AN EXPENSE (manager only)
# =========================================================
def render_add_expense():
    group = get_group_by_id(st.session_state.current_group)

    if group is None or group["manager_id"] != st.session_state.manager_id:
        st.error("Group not found.")
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

    st.title("✏️ Edit expense" if existing else "➕ New expense")

    default_payer_index = names.index(name_by_id[existing["payer_id"]]) if existing else 0
    payer_name = st.selectbox("Who paid?", names, index=default_payer_index)

    amount = st.number_input(
        "Amount (€)", min_value=0.01, step=0.5, format="%.2f",
        value=existing["amount"] if existing else 0.01,
    )

    label = st.text_input(
        "Label (optional)", value=existing["label"] if existing else "",
        placeholder="Groceries, restaurant, gas...",
    )

    default_beneficiary_names = (
        [name_by_id[pid] for pid in existing["beneficiary_ids"]] if existing else names
    )
    beneficiary_names = st.multiselect("For whom? (split equally)", names, default=default_beneficiary_names)

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
            go("group_detail", editing_expense_id=None)
    with col2:
        if existing and st.button("Delete", use_container_width=True):
            delete_expense(existing["id"])
            go("group_detail", editing_expense_id=None)
    with col3:
        if st.button("Save", type="primary", disabled=bool(error), use_container_width=True):
            payer_id = id_by_name[payer_name]
            beneficiary_ids = [id_by_name[n] for n in beneficiary_names]
            if existing:
                update_expense(existing["id"], payer_id, round(amount, 2), label.strip(), beneficiary_ids)
            else:
                add_expense(group["id"], payer_id, round(amount, 2), label.strip(), beneficiary_ids)
            go("group_detail", editing_expense_id=None)


# =========================================================
# 9) PUBLIC READ-ONLY VIEW (share link, no login)
# =========================================================
def render_shared_view(token: str):
    group = get_group_by_share_token(token)

    if group is None:
        st.title("💸 Who owes what?")
        st.error("This link doesn't match any group. Ask the group manager for a fresh link.")
        return

    people = get_people(group["id"])
    expenses = get_expenses(group["id"])
    name_by_id = {p["id"]: p["name"] for p in people}

    st.title(f"💸 {group['name']}")
    st.caption("👀 Read-only view, shared by the group manager · " + (", ".join(p["name"] for p in people) or "no one yet"))

    tab_expenses, tab_totals = st.tabs(["📋 Expenses", "🧮 Totals"])

    with tab_expenses:
        if not expenses:
            st.info("No expenses recorded yet.")
        else:
            for exp in expenses:
                label = exp["label"] or "(no label)"
                payer_name = name_by_id.get(exp["payer_id"], "?")
                benef = "everyone" if set(exp["beneficiary_ids"]) == set(p["id"] for p in people) \
                    else ", ".join(name_by_id.get(pid, "?") for pid in exp["beneficiary_ids"])
                with st.container(border=True):
                    st.markdown(f"**{label}** — {exp['amount']:.2f} €")
                    st.caption(f"Paid by {payer_name} · for {benef}")

    with tab_totals:
        paid, share, balance, total_spent = compute_totals(people, expenses)
        st.metric("Total spent by the group", f"{total_spent:.2f} €")

        if people:
            st.subheader("By person")
            for p in people:
                pid = p["id"]
                b = balance[pid]
                if b > 0.005:
                    status = f"🟢 Is owed {b:.2f} €"
                elif b < -0.005:
                    status = f"🔴 Owes {abs(b):.2f} €"
                else:
                    status = "⚪ Settled up"
                st.markdown(
                    f"**{p['name']}** — paid {paid[pid]:.2f} € · share is {share[pid]:.2f} €  \n{status}"
                )

            st.subheader("Who owes what")
            transactions = simplify_debts(balance, name_by_id)
            if not transactions:
                st.success("Everyone is settled up ✅")
            else:
                for debtor, creditor, amt in transactions:
                    st.markdown(f"- **{debtor}** owes **{amt:.2f} €** to **{creditor}**")

    st.divider()
    if st.button("Manage your own groups"):
        st.query_params.clear()
        st.rerun()


# =========================================================
# 10) ROUTING
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