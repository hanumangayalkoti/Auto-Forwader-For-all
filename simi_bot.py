"""
Simi — AI Support Bot (@Sandesh_Forwader_Help_bot)
Same DB as main bot. Girl persona. Speaks user's language.
Last 20 messages context. Only bot-related help.
"""
import logging
import asyncio

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from openai import AsyncOpenAI

import database as db
from config import (
    SIMI_BOT_TOKEN, OPENAI_API_KEY,
    OWNER_ID, MAIN_BOT_USERNAME, SIMI_BOT_USERNAME,
    plan_display_name,
)
from keyboards import kb_simi_contact_admin, kb_simi_confirm_admin_msg

logger = logging.getLogger(__name__)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SIMI_SYSTEM_PROMPT = f"""Tu Simi hai — ek helpful, friendly AI support girl jo Sandesh Forward Bot ki help karti hai.

Tera kaam:
- Users ki bot-related problems solve karna (login, tasks, forwarding, plans, filters, etc.)
- Plans aur features explain karna
- Hinglish ya English mein reply karna — user jis language mein bole us mein
- Friendly, warm, aur concise rehna

Bot ka naam: @{MAIN_BOT_USERNAME}
Plans: Free (1 task, 60 msgs/day), Basic ($1/mo), Pro ($2/mo), Business ($5/mo)
Support bot: @{SIMI_BOT_USERNAME} (yahi tu hai)

Rules:
1. SIRF bot se related sawaalon ka jawab de
2. Agar off-topic ho to politely redirect kar: "Main sirf Sandesh Forward Bot ki help kar sakti hun!"
3. Agar tu sure nahi ho ya complex issue ho to "Admin se contact karo" suggest kar
4. Kabhi bhi personal info mat maango
5. Friendly reh — emojis use kar lekin zyada nahi
6. Agar user Hinglish mein bole to Hinglish mein jawab, English mein bole to English mein

Bot features tujhe pata hain:
- Login: /login → phone → OTP (word+code format mein) → 2FA optional
- Tasks: Source channel + Target channel → Start → Auto forward
- Filters: Blacklist/Whitelist/Regex/Media filter (plan ke hisaab se)
- Message Tools: Header/Footer/Caption/Remove Links
- Plans: Free → Basic → Pro → Business
- Refer & Earn: 80% commission on first referral payment
- Schedule, Skip Duplicates, Pinned Only, Word Replace, Link Replacer, Watermark"""

# Admin message state for Simi users
_simi_admin_state: dict[int, dict] = {}
_main_bot_ref = None


def set_main_bot(bot):
    global _main_bot_ref
    _main_bot_ref = bot


