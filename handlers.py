from datetime import datetime

from aiogram import types, Bot
from aiogram.dispatcher import Dispatcher

import database as db
from config import MAX_GROUPS, OWNER_ID
from forwarder import (
    user_clients, user_dialogs,
    create_client_for_login, finalize_login,
    logout_user, is_user_logged_in,
    load_dialogs, connect_user,
    set_group_active,
)
from keyboards import (
    kb_login, kb_main, kb_groups, kb_group,
    kb_channels, kb_after_incoming, kb_after_outgoing,
    kb_after_start, kb_delete_confirm, kb_status,
    kb_subscribe, kb_subscribe_only, kb_main_menu_only,
)
from payments import create_order
from admin import broadcast_state

# Per-user state for login and rename
login_states: dict[int, dict] = {}
user_state: dict[int, dict] = {}
# Per-user temp channel selection (before confirming)
temp_selection: dict[int, dict] = {}


def _access_denied_text(reason: str) -> str:
    if reason == "banned":
        return "🚫 Aapka access band kar diya gaya hai. Support se sampark karo."
    return (
        "⏰ *Access Khatam Ho Gaya*\n\n"
        "Tera free trial ya subscription expire ho gaya hai.\n"
        "Bot use karte rehne ke liye subscribe karo:\n\n"
        "₹69/month — full access 30 din ke liye!"
    )


async def _check_and_reply(msg_or_cb, user_id: int, username: str, full_name: str):
    user = await db.get_or_create_user(user_id, username, full_name)
    allowed, reason = db.check_access(user)
    return user, allowed, reason


def _get_dialogs(uid: int) -> list[tuple[int, str]]:
    return user_dialogs.get(uid, [])


def _get_selected(uid: int, gid: int, mode: str) -> set:
    return temp_selection.get(uid, {}).get(f"{gid}_{mode}", set())


def _set_selected(uid: int, gid: int, mode: str, s: set):
    if uid not in temp_selection:
        temp_selection[uid] = {}
    temp_selection[uid][f"{gid}_{mode}"] = s


def _clear_selected(uid: int, gid: int, mode: str):
    if uid in temp_selection:
        temp_selection[uid].pop(f"{gid}_{mode}", None)


def _text_channel_list(uid: int, gid: int, mode: str) -> str:
    dialogs = _get_dialogs(uid)
    selected = _get_selected(uid, gid, mode)
    if not dialogs:
        return "Koi channel/group nahi mila. Pehle login karo."
    lines = []
    for i, (did, dn) in enumerate(dialogs):
        marker = " ✅" if did in selected else ""
        lines.append(f"{i + 1} - {dn}{marker}")
    return "\n".join(lines)


async def _groups_to_dict(uid: int) -> list[dict]:
    groups = await db.get_user_groups(uid)
    result = []
    for g in groups:
        in_ids = {ch.channel_id for ch in g.channels if ch.type == "incoming"}
        out_ids = {ch.channel_id for ch in g.channels if ch.type == "outgoing"}
        result.append({
            "id": g.id,
            "name": g.name,
            "active": g.is_active,
            "incoming": in_ids,
            "outgoing": out_ids,
        })
    return result


async def _text_status(uid: int) -> str:
    groups = await _groups_to_dict(uid)
    if not groups:
        return "*Status*\n\nKoi group nahi hai. Manage Groups se banao!"
    lines = ["*Forwarding Status*\n"]
    dialogs = _get_dialogs(uid)
    name_map = {d[0]: d[1] for d in dialogs}
    for g in groups:
        status = "🟢 Running" if g["active"] else "🔴 Stopped"
        in_names = ", ".join(name_map.get(d, str(d)) for d in g["incoming"]) or "-"
        out_names = ", ".join(name_map.get(d, str(d)) for d in g["outgoing"]) or "-"
        lines.append(f"*{g['name']}* — {status}")
        lines.append(f"  📥 IN:  {in_names}")
        lines.append(f"  📤 OUT: {out_names}\n")
    return "\n".join(lines)


