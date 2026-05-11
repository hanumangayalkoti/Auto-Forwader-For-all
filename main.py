import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiohttp import web

from config import BOT_TOKEN, WEBHOOK_PORT
from database import init_db
from forwarder import startup_connect_all
from payments import create_webhook_app, set_bot as payments_set_bot
from scheduler import start_scheduler, set_bot as scheduler_set_bot
from admin import register_admin
from handlers import register_handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

bot = Bot(token=BOT_TOKEN, parse_mode="Markdown")
dp = Dispatcher(bot)

register_admin(dp, bot)
register_handlers(dp, bot)


async def run_webhook_server():
    app = create_webhook_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logging.info(f"Webhook server port {WEBHOOK_PORT} pe chal raha hai")


async def main():
    logging.info("Database init ho raha hai...")
    await init_db()

    logging.info("Saved sessions se reconnect ho raha hai...")
    await startup_connect_all()

    payments_set_bot(bot)
    scheduler_set_bot(bot)
    start_scheduler()

    await run_webhook_server()

    logging.info("Bot polling shuru ho raha hai...")
    try:
        await dp.skip_updates()
        await dp.start_polling()
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
