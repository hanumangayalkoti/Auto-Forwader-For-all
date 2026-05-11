from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


# ---- LOGIN ----

def kb_login():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔐 Login Karo", callback_data="do_login"))
    return kb


# ---- MAIN MENU ----

def kb_main():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ New Group", callback_data="ng"),
        InlineKeyboardButton("⚙️ Manage Groups", callback_data="grp_list"),
    )
    kb.add(
        InlineKeyboardButton("✏️ Rename Group", callback_data="rename_prompt"),
        InlineKeyboardButton("📊 Status", callback_data="st"),
    )
    kb.add(InlineKeyboardButton("💳 Subscribe / Renew", callback_data="subscribe"))
    return kb


# ---- LOGOUT CONFIRM ----

def kb_logout_confirm():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Confirm Logout", callback_data="logout_confirm"),
        InlineKeyboardButton("❌ No, Cancel", callback_data="logout_cancel"),
    )
    return kb


# ---- STARTALL CONFIRM ----

def kb_startall_confirm():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("▶️ Start Kar Do", callback_data="startall_confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="startall_cancel"),
    )
    return kb


# ---- STOPALL CONFIRM ----

def kb_stopall_confirm():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⏹ Stop Kar Do", callback_data="stopall_confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="stopall_cancel"),
    )
    return kb


# ---- STATUS MENU ----

def kb_status_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📋 Status Groups", callback_data="status_groups"),
        InlineKeyboardButton("💳 Status Subscription", callback_data="status_sub"),
    )
    kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
    return kb


# ---- GROUPS ----

def kb_groups(groups: list):
    kb = InlineKeyboardMarkup(row_width=1)
    for g in groups:
        icon = "🟢" if g["active"] else "🔴"
        kb.add(InlineKeyboardButton(
            icon + " " + g["name"],
            callback_data="grp:" + str(g["id"])
        ))
    kb.add(InlineKeyboardButton("➕ New Group", callback_data="ng"))
    kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
    return kb


def kb_group(gid: int, active: bool):
    if active:
        toggle_btn = InlineKeyboardButton("⏹ Stop Group", callback_data="gx:" + str(gid))
    else:
        toggle_btn = InlineKeyboardButton("▶️ Start Group", callback_data="gs:" + str(gid))
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📥 Incoming", callback_data="gi:" + str(gid)),
        InlineKeyboardButton("📤 Outgoing", callback_data="go:" + str(gid)),
    )
    kb.add(toggle_btn, InlineKeyboardButton("✏️ Rename", callback_data="gr:" + str(gid)))
    kb.add(InlineKeyboardButton("🗑 Delete Group", callback_data="gd:" + str(gid)))
    kb.add(
        InlineKeyboardButton("◀️ Back", callback_data="grp_list"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="mm"),
    )
    return kb


# ---- CHANNEL SELECTION ----

def kb_channels(gid: int, mode: str, all_dialogs: list, selected: set):
    if mode == "in":
        pfx      = "si"
        all_cb   = "sia:" + str(gid)
        clear_cb = "sic:" + str(gid)
        confirm  = "gc:"  + str(gid)
    else:
        pfx      = "to"
        all_cb   = "toa:" + str(gid)
        clear_cb = "toc:" + str(gid)
        confirm  = "gco:" + str(gid)

    kb = InlineKeyboardMarkup(row_width=5)
    buttons = []
    for i, (did, dn) in enumerate(all_dialogs):
        num = str(i + 1)
        label = "[" + num + "]" if did in selected else num
        buttons.append(
            InlineKeyboardButton(label, callback_data=pfx + ":" + str(i) + ":" + str(gid))
        )
    if buttons:
        kb.add(*buttons)
    kb.row(
        InlineKeyboardButton("✅ Select All", callback_data=all_cb),
        InlineKeyboardButton("❌ Clear All",  callback_data=clear_cb),
    )
    kb.row(
        InlineKeyboardButton("◀️ Back",    callback_data="grp:" + str(gid)),
        InlineKeyboardButton("✔️ Confirm", callback_data=confirm),
    )
    return kb


# ---- POST ACTION ----

def kb_after_incoming(gid: int):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📤 Set Outgoing Channel", callback_data="go:" + str(gid)))
    kb.add(InlineKeyboardButton("⚙️ Group Settings",       callback_data="grp:" + str(gid)))
    kb.add(InlineKeyboardButton("🏠 Main Menu",            callback_data="mm"))
    kb.add(InlineKeyboardButton("✖️ Dismiss",              callback_data="dm"))
    return kb


def kb_after_outgoing(gid: int):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("▶️ Start Forwarding", callback_data="gs:" + str(gid)))
    kb.add(InlineKeyboardButton("⚙️ Group Settings",   callback_data="grp:" + str(gid)))
    kb.add(InlineKeyboardButton("🏠 Main Menu",        callback_data="mm"))
    kb.add(InlineKeyboardButton("✖️ Dismiss",          callback_data="dm"))
    return kb


def kb_after_start(gid: int):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 Status",     callback_data="status_groups"),
        InlineKeyboardButton("⏹ Stop Group", callback_data="gx:" + str(gid)),
    )
    kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
    return kb


def kb_delete_confirm(gid: int):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Haan, Delete Karo", callback_data="gdf:" + str(gid)),
        InlineKeyboardButton("❌ Nahi, Cancel",       callback_data="grp:" + str(gid)),
    )
    return kb


def kb_status():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("▶️ Start All", callback_data="sa"),
        InlineKeyboardButton("⏹ Stop All",  callback_data="xa"),
    )
    kb.add(InlineKeyboardButton("⚙️ Manage Groups", callback_data="grp_list"))
    kb.add(InlineKeyboardButton("🏠 Main Menu",     callback_data="mm"))
    return kb


def kb_subscribe(payment_link: str):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 ₹69/month — Pay Now", url=payment_link))
    kb.add(InlineKeyboardButton("🔄 Payment Check Karo", callback_data="check_payment"))
    kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
    return kb


def kb_subscribe_only():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 Subscribe — ₹69/month", callback_data="subscribe"))
    return kb


def kb_main_menu_only():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
    return kb


def kb_confirm_broadcast():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Haan, Bhejo", callback_data="bc_confirm"),
        InlineKeyboardButton("❌ Cancel",      callback_data="bc_cancel"),
    )
    return kb
