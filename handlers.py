"""
Main bot handlers — all user-facing commands and callbacks.
"""
import re
import logging
from datetime import datetime

from aiogram import types, Bot
from aiogram.dispatcher import Dispatcher

import database as db
from config import (
    OWNER_ID, MAIN_BOT_USERNAME, SIMI_BOT_USERNAME,
    get_plan_limits, has_feature, plan_display_name,
    AFFILIATE_MIN_WITHDRAW_USD, DISPLAY_LIMIT,
)
from forwarder import (
    user_clients, user_dialogs,
    create_client_for_login, finalize_login,
    logout_user, is_user_logged_in,
    load_dialogs, resolve_channel,
)
from keyboards import (
    kb_main, kb_main_only, kb_login, kb_status_menu,
    kb_task_list, kb_task, kb_task_delete_confirm,
    kb_filters, kb_message_settings, kb_advanced_settings,
    kb_media_filter, kb_plans, kb_plan_billing, kb_payment_link,
    kb_startall_confirm, kb_stopall_confirm, kb_logout_confirm,
    kb_refer,
)
from payments import create_order
from admin import broadcast_state, admin_msg_state

logger = logging.getLogger(__name__)

# ── State dicts ──
login_states: dict[int, dict] = {}       # user_id → {step, phone, phone_hash, client}
user_state: dict[int, dict] = {}         # user_id → {action, task_id, ...}
temp_selection: dict[int, dict] = {}     # channel selection


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def _access_denied_text(reason: str) -> str:
    if reason == "banned":
        return "🚫 Tumhara account band kar diya gaya hai. Support se sampark karo."
    return "Kuch galat hua. /start karo."


def _status_line(user, reason: str) -> str:
    if "subscribed" in reason:
        parts = reason.split(":")
        plan_n = plan_display_name(parts[1]) if len(parts) > 1 else ""
        days_left = int(parts[2]) if len(parts) > 2 else 0
        return f"{plan_n} | {days_left} din bache"
    elif reason == "free":
        return "🆓 Free Plan"
    return ""


def _plan_days_left(user) -> int:
    if user.plan_end:
        delta = (user.plan_end - datetime.utcnow()).days
        return max(0, delta)
    return 0


def _get_dialogs_limited(uid: int) -> list[tuple[int, str]]:
    return user_dialogs.get(uid, [])[:DISPLAY_LIMIT]


async def _tasks_to_dict(uid: int) -> list[dict]:
    tasks = await db.get_user_tasks(uid)
    result = []
    for t in tasks:
        in_ids = {ch.channel_id for ch in t.channels if ch.type == "source"}
        out_ids = {ch.channel_id for ch in t.channels if ch.type == "target"}
        result.append({
            "id": t.id, "name": t.name, "active": t.is_active,
            "sources": in_ids, "targets": out_ids,
        })
    return result


async def _text_status(uid: int) -> str:
    tasks = await _tasks_to_dict(uid)
    if not tasks:
        return "*Status*\n\nKoi task nahi hai. Manage Tasks se banao!"
    dialogs = user_dialogs.get(uid, [])
    name_map = {d[0]: d[1] for d in dialogs}
    lines = ["*Forwarding Status*\n"]
    for t in tasks:
        status = "🟢 Running" if t["active"] else "🔴 Stopped"
        in_names = ", ".join(name_map.get(d, str(d)) for d in t["sources"]) or "-"
        out_names = ", ".join(name_map.get(d, str(d)) for d in t["targets"]) or "-"
        lines.append(f"*{t['name']}* — {status}")
        lines.append(f"  📥 Source: {in_names}")
        lines.append(f"  📤 Target: {out_names}\n")
    return "\n".join(lines)


async def _text_sub_status(user, reason: str) -> str:
    if user.is_banned:
        return "🚫 Account banned hai."
    if "subscribed" in reason:
        parts = reason.split(":")
        plan_n = plan_display_name(parts[1]) if len(parts) > 1 else ""
        days_left = parts[2] if len(parts) > 2 else "?"
        end_str = user.plan_end.strftime("%d %b %Y") if user.plan_end else "N/A"
        return (
            f"💳 *Subscription Status*\n\n"
            f"✅ Active\nPlan: {plan_n}\n"
            f"Expires: {end_str}\nDin bache: {days_left}"
        )
    return (
        "💳 *Subscription Status*\n\n"
        "🆓 Free Plan\n\n"
        "Upgrade karo more features ke liye!\n/subscribe"
    )


def _channel_list_text(uid: int) -> str:
    dialogs = _get_dialogs_limited(uid)
    if not dialogs:
        return "Koi channel nahi mila."
    lines = []
    for i, (did, dn) in enumerate(dialogs):
        lines.append(f"{i + 1}. {dn}")
    footer = (
        f"\n_Shown: {len(dialogs)} channels_\n"
        f"⚠️ Agar channel nahi dikh raha:\n"
        f"@username ya https://t.me/channel type karo"
    )
    return "\n".join(lines) + footer


async def _notify_admin_start(bot: Bot, user, bot_name: str):
    username = f"@{user.username}" if user.username else f"ID:{user.user_id}"
    try:
        await bot.send_message(
            OWNER_ID,
            f"👤 {username} ne *@{bot_name}* start kiya!\n"
            f"Name: {user.full_name or '-'}\n"
            f"ID: `{user.user_id}`",
            parse_mode="Markdown",
        )
    except Exception:
        pass


# ──────────────────────────────────────────────
# REGISTER HANDLERS
# ──────────────────────────────────────────────

