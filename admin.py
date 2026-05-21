"""
Admin panel — works for both Main Bot and Simi Bot.
Only OWNER_ID can use these commands.
"""
import asyncio
import logging
from datetime import datetime

from aiogram import types, Bot
from aiogram.dispatcher import Dispatcher

import database as db
from config import OWNER_ID, plan_display_name, USD_TO_INR
from keyboards import kb_confirm_broadcast, kb_main_only

logger = logging.getLogger(__name__)

# Broadcast state: {owner_id: {"step": "waiting_message"|"waiting_confirm", "text": str}}
broadcast_state: dict[int, dict] = {}

# Admin message state (for /msg command): {owner_id: {"step": "waiting_msg", "target_id": int}}
admin_msg_state: dict[int, dict] = {}


def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


def register_admin(dp: Dispatcher, bot: Bot):

    # ─────────────── /admin ───────────────
    @dp.message_handler(commands=["admin"])
    async def cmd_admin(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        users = await db.get_all_users()
        now = datetime.utcnow()
        total = len(users)

        active_paid = sum(1 for u in users if not u.is_banned and u.plan != "free"
                          and u.plan_end and u.plan_end > now)
        free_active = sum(1 for u in users if not u.is_banned and u.plan == "free")
        just_started = sum(1 for u in users if not u.is_banned)
        banned = sum(1 for u in users if u.is_banned)
        expired = sum(1 for u in users if not u.is_banned and u.plan != "free"
                      and (not u.plan_end or u.plan_end <= now))

        total_rev_p, month_rev_p, pay_count = await db.get_payment_stats()
        total_payout = await db.get_total_referral_payouts()

        text = (
            "👑 *Admin Dashboard*\n\n"
            "━━━━━━━━━━━━━━━\n"
            "👥 *Users*\n"
            f"  Total: {total}\n"
            f"  Paid Active: {active_paid}\n"
            f"  Free Plan: {free_active}\n"
            f"  Expired: {expired}\n"
            f"  Banned: {banned}\n\n"
            "━━━━━━━━━━━━━━━\n"
            "💰 *Revenue*\n"
            f"  Is Mahine: ₹{month_rev_p // 100}\n"
            f"  Total: ₹{total_rev_p // 100}\n"
            f"  Total Payments: {pay_count}\n"
            f"  Referral Payouts: ${total_payout:.2f}\n\n"
            "━━━━━━━━━━━━━━━\n"
            "📋 *Commands*\n"
            "/users — User list\n"
            "/broadcast — Message bhejo\n"
            "/msg <id> — Individual message\n"
            "/give <id> <days> [plan] — Days do\n"
            "/ban <id> — Ban\n"
            "/unban <id> — Unban\n"
            "/check <id> — User detail\n"
            "/revenue — Revenue report\n"
            "/expiring — Expire hone wale\n"
            "/withdrawals — Pending withdrawals"
        )
        await msg.answer(text, parse_mode="Markdown")

    # ─────────────── /users ───────────────
    @dp.message_handler(commands=["users"])
    async def cmd_users(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        users = await db.get_all_users()
        now = datetime.utcnow()
        if not users:
            await msg.answer("Koi user nahi hai.")
            return
        lines = ["👥 *All Users*\n"]
        for u in users[:50]:
            _, reason = db.check_access(u)
            if u.is_banned:
                status = "🚫 Banned"
            elif "subscribed" in reason:
                parts = reason.split(":")
                plan_n = plan_display_name(parts[1]) if len(parts) > 1 else ""
                days_left = parts[2] if len(parts) > 2 else "?"
                status = f"✅ {plan_n} ({days_left}d)"
            elif reason == "free":
                status = "🆓 Free"
            else:
                status = "❌ Expired"
            name = u.full_name or u.username or str(u.user_id)
            uname = f"@{u.username}" if u.username else "-"
            lines.append(f"`{u.user_id}` | {uname} | {name[:15]} | {status}")
        if len(users) > 50:
            lines.append(f"\n...aur {len(users) - 50} users hain.")
        await msg.answer("\n".join(lines), parse_mode="Markdown")

    # ─────────────── /give ───────────────
    @dp.message_handler(commands=["give"])
    async def cmd_give(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        parts = msg.text.split()
        # /give <id> <days> [plan]
        if len(parts) < 3:
            await msg.answer(
                "Usage: `/give <user_id> <days> [plan]`\n"
                "Plan options: basic, pro, business\n"
                "Example: `/give 123456 30 pro`",
                parse_mode="Markdown",
            )
            return
        try:
            user_id = int(parts[1])
            days = int(parts[2])
            plan = parts[3].lower() if len(parts) > 3 else None
        except ValueError:
            await msg.answer("❌ Galat format. `/give 123456 30 pro`", parse_mode="Markdown")
            return

        user = await db.give_days(user_id, days, plan)
        if not user:
            await msg.answer(f"❌ User `{user_id}` nahi mila.", parse_mode="Markdown")
            return

        plan_label = plan_display_name(user.plan)
        end_str = user.plan_end.strftime("%d %b %Y") if user.plan_end else "N/A"

        # Notify admin
        await msg.answer(
            f"✅ *Done!*\n\n"
            f"User: `{user_id}`\n"
            f"Added: {days} days\n"
            f"Plan: {plan_label}\n"
            f"New expiry: {end_str}",
            parse_mode="Markdown",
        )

        # Notify user
        try:
            await bot.send_message(
                user_id,
                f"🎁 *Admin ne aapko {days} din diye!*\n\n"
                f"Plan: {plan_label}\n"
                f"Expiry: {end_str}\n\n"
                f"Bot enjoy karo! 🚀",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    # ─────────────── /ban ───────────────
    @dp.message_handler(commands=["ban"])
    async def cmd_ban(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        parts = msg.text.split()
        if len(parts) < 2:
            await msg.answer("Usage: `/ban <user_id>`", parse_mode="Markdown")
            return
        try:
            user_id = int(parts[1])
        except ValueError:
            await msg.answer("❌ Galat user ID.", parse_mode="Markdown")
            return
        await db.ban_user(user_id)
        await db.set_all_tasks_active(user_id, False)
        await msg.answer(f"🚫 User `{user_id}` ban ho gaya.", parse_mode="Markdown")
        try:
            await bot.send_message(
                user_id,
                "🚫 Tumhara account ban ho gaya hai. Support se contact karo."
            )
        except Exception:
            pass

    # ─────────────── /unban ───────────────
    @dp.message_handler(commands=["unban"])
    async def cmd_unban(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        parts = msg.text.split()
        if len(parts) < 2:
            await msg.answer("Usage: `/unban <user_id>`", parse_mode="Markdown")
            return
        try:
            user_id = int(parts[1])
        except ValueError:
            await msg.answer("❌ Galat user ID.", parse_mode="Markdown")
            return
        await db.unban_user(user_id)
        await msg.answer(f"✅ User `{user_id}` unban ho gaya.", parse_mode="Markdown")
        try:
            await bot.send_message(user_id, "✅ Tumhara account unban ho gaya! /start karo.")
        except Exception:
            pass

    # ─────────────── /check ───────────────
    @dp.message_handler(commands=["check"])
    async def cmd_check(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        parts = msg.text.split()
        if len(parts) < 2:
            await msg.answer("Usage: `/check <user_id>`", parse_mode="Markdown")
            return
        try:
            user_id = int(parts[1])
        except ValueError:
            await msg.answer("❌ Galat user ID.", parse_mode="Markdown")
            return
        user = await db.get_user(user_id)
        if not user:
            await msg.answer(f"❌ User `{user_id}` nahi mila.", parse_mode="Markdown")
            return

        _, reason = db.check_access(user)
        tasks = await db.get_user_tasks(user_id)
        active_tasks = sum(1 for t in tasks if t.is_active)

        aff = await db.get_or_create_affiliate(user_id)
        end_str = user.plan_end.strftime("%d %b %Y %H:%M") if user.plan_end else "N/A"

        text = (
            f"👤 *User Detail*\n\n"
            f"ID: `{user.user_id}`\n"
            f"Username: @{user.username or '-'}\n"
            f"Name: {user.full_name or '-'}\n"
            f"Plan: {plan_display_name(user.plan)}\n"
            f"Expiry: {end_str}\n"
            f"Status: {'🚫 Banned' if user.is_banned else reason}\n"
            f"Joined: {user.join_date.strftime('%d %b %Y') if user.join_date else '-'}\n\n"
            f"Tasks: {len(tasks)} total, {active_tasks} active\n\n"
            f"Referral Code: `{aff.code}`\n"
            f"Referred: {aff.total_referred}\n"
            f"Wallet: ${aff.balance_usd:.2f}\n"
            f"Referred By: {user.referred_by or 'None'}"
        )
        await msg.answer(text, parse_mode="Markdown")

    # ─────────────── /revenue ───────────────
    @dp.message_handler(commands=["revenue"])
    async def cmd_revenue(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        total_p, month_p, count = await db.get_payment_stats()
        total_payout = await db.get_total_referral_payouts()
        await msg.answer(
            f"💰 *Revenue Report*\n\n"
            f"Is Mahine: ₹{month_p // 100} (${month_p // 100 / USD_TO_INR:.1f})\n"
            f"Total: ₹{total_p // 100} (${total_p // 100 / USD_TO_INR:.1f})\n"
            f"Total Payments: {count}\n"
            f"Referral Payouts: ${total_payout:.2f}",
            parse_mode="Markdown",
        )

    # ─────────────── /expiring ───────────────
    @dp.message_handler(commands=["expiring"])
    async def cmd_expiring(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        users = await db.get_expiring_users(days_ahead=5)
        if not users:
            await msg.answer("Agle 5 din mein koi expire nahi ho raha.")
            return
        now = datetime.utcnow()
        lines = ["⏰ *Expiring Soon (5 days)*\n"]
        for u in users:
            days_left = (u.plan_end - now).days if u.plan_end else 0
            uname = f"@{u.username}" if u.username else str(u.user_id)
            lines.append(f"`{u.user_id}` | {uname} | {plan_display_name(u.plan)} | {days_left}d left")
        await msg.answer("\n".join(lines), parse_mode="Markdown")

    # ─────────────── /withdrawals ───────────────
    @dp.message_handler(commands=["withdrawals"])
    async def cmd_withdrawals(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        from keyboards import kb_admin_withdrawal
        pending = await db.get_pending_withdrawals()
        if not pending:
            await msg.answer("Koi pending withdrawal nahi hai.")
            return
        for wr in pending:
            user = await db.get_user(wr.user_id)
            uname = f"@{user.username}" if user and user.username else f"ID:{wr.user_id}"
            await msg.answer(
                f"💰 *Withdrawal Request #{wr.id}*\n\n"
                f"User: {uname} (`{wr.user_id}`)\n"
                f"Amount: *${wr.amount_usd:.2f}* (≈₹{wr.amount_usd * USD_TO_INR:.0f})\n"
                f"Method: {wr.payment_method.upper()}\n"
                f"Details: `{wr.payment_details}`\n"
                f"Requested: {wr.created_at.strftime('%d %b %Y %H:%M')}",
                parse_mode="Markdown",
                reply_markup=kb_admin_withdrawal(wr.id),
            )

    # ─────────────── /broadcast ───────────────
    @dp.message_handler(commands=["broadcast"])
    async def cmd_broadcast(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        broadcast_state[msg.from_user.id] = {"step": "waiting_message", "text": ""}
        await msg.answer(
            "📢 *Broadcast*\n\nMessage type karo jo saare users ko bhejna hai:\n"
            "(Markdown supported)\n\n"
            "/cancel se rok sakte ho",
            parse_mode="Markdown",
        )

    # ─────────────── /msg <id> ───────────────
    @dp.message_handler(commands=["msg"])
    async def cmd_msg(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        parts = msg.text.split(maxsplit=1)
        if len(parts) < 2:
            await msg.answer(
                "Usage: `/msg <user_id>`\nFir message type karo.",
                parse_mode="Markdown",
            )
            return
        # Try to get user_id from first word after /msg
        args = parts[1].strip().split(maxsplit=1)
        try:
            target_id = int(args[0].replace("@", ""))
        except ValueError:
            await msg.answer("❌ Galat user ID.", parse_mode="Markdown")
            return

        if len(args) > 1:
            # Message inline: /msg 123456 Hello there
            text = args[1]
            try:
                await bot.send_message(target_id, f"📩 *Admin Message:*\n\n{text}", parse_mode="Markdown")
                await msg.answer(f"✅ Message bhej diya user `{target_id}` ko.", parse_mode="Markdown")
            except Exception as e:
                await msg.answer(f"❌ Send fail: {e}")
        else:
            # Wait for next message
            admin_msg_state[msg.from_user.id] = {"step": "waiting_msg", "target_id": target_id}
            await msg.answer(
                f"✏️ User `{target_id}` ko message type karo:",
                parse_mode="Markdown",
            )

    # ─────────────── /reply <id> <msg> ───────────────
    @dp.message_handler(commands=["reply"])
    async def cmd_reply(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        parts = msg.text.split(maxsplit=2)
        if len(parts) < 3:
            await msg.answer(
                "Usage: `/reply <user_id> <message>`\n"
                "Example: `/reply 123456 Tumhara issue fix ho gaya!`",
                parse_mode="Markdown",
            )
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            await msg.answer("❌ Galat user ID.", parse_mode="Markdown")
            return
        text = parts[2]
        try:
            await bot.send_message(
                target_id,
                f"📩 *Support Reply:*\n\n{text}",
                parse_mode="Markdown",
            )
            await msg.answer(f"✅ Reply bhej diya `{target_id}` ko.", parse_mode="Markdown")
        except Exception as e:
            await msg.answer(f"❌ Send fail: {e}")

    # ─────────────── /cancel ───────────────
    @dp.message_handler(commands=["cancel"])
    async def cmd_cancel(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        uid = msg.from_user.id
        if uid in broadcast_state:
            del broadcast_state[uid]
            await msg.answer("✅ Broadcast cancel ho gaya.")
        elif uid in admin_msg_state:
            del admin_msg_state[uid]
            await msg.answer("✅ Message cancel ho gaya.")
        else:
            await msg.answer("Koi active operation nahi hai.")

    # ─────────────── BROADCAST CONFIRM ───────────────
    @dp.callback_query_handler(lambda cb: cb.data in ("bc_confirm", "bc_cancel"))
    async def broadcast_confirm_cb(cb: types.CallbackQuery):
        uid = cb.from_user.id
        if not is_owner(uid):
            await cb.answer("Access nahi!", show_alert=True)
            return
        if cb.data == "bc_cancel":
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
        sent = failed = 0
        for u in targets:
            try:
                await bot.send_message(u.user_id, msg_text, parse_mode="Markdown")
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        broadcast_state.pop(uid, None)
        await cb.message.answer(
            f"✅ *Broadcast Complete!*\n\nSent: {sent}/{len(targets)}\nFailed: {failed}",
            parse_mode="Markdown",
        )
        await cb.answer()

    # ─────────────── TEXT: Broadcast / Admin msg ───────────────
    @dp.message_handler(lambda msg: msg.from_user.id == OWNER_ID and (
        msg.from_user.id in broadcast_state or msg.from_user.id in admin_msg_state
    ))
    async def admin_text_handler(msg: types.Message):
        uid = msg.from_user.id
        text = msg.text.strip() if msg.text else ""

        # Broadcast flow
        if uid in broadcast_state and broadcast_state[uid]["step"] == "waiting_message":
            if text.startswith("/"):
                await msg.answer("⚠️ Broadcast active hai. /cancel karo ya message type karo.")
                return
            broadcast_state[uid]["text"] = text
            broadcast_state[uid]["step"] = "waiting_confirm"
            users = await db.get_all_users()
            count = sum(1 for u in users if not u.is_banned)
            await msg.answer(
                f"📢 *Preview:*\n\n{text}\n\n*{count} users* ko jaayega. Confirm?",
                parse_mode="Markdown",
                reply_markup=kb_confirm_broadcast(),
            )
            return

        # Admin direct message flow
        if uid in admin_msg_state and admin_msg_state[uid]["step"] == "waiting_msg":
            target_id = admin_msg_state[uid]["target_id"]
            admin_msg_state.pop(uid, None)
            try:
                await bot.send_message(
                    target_id,
                    f"📩 *Admin Message:*\n\n{text}",
                    parse_mode="Markdown",
                )
                await msg.answer(f"✅ Message bhej diya user `{target_id}` ko.", parse_mode="Markdown")
            except Exception as e:
                await msg.answer(f"❌ Send fail: {e}")

    return broadcast_state