async def setup_simi_bot(dp_main=None):
    """Create and configure Simi bot. Returns (bot, dp)."""
    bot = Bot(token=SIMI_BOT_TOKEN, parse_mode="Markdown")
    dp = Dispatcher(bot)

    # ────────── /start ──────────
    @dp.message_handler(commands=["start"])
    async def simi_start(msg: types.Message):
        uid = msg.from_user.id
        user, is_new = await db.get_or_create_user(
            uid, msg.from_user.username or "", msg.from_user.full_name or ""
        )
        if is_new:
            uname = f"@{user.username}" if user.username else f"ID:{uid}"
            try:
                await bot.send_message(
                    OWNER_ID,
                    f"👤 {uname} ne *@{SIMI_BOT_USERNAME}* start kiya!\nID: `{uid}`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        # Check if logged in to main bot
        from forwarder import is_user_logged_in
        logged_in = is_user_logged_in(uid)

        if not logged_in:
            await msg.answer(
                f"Hii! Main Simi hun 👋\n\n"
                f"Tumhara @{MAIN_BOT_USERNAME} mein account hai, but pehle wahan login karna padega!\n\n"
                f"1. @{MAIN_BOT_USERNAME} pe jao\n"
                f"2. /login bhejo\n"
                f"3. Phone number aur OTP se login karo\n\n"
                f"Login ke baad main tumhari poori help kar sakti hun 😊"
            )
            return

        _, reason = db.check_access(user)
        plan_name = plan_display_name(user.plan)
        await msg.answer(
            f"Hii {user.full_name or 'there'}! Main Simi hun 🤖\n\n"
            f"Tumhara current plan: *{plan_name}*\n\n"
            f"Kya madad chahiye? Forwarding, filters, plans — sab kuch puchh sakte ho!",
        )

    # ────────── /help ──────────
    @dp.message_handler(commands=["help"])
    async def simi_help(msg: types.Message):
        await msg.answer(
            f"Hii! Main Simi hun 😊\n\n"
            f"Main @{MAIN_BOT_USERNAME} ke baare mein help karti hun:\n\n"
            f"- Login kaise karein\n"
            f"- Tasks setup karna\n"
            f"- Filters use karna\n"
            f"- Plans aur pricing\n"
            f"- Refer & Earn\n\n"
            f"Bas apna sawaal puchho!"
        )

    # ────────── ADMIN: /broadcast (Simi se bhi) ──────────
    @dp.message_handler(commands=["broadcast"])
    async def simi_broadcast(msg: types.Message):
        if msg.from_user.id != OWNER_ID:
            return
        await msg.answer("📢 Broadcast message type karo (Simi bot se jayega):\n/cancel se rok sakte ho")
        _simi_admin_state[msg.from_user.id] = {"step": "broadcast_waiting"}

    @dp.message_handler(commands=["msg"])
    async def simi_admin_msg(msg: types.Message):
        if msg.from_user.id != OWNER_ID:
            return
        parts = msg.text.split(maxsplit=2)
        if len(parts) < 2:
            await msg.answer("Usage: `/msg <user_id> <text>`", parse_mode="Markdown")
            return
        try:
            target_id = int(parts[1])
            text = parts[2] if len(parts) > 2 else ""
        except ValueError:
            await msg.answer("❌ Galat user ID.")
            return
        if text:
            try:
                await bot.send_message(target_id, f"📩 *Admin Message:*\n\n{text}", parse_mode="Markdown")
                await msg.answer(f"✅ Message bhej diya `{target_id}` ko.", parse_mode="Markdown")
            except Exception as e:
                await msg.answer(f"❌ Send fail: {e}")
        else:
            _simi_admin_state[msg.from_user.id] = {"step": "msg_waiting", "target_id": target_id}
            await msg.answer(f"✏️ User `{target_id}` ko message type karo:", parse_mode="Markdown")

    @dp.message_handler(commands=["reply"])
    async def simi_admin_reply(msg: types.Message):
        if msg.from_user.id != OWNER_ID:
            return
        parts = msg.text.split(maxsplit=2)
        if len(parts) < 3:
            await msg.answer("Usage: `/reply <user_id> <message>`", parse_mode="Markdown")
            return
        try:
            target_id = int(parts[1])
            text = parts[2]
        except ValueError:
            await msg.answer("❌ Galat format.")
            return
        try:
            await bot.send_message(target_id, f"📩 *Support Reply:*\n\n{text}", parse_mode="Markdown")
            await msg.answer(f"✅ Reply bhej diya `{target_id}` ko.", parse_mode="Markdown")
        except Exception as e:
            await msg.answer(f"❌ Send fail: {e}")

    @dp.message_handler(commands=["cancel"])
    async def simi_cancel(msg: types.Message):
        if msg.from_user.id == OWNER_ID and msg.from_user.id in _simi_admin_state:
            _simi_admin_state.pop(msg.from_user.id, None)
            await msg.answer("✅ Cancel ho gaya.")

    # ────────── CALLBACK: Contact Admin ──────────
    @dp.callback_query_handler(lambda cb: cb.data == "ca")
    async def simi_contact_admin_prompt(cb: types.CallbackQuery):
        _simi_admin_state[cb.from_user.id] = {"step": "waiting_admin_msg"}
        await cb.message.answer(
            "📩 *Admin ko message bhejo*\n\n"
            "Apni problem ya sawaal type karo. Admin personally reply karenge!\n"
            "_(Tumhari Telegram ID share hogi admin ke saath)_",
            parse_mode="Markdown",
        )
        await cb.answer()

    @dp.callback_query_handler(lambda cb: cb.data in ("ca_confirm", "ca_cancel"))
    async def simi_admin_msg_confirm(cb: types.CallbackQuery):
        uid = cb.from_user.id
        state = _simi_admin_state.get(uid, {})

        if cb.data == "ca_cancel":
            _simi_admin_state.pop(uid, None)
            await cb.message.edit_text("❌ Cancel ho gaya.")
            await cb.answer()
            return

        text = state.get("pending_msg", "")
        if not text:
            await cb.answer("Koi message nahi!", show_alert=True)
            return

        user = await db.get_user(uid)
        uname = f"@{user.username}" if user and user.username else f"ID:{uid}"
        plan_name = plan_display_name(user.plan) if user else "free"

        try:
            await bot.send_message(
                OWNER_ID,
                f"📩 *Support Request (Simi Bot)*\n\n"
                f"From: {uname} (`{uid}`)\n"
                f"Plan: {plan_name}\n\n"
                f"Message:\n{text}\n\n"
                f"_Reply: `/reply {uid} <tumhara jawab>`_",
                parse_mode="Markdown",
            )
            _simi_admin_state.pop(uid, None)
            await cb.message.edit_text(
                "✅ *Message Admin ko bhej diya!*\n\n"
                "Admin jaldi reply karenge. Thoda wait karo 😊"
            )
        except Exception as e:
            await cb.message.edit_text(f"❌ Message nahi gaya. Baad mein try karo.\nError: {e}")

        await cb.answer()

    # ────────── MAIN TEXT HANDLER (AI) ──────────
    @dp.message_handler()
    async def simi_chat(msg: types.Message):
        uid = msg.from_user.id
        text = (msg.text or "").strip()

        if not text:
            return

        # Admin broadcast/msg state
        if uid == OWNER_ID and uid in _simi_admin_state:
            state = _simi_admin_state[uid]
            if state["step"] == "broadcast_waiting":
                users = await db.get_all_users()
                targets = [u for u in users if not u.is_banned]
                await msg.answer(f"📢 Bhej raha hun {len(targets)} users ko (Simi se)...")
                sent = failed = 0
                for u in targets:
                    try:
                        await bot.send_message(u.user_id, text, parse_mode="Markdown")
                        sent += 1
                    except Exception:
                        failed += 1
                    await asyncio.sleep(0.05)
                _simi_admin_state.pop(uid, None)
                await msg.answer(f"✅ Broadcast done!\nSent: {sent} | Failed: {failed}")
                return
            elif state["step"] == "msg_waiting":
                target_id = state["target_id"]
                _simi_admin_state.pop(uid, None)
                try:
                    await bot.send_message(target_id, f"📩 *Admin Message:*\n\n{text}", parse_mode="Markdown")
                    await msg.answer(f"✅ Bhej diya `{target_id}` ko.", parse_mode="Markdown")
                except Exception as e:
                    await msg.answer(f"❌ Fail: {e}")
                return

        # User admin message state
        if uid in _simi_admin_state and _simi_admin_state[uid].get("step") == "waiting_admin_msg":
            _simi_admin_state[uid]["pending_msg"] = text
            _simi_admin_state[uid]["step"] = "confirm_admin_msg"
            await msg.answer(
                f"📋 *Preview:*\n\n{text}\n\nBhejun admin ko?",
                parse_mode="Markdown",
                reply_markup=kb_simi_confirm_admin_msg(),
            )
            return

        # Check if user is logged in
        from forwarder import is_user_logged_in
        user, _ = await db.get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
        logged_in = is_user_logged_in(uid)

        if not logged_in:
            await msg.answer(
                f"Pehle @{MAIN_BOT_USERNAME} mein /login karo, phir main tumhari help kar sakti hun! 😊"
            )
            return

        # Build context from DB
        history = await db.get_simi_history(uid, limit=20)

        # Get user context for system
        _, reason = db.check_access(user)
        plan_name = plan_display_name(user.plan)
        tasks = await db.get_user_tasks(uid)

        user_context = (
            f"\n\nCurrent user info:\n"
            f"- Name: {user.full_name or 'N/A'}\n"
            f"- Plan: {plan_name}\n"
            f"- Tasks: {len(tasks)} total\n"
            f"- Logged in: Yes"
        )

        messages = [{"role": "system", "content": SIMI_SYSTEM_PROMPT + user_context}]
        for h in history:
            messages.append({"role": h.role, "content": h.content})
        messages.append({"role": "user", "content": text})

        # Save user message
        await db.add_simi_message(uid, "user", text)

        # Typing indicator
        await bot.send_chat_action(uid, "typing")

        try:
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=500,
                temperature=0.7,
            )
            reply = response.choices[0].message.content.strip()

            # Save assistant reply
            await db.add_simi_message(uid, "assistant", reply)

            # Check if AI couldn't help — offer admin contact
            cant_help_signals = [
                "admin se contact", "admin ko batao", "support se", "main sure nahi",
                "mujhe nahi pata", "i'm not sure", "contact admin", "reach out to admin",
            ]
            show_admin_btn = any(s in reply.lower() for s in cant_help_signals)

            await msg.answer(
                reply,
                reply_markup=kb_simi_contact_admin() if show_admin_btn else None,
            )

        except Exception as e:
            logger.error(f"[Simi] OpenAI error: {e}")
            await msg.answer(
                "Oops! Thodi technical dikkat aa gayi 😅\n\n"
                "Seedha admin se contact karo:",
                reply_markup=kb_simi_contact_admin(),
            )

    return bot, dp
