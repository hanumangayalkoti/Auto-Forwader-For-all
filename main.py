import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.utils import executor
from aiohttp import web

from config import BOT_TOKEN, SIMI_BOT_TOKEN, WEBHOOK_PORT
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
    logger.info("Database init ho raha hai...")
    await init_db()

    logger.info("Saved sessions se reconnect ho raha hai...")
    await startup_connect_all()

    # Wire up bot references
    payments_set_bot(bot)
    scheduler_set_bot(bot)
    forwarder_set_bot(bot)

    start_scheduler()

    # Webhook server
    await run_webhook_server()

    # ── Simi Bot ──
    if SIMI_BOT_TOKEN:
        logger.info("Simi bot start ho rahi hai...")
        simi_bot, simi_dp = await setup_simi_bot()
        set_main_bot(bot)
        # Run Simi bot polling in background
        asyncio.create_task(_run_simi(simi_bot, simi_dp))
    else:
        logger.warning("SIMI_BOT_TOKEN nahi mila — Simi bot skip ho gayi")

    # Set bot commands
    await _set_commands()

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
    from aiogram.types import BotCommand
    commands = [
        BotCommand("start", "Main menu"),
        BotCommand("login", "Telegram login"),
        BotCommand("logout", "Logout"),
        BotCommand("tasks", "Tasks manage karo"),
        BotCommand("status", "Forwarding status"),
        BotCommand("startall", "Saare tasks start"),
        BotCommand("stopall", "Saare tasks stop"),
        BotCommand("myplan", "Plan details"),
        BotCommand("subscribe", "Plans dekho"),
        BotCommand("refer", "Refer & Earn"),
        BotCommand("help", "Help guide"),
        BotCommand("support", "AI support"),
    ]
    try:
        await bot.set_my_commands(commands)
        logger.info("Bot commands set ho gaye")
    except Exception as e:
        logger.warning(f"Commands set nahi ho sake: {e}")


if __name__ == "__main__":
    asyncio.run(main())
