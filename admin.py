from datetime import datetime

from aiogram import types
from aiogram.dispatcher import Dispatcher

from config import OWNER_ID
from database import (
    get_all_users, get_user, ban_user, unban_user,
    give_days, get_payment_stats, get_expiring_users_list,
    check_access,
)
from keyboards import kb_confirm_broadcast, kb_main_menu_only

# Broadcast state: {owner_id: {"step": "waiting_message"|"waiting_confirm", "text": str}}
broadcast_state: dict[int, dict] = {}


def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


def register_admin(dp: Dispatcher):

    @dp.message_handler(commands=["admin"])
    async def cmd_admin(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        users = await get_all_users()
        now = datetime.utcnow()
        total = len(users)
        active = sum(1 for u in users if not u.is_banned and (
            (u.sub_end and u.sub_end > now) or (u.trial_end and u.trial_end > now and not u.sub_end)
        ))
        on_trial = sum(1 for u in users if not u.is_banned and not u.sub_end and u.trial_end and u.trial_end > now)
        paid_active = sum(1 for u in users if not u.is_banned and u.sub_end and u.sub_end > now)
        banned = sum(1 for u in users if u.is_banned)
        expired = sum(1 for u in users if not u.is_banned and (
            (u.sub_end and u.sub_end <= now) or
            (not u.sub_end and u.trial_end and u.trial_end <= now)
        ))
        total_rev, month_rev, pay_count = await get_payment_stats()
        text = (
            "👑 *Admin Dashboard*\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👥 *Users*\n"
            f"  Total: {total}\n"
            f"  Active (trial+paid): {active}\n"
            f"  Free Trial: {on_trial}\n"
            f"  Paid Active: {paid_active}\n"
            f"  Expired: {expired}\n"
            f"  Banned: {banned}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 *Revenue*\n"
            f"  Is Mahine: ₹{month_rev // 100}\n"
            f"  Total: ₹{total_rev // 100}\n"
            f"  Total Payments: {pay_count}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📋 *Commands*\n"
            f"/users — User list\n"
            f"/broadcast — Saare users ko message\n"
            f"/give <id> <days> — Free days do\n"
            f"/ban <id> — User ban karo\n"
            f"/unban <id> — Unban karo\n"
            f"/check <id> — User ki details\n"
            f"/revenue — Revenue report\n"
            f"/expiring — Expire hone wale users"
        )
        await msg.answer(text, parse_mode="Markdown")

    @dp.message_handler(commands=["users"])
    async def cmd_users(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        users = await get_all_users()
        now = datetime.utcnow()
        if not users:
            await msg.answer("Koi user nahi hai abhi.")
            return
        lines = ["👥 *All Users*\n"]
        for u in users[:50]:
            allowed, reason = check_access(u)
            if u.is_banned:
                status = "🚫 Banned"
            elif "subscribed" in reason:
                days_left = (u.sub_end - now).days if u.sub_end else 0
                status = f"✅ Paid ({days_left}d left)"
            elif "trial" in reason:
                d = reason.split(":")[1]
                status = f"⏳ Trial ({d}d left)"
            else:
                status = "❌ Expired"
            name = u.full_name or u.username or str(u.user_id)
            lines.append(f"`{u.user_id}` | {name} | {status}")
        if len(users) > 50:
            lines.append(f"\n...aur {len(users) - 50} users hain.")
        await msg.answer("\n".join(lines), parse_mode="Markdown")

    @dp.message_handler(commands=["broadcast"])
    async def cmd_broadcast(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        broadcast_state[msg.from_user.id] = {"step": "waiting_message", "text": ""}
        await msg.answer(
            "📢 *Broadcast*\n\n"
            "Kya message bhejnaa chahte ho saare users ko?\n"
            "Ab type karo (ya /cancel karo):",
            parse_mode="Markdown",
        )

    @dp.message_handler(commands=["give"])
    async def cmd_give(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        parts = msg.text.split()
        if len(parts) != 3:
            await msg.answer("Usage: `/give <user_id> <days>`", parse_mode="Markdown")
            return
        try:
            uid = int(parts[1])
            days = int(parts[2])
        except ValueError:
            await msg.answer("User ID aur days number mein hone chahiye.")
            return
        ok = await give_days(uid, days)
        if ok:
            await msg.answer(f"✅ User `{uid}` ko {days} din ka access de diya!", parse_mode="Markdown")
            try:
                from aiogram import Bot
                import config
                b = Bot(token=config.BOT_TOKEN)
                await b.send_message(
                    uid,
                    f"🎁 *Admin ne aapko {days} din ka free access diya hai!*\n\n"
                    "Ab /start karo aur enjoy karo!",
                    parse_mode="Markdown",
                )
                await b.session.close()
            except Exception:
                pass
        else:
            await msg.answer(f"❌ User `{uid}` nahi mila.", parse_mode="Markdown")

    @dp.message_handler(commands=["ban"])
    async def cmd_ban(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        parts = msg.text.split()
        if len(parts) != 2:
            await msg.answer("Usage: `/ban <user_id>`", parse_mode="Markdown")
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await msg.answer("User ID number mein dalo.")
            return
        await ban_user(uid)
        await msg.answer(f"🚫 User `{uid}` ban ho gaya!", parse_mode="Markdown")
        try:
            from aiogram import Bot
            import config
            b = Bot(token=config.BOT_TOKEN)
            await b.send_message(uid, "🚫 Aapka access band kar diya gaya hai.")
            await b.session.close()
        except Exception:
            pass

    @dp.message_handler(commands=["unban"])
    async def cmd_unban(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        parts = msg.text.split()
        if len(parts) != 2:
            await msg.answer("Usage: `/unban <user_id>`", parse_mode="Markdown")
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await msg.answer("User ID number mein dalo.")
            return
        await unban_user(uid)
        await msg.answer(f"✅ User `{uid}` unban ho gaya!", parse_mode="Markdown")
        try:
            from aiogram import Bot
            import config
            b = Bot(token=config.BOT_TOKEN)
            await b.send_message(
                uid,
                "✅ *Aapka access restore kar diya gaya hai!*\n\n/start karo.",
                parse_mode="Markdown",
            )
            await b.session.close()
        except Exception:
            pass

    @dp.message_handler(commands=["check"])
    async def cmd_check(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        parts = msg.text.split()
        if len(parts) != 2:
            await msg.answer("Usage: `/check <user_id>`", parse_mode="Markdown")
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await msg.answer("User ID number mein dalo.")
            return
        user = await get_user(uid)
        if not user:
            await msg.answer(f"❌ User `{uid}` nahi mila database mein.", parse_mode="Markdown")
            return
        allowed, reason = check_access(user)
        now = datetime.utcnow()
        trial_str = user.trial_end.strftime("%d %b %Y") if user.trial_end else "N/A"
        sub_str = user.sub_end.strftime("%d %b %Y") if user.sub_end else "N/A"
        join_str = user.join_date.strftime("%d %b %Y")
        if user.is_banned:
            status = "🚫 Banned"
        elif "subscribed" in reason:
            days_left = (user.sub_end - now).days if user.sub_end else 0
            status = f"✅ Paid Active ({days_left} din bacha)"
        elif "trial" in reason:
            d = reason.split(":")[1]
            status = f"⏳ Free Trial ({d} din bacha)"
        else:
            status = "❌ Expired"
        text = (
            f"👤 *User Details*\n\n"
            f"ID: `{user.user_id}`\n"
            f"Name: {user.full_name}\n"
            f"Username: @{user.username or 'N/A'}\n"
            f"Join Date: {join_str}\n\n"
            f"Trial Ends: {trial_str}\n"
            f"Sub Ends: {sub_str}\n"
            f"Status: {status}"
        )
        await msg.answer(text, parse_mode="Markdown")

    @dp.message_handler(commands=["revenue"])
    async def cmd_revenue(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        total_rev, month_rev, pay_count = await get_payment_stats()
        await msg.answer(
            f"💰 *Revenue Report*\n\n"
            f"Is Mahine: ₹{month_rev // 100}\n"
            f"Total (all time): ₹{total_rev // 100}\n"
            f"Total Successful Payments: {pay_count}",
            parse_mode="Markdown",
        )

    @dp.message_handler(commands=["expiring"])
    async def cmd_expiring(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        users = await get_expiring_users_list(5)
        if not users:
            await msg.answer("Koi user agle 5 din mein expire nahi ho raha.")
            return
        now = datetime.utcnow()
        lines = ["⚠️ *Expiring in 5 days*\n"]
        for u in users:
            days_left = (u.sub_end - now).days if u.sub_end else 0
            name = u.full_name or u.username or str(u.user_id)
            lines.append(f"`{u.user_id}` | {name} | {days_left} din bacha")
        await msg.answer("\n".join(lines), parse_mode="Markdown")

    @dp.message_handler(commands=["cancel"])
    async def cmd_cancel(msg: types.Message):
        if not is_owner(msg.from_user.id):
            return
        if msg.from_user.id in broadcast_state:
            del broadcast_state[msg.from_user.id]
            await msg.answer("✅ Cancel ho gaya.")

    return broadcast_state