async def _text_group(uid: int, gid: int) -> str:
    g = await db.get_group(gid)
    if not g or g.user_id != uid:
        return "Group nahi mila!"
    dialogs = _get_dialogs(uid)
    name_map = {d[0]: d[1] for d in dialogs}
    in_ids = [ch.channel_id for ch in g.channels if ch.type == "incoming"]
    out_ids = [ch.channel_id for ch in g.channels if ch.type == "outgoing"]
    status = "🟢 Running" if g.is_active else "🔴 Stopped"
    in_list = "\n  ".join("- " + name_map.get(d, str(d)) for d in in_ids) or "  -"
    out_list = "\n  ".join("- " + name_map.get(d, str(d)) for d in out_ids) or "  -"
    return (
        f"*{g.name}*\n\n"
        f"Status: {status}\n\n"
        f"Incoming ({len(in_ids)}):\n  {in_list}\n\n"
        f"Outgoing ({len(out_ids)}):\n  {out_list}"
    )


def _status_line(user, reason: str) -> str:
    if "subscribed" in reason:
        now = datetime.utcnow()
        days_left = (user.sub_end - now).days if user.sub_end else 0
        return f"✅ Subscription active — {days_left} din bacha"
    elif "trial" in reason:
        d = reason.split(":")[1]
        return f"⏳ Free Trial — {d} din bacha"
    return ""


