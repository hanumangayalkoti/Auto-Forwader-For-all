import re
from datetime import datetime

from aiogram import types, Bot
from aiogram.dispatcher import Dispatcher

import database as db
from config import MAX_GROUPS, OWNER_ID
from forwarder import (
    user_clients, user_dialogs,
    create_client_for_login, finalize_login,
    logout_user, is_user_logged_in,
    load_dialogs, check_is_member, check_can_post,
)
from keyboards import (
    kb_login, kb_main, kb_groups, kb_group,
    kb_channels, kb_after_incoming, kb_after_outgoing,
    kb_after_start, kb_delete_confirm, kb_status,
    kb_subscribe, kb_subscribe_only, kb_main_menu_only,
    kb_logout_confirm, kb_startall_confirm, kb_stopall_confirm,
    kb_status_menu,
)
from payments import create_order
from admin import broadcast_state

login_states: dict[int, dict] = {}
user_state: dict[int, dict] = {}
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
        return "Koi channel/group nahi mila. Pehle /login karo."
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


def _sub_status_text(user, reason: str) -> str:
    now = datetime.utcnow()
    if reason == "banned":
        return "🚫 Account banned hai."
    if "subscribed" in reason:
        days_left = (user.sub_end - now).days if user.sub_end else 0
        end_str = user.sub_end.strftime("%d %b %Y") if user.sub_end else "N/A"
        return (
            "💳 *Subscription Status*\n\n"
            f"✅ Active\n"
            f"Plan: ₹69/month\n"
            f"Expires: {end_str}\n"
            f"Din bache: {days_left}"
        )
    if "trial" in reason:
        d = reason.split(":")[1]
        end_str = user.trial_end.strftime("%d %b %Y") if user.trial_end else "N/A"
        return (
            "💳 *Subscription Status*\n\n"
            f"⏳ Free Trial\n"
            f"Trial ends: {end_str}\n"
            f"Din bache: {d}\n\n"
            "Subscribe karo full access ke liye!"
        )
    return (
        "💳 *Subscription Status*\n\n"
        "❌ Expired\n\n"
        "Subscribe karo bot use karne ke liye!"
    )


def _extract_otp(text: str) -> str:
    return re.sub(r'\D', '', text)


