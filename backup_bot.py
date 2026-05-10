"""
DealsKoti Backup Bot
--------------------
Backup bot in case the main bot gets deleted/banned.
Users can check their subscription status here.
Admin can manage users from here too.

Set BACKUP_BOT_TOKEN in environment variables.
Run: python backup_bot.py
"""
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

from config import OWNER_ID, BACKUP_BOT_TOKEN
from database import (
    init_db,
    get_or_create_user,
    get_user,
    get_all_users,
    check_access,
    ban_user,
    unban_user,
    give_days,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

bot = Bot(token=BACKUP_BOT_TOKEN, parse_mode="Markdown")
dp = Dispatcher(bot)


def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


# ---- /start ----

@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    uid = msg.from_user.id
    user = await get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
    allowed, reason = check_access(user)
    now = datetime.utcnow()

    if reason == "banned":
        await msg.answer("🚫 Aapka account ban hai. Support se contact karo.")
        return

    if "subscribed" in reason:
        days_left = (user.sub_end - now).days if user.sub_end else 0
        end_str = user.sub_end.strftime("%d %b %Y") if user.sub_end else "N/A"
        status = f"✅ Paid Active — {end_str} tak ({days_left} din bache)"
    elif "trial" in reason:
        d = reason.split(":")[1]
        end_str = user.trial_end.strftime("%d %b %Y") if user.trial_end else "N/A"
        status = f"⏳ Free Trial — {end_str} tak ({d} din bache)"
    else:
        status = "❌ Expired"

    await msg.answer(
        "🔄 *DealsKoti Backup Bot*\n\n"
        "Ye main bot ka backup hai.\n"
        "Agar main bot nahi chal raha toh yahan se apna status check karo.\n\n"
        "━━━━━━━━━━━━━━━\n"
        f"📋 *Aapka Status:*\n{status}\n"
        "━━━━━━━━━━━━━━━\n\n"
        "Commands:\n"
        "/myplan — Subscription details\n"
        "/help — Help\n\n"
        "⚠️ Forwarding setup ke liye main bot use karo.",
    )


# ---- /myplan ----

@dp.message_handler(commands=["myplan"])
async def cmd_myplan(msg: types.Message):
    uid = msg.from_user.id
    user = await get_or_create_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
    _, reason = check_access(user)
    now = datetime.utcnow()

    if reason == "banned":
        await msg.answer("🚫 Account ban hai. Support se contact karo.")
        return

    if "subscribed" in reason:
        days_left = (user.sub_end - now).days if user.sub_end else 0
        end_str = user.sub_end.strftime("%d %b %Y") if user.sub_end else "N/A"
        await msg.answer(
            "💳 *Mera Plan*\n\n"
            "Plan: ₹69/month\n"
            f"Status: ✅ Active\n"
            f"Valid Until: {end_str}\n"
            f"Din Bache: {days_left}"
        )
    elif "trial" in reason:
        d = reason.split(":")[1]
        end_str = user.trial_end.strftime("%d %b %Y") if user.trial_end else "N/A"
        await msg.answer(
            "💳 *Mera Plan*\n\n"
            "Plan: Free Trial\n"
            f"Status: ⏳ Active\n"
            f"Trial Ends: {end_str}\n"
            f"Din Bache: {d}"
        )
    else:
        await msg.answer(
            "💳 *Mera Plan*\n\n"
            "Status: ❌ Expired\n\n"
            "Subscribe karne ke liye main bot pe /subscribe karo."
        )


# ---- /help ----

@dp.message_handler(commands=["help"])
async def cmd_help(msg: types.Message):
    text = (
        "🔄 *DealsKoti Backup Bot — Help*\n\n"
        "Ye bot ek backup hai main bot ka.\n"
        "Agar main bot delete/ban ho jaye toh:\n"
        "1. Yahan /start karo — aapka subscription safe hai\n"
        "2. Admin se contact karo — naya main bot milega\n"
        "3. Subscription ka data is bot mein bhi accessible hai\n\n"
        "Commands:\n"
        "/start — Status check\n"
        "/myplan — Plan details\n"
        "/help — Ye message"
    )
    await msg.answer(text)


# ---- ADMIN: /admin ----

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
    banned = sum(1 for u in users if u.is_banned)
    await msg.answer(
        "👑 *Backup Bot — Admin*\n\n"
        f"Total Users: {total}\n"
        f"Active: {active}\n"
        f"Banned: {banned}\n\n"
        "Commands:\n"
        "/users — User list\n"
        "/give <id> <days> — Days do\n"
        "/ban <id> — Ban karo\n"
        "/unban <id> — Unban karo\n"
        "/check <id> — User details"
    )


# ---- ADMIN: /users ----

@dp.message_handler(commands=["users"])
async def cmd_users(msg: types.Message):
    if not is_owner(msg.from_user.id):
        return
    users = await get_all_users()
    now = datetime.utcnow()
    if not users:
        await msg.answer("Koi user nahi.")
        return
    lines = ["👥 *Users*\n"]
    for u in users[:50]:
        _, reason = check_access(u)
        if u.is_banned:
            s = "🚫"
        elif "subscribed" in reason:
            s = f"✅ ({(u.sub_end - now).days}d)"
        elif "trial" in reason:
            s = f"⏳ ({reason.split(':')[1]}d)"
        else:
            s = "❌"
        name = u.full_name or u.username or str(u.user_id)
        lines.append(f"`{u.user_id}` {name} {s}")
    if len(users) > 50:
        lines.append(f"...aur {len(users) - 50} users")
    await msg.answer("\n".join(lines))


# ---- ADMIN: /give ----

@dp.message_handler(commands=["give"])
async def cmd_give(msg: types.Message):
    if not is_owner(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) != 3:
        await msg.answer("Usage: /give <user_id> <days>")
        return
    try:
        uid, days = int(parts[1]), int(parts[2])
    except ValueError:
        await msg.answer("Numbers dalo.")
        return
    ok = await give_days(uid, days)
    if ok:
        await msg.answer(f"✅ User `{uid}` ko {days} din diye!")
        try:
            await bot.send_message(uid, f"🎁 Admin ne {days} din ka access diya! /start karo.")
        except Exception:
            pass
    else:
        await msg.answer(f"❌ User `{uid}` nahi mila.")


# ---- ADMIN: /ban ----

@dp.message_handler(commands=["ban"])
async def cmd_ban(msg: types.Message):
    if not is_owner(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) != 2:
        await msg.answer("Usage: /ban <user_id>")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await msg.answer("Number dalo.")
        return
    if uid == OWNER_ID:
        await msg.answer("Khud ko ban nahi kar sakte!")
        return
    await ban_user(uid)
    await msg.answer(f"🚫 User `{uid}` ban ho gaya!")


# ---- ADMIN: /unban ----

@dp.message_handler(commands=["unban"])
async def cmd_unban(msg: types.Message):
    if not is_owner(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) != 2:
        await msg.answer("Usage: /unban <user_id>")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await msg.answer("Number dalo.")
        return
    await unban_user(uid)
    await msg.answer(f"✅ User `{uid}` unban ho gaya!")
    try:
        await bot.send_message(uid, "✅ Access restore ho gaya! /start karo.")
    except Exception:
        pass


# ---- ADMIN: /check ----

@dp.message_handler(commands=["check"])
async def cmd_check(msg: types.Message):
    if not is_owner(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) != 2:
        await msg.answer("Usage: /check <user_id>")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await msg.answer("Number dalo.")
        return
    user = await get_user(uid)
    if not user:
        await msg.answer(f"User `{uid}` nahi mila.")
        return
    _, reason = check_access(user)
    now = datetime.utcnow()
    if user.is_banned:
        status = "🚫 Banned"
    elif "subscribed" in reason:
        status = f"✅ Paid ({(user.sub_end - now).days}d left)"
    elif "trial" in reason:
        status = f"⏳ Trial ({reason.split(':')[1]}d left)"
    else:
        status = "❌ Expired"
    await msg.answer(
        f"👤 `{user.user_id}` — {user.full_name or user.username or 'N/A'}\n"
        f"Status: {status}\n"
        f"Trial: {user.trial_end}\n"
        f"Sub: {user.sub_end}"
    )


async def on_startup(_):
    await init_db()
    logging.info("Backup bot started.")


if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)
