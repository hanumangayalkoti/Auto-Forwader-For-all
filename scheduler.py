from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database import (
    get_users_expiring_in_days,
    get_trial_users_expiring_in_days,
    get_expired_users,
    get_expired_trial_users,
)
from keyboards import kb_subscribe_only

bot_instance = None


def set_bot(bot):
    global bot_instance
    bot_instance = bot


async def _send(user_id: int, text: str, kb=None):
    if not bot_instance:
        return
    try:
        await bot_instance.send_message(user_id, text, parse_mode="Markdown", reply_markup=kb)
    except Exception as err:
        print(f"[Scheduler] Failed to message {user_id}: {err}")


async def run_daily_check():
    print("[Scheduler] Daily check chal raha hai...")

    for days in [5, 2, 1]:
        users = await get_users_expiring_in_days(days)
        for u in users:
            if days == 5:
                msg = (
                    "⚠️ *Subscription Reminder*\n\n"
                    "Bhai, teri subscription *5 din* mein khatam ho jayegi!\n"
                    "Abhi renew kar lo takay forwarding band na ho.\n\n"
                    "₹69/month — neeche button dabao 👇"
                )
            elif days == 2:
                msg = (
                    "⚠️ *Urgent Reminder*\n\n"
                    "Sirf *2 din* bache hain teri subscription mein!\n"
                    "Abhi renew karo nahi to forwarding band ho jayegi.\n\n"
                    "₹69/month 👇"
                )
            else:
                msg = (
                    "🚨 *Last Warning!*\n\n"
                    "*Kal* teri subscription khatam ho rahi hai!\n"
                    "Abhi renew karo — bas ek click!\n\n"
                    "₹69/month 👇"
                )
            await _send(u.user_id, msg, kb_subscribe_only())

    for days in [2]:
        trial_users = await get_trial_users_expiring_in_days(days)
        for u in trial_users:
            msg = (
                "⏳ *Trial Khatam Hone Wala Hai!*\n\n"
                f"Bhai, tera *7 din ka free trial {days} din* mein khatam ho raha hai.\n"
                "Subscription lo nahi to forwarding band ho jayegi.\n\n"
                "*₹69/month* sirf — full features ke saath!\n"
                "Neeche button dabao 👇"
            )
            await _send(u.user_id, msg, kb_subscribe_only())

    expired_paid = await get_expired_users()
    for u in expired_paid:
        msg = (
            "❌ *Subscription Khatam Ho Gayi*\n\n"
            "Teri subscription expire ho gayi hai.\n"
            "Forwarding automatically band ho gayi hai.\n\n"
            "Wapas shuru karne ke liye *₹69/month* subscribe karo:"
        )
        await _send(u.user_id, msg, kb_subscribe_only())

    expired_trial = await get_expired_trial_users()
    for u in expired_trial:
        msg = (
            "⌛ *Free Trial Khatam Ho Gayi*\n\n"
            "Tera 7 din ka free trial expire ho gaya.\n"
            "Forwarding automatically band ho gayi hai.\n\n"
            "Continue karne ke liye sirf *₹69/month* — subscribe karo:"
        )
        await _send(u.user_id, msg, kb_subscribe_only())

    print("[Scheduler] Daily check complete.")


def start_scheduler():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_daily_check,
        CronTrigger(hour=4, minute=30),  # 10:00 AM IST = 04:30 UTC
        id="daily_check",
        replace_existing=True,
    )
    scheduler.start()
    print("[Scheduler] Started. Daily check at 10:00 AM IST.")
    return scheduler