def register_handlers(dp: Dispatcher, bot: Bot):

    # ---- /start ----
    @dp.message_handler(commands=["start"])
    async def cmd_start(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(
            uid,
            msg.from_user.username or "",
            msg.from_user.full_name or "",
        )
        allowed, reason = db.check_access(user)

        if not allowed:
            if reason == "banned":
                await msg.answer(_access_denied_text("banned"))
                return
            await msg.answer(
                "👋 *DealsKoti Forward Bot mein aapka swagat hai!*\n\n"
                + _access_denied_text("expired"),
                parse_mode="Markdown",
                reply_markup=kb_subscribe_only(),
            )
            return

        logged_in = await is_user_logged_in(uid)
        if not logged_in:
            await msg.answer(
                "👋 *DealsKoti Forward Bot*\n\n"
                "Messages automatically forward karo — bina 'Forwarded' tag ke!\n\n"
                f"_{_status_line(user, reason)}_\n\n"
                "Pehle apne Telegram account se login karo:",
                parse_mode="Markdown",
                reply_markup=kb_login(),
            )
            return

        text = (
            "🏠 *DealsKoti Forward Bot*\n\n"
            f"_{_status_line(user, reason)}_\n\n"
            "*Quick Guide:*\n"
            "1️⃣  Manage Groups → New Group banao\n"
            "2️⃣  Incoming Channel set karo\n"
            "3️⃣  Outgoing Channel set karo\n"
            "4️⃣  Start Forwarding!\n\n"
            "Neeche se option choose karo:"
        )
        await msg.answer(text, parse_mode="Markdown", reply_markup=kb_main())

    # ---- /login ----
    @dp.message_handler(commands=["login"])
    async def cmd_login(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        allowed, reason = db.check_access(user)
        if not allowed and reason != "expired" and reason != "banned":
            await msg.answer(_access_denied_text(reason))
            return
        if reason == "banned":
            await msg.answer(_access_denied_text("banned"))
            return
        if await is_user_logged_in(uid):
            await msg.answer("Pehle se logged in ho! /start karo.")
            return
        login_states[uid] = {"step": "phone", "phone": None, "phone_hash": None}
        await msg.answer(
            "📱 *Login — Step 1/3*\n\n"
            "Apna Telegram phone number dalo (country code ke saath):\n"
            "Example: `+919876543210`",
            parse_mode="Markdown",
        )

    # ---- /logout ----
    @dp.message_handler(commands=["logout"])
    async def cmd_logout(msg: types.Message):
        uid = msg.from_user.id
        await logout_user(uid)
        login_states.pop(uid, None)
        await msg.answer("✅ Logout ho gaye. /login karke dobara login karo.")

    # ---- /status ----
    @dp.message_handler(commands=["status"])
    async def cmd_status(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        allowed, reason = db.check_access(user)
        if not allowed:
            await msg.answer(_access_denied_text(reason), reply_markup=kb_subscribe_only() if reason != "banned" else None)
            return
        text = await _text_status(uid)
        await msg.answer(text, parse_mode="Markdown", reply_markup=kb_status())

    # ---- /groups ----
    @dp.message_handler(commands=["groups"])
    async def cmd_groups(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        allowed, reason = db.check_access(user)
        if not allowed:
            await msg.answer(_access_denied_text(reason), reply_markup=kb_subscribe_only() if reason != "banned" else None)
            return
        groups = await _groups_to_dict(uid)
        if not groups:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("➕ New Group", callback_data="ng"))
            await msg.answer("Koi group nahi hai. Naya banao!", reply_markup=kb)
        else:
            await msg.answer("*Saare Groups:*", parse_mode="Markdown", reply_markup=kb_groups(groups))

    # ---- /startall ----
    @dp.message_handler(commands=["startall"])
    async def cmd_startall(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        allowed, reason = db.check_access(user)
        if not allowed:
            await msg.answer(_access_denied_text(reason), reply_markup=kb_subscribe_only() if reason != "banned" else None)
            return
        groups = await db.get_user_groups(uid)
        count = 0
        for g in groups:
            in_ids = [ch for ch in g.channels if ch.type == "incoming"]
            out_ids = [ch for ch in g.channels if ch.type == "outgoing"]
            if in_ids and out_ids:
                await db.set_group_active(g.id, True)
                count += 1
        await msg.answer(f"▶️ {count} group(s) start ho gaye!")

    # ---- /stopall ----
    @dp.message_handler(commands=["stopall"])
    async def cmd_stopall(msg: types.Message):
        uid = msg.from_user.id
        await db.set_group_active_for_user(uid, False)
        await msg.answer("⏹ Sab groups band ho gaye!")

    # ---- /subscribe ----
    @dp.message_handler(commands=["subscribe"])
    async def cmd_subscribe(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        if user.is_banned:
            await msg.answer(_access_denied_text("banned"))
            return
        await msg.answer("⏳ Payment link generate ho raha hai...")
        try:
            order_id, pay_url = await create_order(uid)
            await msg.answer(
                "💳 *Subscribe — ₹69/month*\n\n"
                "30 din ka full access milega.\n"
                "UPI, Card, Net Banking — sab accept hai.\n\n"
                "Neeche button dabao aur pay karo:",
                parse_mode="Markdown",
                reply_markup=kb_subscribe(pay_url),
            )
        except Exception as err:
            await msg.answer(
                "❌ Payment link banane mein error aaya. Thodi der baad try karo.\n"
                f"Error: {err}"
            )

    # ---- /help ----
    @dp.message_handler(commands=["help"])
    async def cmd_help(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        _, reason = db.check_access(user)
        status_line = _status_line(user, reason) if user else ""

        text = (
            "❓ *Help — DealsKoti Forward Bot*\n\n"
            f"_{status_line}_\n\n" if status_line else "❓ *Help — DealsKoti Forward Bot*\n\n"
        ) + (
            "━━━━━━━━━━━━━━━━━━━\n"
            "*🔐 Login Kaise Kare:*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "1. `/login` bhejo\n"
            "2. Phone number dalo (e.g. `+919876543210`)\n"
            "3. Telegram se aaya OTP dalo\n"
            "4. (Agar 2FA hai) Password bhi dalo\n"
            "5. Done! `/start` se main menu khulega\n\n"

            "━━━━━━━━━━━━━━━━━━━\n"
            "*⚙️ Forwarding Setup:*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "1. Manage Groups → New Group banao\n"
            "2. Incoming channel select karo → Confirm\n"
            "3. Outgoing channel select karo → Confirm\n"
            "4. Start Forwarding!\n\n"

            "━━━━━━━━━━━━━━━━━━━\n"
            "*📋 Saare Commands:*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "`/start` — Main menu\n"
            "`/login` — Telegram account se login\n"
            "`/logout` — Logout karo\n"
            "`/groups` — Groups manage karo\n"
            "`/status` — Forwarding status dekho\n"
            "`/startall` — Saare groups start\n"
            "`/stopall` — Saare groups band\n"
            "`/subscribe` — ₹69/month subscription lo\n"
            "`/help` — Ye message\n\n"

            "━━━━━━━━━━━━━━━━━━━\n"
            "*✨ Features:*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"- Max {MAX_GROUPS} groups supported\n"
            "- Ek group mein multiple incoming/outgoing\n"
            "- Bina 'Forwarded' tag ke forward\n"
            "- Private & restricted channels support\n"
            "- Data kabhi nahi jata (restart-proof)\n"
            "- 7 din free trial\n\n"

            "━━━━━━━━━━━━━━━━━━━\n"
            "*💳 Subscription:*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "- 7 din FREE trial\n"
            "- Uske baad ₹69/month\n"
            "- UPI, Card, Net Banking accept\n"
            "- `/subscribe` se pay karo"
        )
        await msg.answer(text, parse_mode="Markdown", reply_markup=kb_main_menu_only())

    # ---- TEXT HANDLER (login + rename + broadcast) ----
    @dp.message_handler()
    async def text_handler(msg: types.Message):
        uid = msg.from_user.id
        text = msg.text.strip()

        # Broadcast flow (owner only)
        if uid == OWNER_ID and uid in broadcast_state:
            state = broadcast_state[uid]
            if state["step"] == "waiting_message":
                state["text"] = text
                state["step"] = "waiting_confirm"
                users = await db.get_all_users()
                count = sum(1 for u in users if not u.is_banned)
                await msg.answer(
                    f"📢 *Broadcast Preview*\n\n{text}\n\n"
                    f"Ye message *{count} users* ko jayega.\n"
                    "Confirm karo?",
                    parse_mode="Markdown",
                    reply_markup=kb_confirm_broadcast() if True else None,
                )
                from keyboards import kb_confirm_broadcast
                await msg.answer("Confirm?", reply_markup=kb_confirm_broadcast())
                return

        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        allowed, reason = db.check_access(user)

        # LOGIN FLOW
        ls = login_states.get(uid)
        if ls:
            from telethon.errors import (
                SessionPasswordNeededError, PhoneCodeInvalidError,
                PhoneCodeExpiredError, PasswordHashInvalidError,
            )

            if ls["step"] == "phone":
                phone = text
                try:
                    client = await create_client_for_login(uid)
                    result = await client.send_code_request(phone)
                    ls["phone"] = phone
                    ls["phone_hash"] = result.phone_code_hash
                    ls["step"] = "otp"
                    await msg.answer(
                        "📨 *Login — Step 2/3*\n\n"
                        "OTP Telegram pe bhej diya!\n"
                        "Ab OTP dalo (sirf numbers):\n"
                        "Example: `12345`",
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    login_states.pop(uid, None)
                    await msg.answer(
                        f"❌ Phone number galat ya error:\n`{e}`\n\nDobara /login karo.",
                        parse_mode="Markdown",
                    )
                return

            if ls["step"] == "otp":
                otp = text.replace(" ", "").replace("-", "")
                client = user_clients.get(uid)
                if not client:
                    login_states.pop(uid, None)
                    await msg.answer("Session expire ho gaya. Dobara /login karo.")
                    return
                try:
                    await client.sign_in(
                        phone=ls["phone"],
                        code=otp,
                        phone_code_hash=ls["phone_hash"],
                    )
                    login_states.pop(uid, None)
                    await finalize_login(uid)
                    await msg.answer(
                        "✅ *Login Ho Gaye!*\n\n"
                        "Ab /start karo aur forwarding setup karo!",
                        parse_mode="Markdown",
                    )
                except SessionPasswordNeededError:
                    ls["step"] = "2fa"
                    await msg.answer(
                        "🔒 *Login — Step 3/3*\n\n"
                        "2-Step Verification ON hai.\n"
                        "Apna Telegram password dalo:",
                        parse_mode="Markdown",
                    )
                except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
                    login_states.pop(uid, None)
                    await msg.answer(f"❌ OTP galat ya expire:\n`{e}`\n\nDobara /login karo.", parse_mode="Markdown")
                except Exception as e:
                    login_states.pop(uid, None)
                    await msg.answer(f"❌ Error:\n`{e}`\n\nDobara /login karo.", parse_mode="Markdown")
                return

            if ls["step"] == "2fa":
                client = user_clients.get(uid)
                if not client:
                    login_states.pop(uid, None)
                    await msg.answer("Session expire ho gaya. Dobara /login karo.")
                    return
                try:
                    from telethon.errors import PasswordHashInvalidError
                    await client.sign_in(password=text)
                    login_states.pop(uid, None)
                    await finalize_login(uid)
                    await msg.answer(
                        "✅ *Login Ho Gaye!*\n\n"
                        "Ab /start karo aur forwarding setup karo!",
                        parse_mode="Markdown",
                    )
                except PasswordHashInvalidError:
                    await msg.answer("❌ Password galat hai. Dobara dalo:")
                except Exception as e:
                    login_states.pop(uid, None)
                    await msg.answer(f"❌ Error:\n`{e}`\n\nDobara /login karo.", parse_mode="Markdown")
                return

        # RENAME FLOW
        state = user_state.get(uid)
        if state and state.get("action") == "rename":
            gid = state["group_id"]
            g = await db.get_group(gid)
            if g and g.user_id == uid:
                new_name = text[:30]
                await db.rename_group(gid, new_name)
                user_state.pop(uid, None)
                await msg.answer(
                    f"✅ Naam badal diya: *{new_name}*",
                    parse_mode="Markdown",
                    reply_markup=kb_group(gid, g.is_active),
                )
            else:
                user_state.pop(uid, None)
                await msg.answer("Group nahi mila.")

    # ---- CALLBACK HANDLER ----
    @dp.callback_query_handler()
    async def on_callback(cb: types.CallbackQuery):
        uid = cb.from_user.id
        data = cb.data

        user = await db.get_or_create_user(uid, cb.from_user.username or "", cb.from_user.full_name or "")
        allowed, reason = db.check_access(user)

        # Login callback — allowed even when expired
        if data == "do_login":
            if await is_user_logged_in(uid):
                await cb.message.edit_text("Pehle se logged in ho! /start karo.")
                return
            login_states[uid] = {"step": "phone", "phone": None, "phone_hash": None}
            await cb.message.edit_text(
                "📱 *Login — Step 1/3*\n\n"
                "Apna phone number dalo (country code ke saath):\n"
                "Example: `+919876543210`",
                parse_mode="Markdown",
            )
            await cb.answer()
            return

        if data == "subscribe":
            if user.is_banned:
                await cb.answer(_access_denied_text("banned"), show_alert=True)
                return
            await cb.answer("Payment link generate ho raha hai...")
            try:
                order_id, pay_url = await create_order(uid)
                await cb.message.answer(
                    "💳 *Subscribe — ₹69/month*\n\n"
                    "30 din ka full access milega.\n"
                    "UPI, Card, Net Banking — sab accept hai.\n\n"
                    "Neeche button dabao aur pay karo:",
                    parse_mode="Markdown",
                    reply_markup=kb_subscribe(pay_url),
                )
            except Exception as err:
                await cb.message.answer(f"❌ Error: {err}\n\nThodi der baad try karo.")
            return

        if data == "check_payment":
            await db.get_or_create_user(uid, cb.from_user.username or "", cb.from_user.full_name or "")
            fresh = await db.get_user(uid)
            if fresh:
                now = datetime.utcnow()
                if fresh.sub_end and fresh.sub_end > now:
                    await cb.answer("✅ Payment confirm ho gayi! Ab bot use kar sakte ho.", show_alert=True)
                else:
                    await cb.answer("❌ Abhi payment confirm nahi hui. Pay karo ya thodi der baad check karo.", show_alert=True)
            return

        # Broadcast confirm/cancel (owner only)
        if uid == OWNER_ID and data in ("bc_confirm", "bc_cancel"):
            if data == "bc_cancel":
                broadcast_state.pop(uid, None)
                await cb.message.edit_text("✅ Broadcast cancel ho gaya.")
                await cb.answer()
                return
            state = broadcast_state.get(uid, {})
            msg_text = state.get("text", "")
            if not msg_text:
                await cb.answer("Koi message nahi!", show_alert=True)
                return
            users = await db.get_all_users()
            targets = [u for u in users if not u.is_banned]
            await cb.message.edit_text(f"📢 Bhej raha hun {len(targets)} users ko...")
            sent = 0
            for u in targets:
                try:
                    await bot.send_message(u.user_id, msg_text, parse_mode="Markdown")
                    sent += 1
                except Exception:
                    pass
            broadcast_state.pop(uid, None)
            await cb.message.answer(f"✅ Broadcast complete! {sent}/{len(targets)} users ko deliver hua.")
            await cb.answer()
            return

        # For all other actions, access check
        if not allowed:
            await cb.answer(
                "Access nahi hai! Subscribe karo.",
                show_alert=True,
            )
            return

        if not await is_user_logged_in(uid):
            if data not in ("mm", "hl", "subscribe"):
                await cb.answer("Pehle /login karo!", show_alert=True)
                return

        # MAIN MENU
        if data == "mm":
            text = (
                "🏠 *DealsKoti Forward Bot*\n\n"
                f"_{_status_line(user, reason)}_\n\n"
                "Option choose karo:"
            )
            await cb.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_main())

        elif data == "dm":
            await cb.message.delete()

        elif data == "st":
            text = await _text_status(uid)
            await cb.message.answer(text, parse_mode="Markdown", reply_markup=kb_status())

        elif data == "hl":
            await cb.message.answer(
                "❓ *Quick Help*\n\n"
                "1. Manage Groups → New Group\n"
                "2. Incoming channel select → Confirm\n"
                "3. Outgoing channel select → Confirm\n"
                "4. Start Forwarding!\n\n"
                f"Max {MAX_GROUPS} groups | Private channels OK\n"
                "/help se full guide dekho.",
                parse_mode="Markdown",
                reply_markup=kb_main_menu_only(),
            )

        elif data == "grp_list":
            groups = await _groups_to_dict(uid)
            if not groups:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("➕ New Group", callback_data="ng"))
                kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
                await cb.message.edit_text(
                    "*Groups*\n\nKoi group nahi hai. Naya banao!",
                    parse_mode="Markdown",
                    reply_markup=kb,
                )
            else:
                await cb.message.edit_text(
                    "*Saare Groups*\n\nGroup select karo:",
                    parse_mode="Markdown",
                    reply_markup=kb_groups(groups),
                )

        elif data == "ng":
            count = await db.count_user_groups(uid)
            if count >= MAX_GROUPS:
                await cb.answer(f"Max {MAX_GROUPS} groups bana sakte ho!", show_alert=True)
                return
            existing = await db.get_user_groups(uid)
            existing_nums = set()
            for g in existing:
                if g.name.startswith("Group "):
                    try:
                        existing_nums.add(int(g.name.split(" ")[1]))
                    except Exception:
                        pass
            nid = next(i for i in range(1, MAX_GROUPS + 2) if i not in existing_nums)
            g = await db.create_group(uid, f"Group {nid}")
            await cb.message.edit_text(
                f"*{g.name}* bana diya!\n\nAb incoming aur outgoing channels set karo.",
                parse_mode="Markdown",
                reply_markup=kb_group(g.id, False),
            )

        elif data.startswith("grp:"):
            gid = int(data[4:])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                await cb.answer("Group nahi mila!", show_alert=True)
                return
            await cb.message.edit_text(
                await _text_group(uid, gid),
                parse_mode="Markdown",
                reply_markup=kb_group(gid, g.is_active),
            )

        elif data.startswith("gi:") or data.startswith("go:"):
            mode = "in" if data.startswith("gi:") else "out"
            gid = int(data[3:])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                await cb.answer("Group nahi mila!", show_alert=True)
                return
            await load_dialogs(uid)
            dialogs = _get_dialogs(uid)
            if not dialogs:
                await cb.answer("Koi channel nahi mila! /login karo.", show_alert=True)
                return
            # Load existing selection
            existing = await db.get_channels(gid, "incoming" if mode == "in" else "outgoing")
            selected = {ch.channel_id for ch in existing}
            _set_selected(uid, gid, mode, selected)
            label = "Incoming" if mode == "in" else "Outgoing"
            text = (
                f"*{g.name} — {label}*\n\n"
                "Number dabao to select/deselect karo:\n\n"
                + _text_channel_list(uid, gid, mode)
            )
            await cb.message.edit_text(
                text,
                reply_markup=kb_channels(gid, mode, dialogs, selected),
            )

        elif data.startswith("si:") or data.startswith("to:"):
            mode = "in" if data.startswith("si:") else "out"
            parts = data.split(":")
            idx = int(parts[1])
            gid = int(parts[2])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                return
            dialogs = _get_dialogs(uid)
            if 0 <= idx < len(dialogs):
                did = dialogs[idx][0]
                selected = _get_selected(uid, gid, mode)
                if did in selected:
                    selected.discard(did)
                else:
                    selected.add(did)
                _set_selected(uid, gid, mode, selected)
                label = "Incoming" if mode == "in" else "Outgoing"
                text = (
                    f"*{g.name} — {label}*\n\n"
                    "Number dabao to select/deselect karo:\n\n"
                    + _text_channel_list(uid, gid, mode)
                )
                await cb.message.edit_text(
                    text,
                    reply_markup=kb_channels(gid, mode, dialogs, selected),
                )

        elif data.startswith("sia:") or data.startswith("toa:"):
            mode = "in" if data.startswith("sia:") else "out"
            gid = int(data[4:])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                return
            dialogs = _get_dialogs(uid)
            selected = {d[0] for d in dialogs}
            _set_selected(uid, gid, mode, selected)
            label = "Incoming" if mode == "in" else "Outgoing"
            text = (
                f"*{g.name} — {label}*\n\n"
                "Number dabao to select/deselect karo:\n\n"
                + _text_channel_list(uid, gid, mode)
            )
            await cb.message.edit_text(
                text,
                reply_markup=kb_channels(gid, mode, dialogs, selected),
            )

        elif data.startswith("sic:") or data.startswith("toc:"):
            mode = "in" if data.startswith("sic:") else "out"
            gid = int(data[4:])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                return
            dialogs = _get_dialogs(uid)
            _set_selected(uid, gid, mode, set())
            label = "Incoming" if mode == "in" else "Outgoing"
            text = (
                f"*{g.name} — {label}*\n\n"
                "Number dabao to select/deselect karo:\n\n"
                + _text_channel_list(uid, gid, mode)
            )
            await cb.message.edit_text(
                text,
                reply_markup=kb_channels(gid, mode, dialogs, set()),
            )

        elif data.startswith("gc:") or data.startswith("gco:"):
            mode = "in" if data.startswith("gc:") else "out"
            gid = int(data[3:] if mode == "in" else data[4:])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                return
            dialogs = _get_dialogs(uid)
            name_map = {d[0]: d[1] for d in dialogs}
            selected = _get_selected(uid, gid, mode)
            if not selected:
                await cb.answer("Koi channel select nahi kiya!", show_alert=True)
                return
            ch_type = "incoming" if mode == "in" else "outgoing"
            channel_list = [(did, name_map.get(did, str(did))) for did in selected]
            await db.set_channels(gid, ch_type, channel_list)
            _clear_selected(uid, gid, mode)
            count = len(selected)
            names = "\n".join("- " + name_map.get(d, str(d)) for d in selected)
            if mode == "in":
                await cb.message.edit_text(
                    f"✅ *Incoming Confirmed!*\n\n{count} channel(s) set:\n{names}\n\nAb outgoing channel set karo.",
                    parse_mode="Markdown",
                    reply_markup=kb_after_incoming(gid),
                )
            else:
                await cb.message.edit_text(
                    f"✅ *Outgoing Confirmed!*\n\n{count} channel(s) set:\n{names}\n\nAb forwarding start karo!",
                    parse_mode="Markdown",
                    reply_markup=kb_after_outgoing(gid),
                )

        elif data.startswith("gs:"):
            gid = int(data[3:])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                return
            in_chs = [ch for ch in g.channels if ch.type == "incoming"]
            out_chs = [ch for ch in g.channels if ch.type == "outgoing"]
            if not in_chs:
                await cb.answer("Pehle incoming channel set karo!", show_alert=True)
                return
            if not out_chs:
                await cb.answer("Pehle outgoing channel set karo!", show_alert=True)
                return
            await db.set_group_active(gid, True)
            dialogs = _get_dialogs(uid)
            name_map = {d[0]: d[1] for d in dialogs}
            in_names = ", ".join(name_map.get(ch.channel_id, str(ch.channel_id)) for ch in in_chs)
            out_names = ", ".join(name_map.get(ch.channel_id, str(ch.channel_id)) for ch in out_chs)
            await cb.message.edit_text(
                f"▶️ *Forwarding Started!*\n\n"
                f"*{g.name}*\n"
                f"📥 From: {in_names}\n"
                f"📤 To: {out_names}\n\n"
                "Messages automatically forward ho rahe hain!",
                parse_mode="Markdown",
                reply_markup=kb_after_start(gid),
            )

        elif data.startswith("gx:"):
            gid = int(data[3:])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                return
            await db.set_group_active(gid, False)
            await cb.message.edit_text(
                f"⏹ *{g.name}* band ho gaya!",
                parse_mode="Markdown",
                reply_markup=kb_group(gid, False),
            )

        elif data.startswith("gr:"):
            gid = int(data[3:])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                return
            user_state[uid] = {"action": "rename", "group_id": gid}
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data="grp:" + str(gid)))
            await cb.message.edit_text(
                f"*{g.name}* ka naya naam type karo (max 30 chars):",
                parse_mode="Markdown",
                reply_markup=kb,
            )

        elif data.startswith("gd:"):
            gid = int(data[3:])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                return
            await cb.message.edit_text(
                f"'*{g.name}*' delete karna chahte ho?\n\nYe action undo nahi hogi!",
                parse_mode="Markdown",
                reply_markup=kb_delete_confirm(gid),
            )

        elif data.startswith("gdf:"):
            gid = int(data[4:])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                return
            name = g.name
            await db.delete_group(gid)
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("⚙️ Groups", callback_data="grp_list"),
                InlineKeyboardButton("🏠 Main Menu", callback_data="mm"),
            )
            await cb.message.edit_text(f"🗑 '*{name}*' delete ho gaya!", parse_mode="Markdown", reply_markup=kb)

        elif data == "sa":
            groups = await db.get_user_groups(uid)
            count = 0
            for g in groups:
                in_chs = [ch for ch in g.channels if ch.type == "incoming"]
                out_chs = [ch for ch in g.channels if ch.type == "outgoing"]
                if in_chs and out_chs:
                    await db.set_group_active(g.id, True)
                    count += 1
            await cb.answer(f"▶️ {count} group(s) start ho gaye!", show_alert=True)

        elif data == "xa":
            await db.set_group_active_for_user(uid, False)
            await cb.answer("⏹ Sab groups band ho gaye!", show_alert=True)

        elif data == "quick_start":
            groups = await db.get_user_groups(uid)
            count = 0
            for g in groups:
                in_chs = [ch for ch in g.channels if ch.type == "incoming"]
                out_chs = [ch for ch in g.channels if ch.type == "outgoing"]
                if in_chs and out_chs:
                    await db.set_group_active(g.id, True)
                    count += 1
            if count == 0:
                await cb.answer("Koi configured group nahi! Pehle setup karo.", show_alert=True)
            else:
                await cb.answer(f"▶️ {count} group(s) start ho gaye!", show_alert=True)

        elif data == "quick_stop":
            await db.set_group_active_for_user(uid, False)
            await cb.answer("⏹ Sab forwarding band ho gaya!", show_alert=True)

        elif data == "menu_inc":
            groups = await _groups_to_dict(uid)
            if not groups:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("➕ New Group", callback_data="ng"))
                kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
                await cb.message.edit_text("Pehle ek group banao:", reply_markup=kb)
            elif len(groups) == 1:
                gid = groups[0]["id"]
                await load_dialogs(uid)
                dialogs = _get_dialogs(uid)
                selected = _get_selected(uid, gid, "in")
                g = await db.get_group(gid)
                await cb.message.edit_text(
                    f"*{groups[0]['name']} — Incoming*\nSelect karo:",
                    parse_mode="Markdown",
                    reply_markup=kb_channels(gid, "in", dialogs, selected),
                )
            else:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                kb = InlineKeyboardMarkup(row_width=1)
                for g in groups:
                    kb.add(InlineKeyboardButton(g["name"], callback_data="gi:" + str(g["id"])))
                kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
                await cb.message.edit_text("Kaun se group ka incoming set karna hai?", reply_markup=kb)

        elif data == "menu_out":
            groups = await _groups_to_dict(uid)
            if not groups:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("➕ New Group", callback_data="ng"))
                kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
                await cb.message.edit_text("Pehle ek group banao:", reply_markup=kb)
            elif len(groups) == 1:
                gid = groups[0]["id"]
                await load_dialogs(uid)
                dialogs = _get_dialogs(uid)
                selected = _get_selected(uid, gid, "out")
                await cb.message.edit_text(
                    f"*{groups[0]['name']} — Outgoing*\nSelect karo:",
                    parse_mode="Markdown",
                    reply_markup=kb_channels(gid, "out", dialogs, selected),
                )
            else:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                kb = InlineKeyboardMarkup(row_width=1)
                for g in groups:
                    kb.add(InlineKeyboardButton(g["name"], callback_data="go:" + str(g["id"])))
                kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
                await cb.message.edit_text("Kaun se group ka outgoing set karna hai?", reply_markup=kb)

        await cb.answer()
