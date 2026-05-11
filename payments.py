import json
import logging
import uuid

import aiohttp
from aiohttp import web

from config import (
    CASHFREE_APP_ID, CASHFREE_SECRET_KEY, CASHFREE_ENV,
    SUBSCRIPTION_PRICE, SUBSCRIPTION_DAYS
)
from database import create_payment, confirm_payment, extend_subscription

logger = logging.getLogger(__name__)

bot_instance = None


def set_bot(bot):
    global bot_instance
    bot_instance = bot


def _base_url() -> str:
    if CASHFREE_ENV == "PROD":
        return "https://api.cashfree.com/pg"
    return "https://sandbox.cashfree.com/pg"


def _headers() -> dict:
    return {
        "x-client-id": CASHFREE_APP_ID,
        "x-client-secret": CASHFREE_SECRET_KEY,
        "x-api-version": "2023-08-01",
        "Content-Type": "application/json",
    }


async def create_order(user_id: int) -> tuple[str, str]:
    link_id = f"user_{user_id}_{uuid.uuid4().hex[:8]}"

    payload = {
        "link_id": link_id,
        "link_amount": SUBSCRIPTION_PRICE,
        "link_currency": "INR",
        "link_purpose": "DealsKoti Bot — 30 din ka access",
        "customer_details": {
            "customer_phone": "9999999999",
            "customer_name": f"User {user_id}",
        },
        "link_notify": {
            "send_sms": False,
            "send_email": False,
        },
        "link_meta": {
            "upi_intent": False,
        },
        "link_notes": {
            "user_id": str(user_id),
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{_base_url()}/links",
            headers=_headers(),
            json=payload,
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                logger.error(f"[Cashfree] Link create failed: {data}")
                raise Exception(f"Cashfree link create error: {data}")

    link_url = data.get("link_url", "")
    order_id = link_id

    await create_payment(user_id, order_id, int(SUBSCRIPTION_PRICE * 100))
    return order_id, link_url


async def webhook_handler(request: web.Request) -> web.Response:
    body = await request.read()

    try:
        data = json.loads(body)
    except Exception:
        return web.Response(status=200, text="ok")

    event = data.get("event", "") or data.get("type", "")
    logger.info(f"[Webhook] Event received: {event}")

    if "SUCCESS" not in event.upper() and "PAID" not in event.upper():
        return web.Response(status=200, text="ok")

    try:
        nested = data.get("data", {})

        # Payment Link webhook se data nikalo
        link_data = (
            data.get("linkDetails")
            or nested.get("link")
            or {}
        )
        payment_data = (
            data.get("paymentDetails")
            or nested.get("payment")
            or {}
        )

        link_id = (
            link_data.get("linkId")
            or link_data.get("link_id")
            or ""
        )
        payment_id = str(
            payment_data.get("cfPaymentId")
            or payment_data.get("cf_payment_id")
            or ""
        )

        notes = (
            link_data.get("linkNotes")
            or link_data.get("link_notes")
            or {}
        )
        user_id_str = notes.get("user_id", "")

        logger.info(f"[Webhook] link_id={link_id} payment_id={payment_id} user_id={user_id_str}")

        if not link_id:
            logger.warning("[Webhook] link_id nahi mila — skip.")
            return web.Response(status=200, text="ok")

        uid = await confirm_payment(link_id, payment_id)
        if uid:
            await extend_subscription(uid, SUBSCRIPTION_DAYS)
            logger.info(f"[Webhook] Subscription extended for user {uid}")
            if bot_instance:
                try:
                    await bot_instance.send_message(
                        uid,
                        "✅ *Payment Successful!*\n\n"
                        "₹69 receive ho gaya.\n"
                        "30 din ka access mil gaya hai!\n\n"
                        "Ab /start karo aur forwarding enjoy karo! 🎉",
                        parse_mode="Markdown",
                    )
                except Exception as msg_err:
                    logger.warning(f"[Webhook] Message send failed for {uid}: {msg_err}")
        else:
            logger.warning(f"[Webhook] confirm_payment ne None return kiya for link_id={link_id}")

    except Exception as err:
        logger.exception(f"[Webhook Error] {err}")

    return web.Response(status=200, text="ok")


def create_webhook_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/webhook", webhook_handler)
    app.router.add_get("/health", lambda r: web.Response(text="ok"))
    return app
