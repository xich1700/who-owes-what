"""
Who owes what? - Front-end prototype (Streamlit)
Tier 1 (Base): one person enters a group's expenses
and gets clear totals (who paid what, who owes how much).

Run with:  streamlit run app_tier1.py
"""

import uuid
import streamlit as st

st.set_page_config(page_title="Who owes what?", page_icon="💸", layout="centered")

# =========================================================
# 1) SESSION STATE (acts as a volatile "database")
# =========================================================
if "groups" not in st.session_state:
    st.session_state.groups = {}
if "page" not in st.session_state:
    st.session_state.page = "home"
if "current_group" not in st.session_state:
    st.session_state.current_group = None
if "editing_expense_id" not in st.session_state:
    st.session_state.editing_expense_id = None
if "new_group_people" not in st.session_state:
    st.session_state.new_group_people = []


def go(page, **kwargs):
    st.session_state.page = page
    for k, v in kwargs.items():
        st.session_state[k] = v
    st.rerun()


# =========================================================
# 2) BUSINESS LOGIC
# =========================================================
def normalized(name: str) -> str:
    return name.strip().lower()


def compute_shares(amount: float, beneficiaries: list) -> dict:
    cents_total = round(amount * 100)
    n = len(beneficiaries)
    base = cents_total // n
    remainder = cents_total % n
    shares = {}
    for i, person in enumerate(beneficiaries):
        cents = base + (1 if i < remainder else 0)
        shares[person] = cents / 100
    return shares


def compute_totals(group: dict):
    paid = {p: 0.0 for p in group["people"]}
    share = {p: 0.0 for p in group["people"]}
    for exp in group["expenses"]:
        paid[exp["payer"]] = paid.get(exp["payer"], 0.0) + exp["amount"]
        for person, amt in compute_shares(exp["amount"], exp["beneficiaries"]).items():
            share[person] = share.get(person, 0.0) + amt
    balance = {p: round(paid[p] - share[p], 2) for p in group["people"]}
    total_spent = sum(paid.values())
    return paid, share, balance, total_spent


def simplify_debts(balance: dict):
    creditors = [[p, b] for p, b in balance.items() if b > 0.005]
    debtors = [[p, -b] for p, b in balance.items() if b < -0.005]
    creditors.sort(key=lambda x: -x[1])
    debtors.sort(key=lambda x: -x[1])

    transactions = []
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        debtor, damt = debtors[i]
        creditor, camt = creditors[j]
        amt = round(min(damt, camt), 2)
        transactions.append((debtor, creditor, amt))
        debtors[i][1] -= amt
        creditors[j][1] -= amt
        if debtors[i][1] < 0.01:
            i += 1
        if creditors[j][1] < 0.01:
            j += 1
    return transactions


# =========================================================
# 3) SCREEN 1 - HOME / GROUP LIST
# =========================================================
def render_home():
    st.title("💸 Who owes what?")
    st.caption("A group's accounts, without spreadsheets or migraines.")

    if not st.session_state.groups:
        st.info("No groups yet. Create your first group to get started.")
    else:
        st.subheader("Your groups")
        for name in st.session_state.groups:
            people_count = len(st.session_state.groups[name]["people"])
            expense_count = len(st.session_state.groups[name]["expenses"])
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"**{name}**  \n{people_count} people · {expense_count} expenses")
            with col2:
                if st.button("Open", key=f"open_{name}"):
                    go("group_detail", current_group=name)
            st.divider()

    if st.button("➕ New group", type="primary", use_container_width=True):
        go("create_group", new_group_people=[])


# =========================================================
# 4) SCREEN 2 - CREATE GROUP
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
    if group_name.strip() in st.session_state.groups:
        st.error("A group with this name already exists.")
        can_create = False
    elif len(st.session_state.new_group_people) < 2:
        st.caption("⚠️ A group must contain at least two people.")

    if st.button("Create group", type="primary", disabled=not can_create, use_container_width=True):
        st.session_state.groups[group_name.strip()] = {
            "people": list(st.session_state.new_group_people),
            "expenses": [],
        }
        go("group_detail", current_group=group_name.strip(), new_group_people=[])


