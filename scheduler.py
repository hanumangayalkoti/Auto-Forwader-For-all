import asyncio
import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import database as db
from config import plan_display_name

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_bot_ref = None
IST = timezone(timedelta(hours=5, minutes=30))


def set_bot(bot):
    global _bot_ref
    _bot_ref = bot


def start_scheduler():
    # Every hour: check plan expiry
    scheduler.add_job(
        check_plan_expiry,
        CronTrigger(minute=0),
        id="plan_expiry",
        replace_existing=True,
    )
    # IST midnight (UTC 18:30 = IST 00:00): reset daily counters
    scheduler.add_job(
        reset_daily_counters,
        CronTrigger(hour=18, minute=30, timezone="UTC"),
        id="daily_reset",
        replace_existing=True,
    )
    if not scheduler.running:
        scheduler.start()
    logger.info("Scheduler started")


async def check_plan_expiry():
    """Downgrade expired paid users to free and stop their tasks."""
    try:
        expired_users = await db.get_expired_paid_users()
        if not expired_users:
            return

        for user in expired_users:
            plan_name = plan_display_name(user.plan)
            # Downgrade to free
            await db.downgrade_to_free(user.user_id)
            # Stop all tasks
            await db.set_all_tasks_active(user.user_id, False)

            # Notify user
            if _bot_ref:
                try:
                    await _bot_ref.send_message(
                        user.user_id,
                        f"⏰ *{plan_name} Plan Expire Ho Gaya!*\n\n"
                        f"Tera subscription khatam ho gaya hai.\n"
                        f"Saare tasks band kar diye gaye hain.\n\n"
                        f"🔄 Renew karo full access ke liye:\n"
                        f"/subscribe",
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.warning(f"[Scheduler] Could not notify user {user.user_id}: {e}")

        logger.info(f"[Scheduler] Plan expiry checked: {len(expired_users)} users downgraded")

    except Exception as e:
        logger.error(f"[Scheduler] check_plan_expiry error: {e}", exc_info=True)


async def reset_daily_counters():
    """Reset free plan daily message counters at IST midnight."""
    try:
        await db.reset_all_daily_counts()
        logger.info("[Scheduler] Daily message counters reset (IST midnight)")
    except Exception as e:
        logger.error(f"[Scheduler] reset_daily_counters error: {e}", exc_info=True)
