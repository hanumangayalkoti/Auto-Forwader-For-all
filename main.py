import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiohttp import web

from config import BOT_TOKEN, SIMI_BOT_TOKEN, WEBHOOK_PORT
from migrate import run_migrations
from database import init_db
from forwarder import startup_connect_all, set_bot as forwarder_set_bot
from payments import create_webhook_app, set_bot as payments_set_bot
from scheduler import start_scheduler, set_bot as scheduler_set_bot
from admin import register_admin
from handlers import register_handlers
from affiliate import register_affiliate
from simi_bot import setup_simi_bot, set_main_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Main Bot ──
bot = Bot(token=BOT_TOKEN, parse_mode="Markdown")
dp = Dispatcher(bot)

register_admin(dp, bot)
register_handlers(dp, bot)
register_affiliate(dp, bot)


async def run_webhook_server():
    app = create_webhook_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logger.info(f"Webhook server port {WEBHOOK_PORT} pe chal raha hai")


async def main():
    # Step 1: Migrate old schema → new schema
    logger.info("Database migration check ho raha hai...")
    await run_migrations()

    # Step 2: Create any missing new tables
    logger.info("Database init ho raha hai...")
    await init_db()

    # Step 3: Reconnect saved Telethon sessions
    logger.info("Saved sessions se reconnect ho raha hai...")
    await startup_connect_all()

    # Step 4: Wire up bot references
    payments_set_bot(bot)
    scheduler_set_bot(bot)
    forwarder_set_bot(bot)

    # Step 5: Start scheduler
    start_scheduler()

    # Step 6: Start webhook server for Razorpay callbacks
    await run_webhook_server()

    # Step 7: Start Simi Bot in background
    if SIMI_BOT_TOKEN:
        logger.info("Simi bot start ho rahi hai...")
        simi_bot, simi_dp = await setup_simi_bot()
        set_main_bot(bot)
        asyncio.create_task(_run_simi(simi_bot, simi_dp))
    else:
        logger.warning("SIMI_BOT_TOKEN nahi mila — Simi bot skip ho gayi")

    # Step 8: Set bot menu commands (BotFather zaroorat nahi!)
    await _set_commands()

    # Step 9: Start main bot polling
    logger.info("Main bot polling shuru ho raha hai...")
    try:
        await dp.skip_updates()
        await dp.start_polling()
    finally:
        await bot.close()


async def _run_simi(simi_bot, simi_dp):
    try:
        await simi_dp.skip_updates()
        await simi_dp.start_polling()
    except Exception as e:
        logger.error(f"[Simi] Polling error: {e}", exc_info=True)
    finally:
        await simi_bot.close()


async def _set_commands():
    """
    Automatically sets bot commands visible in Telegram menu (/ ke baad dikhte hain).
    BotFather se manually add karne ki zaroorat nahi!
    set_my_commands() API call Telegram ko commands bhejta hai.
    """
    from aiogram.types import BotCommand
    commands = [
        BotCommand("start", "Main menu"),
        BotCommand("login", "Telegram se login karo"),
        BotCommand("logout", "Logout"),
        BotCommand("tasks", "Tasks manage karo"),
        BotCommand("status", "Forwarding status"),
        BotCommand("startall", "Saare tasks start karo"),
        BotCommand("stopall", "Saare tasks band karo"),
        BotCommand("myplan", "Plan aur expiry dekho"),
        BotCommand("subscribe", "Plans aur pricing"),
        BotCommand("refer", "Refer & Earn"),
        BotCommand("help", "Help guide"),
        BotCommand("support", "AI support — Simi"),
    ]
    try:
        await bot.set_my_commands(commands)
        logger.info("Bot commands menu mein set ho gaye")
    except Exception as e:
        logger.warning(f"Commands set nahi ho sake: {e}")


if __name__ == "__main__":
    asyncio.run(main())