# =========================================================
# 5) SCREEN 3 - GROUP DETAIL (expenses / totals)
# =========================================================
def render_group_detail():
    group_name = st.session_state.current_group
    group = st.session_state.groups.get(group_name)

    if group is None:
        st.error("Group not found.")
        if st.button("← Back to home"):
            go("home")
        return

    col1, col2 = st.columns([4, 1])
    with col1:
        st.title(group_name)
        st.caption(", ".join(group["people"]))
    with col2:
        if st.button("← Groups"):
            go("home")

    tab_expenses, tab_totals = st.tabs(["📋 Expenses", "🧮 Totals"])

    with tab_expenses:
        if not group["expenses"]:
            st.info("No expenses recorded yet.")
        else:
            for exp in group["expenses"]:
                label = exp["label"] or "(no label)"
                benef = "everyone" if set(exp["beneficiaries"]) == set(group["people"]) \
                    else ", ".join(exp["beneficiaries"])
                with st.container(border=True):
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        st.markdown(f"**{label}** — {exp['amount']:.2f} €")
                        st.caption(f"Paid by {exp['payer']} · for {benef}")
                    with c2:
                        if st.button("Edit", key=f"edit_{exp['id']}"):
                            go("add_expense", editing_expense_id=exp["id"])
                        if st.button("Delete", key=f"delete_{exp['id']}"):
                            group["expenses"] = [e for e in group["expenses"] if e["id"] != exp["id"]]
                            st.rerun()

        st.write("")
        if st.button("➕ Add an expense", type="primary", use_container_width=True):
            go("add_expense", editing_expense_id=None)

    with tab_totals:
        paid, share, balance, total_spent = compute_totals(group)

        st.metric("Total spent by the group", f"{total_spent:.2f} €")

        st.subheader("By person")
        for person in group["people"]:
            b = balance[person]
            if b > 0.005:
                status = f"🟢 Is owed {b:.2f} €"
            elif b < -0.005:
                status = f"🔴 Owes {abs(b):.2f} €"
            else:
                status = "⚪ Settled up"
            st.markdown(
                f"**{person}** — paid {paid[person]:.2f} € · share is {share[person]:.2f} €  \n{status}"
            )

        st.subheader("Who owes what (settle with the fewest transfers)")
        transactions = simplify_debts(balance)
        if not transactions:
            st.success("Everyone is settled up ✅")
        else:
            for debtor, creditor, amt in transactions:
                st.markdown(f"- **{debtor}** owes **{amt:.2f} €** to **{creditor}**")


# =========================================================
# 6) SCREEN 4 - ADD / EDIT AN EXPENSE
# =========================================================
def render_add_expense():
    group_name = st.session_state.current_group
    group = st.session_state.groups.get(group_name)

    if group is None:
        st.error("Group not found.")
        if st.button("← Back to home"):
            go("home")
        return

    editing_id = st.session_state.editing_expense_id
    existing = None
    if editing_id:
        existing = next((e for e in group["expenses"] if e["id"] == editing_id), None)

    st.title("✏️ Edit expense" if existing else "➕ New expense")

    people = group["people"]

    default_payer_index = people.index(existing["payer"]) if existing else 0
    payer = st.selectbox("Who paid?", people, index=default_payer_index)

    amount = st.number_input(
        "Amount (€)",
        min_value=0.01,
        step=0.5,
        format="%.2f",
        value=existing["amount"] if existing else 0.01,
    )

    label = st.text_input(
        "Label (optional)",
        value=existing["label"] if existing else "",
        placeholder="Groceries, restaurant, gas...",
    )

    default_beneficiaries = existing["beneficiaries"] if existing else people
    beneficiaries = st.multiselect(
        "For whom? (split equally)",
        people,
        default=default_beneficiaries,
    )

    st.divider()

    error = None
    if not payer:
        error = "You must choose a payer."
    elif amount <= 0:
        error = "The amount must be strictly positive."
    elif not beneficiaries:
        error = "You need at least one beneficiary."

    if error:
        st.warning(error)

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Cancel", use_container_width=True):
            go("group_detail", editing_expense_id=None)
    with col2:
        if existing and st.button("Delete", use_container_width=True):
            group["expenses"] = [e for e in group["expenses"] if e["id"] != existing["id"]]
            go("group_detail", editing_expense_id=None)
    with col3:
        if st.button("Save", type="primary", disabled=bool(error), use_container_width=True):
            if existing:
                existing.update(
                    payer=payer, amount=round(amount, 2), label=label.strip(),
                    beneficiaries=beneficiaries,
                )
            else:
                group["expenses"].append({
                    "id": uuid.uuid4().hex,
                    "payer": payer,
                    "amount": round(amount, 2),
                    "label": label.strip(),
                    "beneficiaries": beneficiaries,
                })
            go("group_detail", editing_expense_id=None)


# =========================================================
# 7) ROUTING
# =========================================================
PAGES = {
    "home": render_home,
    "create_group": render_create_group,
    "group_detail": render_group_detail,
    "add_expense": render_add_expense,
}

PAGES[st.session_state.page]()