def register_handlers(dp: Dispatcher, bot: Bot):

    # ════════════════ /start ════════════════
    @dp.message_handler(commands=["start"])
    async def cmd_start(msg: types.Message):
        uid = msg.from_user.id
        args = msg.get_args()

        user, is_new = await db.get_or_create_user(
            uid, msg.from_user.username or "", msg.from_user.full_name or ""
        )

        # Admin notification — only for new users
        if is_new:
            await _notify_admin_start(bot, user, MAIN_BOT_USERNAME)

        # Referral tracking
        if args and is_new:
            aff = await db.get_affiliate_by_code(args.upper())
            if aff and aff.user_id != uid:
                await db.set_referred_by(uid, aff.user_id)

        allowed, reason = db.check_access(user)
        if not allowed:
            await msg.answer(_access_denied_text(reason))
            return

        if not is_user_logged_in(uid):
            await msg.answer(
                f"👋 *Sandesh Forward Bot*\n\n"
                f"Messages forward karo bina 'Forwarded from' tag ke!\n\n"
                f"_{_status_line(user, reason)}_\n\n"
                f"Shuru karne ke liye login karo:",
                parse_mode="Markdown",
                reply_markup=kb_login(),
            )
            return

        days_left = _plan_days_left(user)
        await msg.answer(
            f"🏠 *Sandesh Forward Bot*\n\n"
            f"_{_status_line(user, reason)}_\n\n"
            f"Option choose karo:",
            parse_mode="Markdown",
            reply_markup=kb_main(user.plan, days_left),
        )

    # ════════════════ /login ════════════════
    @dp.message_handler(commands=["login"])
    async def cmd_login(msg: types.Message):
        uid = msg.from_user.id
        if is_user_logged_in(uid):
            await msg.answer(
                "✅ Already logged in ho! /start se menu dekho.",
                reply_markup=kb_main_only(),
            )
            return
        login_states[uid] = {"step": "phone"}
        await msg.answer(
            "📱 *Login — Step 1/3*\n\n"
            "Phone number dalo (country code ke saath):\n"
            "🇮🇳 India:  +919876543210\n"
            "🇺🇸 USA:    +12025551234\n"
            "🇬🇧 UK:     +447911123456\n\n"
            "⚠️ Dhyan rakho:\n"
            "- Plus (+) se shuru karo\n"
            "- Numbers ke beech koi *space mat dalo*",
            parse_mode="Markdown",
        )

    # ════════════════ /logout ════════════════
    @dp.message_handler(commands=["logout"])
    async def cmd_logout(msg: types.Message):
        uid = msg.from_user.id
        if not is_user_logged_in(uid):
            await msg.answer("Already logged out ho. /login se login karo.")
            return
        await msg.answer(
            "🚪 *Logout*\n\n"
            "Logout karne se:\n"
            "- Teri session delete ho jaayegi\n"
            "- Saari forwarding band ho jaayegi\n"
            "- Tasks/Settings save rahenge\n\n"
            "Pakka logout karna hai?",
            parse_mode="Markdown",
            reply_markup=kb_logout_confirm(),
        )

    # ════════════════ /status ════════════════
    @dp.message_handler(commands=["status"])
    async def cmd_status(msg: types.Message):
        await msg.answer(
            "📊 *Status*\n\nKya dekhna hai?",
            parse_mode="Markdown",
            reply_markup=kb_status_menu(),
        )

    # ════════════════ /tasks ════════════════
    @dp.message_handler(commands=["tasks"])
    async def cmd_tasks(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_user(uid)
        if not user:
            return
        allowed, reason = db.check_access(user)
        if not allowed:
            await msg.answer(_access_denied_text(reason))
            return
        tasks = await _tasks_to_dict(uid)
        if not tasks:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("➕ New Task", callback_data="ng"))
            kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
            await msg.answer("*Tasks*\n\nKoi task nahi hai. Naya banao!", parse_mode="Markdown", reply_markup=kb)
        else:
            await msg.answer("*Tasks*\n\nTask select karo:", parse_mode="Markdown", reply_markup=kb_task_list(tasks))

    # ════════════════ /startall ════════════════
    @dp.message_handler(commands=["startall"])
    async def cmd_startall(msg: types.Message):
        uid = msg.from_user.id
        tasks = await db.get_user_tasks(uid)
        count = 0
        for t in tasks:
            if any(ch.type == "source" for ch in t.channels) and any(ch.type == "target" for ch in t.channels):
                await db.set_task_active(t.id, True)
                count += 1
        await msg.answer(f"▶️ *{count} task(s) start ho gaye!*", parse_mode="Markdown", reply_markup=kb_main_only())

    # ════════════════ /stopall ════════════════
    @dp.message_handler(commands=["stopall"])
    async def cmd_stopall(msg: types.Message):
        uid = msg.from_user.id
        await db.set_all_tasks_active(uid, False)
        await msg.answer("⏹ *Saare tasks band ho gaye!*", parse_mode="Markdown", reply_markup=kb_main_only())

    # ════════════════ /myplan ════════════════
    @dp.message_handler(commands=["myplan"])
    async def cmd_myplan(msg: types.Message):
        uid = msg.from_user.id
        user = await db.get_user(uid)
        if not user:
            return
        _, reason = db.check_access(user)
        await msg.answer(
            await _text_sub_status(user, reason),
            parse_mode="Markdown",
            reply_markup=kb_plans(),
        )

    # ════════════════ /subscribe ════════════════
    @dp.message_handler(commands=["subscribe"])
    async def cmd_subscribe(msg: types.Message):
        await msg.answer(
            "💳 *Plans*\n\nKaunsa plan lena hai?",
            parse_mode="Markdown",
            reply_markup=kb_plans(),
        )

    # ════════════════ /help ════════════════
    @dp.message_handler(commands=["help"])
    async def cmd_help(msg: types.Message):
        uid = msg.from_user.id
        text = (
            "❓ *Help — Sandesh Forward Bot*\n\n"
            "━━━━━━━━━━━━━━━━━\n"
            "*Login Kaise Kare:*\n"
            "1. /login bhejo\n"
            "2. Phone: +919876543210\n"
            "3. OTP aaye to: word+OTP (code12345)\n"
            "4. 2FA? password dalo\n\n"
            "━━━━━━━━━━━━━━━━━\n"
            "*Commands:*\n"
            "/start — Main menu\n"
            "/login — Login\n"
            "/logout — Logout\n"
            "/tasks — Tasks manage\n"
            "/status — Forwarding status\n"
            "/startall — Saare tasks start\n"
            "/stopall — Saare tasks stop\n"
            "/myplan — Plan details\n"
            "/subscribe — Plans dekho\n"
            "/refer — Refer & Earn\n"
            "/help — Ye guide\n\n"
            "━━━━━━━━━━━━━━━━━\n"
            "*Support:*\n"
            f"AI Help: @{SIMI_BOT_USERNAME}"
        )
        await msg.answer(text, parse_mode="Markdown", reply_markup=kb_main_only())

    # ════════════════ /support ════════════════
    @dp.message_handler(commands=["support"])
    async def cmd_support(msg: types.Message):
        await msg.answer(
            f"🤖 *Support chahiye?*\n\n"
            f"AI support ke liye:\n"
            f"👉 @{SIMI_BOT_USERNAME}\n\n"
            f"Simi tumhari madad karegi — login, tasks, plans, sab kuch!",
            parse_mode="Markdown",
            reply_markup=kb_main_only(),
        )

    # ════════════════ TEXT HANDLER ════════════════
    @dp.message_handler()
    async def text_handler(msg: types.Message):
        uid = msg.from_user.id
        text = (msg.text or "").strip()

        # Skip if admin is in broadcast/msg state — handled by admin.py
        if uid == OWNER_ID and (uid in broadcast_state or uid in admin_msg_state):
            return

        user = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        user = user[0]

        # ── LOGIN FLOW ──
        ls = login_states.get(uid)
        if ls:
            await _handle_login_text(msg, uid, text, ls, bot)
            return

        # ── USER STATE: rename task ──
        us = user_state.get(uid)
        if us and us.get("action") == "rename":
            task_id = us["task_id"]
            if len(text) > 30:
                await msg.answer("❌ Naam max 30 characters ka hona chahiye. Dobara type karo:")
                return
            await db.rename_task(task_id, text)
            user_state.pop(uid, None)
            task = await db.get_task(task_id)
            tasks = await _tasks_to_dict(uid)
            await msg.answer(
                f"✅ Task rename ho gaya: *{text}*",
                parse_mode="Markdown",
                reply_markup=kb_task_list(tasks),
            )
            return

        # ── USER STATE: channel input (@username or link) ──
        if us and us.get("action") in ("set_source", "set_target"):
            await _handle_channel_identifier(msg, uid, text, us, bot, user)
            return

        # ── USER STATE: filter/settings text inputs ──
        if us and us.get("action"):
            await _handle_settings_text(msg, uid, text, us, bot)
            return

        # Default
        await msg.answer(
            "Menu ke liye /start bhejo.",
            reply_markup=kb_main_only(),
        )


    # ════════════════ LOGIN HELPER ════════════════

    async def _handle_login_text(msg, uid, text, ls, bot):
        from telethon.errors import (
            SessionPasswordNeededError, PhoneCodeInvalidError,
            PhoneCodeExpiredError, PasswordHashInvalidError,
            FloodWaitError, PhoneNumberInvalidError,
            PhoneNumberBannedError,
        )

        step = ls.get("step")

        if step == "phone":
            phone = text.strip()
            # Validate phone
            clean_phone = phone.replace(" ", "").replace("-", "")
            if " " in phone or "-" in phone:
                await msg.answer(
                    "❌ *Number mein space ya dash nahi hona chahiye!*\n\n"
                    f"Tumne diya: `{phone}`\n"
                    f"Sahi format: `{clean_phone}`\n\n"
                    "Dobara bhejo (spaces hata ke):",
                    parse_mode="Markdown",
                )
                return
            if not clean_phone.startswith("+") or not clean_phone[1:].isdigit():
                await msg.answer(
                    "❌ *Phone format galat hai!*\n\n"
                    "Country code ke saath dalo:\n"
                    "🇮🇳 +919876543210\n"
                    "🇺🇸 +12025551234\n\n"
                    "Dobara bhejo:",
                    parse_mode="Markdown",
                )
                return
            try:
                client = await create_client_for_login(uid)
                result = await client.send_code_request(clean_phone)
                ls["phone"] = clean_phone
                ls["phone_hash"] = result.phone_code_hash
                ls["step"] = "otp"
                await msg.answer(
                    "📨 *Login — Step 2/3*\n\n"
                    "✅ OTP Telegram pe bhej diya!\n\n"
                    "⚠️ *OTP seedha mat type karo!*\n"
                    "Koi bhi word + OTP likho:\n"
                    "Example: `code12345` ya `hello12345`\n\n"
                    "_Seedha OTP doge to Telegram error dega_",
                    parse_mode="Markdown",
                )
            except PhoneNumberInvalidError:
                await msg.answer("❌ Ye phone number Telegram pe register nahi hai. Dobara try karo:")
            except PhoneNumberBannedError:
                await msg.answer("❌ Ye number Telegram pe banned hai.")
                login_states.pop(uid, None)
            except FloodWaitError as e:
                await msg.answer(f"⏳ Bahut tries ho gaye. {e.seconds} seconds baad try karo.")
                login_states.pop(uid, None)
            except Exception as e:
                await msg.answer(f"❌ Error: {str(e)[:200]}")
            return

        if step == "otp":
            digits = re.sub(r"\D", "", text)
            if not digits:
                await msg.answer("❌ OTP mein digits nahi mile. `code12345` format mein do.")
                return
            try:
                client = list(
                    c for uid2, c in __import__("forwarder")._login_clients.items()
                    if uid2 == uid
                )[0] if uid in __import__("forwarder")._login_clients else None
                if not client:
                    await msg.answer("❌ Session expire. /login se dobara try karo.")
                    login_states.pop(uid, None)
                    return
                await client.sign_in(ls["phone"], digits, phone_code_hash=ls["phone_hash"])
                await finalize_login(uid)
                login_states.pop(uid, None)
                user, _ = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
                days_left = _plan_days_left(user)
                _, reason = db.check_access(user)
                await msg.answer(
                    "✅ *Login Ho Gaya!*\n\nAb forwarding setup karo:",
                    parse_mode="Markdown",
                    reply_markup=kb_main(user.plan, days_left),
                )
            except SessionPasswordNeededError:
                ls["step"] = "2fa"
                await msg.answer(
                    "🔐 *Login — Step 3/3*\n\n"
                    "Tumhara 2FA (Two-Factor Authentication) enable hai.\n"
                    "Apna password dalo:",
                    parse_mode="Markdown",
                )
            except (PhoneCodeInvalidError, PhoneCodeExpiredError):
                await msg.answer(
                    "❌ *OTP galat ya expire ho gaya!*\n\n"
                    "/login se dobara try karo.",
                    parse_mode="Markdown",
                )
                login_states.pop(uid, None)
            except Exception as e:
                await msg.answer(f"❌ Error: {str(e)[:200]}")
            return

        if step == "2fa":
            try:
                from forwarder import _login_clients
                client = _login_clients.get(uid)
                if not client:
                    await msg.answer("❌ Session expire. /login se dobara try karo.")
                    login_states.pop(uid, None)
                    return
                await client.sign_in(password=text)
                await finalize_login(uid)
                login_states.pop(uid, None)
                user, _ = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
                days_left = _plan_days_left(user)
                _, reason = db.check_access(user)
                await msg.answer(
                    "✅ *Login Ho Gaya!*\n\nAb forwarding setup karo:",
                    parse_mode="Markdown",
                    reply_markup=kb_main(user.plan, days_left),
                )
            except Exception as e:
                if "PASSWORD" in str(e).upper() or "password" in str(e).lower():
                    await msg.answer("❌ Password galat hai. Dobara try karo:")
                else:
                    await msg.answer(f"❌ Error: {str(e)[:200]}")
            return


    async def _handle_channel_identifier(msg, uid, text, us, bot, user):
        """Handle @username or t.me link typed by user for channel lookup."""
        task_id = us["task_id"]
        ch_type = "source" if us["action"] == "set_source" else "target"

        # Check if it's an identifier (starts with @ or http)
        is_identifier = text.startswith("@") or text.startswith("http")
        if is_identifier:
            await msg.answer("🔍 Channel dhundh raha hun...")
            result = await resolve_channel(uid, text)
            if not result:
                await msg.answer(
                    "❌ Channel nahi mila.\n"
                    "Check karo:\n"
                    "- Channel public hai?\n"
                    "- Username sahi hai?\n"
                    "- Bot/User us channel ka member hai?\n\n"
                    "Dobara try karo ya list se number select karo:",
                )
                return
            channel_id, channel_name = result
            # Save this single channel
            plan = user.plan
            limits = get_plan_limits(plan)
            max_allowed = limits["max_sources"] if ch_type == "source" else limits["max_targets"]

            task = await db.get_task(task_id)
            existing = [ch for ch in task.channels if ch.type == ch_type]
            if len(existing) >= max_allowed:
                await msg.answer(
                    f"❌ Tumhare plan mein max {max_allowed} {ch_type} channels allowed hain.\n"
                    f"Upgrade karo /subscribe"
                )
                return

            await db.set_task_channels(task_id, ch_type, [(channel_id, channel_name)] + [(c.channel_id, c.channel_name) for c in existing])
            user_state.pop(uid, None)
            await msg.answer(
                f"✅ *{channel_name}* {ch_type} mein add ho gaya!",
                parse_mode="Markdown",
                reply_markup=kb_task(task_id, task.is_active, plan),
            )
            return

        # Number selection from list
        await _handle_channel_numbers(msg, uid, text, us, bot, user)


    async def _handle_channel_numbers(msg, uid, text, us, bot, user):
        task_id = us["task_id"]
        ch_type = "source" if us["action"] == "set_source" else "target"
        plan = user.plan
        limits = get_plan_limits(plan)
        max_allowed = limits["max_sources"] if ch_type == "source" else limits["max_targets"]

        dialogs = _get_dialogs_limited(uid)
        try:
            nums = [int(n.strip()) for n in text.replace(",", " ").split() if n.strip().isdigit()]
        except ValueError:
            await msg.answer("❌ Sirf numbers type karo. Example: `1,3,5`", parse_mode="Markdown")
            return

        if not nums:
            await msg.answer("❌ Koi valid number nahi mila. Example: `1,3,5`", parse_mode="Markdown")
            return

        if len(nums) > max_allowed:
            await msg.answer(
                f"❌ Tumhare plan mein max *{max_allowed}* {ch_type} channels allowed hain.\n"
                f"Sirf {max_allowed} number select karo.",
                parse_mode="Markdown",
            )
            return

        selected = []
        for n in nums:
            if 1 <= n <= len(dialogs):
                selected.append(dialogs[n - 1])

        if not selected:
            await msg.answer("❌ Koi valid channel nahi mila. Number list ke andar hona chahiye.")
            return

        # Validate: source != target
        task = await db.get_task(task_id)
        opposite_type = "target" if ch_type == "source" else "source"
        opposite_ids = {ch.channel_id for ch in task.channels if ch.type == opposite_type}
        selected_ids = {s[0] for s in selected}
        conflict = selected_ids & opposite_ids
        if conflict:
            await msg.answer("❌ Same channel source aur target dono nahi ho sakta!")
            return

        await db.set_task_channels(task_id, ch_type, selected)
        user_state.pop(uid, None)

        names = "\n".join(f"  {i+1}. {s[1]}" for i, s in enumerate(selected))
        task = await db.get_task(task_id)
        await msg.answer(
            f"✅ *{ch_type.capitalize()} channels set ho gaye!*\n\n{names}",
            parse_mode="Markdown",
            reply_markup=kb_task(task_id, task.is_active, plan),
        )


    async def _handle_settings_text(msg, uid, text, us, bot):
        action = us.get("action")
        task_id = us.get("task_id")
        user = await db.get_user(uid)
        plan = user.plan if user else "free"

        if action == "set_header":
            await db.update_task_settings(task_id, header_text=text)
            user_state.pop(uid, None)
            task = await db.get_task(task_id)
            await msg.answer("✅ Header set ho gaya!", reply_markup=kb_message_settings(task_id, plan))

        elif action == "set_footer":
            await db.update_task_settings(task_id, footer_text=text)
            user_state.pop(uid, None)
            await msg.answer("✅ Footer set ho gaya!", reply_markup=kb_message_settings(task_id, plan))

        elif action == "set_caption":
            await db.update_task_settings(task_id, custom_caption=text)
            user_state.pop(uid, None)
            await msg.answer(
                "✅ Custom caption set ho gaya!\n_Tip: {original_caption} placeholder use kar sakte ho_",
                parse_mode="Markdown",
                reply_markup=kb_message_settings(task_id, plan),
            )

        elif action == "set_blacklist":
            words = [w.strip() for w in text.replace(",", "\n").splitlines() if w.strip()]
            task = await db.get_task(task_id)
            existing = task.blacklist_words or []
            merged = list(set(existing + words))
            await db.update_task_settings(task_id, blacklist_words=merged)
            user_state.pop(uid, None)
            await msg.answer(f"✅ {len(words)} words blacklist mein add ho gaye!", reply_markup=kb_filters(task_id, plan))

        elif action == "set_whitelist":
            words = [w.strip() for w in text.replace(",", "\n").splitlines() if w.strip()]
            task = await db.get_task(task_id)
            existing = task.whitelist_words or []
            merged = list(set(existing + words))
            await db.update_task_settings(task_id, whitelist_words=merged)
            user_state.pop(uid, None)
            await msg.answer(f"✅ {len(words)} words whitelist mein add ho gaye!", reply_markup=kb_filters(task_id, plan))

        elif action == "set_regex":
            import re as re_module
            try:
                re_module.compile(text)
                await db.update_task_settings(task_id, regex_pattern=text)
                user_state.pop(uid, None)
                await msg.answer("✅ Regex set ho gaya!", reply_markup=kb_filters(task_id, plan))
            except re_module.error as e:
                await msg.answer(f"❌ Regex invalid hai: {e}\n\nDobara try karo:")

        elif action == "set_delay":
            try:
                seconds = float(text)
                await db.update_task_settings(task_id, delay_mode="fixed", delay_seconds=seconds)
                user_state.pop(uid, None)
                task = await db.get_task(task_id)
                await msg.answer(
                    f"✅ Fixed delay set: {seconds}s",
                    reply_markup=kb_advanced_settings(task_id, plan, task)
                )
            except ValueError:
                await msg.answer("❌ Sirf number dalo. Example: 5 ya 2.5")

        elif action == "set_delay_random":
            parts = text.replace("-", " ").split()
            try:
                mn, mx = float(parts[0]), float(parts[1])
                await db.update_task_settings(task_id, delay_mode="random", delay_random_min=mn, delay_random_max=mx)
                user_state.pop(uid, None)
                task = await db.get_task(task_id)
                await msg.answer(
                    f"✅ Random delay set: {mn}s - {mx}s",
                    reply_markup=kb_advanced_settings(task_id, plan, task)
                )
            except (ValueError, IndexError):
                await msg.answer("❌ Format: `min max` Example: `3 8`", parse_mode="Markdown")

        elif action == "set_word_replace":
            # Format: "original → new" or "original - new"
            if "→" in text:
                parts_split = text.split("→", 1)
            elif " - " in text:
                parts_split = text.split(" - ", 1)
            else:
                await msg.answer("❌ Format: `original → new`\nExample: `Amazon → My Store`", parse_mode="Markdown")
                return
            frm, to = parts_split[0].strip(), parts_split[1].strip()
            task = await db.get_task(task_id)
            pairs = task.word_replace_pairs or []
            pairs.append({"from": frm, "to": to})
            await db.update_task_settings(task_id, word_replace_pairs=pairs)
            user_state.pop(uid, None)
            await msg.answer(f"✅ Replace: `{frm}` → `{to}`", parse_mode="Markdown", reply_markup=kb_filters(task_id, plan))

        elif action == "set_link_replacer":
            lines = text.strip().splitlines()
            if len(lines) < 2:
                await msg.answer("❌ 2 lines chahiye:\nLine 1: Original link\nLine 2: New link")
                return
            orig, new = lines[0].strip(), lines[1].strip()
            await db.add_link_replacer(task_id, orig, new)
            user_state.pop(uid, None)
            await msg.answer(
                f"✅ Link replacer add ho gaya!\n`{orig}`\n→ `{new}`",
                parse_mode="Markdown",
                reply_markup=kb_filters(task_id, plan),
            )

        elif action == "set_watermark":
            await db.update_task_settings(task_id, watermark_text=text, watermark_enabled=True)
            user_state.pop(uid, None)
            task = await db.get_task(task_id)
            await msg.answer(
                f"✅ Watermark set: `{text}`\nPosition: {task.watermark_position}",
                parse_mode="Markdown",
                reply_markup=kb_message_settings(task_id, plan),
            )

        elif action == "set_schedule":
            # Format: "Mon-Fri 09:00-21:00"
            await _parse_schedule(msg, uid, text, task_id, plan)

        else:
            user_state.pop(uid, None)


    async def _parse_schedule(msg, uid, text, task_id, plan):
        import re as re_mod
        text = text.strip()
        DAY_MAP = {
            "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        try:
            time_match = re_mod.search(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})", text)
            start_time = time_match.group(1) if time_match else "00:00"
            end_time = time_match.group(2) if time_match else "23:59"

            day_part = text[:time_match.start()].strip().lower() if time_match else text.lower()
            days = []
            if "everyday" in day_part or "daily" in day_part or not day_part:
                days = list(range(7))
            elif "-" in day_part or "–" in day_part:
                parts = re_mod.split(r"[-–]", day_part)
                start_d = DAY_MAP.get(parts[0].strip(), 0)
                end_d = DAY_MAP.get(parts[-1].strip(), 6)
                days = list(range(start_d, end_d + 1))
            else:
                for word in re_mod.split(r"[,\s]+", day_part):
                    d = DAY_MAP.get(word.strip())
                    if d is not None:
                        days.append(d)

            days_str = ",".join(str(d) for d in sorted(set(days)))
            await db.update_task_settings(
                task_id,
                schedule_enabled=True,
                schedule_days=days_str,
                schedule_start=start_time,
                schedule_end=end_time,
            )
            user_state.pop(uid, None)
            task = await db.get_task(task_id)
            await msg.answer(
                f"✅ Schedule set!\nDays: {days_str}\nTime: {start_time} - {end_time} (IST)",
                reply_markup=kb_advanced_settings(task_id, plan, task)
            )
        except Exception as e:
            await msg.answer(
                f"❌ Format galat hai.\n\nExamples:\n"
                f"`Mon-Fri 09:00-21:00`\n"
                f"`Everyday 08:00-22:00`\n"
                f"`Mon,Wed,Fri 10:00-20:00`",
                parse_mode="Markdown",
            )


    # ════════════════ CALLBACK HANDLER ════════════════

    @dp.callback_query_handler()
    async def callback_handler(cb: types.CallbackQuery):
        uid = cb.from_user.id
        data = cb.data

        user, _ = await db.get_or_create_user(uid, cb.from_user.username or "", cb.from_user.full_name or "")
        allowed, reason = db.check_access(user)
        plan = user.plan

        # Clear user_state on navigation callbacks
        nav_prefixes = ("mm", "tl", "st", "fst", "sst", "hl", "pl", "ref", "rst", "rws")
        if data in nav_prefixes or any(data.startswith(p + ":") for p in nav_prefixes):
            user_state.pop(uid, None)

        # ── MAIN MENU ──
        if data == "mm":
            days_left = _plan_days_left(user)
            await cb.message.edit_text(
                f"🏠 *Sandesh Forward Bot*\n\n_{_status_line(user, reason)}_\n\nOption choose karo:",
                parse_mode="Markdown",
                reply_markup=kb_main(plan, days_left),
            )

        elif data == "dm":
            await cb.message.delete()

        elif data == "hl":
            await cb.message.answer(
                "❓ *Quick Help*\n\n"
                "1. Manage Tasks → New Task\n"
                "2. Source channel select karo\n"
                "3. Target channel select karo\n"
                "4. Start Forwarding!\n\n"
                f"Support: @{SIMI_BOT_USERNAME}",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── STATUS ──
        elif data == "st":
            await cb.message.edit_text("📊 *Status*", parse_mode="Markdown", reply_markup=kb_status_menu())

        elif data == "fst":
            text = await _text_status(uid)
            await cb.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_status_menu())

        elif data == "sst":
            text = await _text_sub_status(user, reason)
            await cb.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_plans())

        # ── LOGIN ──
        elif data == "do_login":
            login_states[uid] = {"step": "phone"}
            await cb.message.edit_text(
                "📱 *Login — Step 1/3*\n\n"
                "Phone number dalo:\n"
                "🇮🇳 +919876543210\n"
                "🇺🇸 +12025551234\n\n"
                "⚠️ Spaces mat dalo!",
                parse_mode="Markdown",
            )

        # ── LOGOUT ──
        elif data == "lg":
            await cb.message.edit_text(
                "🚪 *Logout*\n\nPakka?",
                parse_mode="Markdown",
                reply_markup=kb_logout_confirm(),
            )

        elif data == "lgc":
            await logout_user(uid)
            await cb.message.edit_text(
                "✅ Logout ho gaya!\n\n/login se dobara connect karo.",
                reply_markup=kb_main_only(),
            )

        # ── TASK LIST ──
        elif data == "tl":
            if not is_user_logged_in(uid):
                await cb.answer("Pehle /login karo!", show_alert=True)
                return
            tasks = await _tasks_to_dict(uid)
            if not tasks:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("➕ New Task", callback_data="ng"))
                kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
                await cb.message.edit_text("*Tasks*\n\nKoi task nahi.", parse_mode="Markdown", reply_markup=kb)
            else:
                await cb.message.edit_text("*Tasks*\n\nSelect karo:", parse_mode="Markdown", reply_markup=kb_task_list(tasks))

        # ── NEW TASK ──
        elif data == "ng":
            if not allowed:
                await cb.answer("Access nahi!", show_alert=True)
                return
            limits = get_plan_limits(plan)
            count = await db.count_user_tasks(uid)
            if count >= limits["max_tasks"]:
                await cb.answer(
                    f"Max {limits['max_tasks']} tasks allowed ({plan_display_name(plan)} plan).\n"
                    f"Upgrade karo /subscribe",
                    show_alert=True,
                )
                return
            existing = await db.get_user_tasks(uid)
            existing_nums = {int(t.name.split()[-1]) for t in existing if t.name.startswith("Task ")}
            n = 1
            while n in existing_nums:
                n += 1
            task = await db.create_task(uid, f"Task {n}")
            await cb.message.edit_text(
                f"*Task {n}*\n\nStatus: 🔴 Stopped\nSource: -\nTarget: -",
                parse_mode="Markdown",
                reply_markup=kb_task(task.id, False, plan),
            )

        # ── TASK DETAIL ──
        elif data.startswith("t:"):
            task_id = int(data[2:])
            task = await db.get_task(task_id)
            if not task or task.user_id != uid:
                await cb.answer("Task nahi mila!", show_alert=True)
                return
            dialogs = user_dialogs.get(uid, [])
            name_map = {d[0]: d[1] for d in dialogs}
            src = [ch for ch in task.channels if ch.type == "source"]
            tgt = [ch for ch in task.channels if ch.type == "target"]
            src_text = ", ".join(name_map.get(ch.channel_id, ch.channel_name or str(ch.channel_id)) for ch in src) or "-"
            tgt_text = ", ".join(name_map.get(ch.channel_id, ch.channel_name or str(ch.channel_id)) for ch in tgt) or "-"
            status = "🟢 Running" if task.is_active else "🔴 Stopped"
            await cb.message.edit_text(
                f"*{task.name}*\n\nStatus: {status}\n\n"
                f"📥 Source ({len(src)}): {src_text}\n"
                f"📤 Target ({len(tgt)}): {tgt_text}",
                parse_mode="Markdown",
                reply_markup=kb_task(task_id, task.is_active, plan),
            )

        # ── TASK START ──
        elif data.startswith("tst:"):
            task_id = int(data[4:])
            task = await db.get_task(task_id)
            if not task or task.user_id != uid:
                await cb.answer("Task nahi mila!", show_alert=True)
                return
            src = [ch for ch in task.channels if ch.type == "source"]
            tgt = [ch for ch in task.channels if ch.type == "target"]
            if not src or not tgt:
                await cb.answer("Pehle source aur target set karo!", show_alert=True)
                return
            await db.set_task_active(task_id, True)
            await cb.answer("▶️ Task start ho gaya!")
            # Refresh task detail
            task = await db.get_task(task_id)
            await cb.message.edit_reply_markup(reply_markup=kb_task(task_id, True, plan))

        # ── TASK STOP ──
        elif data.startswith("tsp:"):
            task_id = int(data[4:])
            await db.set_task_active(task_id, False)
            await cb.answer("⏹ Task band ho gaya!")
            await cb.message.edit_reply_markup(reply_markup=kb_task(task_id, False, plan))

        # ── TASK DELETE (confirm screen) ──
        elif data.startswith("td:"):
            task_id = int(data[3:])
            task = await db.get_task(task_id)
            if not task or task.user_id != uid:
                await cb.answer("Task nahi mila!", show_alert=True)
                return
            await cb.message.edit_text(
                f"🗑️ *Delete '{task.name}'?*\n\nYe task aur iske saare settings delete ho jaayenge.",
                parse_mode="Markdown",
                reply_markup=kb_task_delete_confirm(task_id),
            )

        # ── TASK DELETE CONFIRMED ──
        elif data.startswith("tdc:"):
            task_id = int(data[4:])
            await db.delete_task(task_id)
            tasks = await _tasks_to_dict(uid)
            await cb.message.edit_text(
                "✅ *Task delete ho gaya!*",
                parse_mode="Markdown",
                reply_markup=kb_task_list(tasks) if tasks else kb_main_only(),
            )

        # ── TASK RENAME ──
        elif data.startswith("tr:"):
            task_id = int(data[3:])
            user_state[uid] = {"action": "rename", "task_id": task_id}
            await cb.message.edit_text(
                "✏️ Naya naam type karo (max 30 chars):",
                reply_markup=kb_main_only(),
            )

        # ── SET SOURCE ──
        elif data.startswith("src:"):
            task_id = int(data[4:])
            if not is_user_logged_in(uid):
                await cb.answer("Pehle /login karo!", show_alert=True)
                return
            await cb.message.answer("📋 Channels load ho rahi hain...")
            dialogs = await load_dialogs(uid)
            user_state[uid] = {"action": "set_source", "task_id": task_id}
            await cb.message.answer(
                f"📥 *Source Channel Select Karo*\n\n{_channel_list_text(uid)}\n\n"
                f"Numbers type karo (comma se): `1,3,5`",
                parse_mode="Markdown",
            )

        # ── SET TARGET ──
        elif data.startswith("tgt:"):
            task_id = int(data[4:])
            if not is_user_logged_in(uid):
                await cb.answer("Pehle /login karo!", show_alert=True)
                return
            await cb.message.answer("📋 Channels load ho rahi hain...")
            await load_dialogs(uid)
            user_state[uid] = {"action": "set_target", "task_id": task_id}
            await cb.message.answer(
                f"📤 *Target Channel Select Karo*\n\n{_channel_list_text(uid)}\n\n"
                f"Numbers type karo (comma se): `1,3,5`",
                parse_mode="Markdown",
            )

        # ── FILTERS MENU ──
        elif data.startswith("fi:"):
            task_id = int(data[3:])
            task = await db.get_task(task_id)
            if not task or task.user_id != uid:
                await cb.answer("Task nahi mila!", show_alert=True)
                return
            bl = ", ".join(task.blacklist_words or []) or "None"
            wl = ", ".join(task.whitelist_words or []) or "None"
            await cb.message.edit_text(
                f"⚙️ *Filters — {task.name}*\n\n"
                f"Blacklist: {bl[:50]}\n"
                f"Whitelist: {wl[:50]}\n"
                f"Regex: {task.regex_pattern or 'None'}",
                parse_mode="Markdown",
                reply_markup=kb_filters(task_id, plan),
            )

        # ── BLACKLIST ──
        elif data.startswith("bl:"):
            task_id = int(data[3:])
            user_state[uid] = {"action": "set_blacklist", "task_id": task_id}
            task = await db.get_task(task_id)
            current = ", ".join(task.blacklist_words or []) or "Koi nahi"
            await cb.message.edit_text(
                f"🚫 *Blacklist*\n\nCurrent: {current}\n\n"
                f"Naye words add karo (comma se alag karo):\n"
                f"Example: `casino, gambling, spam`",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── WHITELIST ──
        elif data.startswith("wl:"):
            task_id = int(data[3:])
            user_state[uid] = {"action": "set_whitelist", "task_id": task_id}
            task = await db.get_task(task_id)
            current = ", ".join(task.whitelist_words or []) or "Koi nahi"
            await cb.message.edit_text(
                f"✅ *Whitelist*\n\nCurrent: {current}\n\n"
                f"Sirf in words wale messages forward honge.\n"
                f"Words add karo (comma se):\n"
                f"Example: `deal, offer, discount`",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── REGEX ──
        elif data.startswith("rx:"):
            task_id = int(data[3:])
            user_state[uid] = {"action": "set_regex", "task_id": task_id}
            task = await db.get_task(task_id)
            await cb.message.edit_text(
                f"🔤 *Regex Filter*\n\nCurrent: `{task.regex_pattern or 'None'}`\n\n"
                f"Regex pattern type karo:\n"
                f"Example: `.*discount.*` ya `^Deal:`",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── WORD REPLACE ──
        elif data.startswith("wr:"):
            task_id = int(data[3:])
            user_state[uid] = {"action": "set_word_replace", "task_id": task_id}
            task = await db.get_task(task_id)
            pairs = task.word_replace_pairs or []
            current = "\n".join(f"  `{p['from']}` → `{p['to']}`" for p in pairs) or "Koi nahi"
            await cb.message.edit_text(
                f"🔄 *Word Replace*\n\nCurrent pairs:\n{current}\n\n"
                f"Naya pair add karo:\n"
                f"Format: `original → new`\n"
                f"Example: `Amazon → My Store`",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── LINK REPLACER ──
        elif data.startswith("lr:"):
            task_id = int(data[3:])
            user_state[uid] = {"action": "set_link_replacer", "task_id": task_id}
            replacers = await db.get_link_replacers(task_id)
            lines = [f"  `{r.original_link[:30]}` → `{r.new_link[:30]}`" for r in replacers]
            current = "\n".join(lines) or "Koi nahi"
            await cb.message.edit_text(
                f"🔗 *Link Replacer*\n\nCurrent:\n{current}\n\n"
                f"Naya replacer add karo (2 lines):\n"
                f"Line 1: Original link\n"
                f"Line 2: New link",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── MEDIA FILTER ──
        elif data.startswith("mf:"):
            task_id = int(data[3:])
            task = await db.get_task(task_id)
            if not task or task.user_id != uid:
                await cb.answer("Task nahi mila!", show_alert=True)
                return
            await cb.message.edit_text(
                f"🖼️ *Media Filter — {task.name}*\n\nKaunse media forward karne hain?",
                parse_mode="Markdown",
                reply_markup=kb_media_filter(task_id, task.media_filter),
            )

        # ── MEDIA FILTER TOGGLE ──
        elif data.startswith("mft:"):
            _, key, task_id_str = data.split(":")
            task_id = int(task_id_str)
            task = await db.get_task(task_id)
            defaults = {"images": True, "videos": True, "documents": True,
                        "audio": True, "stickers": False, "links": True}
            mf = dict(task.media_filter or defaults)
            mf[key] = not mf.get(key, defaults.get(key, True))
            await db.update_task_settings(task_id, media_filter=mf)
            task = await db.get_task(task_id)
            await cb.message.edit_reply_markup(reply_markup=kb_media_filter(task_id, task.media_filter))
            await cb.answer()
            return

        # ── MESSAGE SETTINGS ──
        elif data.startswith("ms:"):
            task_id = int(data[3:])
            task = await db.get_task(task_id)
            if not task or task.user_id != uid:
                await cb.answer("Task nahi mila!", show_alert=True)
                return
            rl_icon = "✅" if task.remove_links else "❌"
            await cb.message.edit_text(
                f"🛠️ *Message Settings — {task.name}*\n\n"
                f"Header: {task.header_text or 'None'}\n"
                f"Footer: {task.footer_text or 'None'}\n"
                f"Remove Links: {rl_icon}",
                parse_mode="Markdown",
                reply_markup=kb_message_settings(task_id, plan),
            )

        # ── HEADER ──
        elif data.startswith("hd:"):
            task_id = int(data[3:])
            user_state[uid] = {"action": "set_header", "task_id": task_id}
            task = await db.get_task(task_id)
            await cb.message.edit_text(
                f"📝 *Header Text*\n\nCurrent: `{task.header_text or 'None'}`\n\n"
                f"Naya header type karo (ya 'none' type karo hatane ke liye):",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── FOOTER ──
        elif data.startswith("ft:"):
            task_id = int(data[3:])
            user_state[uid] = {"action": "set_footer", "task_id": task_id}
            task = await db.get_task(task_id)
            await cb.message.edit_text(
                f"📝 *Footer Text*\n\nCurrent: `{task.footer_text or 'None'}`\n\n"
                f"Naya footer type karo:",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── CUSTOM CAPTION ──
        elif data.startswith("cp:"):
            task_id = int(data[3:])
            user_state[uid] = {"action": "set_caption", "task_id": task_id}
            task = await db.get_task(task_id)
            await cb.message.edit_text(
                f"💬 *Custom Caption*\n\nCurrent: `{task.custom_caption or 'None'}`\n\n"
                f"Naya caption type karo.\n"
                f"_Tip: {{original_caption}} se original caption include kar sakte ho_",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── REMOVE LINKS TOGGLE ──
        elif data.startswith("rl:"):
            task_id = int(data[3:])
            task = await db.get_task(task_id)
            if not task or task.user_id != uid:
                await cb.answer("Task nahi mila!", show_alert=True)
                return
            new_val = not task.remove_links
            await db.update_task_settings(task_id, remove_links=new_val)
            icon = "✅" if new_val else "❌"
            await cb.answer(f"Remove Links: {icon}")
            await cb.message.edit_reply_markup(reply_markup=kb_message_settings(task_id, plan))

        # ── ADVANCED SETTINGS ──
        elif data.startswith("as:"):
            task_id = int(data[3:])
            task = await db.get_task(task_id)
            if not task or task.user_id != uid:
                await cb.answer("Task nahi mila!", show_alert=True)
                return
            await cb.message.edit_text(
                f"⚙️ *Advanced Settings — {task.name}*",
                parse_mode="Markdown",
                reply_markup=kb_advanced_settings(task_id, plan, task),
            )

        # ── DELAY ──
        elif data.startswith("dl:"):
            task_id = int(data[3:])
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("⏱️ Fixed", callback_data=f"dlf:{task_id}"),
                InlineKeyboardButton("🎲 Random", callback_data=f"dlr:{task_id}"),
                InlineKeyboardButton("❌ No Delay", callback_data=f"dln:{task_id}"),
            )
            kb.add(InlineKeyboardButton("🔙 Back", callback_data=f"as:{task_id}"))
            await cb.message.edit_text("⏱️ *Delay Type*\n\nKaunsa delay chahiye?", parse_mode="Markdown", reply_markup=kb)

        elif data.startswith("dlf:"):
            task_id = int(data[4:])
            user_state[uid] = {"action": "set_delay", "task_id": task_id}
            await cb.message.edit_text("⏱️ Fixed delay seconds mein type karo:\nExample: `5` ya `2.5`", parse_mode="Markdown", reply_markup=kb_main_only())

        elif data.startswith("dlr:"):
            task_id = int(data[4:])
            user_state[uid] = {"action": "set_delay_random", "task_id": task_id}
            await cb.message.edit_text("🎲 Random delay range type karo:\nFormat: `min max`\nExample: `3 8`", parse_mode="Markdown", reply_markup=kb_main_only())

        elif data.startswith("dln:"):
            task_id = int(data[4:])
            await db.update_task_settings(task_id, delay_mode="none", delay_seconds=0)
            await cb.answer("✅ Delay hataa diya!")
            task = await db.get_task(task_id)
            await cb.message.edit_reply_markup(reply_markup=kb_advanced_settings(task_id, plan, task))

        # ── SKIP DUPLICATES TOGGLE ──
        elif data.startswith("sd:"):
            task_id = int(data[3:])
            task = await db.get_task(task_id)
            if not task or task.user_id != uid:
                await cb.answer("Task nahi mila!", show_alert=True)
                return
            new_val = not task.skip_duplicates
            await db.update_task_settings(task_id, skip_duplicates=new_val)
            icon = "✅" if new_val else "❌"
            await cb.answer(f"Skip Duplicates: {icon}")
            task = await db.get_task(task_id)
            await cb.message.edit_reply_markup(reply_markup=kb_advanced_settings(task_id, plan, task))

        # ── PINNED ONLY TOGGLE ──
        elif data.startswith("po:"):
            task_id = int(data[3:])
            task = await db.get_task(task_id)
            if not task or task.user_id != uid:
                await cb.answer("Task nahi mila!", show_alert=True)
                return
            new_val = not task.pinned_only
            await db.update_task_settings(task_id, pinned_only=new_val)
            icon = "✅" if new_val else "❌"
            await cb.answer(f"Pinned Only: {icon}")
            task = await db.get_task(task_id)
            await cb.message.edit_reply_markup(reply_markup=kb_advanced_settings(task_id, plan, task))

        # ── SCHEDULE ──
        elif data.startswith("sc:"):
            task_id = int(data[3:])
            task = await db.get_task(task_id)
            user_state[uid] = {"action": "set_schedule", "task_id": task_id}
            sch = "None"
            if task.schedule_enabled:
                sch = f"Days: {task.schedule_days} | Time: {task.schedule_start}-{task.schedule_end}"
            await cb.message.edit_text(
                f"📅 *Schedule*\n\nCurrent: {sch}\n\n"
                f"Format type karo:\n"
                f"`Mon-Fri 09:00-21:00`\n"
                f"`Everyday 08:00-22:00`\n"
                f"`Mon,Wed,Fri 10:00-20:00`",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── WATERMARK ──
        elif data.startswith("wm:"):
            task_id = int(data[3:])
            user_state[uid] = {"action": "set_watermark", "task_id": task_id}
            task = await db.get_task(task_id)
            await cb.message.edit_text(
                f"🎨 *Watermark*\n\nCurrent: `{task.watermark_text or 'None'}`\n\n"
                f"Watermark text type karo:\nExample: `@MyChannel`",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── PLANS ──
        elif data == "pl":
            await cb.message.edit_text(
                "💳 *Plans*\n\n"
                "🆓 Free — 1 Task | 1 Source | 1 Target | 60 msgs/day\n\n"
                "⭐ Basic — $1/mo | $10/yr\n"
                "   3 Tasks | 3 Sources | 3 Targets\n"
                "   Header/Footer, Delay, Media Filter\n\n"
                "💎 Pro — $2/mo | $20/yr\n"
                "   5 Tasks | 8 Sources | 8 Targets\n"
                "   All Basic + Schedule, Whitelist, Link Replacer\n\n"
                "🚀 Business — $5/mo | $45/yr\n"
                "   10 Tasks | 15 Sources | 15 Targets\n"
                "   All Pro + Regex, Watermark, Priority Support\n\n"
                "_Annual plan = 2 months FREE!_",
                parse_mode="Markdown",
                reply_markup=kb_plans(),
            )

        elif data.startswith("pp:"):
            parts = data.split(":")
            plan_key = parts[1]
            billing = parts[2] if len(parts) > 2 else "choose"

            if billing == "choose":
                await cb.message.edit_text(
                    f"💳 {plan_display_name(plan_key)}\n\nBilling choose karo:",
                    parse_mode="Markdown",
                    reply_markup=kb_plan_billing(plan_key),
                )
            else:
                # Create payment
                try:
                    _, url, amount_paise, amount_usd = await create_order(uid, plan_key, billing)
                    plan_name = plan_display_name(plan_key)
                    billing_label = "Monthly" if billing == "monthly" else "Annual"
                    await cb.message.edit_text(
                        f"💳 *{plan_name} — {billing_label}*\n\n"
                        f"Amount: ${amount_usd:.0f} (₹{amount_paise // 100})\n\n"
                        f"Neeche link pe click karo aur payment karo.\n"
                        f"Payment ke baad automatic activate ho jaayega!",
                        parse_mode="Markdown",
                        reply_markup=kb_payment_link(url),
                    )
                except Exception as e:
                    await cb.message.edit_text(
                        f"❌ Payment link create karne mein error:\n{str(e)[:200]}\n\nThodi der baad try karo.",
                        reply_markup=kb_main_only(),
                    )

        # ── START ALL ──
        elif data == "sa":
            await cb.message.edit_text(
                "▶️ *Start All Tasks?*\n\nSaare tasks start ho jaayenge.",
                parse_mode="Markdown",
                reply_markup=kb_startall_confirm(),
            )

        elif data == "sac":
            tasks = await db.get_user_tasks(uid)
            count = 0
            for t in tasks:
                if any(ch.type == "source" for ch in t.channels) and any(ch.type == "target" for ch in t.channels):
                    await db.set_task_active(t.id, True)
                    count += 1
            await cb.message.edit_text(
                f"▶️ *{count} task(s) start ho gaye!*",
                parse_mode="Markdown",
                reply_markup=kb_main_only(),
            )

        # ── STOP ALL ──
        elif data == "xa":
            await cb.message.edit_text(
                "⏹ *Stop All Tasks?*",
                parse_mode="Markdown",
                reply_markup=kb_stopall_confirm(),
            )

        elif data == "xac":
            await db.set_all_tasks_active(uid, False)
            await cb.message.edit_text("⏹ *Saare tasks band ho gaye!*", parse_mode="Markdown", reply_markup=kb_main_only())

        elif data == "scx":
            await cb.message.edit_text("✅ Cancel ho gaya.", reply_markup=kb_main_only())

        # ── REFER & EARN ──
        elif data == "ref":
            aff = await db.get_or_create_affiliate(uid)
            from config import MAIN_BOT_USERNAME
            link = f"t.me/{MAIN_BOT_USERNAME}?start={aff.code}"
            await cb.message.edit_text(
                f"👥 *Refer & Earn*\n\n"
                f"Tera referral code: `{aff.code}`\n"
                f"Link: `{link}`\n\n"
                f"Commission: 80% — sirf pehli payment pe\n"
                f"_(Renewal pe commission nahi milti)_\n\n"
                f"Referred: {aff.total_referred} log\n"
                f"Total Kamaaye: ${aff.total_earned_usd:.2f}\n"
                f"Withdraw kar sakta hai: ${aff.balance_usd:.2f}\n"
                f"Minimum: ${AFFILIATE_MIN_WITHDRAW_USD}",
                parse_mode="Markdown",
                reply_markup=kb_refer(
                    aff.code, aff.balance_usd, aff.total_earned_usd,
                    aff.total_referred, MAIN_BOT_USERNAME,
                ),
            )

        try:
            await cb.answer()
        except Exception:
            pass