def register_handlers(dp: Dispatcher, bot: Bot):

    # ---- /start ----
    @dp.message_handler(commands=["start"])
    async def cmd_start(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
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
                "Messages automatically forward karo — bina Forwarded tag ke!\n\n"
                f"_{_status_line(user, reason)}_\n\n"
                "Pehle apne Telegram account se login karo:",
                parse_mode="Markdown",
                reply_markup=kb_login(),
            )
            return

        await msg.answer(
            "🏠 *DealsKoti Forward Bot*\n\n"
            f"_{_status_line(user, reason)}_\n\n"
            "Neeche se option choose karo:",
            parse_mode="Markdown",
            reply_markup=kb_main(),
        )

    # ---- /login ----
    @dp.message_handler(commands=["login"])
    async def cmd_login(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        allowed, reason = db.check_access(user)
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
            "Example: +919876543210",
            parse_mode="Markdown",
        )

    # ---- /logout ----
    @dp.message_handler(commands=["logout"])
    async def cmd_logout(msg: types.Message):
        await msg.answer(
            "🚪 *Logout Confirm Karo*\n\n"
            "Logout karne se tumhara session delete ho jayega.\n"
            "Dobara /login karna padega.\n\n"
            "Pakka logout karna hai?",
            parse_mode="Markdown",
            reply_markup=kb_logout_confirm(),
        )

    # ---- /status ----
    @dp.message_handler(commands=["status"])
    async def cmd_status(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        allowed, reason = db.check_access(user)
        if not allowed:
            await msg.answer(_access_denied_text(reason), reply_markup=kb_subscribe_only() if reason != "banned" else None)
            return
        await msg.answer(
            "📊 *Status*\n\nKya dekhna hai?",
            parse_mode="Markdown",
            reply_markup=kb_status_menu(),
        )

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
        ready = sum(1 for g in groups if
                    any(ch.type == "incoming" for ch in g.channels) and
                    any(ch.type == "outgoing" for ch in g.channels))
        await msg.answer(
            f"▶️ *Start All Groups*\n\n"
            f"Saare {ready} groups mein forwarding start ho jayegi.\n\n"
            "Confirm karo?",
            parse_mode="Markdown",
            reply_markup=kb_startall_confirm(),
        )

    # ---- /stopall ----
    @dp.message_handler(commands=["stopall"])
    async def cmd_stopall(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        allowed, reason = db.check_access(user)
        if not allowed:
            await msg.answer(_access_denied_text(reason), reply_markup=kb_subscribe_only() if reason != "banned" else None)
            return
        await msg.answer(
            "⏹ *Stop All Groups*\n\n"
            "Saare groups mein forwarding STOP ho jayegi.\n\n"
            "Confirm karo?",
            parse_mode="Markdown",
            reply_markup=kb_stopall_confirm(),
        )

    # ---- /myplan ----
    @dp.message_handler(commands=["myplan"])
    async def cmd_myplan(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        _, reason = db.check_access(user)
        now = datetime.utcnow()

        if user.is_banned:
            await msg.answer("🚫 Aapka account ban hai. Support se contact karo.")
            return

        if "subscribed" in reason:
            days_left = (user.sub_end - now).days if user.sub_end else 0
            end_str = user.sub_end.strftime("%d %b %Y") if user.sub_end else "N/A"
            await msg.answer(
                "💳 *Mera Plan*\n\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "Plan: ₹69/month\n"
                f"Status: ✅ Active\n"
                f"Valid Until: {end_str}\n"
                f"Din Bache: {days_left}\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                "Renew karne ke liye /renew bhejo.",
                parse_mode="Markdown",
                reply_markup=kb_main_menu_only(),
            )
        elif "trial" in reason:
            d = reason.split(":")[1]
            end_str = user.trial_end.strftime("%d %b %Y") if user.trial_end else "N/A"
            await msg.answer(
                "💳 *Mera Plan*\n\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "Plan: Free Trial\n"
                f"Status: ⏳ Active\n"
                f"Trial Ends: {end_str}\n"
                f"Din Bache: {d}\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                "Trial khatam hone se pehle subscribe karo:\n"
                "₹69/month — /subscribe",
                parse_mode="Markdown",
                reply_markup=kb_subscribe_only(),
            )
        else:
            await msg.answer(
                "💳 *Mera Plan*\n\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "Status: ❌ Expired\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                "Subscribe karo bot use karne ke liye!\n"
                "₹69/month — /subscribe",
                parse_mode="Markdown",
                reply_markup=kb_subscribe_only(),
            )

    # ---- /renew ----
    @dp.message_handler(commands=["renew"])
    async def cmd_renew(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        if user.is_banned:
            await msg.answer(_access_denied_text("banned"))
            return
        _, reason = db.check_access(user)
        now = datetime.utcnow()

        if "subscribed" in reason and user.sub_end:
            days_left = (user.sub_end - now).days
            end_str = user.sub_end.strftime("%d %b %Y")
            intro = (
                f"📅 Abhi subscription {end_str} tak valid hai ({days_left} din bache).\n"
                "Renew karne pe ye aur 30 din extend ho jayega.\n\n"
            )
        else:
            intro = "🔄 Subscription renew karo — 30 din ka full access milega.\n\n"

        await msg.answer("⏳ Payment link generate ho raha hai...")
        try:
            order_id, pay_url = await create_order(uid)
            await msg.answer(
                "💳 *Renew Subscription — ₹69/month*\n\n"
                + intro +
                "UPI, Card, Net Banking — sab accept hai.\n\n"
                "Neeche button dabao aur pay karo:",
                parse_mode="Markdown",
                reply_markup=kb_subscribe(pay_url),
            )
        except Exception as err:
            await msg.answer(f"❌ Payment link banane mein error. Thodi der baad try karo.\nError: {err}")

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
            await msg.answer(f"❌ Payment link banane mein error aaya. Thodi der baad try karo.\nError: {err}")

    # ---- /help ----
    @dp.message_handler(commands=["help"])
    async def cmd_help(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        _, reason = db.check_access(user)
        status_line = _status_line(user, reason)

        user_text = (
            "❓ *Help — DealsKoti Forward Bot*\n"
            + (f"\n_{status_line}_\n" if status_line else "") +
            "\n━━━━━━━━━━━━━━━━━━━\n"
            "*🔐 Login Kaise Kare:*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "1. /login bhejo\n"
            "2. Phone number dalo (+919876543210)\n"
            "3. OTP aaye to: word + OTP likho (e.g. code12345)\n"
            "4. Agar 2FA hai → password dalo\n"
            "5. Done! /start se menu khulega\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "*📋 Tumhare Commands:*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "/start — Main menu\n"
            "/login — Telegram account se login\n"
            "/logout — Logout karo\n"
            "/groups — Groups manage karo\n"
            "/status — Forwarding aur subscription status\n"
            "/startall — Saare groups start\n"
            "/stopall — Saare groups band\n"
            "/myplan — Apna plan aur expiry dekho\n"
            "/subscribe — ₹69/month subscription lo\n"
            "/renew — Subscription renew karo\n"
            "/help — Ye message\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "*✨ Features:*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"- Max {MAX_GROUPS} groups\n"
            "- Top 20 channels (pinned pehle)\n"
            "- Bina Forwarded tag ke forward\n"
            "- Private channels support\n"
            "- 7 din free trial\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "*💳 Subscription:*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "- 7 din FREE trial\n"
            "- ₹69/month\n"
            "- UPI, Card, Net Banking accept"
        )

        owner_extra = (
            "\n\n━━━━━━━━━━━━━━━━━━━\n"
            "*👑 Owner Commands:*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "*📊 Dashboard & Stats:*\n"
            "/admin — Full dashboard (users + revenue)\n"
            "/users — Saare users ki list\n"
            "/revenue — Revenue report\n"
            "/expiring — Agle 5 din mein expire hone wale\n\n"
            "*👤 User Management:*\n"
            "/give `<id>` `<days>` — User ko days do\n"
            "/removeuser `<id>` — Access turant hatao\n"
            "/ban `<id>` — User ban karo\n"
            "/unban `<id>` — User unban karo\n"
            "/check `<id>` — User ki poori detail\n\n"
            "*📢 Broadcast:*\n"
            "/broadcast — Saare users ko message bhejo\n"
            "/cancel — Broadcast cancel karo"
        )

        if uid == OWNER_ID:
            text = user_text + owner_extra
        else:
            text = user_text

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
                from keyboards import kb_confirm_broadcast
                await msg.answer(
                    f"📢 *Broadcast Preview*\n\n{text}\n\n"
                    f"Ye message *{count} users* ko jayega.\nConfirm karo?",
                    parse_mode="Markdown",
                    reply_markup=kb_confirm_broadcast(),
                )
                return

        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")

        # LOGIN FLOW
        ls = login_states.get(uid)
        if ls:
            from telethon.errors import (
                SessionPasswordNeededError, PhoneCodeInvalidError,
                PhoneCodeExpiredError, PasswordHashInvalidError,
                FloodWaitError, PhoneNumberInvalidError,
                PhoneNumberBannedError, PhoneNumberUnoccupiedError,
            )

            if ls["step"] == "phone":
                phone = text.strip()
                if not phone.startswith("+") or not phone[1:].isdigit():
                    await msg.answer(
                        "❌ *Phone number galat format mein hai!*\n\n"
                        "Country code ke saath dalo:\n"
                        "Example: +919876543210\n\n"
                        "Dobara try karo:",
                        parse_mode="Markdown",
                    )
                    return
                try:
                    client = await create_client_for_login(uid)
                    result = await client.send_code_request(phone)
                    ls["phone"] = phone
                    ls["phone_hash"] = result.phone_code_hash
                    ls["step"] = "otp"
                    await msg.answer(
                        "📨 *Login — Step 2/3*\n\n"
                        "✅ OTP Telegram pe bhej diya!\n\n"
                        "⚠️ OTP seedha mat bhejo!\n"
                        "Koi bhi word + OTP likho:\n"
                        "Example: code12345 ya hello12345\n\n"
                        "_(Ye Telegram security ke liye zaroori hai)_",
                        parse_mode="Markdown",
                    )
                except PhoneNumberInvalidError:
                    login_states.pop(uid, None)
                    await msg.answer(
                        "❌ *Phone number invalid hai!*\n\n"
                        "Ye number Telegram pe registered nahi hai.\n"
                        "Sahi number dalo aur dobara /login karo.",
                        parse_mode="Markdown",
                    )
                except PhoneNumberBannedError:
                    login_states.pop(uid, None)
                    await msg.answer(
                        "❌ *Ye number Telegram pe ban hai!*\n\n"
                        "Dusra number try karo ya /login se dobara shuru karo.",
                        parse_mode="Markdown",
                    )
                except PhoneNumberUnoccupiedError:
                    login_states.pop(uid, None)
                    await msg.answer(
                        "❌ *Ye number Telegram pe registered nahi hai!*\n\n"
                        "Pehle Telegram app se account banao, phir /login karo.",
                        parse_mode="Markdown",
                    )
                except FloodWaitError as e:
                    login_states.pop(uid, None)
                    await msg.answer(
                        f"⏳ *Telegram ne temporarily block kiya!*\n\n"
                        f"{e.seconds // 60} minute {e.seconds % 60} second baad dobara try karo.",
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    login_states.pop(uid, None)
                    await msg.answer(
                        f"❌ *Login shuru karne mein error aaya!*\n\n"
                        f"Error: {e}\n\nThodi der baad dobara /login karo.",
                        parse_mode="Markdown",
                    )
                return

            if ls["step"] == "otp":
                otp = _extract_otp(text)
                if not otp:
                    await msg.answer(
                        "❌ *OTP mein koi number nahi mila!*\n\n"
                        "Koi bhi word + OTP likho:\n"
                        "Example: code12345 ya hello12345",
                        parse_mode="Markdown",
                    )
                    return
                client = user_clients.get(uid)
                if not client:
                    login_states.pop(uid, None)
                    await msg.answer("⚠️ Session expire ho gaya. Dobara /login karo.", parse_mode="Markdown")
                    return
                try:
                    await client.sign_in(phone=ls["phone"], code=otp, phone_code_hash=ls["phone_hash"])
                    login_states.pop(uid, None)
                    await finalize_login(uid)
                    await msg.answer(
                        "✅ *Login Ho Gaye!*\n\n"
                        "Aapka Telegram account successfully connect ho gaya!\n\n"
                        "Ab /start karo aur forwarding setup karo!",
                        parse_mode="Markdown",
                    )
                except SessionPasswordNeededError:
                    ls["step"] = "2fa"
                    await msg.answer(
                        "🔒 *Login — Step 3/3*\n\n"
                        "2-Step Verification ON hai.\n\n"
                        "Apna Telegram cloud password dalo\n"
                        "_(Telegram Settings → Privacy → Two-Step Verification wala)_:",
                        parse_mode="Markdown",
                    )
                except PhoneCodeInvalidError:
                    await msg.answer(
                        "❌ *OTP galat hai!*\n\n"
                        "Format: word + OTP\n"
                        "Example: code12345\n\n"
                        "Dobara try karo ya /login se restart karo:",
                        parse_mode="Markdown",
                    )
                except PhoneCodeExpiredError:
                    login_states.pop(uid, None)
                    await msg.answer(
                        "⏰ *OTP expire ho gaya!*\n\n"
                        "OTP sirf 2 minute tak valid hota hai.\n"
                        "Dobara /login karo aur OTP aate hi turant bhejo.",
                        parse_mode="Markdown",
                    )
                except FloodWaitError as e:
                    login_states.pop(uid, None)
                    await msg.answer(
                        f"⏳ *Bahut zyada galat attempts!*\n\n"
                        f"{e.seconds // 60} minute baad try karo.",
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    login_states.pop(uid, None)
                    await msg.answer(f"❌ Error: {e}\n\nDobara /login karo.", parse_mode="Markdown")
                return

            if ls["step"] == "2fa":
                client = user_clients.get(uid)
                if not client:
                    login_states.pop(uid, None)
                    await msg.answer("⚠️ Session expire ho gaya. Dobara /login karo.", parse_mode="Markdown")
                    return
                try:
                    await client.sign_in(password=text)
                    login_states.pop(uid, None)
                    await finalize_login(uid)
                    await msg.answer(
                        "✅ *Login Ho Gaye!*\n\n"
                        "2FA verify ho gaya! Account connected.\n\n"
                        "Ab /start karo!",
                        parse_mode="Markdown",
                    )
                except PasswordHashInvalidError:
                    await msg.answer(
                        "❌ *Password galat hai!*\n\n"
                        "Telegram Settings → Privacy & Security → Two-Step Verification wala password chahiye.\n\n"
                        "Dobara try karo:",
                        parse_mode="Markdown",
                    )
                except FloodWaitError as e:
                    login_states.pop(uid, None)
                    await msg.answer(
                        f"⏳ Bahut zyada galat attempts. {e.seconds // 60} minute baad try karo.",
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    login_states.pop(uid, None)
                    await msg.answer(f"❌ Error: {e}\n\nDobara /login karo.", parse_mode="Markdown")
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

        # ---- LOGIN ----
        if data == "do_login":
            if await is_user_logged_in(uid):
                await cb.message.edit_text("Pehle se logged in ho! /start karo.")
                return
            login_states[uid] = {"step": "phone", "phone": None, "phone_hash": None}
            await cb.message.edit_text(
                "📱 *Login — Step 1/3*\n\n"
                "Apna phone number dalo (country code ke saath):\n"
                "Example: +919876543210",
                parse_mode="Markdown",
            )
            await cb.answer()
            return

        # ---- LOGOUT CONFIRM/CANCEL ----
        if data == "logout_confirm":
            await logout_user(uid)
            login_states.pop(uid, None)
            await cb.message.edit_text(
                "✅ *Logout ho gaye!*\n\n/login karke dobara login karo.",
                parse_mode="Markdown",
            )
            await cb.answer()
            return

        if data == "logout_cancel":
            await cb.message.edit_text(
                "✅ Logout cancel ho gaya. Bot use karte raho!",
                reply_markup=kb_main_menu_only(),
            )
            await cb.answer()
            return

        # ---- SUBSCRIBE ----
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

        # ---- CHECK PAYMENT ----
        if data == "check_payment":
            fresh = await db.get_user(uid)
            if fresh:
                now = datetime.utcnow()
                if fresh.sub_end and fresh.sub_end > now:
                    await cb.answer("✅ Payment confirm ho gayi! Ab bot use kar sakte ho.", show_alert=True)
                else:
                    await cb.answer("❌ Payment abhi confirm nahi hui. Pay karo ya thodi der baad check karo.", show_alert=True)
            return

        # ---- STATUS MENU ----
        if data == "status_groups":
            if not allowed:
                await cb.answer("Access nahi hai!", show_alert=True)
                return
            text = await _text_status(uid)
            await cb.message.answer(text, parse_mode="Markdown", reply_markup=kb_status())
            await cb.answer()
            return

        if data == "status_sub":
            text = _sub_status_text(user, reason)
            await cb.message.answer(
                text,
                parse_mode="Markdown",
                reply_markup=kb_subscribe_only() if reason in ("expired", "trial") else kb_main_menu_only(),
            )
            await cb.answer()
            return

        # ---- STARTALL CONFIRM/CANCEL ----
        if data == "startall_confirm":
            if not allowed:
                await cb.answer("Access nahi hai!", show_alert=True)
                return
            groups = await db.get_user_groups(uid)
            count = 0
            for g in groups:
                if any(ch.type == "incoming" for ch in g.channels) and any(ch.type == "outgoing" for ch in g.channels):
                    await db.set_group_active(g.id, True)
                    count += 1
            await cb.message.edit_text(
                f"▶️ *{count} group(s) start ho gaye!*\n\nForwarding shuru ho gayi.",
                parse_mode="Markdown",
                reply_markup=kb_main_menu_only(),
            )
            await cb.answer()
            return

        if data == "startall_cancel":
            await cb.message.edit_text("✅ Cancel ho gaya.", reply_markup=kb_main_menu_only())
            await cb.answer()
            return

        # ---- STOPALL CONFIRM/CANCEL ----
        if data == "stopall_confirm":
            await db.set_group_active_for_user(uid, False)
            await cb.message.edit_text(
                "⏹ *Sab groups band ho gaye!*",
                parse_mode="Markdown",
                reply_markup=kb_main_menu_only(),
            )
            await cb.answer()
            return

        if data == "stopall_cancel":
            await cb.message.edit_text("✅ Cancel ho gaya.", reply_markup=kb_main_menu_only())
            await cb.answer()
            return

        # ---- BROADCAST (owner only) ----
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

        # ---- ACCESS CHECK for remaining callbacks ----
        if not allowed:
            await cb.answer("Access nahi hai! /subscribe karo.", show_alert=True)
            return

        if not await is_user_logged_in(uid):
            if data not in ("mm", "hl", "subscribe"):
                await cb.answer("Pehle /login karo!", show_alert=True)
                return

        # ---- MAIN MENU ----
        if data == "mm":
            user = await db.get_or_create_user(uid, cb.from_user.username or "", cb.from_user.full_name or "")
            _, reason = db.check_access(user)
            await cb.message.edit_text(
                "🏠 *DealsKoti Forward Bot*\n\n"
                f"_{_status_line(user, reason)}_\n\n"
                "Option choose karo:",
                parse_mode="Markdown",
                reply_markup=kb_main(),
            )

        elif data == "dm":
            await cb.message.delete()

        elif data == "st":
            await cb.message.edit_text(
                "📊 *Status*\n\nKya dekhna hai?",
                parse_mode="Markdown",
                reply_markup=kb_status_menu(),
            )

        elif data == "hl":
            await cb.message.answer(
                "❓ *Quick Help*\n\n"
                "1. Manage Groups → New Group\n"
                "2. Incoming channel select → Confirm\n"
                "3. Outgoing channel select → Confirm\n"
                "4. Start Forwarding!\n\n"
                f"Max {MAX_GROUPS} groups | Top 20 channels (pinned pehle)\n"
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

        elif data == "rename_prompt":
            groups = await _groups_to_dict(uid)
            if not groups:
                await cb.answer("Pehle ek group banao!", show_alert=True)
                return
            if len(groups) == 1:
                gid = groups[0]["id"]
                g = await db.get_group(gid)
                user_state[uid] = {"action": "rename", "group_id": gid}
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("❌ Cancel", callback_data="grp:" + str(gid)))
                await cb.message.edit_text(
                    f"*{groups[0]['name']}* ka naya naam type karo (max 30 chars):",
                    parse_mode="Markdown",
                    reply_markup=kb,
                )
            else:
                await cb.message.edit_text(
                    "*Rename* — Pehle group select karo:",
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
            await cb.answer("Channels load ho rahe hain...")
            await load_dialogs(uid)
            dialogs = _get_dialogs(uid)
            if not dialogs:
                await cb.message.answer(
                    "⚠️ Koi channel nahi mila!\n\n"
                    "Possible reasons:\n"
                    "- Aapke account mein koi channel/group nahi\n"
                    "- Pehle kuch channels ko pin karo Telegram mein\n\n"
                    "Telegram mein kisi channel pe jaao → Pin karo → phir wapas aao.",
                    reply_markup=kb_group(gid, g.is_active),
                )
                return
            existing = await db.get_channels(gid, "incoming" if mode == "in" else "outgoing")
            selected = {ch.channel_id for ch in existing}
            _set_selected(uid, gid, mode, selected)
            label = "Incoming" if mode == "in" else "Outgoing"
            text = (
                f"*{g.name} — {label}*\n\n"
                "📌 Pinned channels pehle dikhte hain\n"
                "Number dabao to select/deselect karo:\n\n"
                + _text_channel_list(uid, gid, mode)
            )
            await cb.message.edit_text(
                text,
                parse_mode="Markdown",
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
                    "📌 Pinned channels pehle dikhte hain\n"
                    "Number dabao to select/deselect karo:\n\n"
                    + _text_channel_list(uid, gid, mode)
                )
                await cb.message.edit_text(
                    text,
                    parse_mode="Markdown",
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
            await cb.message.edit_text(
                f"*{g.name} — {label}*\n\nSab select ho gaye!\n\n" + _text_channel_list(uid, gid, mode),
                parse_mode="Markdown",
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
            await cb.message.edit_text(
                f"*{g.name} — {label}*\n\nSelection clear ho gayi!\n\n" + _text_channel_list(uid, gid, mode),
                parse_mode="Markdown",
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

            # ---- VALIDATION ----
            await cb.answer("Checking permissions...")
            failed = []
            for did in selected:
                ch_name = name_map.get(did, str(did))
                if mode == "in":
                    ok, result = await check_is_member(uid, did)
                    if not ok:
                        if result == "private_channel":
                            failed.append(
                                f"❌ *{ch_name}*\n"
                                "   → Ye private channel hai. Pehle is channel ko join karo, tab select karo."
                            )
                        else:
                            failed.append(
                                f"❌ *{ch_name}*\n"
                                "   → Channel access nahi mila. Pehle join karo."
                            )
                else:
                    ok, result = await check_can_post(uid, did)
                    if not ok:
                        if result.startswith("no_permission:"):
                            name = result.split(":", 1)[1]
                            failed.append(
                                f"❌ *{name}*\n"
                                "   → Aapko is group/channel ka *Admin* banana padega.\n"
                                "   → Telegram mein jaao → Group → Admin banao → Wapas aao."
                            )
                        elif result.startswith("not_member:"):
                            name = result.split(":", 1)[1]
                            failed.append(
                                f"❌ *{name}*\n"
                                "   → Aap is group ke member nahi hain. Pehle join karo."
                            )
                        else:
                            failed.append(
                                f"❌ *{ch_name}*\n"
                                f"   → Permission error. Group mein admin rights do."
                            )

            if failed:
                label = "Incoming" if mode == "in" else "Outgoing"
                err_lines = "\n\n".join(failed)
                if mode == "in":
                    guide = (
                        "📌 *Incoming channel ke liye:*\n"
                        "Jis channel ke messages forward karne hain, us channel mein *aap (jis number se login kiya) joined hona chahiye.*\n\n"
                        "Channel join karo → Wapas aao → Dobara select karo"
                    )
                else:
                    guide = (
                        "📌 *Outgoing group ke liye:*\n"
                        "Jis group mein messages forward karne hain, usme *aapko Admin banana padega.*\n\n"
                        "Group → Members → Apna number dhundo → Admin banao → Wapas aao → Dobara select karo"
                    )
                await cb.message.edit_text(
                    f"⚠️ *{label} Validation Failed!*\n\n"
                    f"{err_lines}\n\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"{guide}",
                    parse_mode="Markdown",
                    reply_markup=kb_group(gid, g.is_active),
                )
                return

            # ---- SAVE ----
            ch_type = "incoming" if mode == "in" else "outgoing"
            channel_list = [(did, name_map.get(did, str(did))) for did in selected]
            await db.set_channels(gid, ch_type, channel_list)
            _clear_selected(uid, gid, mode)
            count = len(selected)
            names = "\n".join("- " + name_map.get(d, str(d)) for d in selected)
            if mode == "in":
                await cb.message.edit_text(
                    f"✅ *Incoming Confirmed!*\n\n{count} channel(s) set:\n{names}\n\nAb outgoing group set karo jisme forward karna hai.",
                    parse_mode="Markdown",
                    reply_markup=kb_after_incoming(gid),
                )
            else:
                await cb.message.edit_text(
                    f"✅ *Outgoing Confirmed!*\n\n{count} group(s) set:\n{names}\n\nAb forwarding start karo!",
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
                f"*{g.name}* delete karna chahte ho?\n\nYe action undo nahi hogi!",
                parse_mode="Markdown",
                reply_markup=kb_delete_confirm(gid),
            )

        elif data.startswith("gdf:"):
            gid = int(data[4:])
            g = await db.get_group(gid)
            if not g or g.user_id != uid:
                return
            await db.delete_group(gid)
            groups = await _groups_to_dict(uid)
            if not groups:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("➕ New Group", callback_data="ng"))
                kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
                await cb.message.edit_text(
                    "✅ *Group delete ho gaya!*\n\nKoi group nahi bacha.",
                    parse_mode="Markdown",
                    reply_markup=kb,
                )
            else:
                await cb.message.edit_text(
                    "✅ *Group delete ho gaya!*\n\n*Baaki Groups:*",
                    parse_mode="Markdown",
                    reply_markup=kb_groups(groups),
                )

        elif data == "sa":
            groups = await db.get_user_groups(uid)
            count = 0
            for g in groups:
                if any(ch.type == "incoming" for ch in g.channels) and any(ch.type == "outgoing" for ch in g.channels):
                    await db.set_group_active(g.id, True)
                    count += 1
            await cb.answer(f"▶️ {count} groups start ho gaye!", show_alert=True)

        elif data == "xa":
            await db.set_group_active_for_user(uid, False)
            await cb.answer("⏹ Sab groups band ho gaye!", show_alert=True)

        await cb.answer()
