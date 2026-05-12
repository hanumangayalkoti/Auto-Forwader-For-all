import asyncio
import hashlib
import hmac
import json
import logging

import razorpay
from aiohttp import web

from config import (
    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET,
    SUBSCRIPTION_PRICE, SUBSCRIPTION_DAYS
)
from database import create_payment, confirm_payment, extend_subscription

logger = logging.getLogger(__name__)

bot_instance = None


def set_bot(bot):
    global bot_instance
    bot_instance = bot


def _get_client() -> razorpay.Client:
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


async def create_order(user_id: int) -> tuple[str, str]:
    amount_paise = int(SUBSCRIPTION_PRICE * 100)  # Razorpay paise mein leta hai

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "description": "DealsKoti Bot — 30 din ka access",
        "customer": {
            "name": f"User {user_id}",
        },
        "notes": {
            "user_id": str(user_id),
        },
        "reminder_enable": False,
    }

    client = _get_client()
    data = await asyncio.to_thread(client.payment_link.create, payload)

    link_id = data.get("id", "")
    link_url = data.get("short_url", "")

    if not link_id:
        logger.error(f"[Razorpay] Payment link create failed: {data}")
        raise Exception(f"Razorpay link create error: {data}")

    await create_payment(user_id, link_id, amount_paise)
    return link_id, link_url


async def webhook_handler(request: web.Request) -> web.Response:
    body = await request.read()

    # Signature verify karo
    signature = request.headers.get("X-Razorpay-Signature", "")
    if RAZORPAY_WEBHOOK_SECRET and signature:
        expected = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            logger.warning("[Webhook] Invalid signature — request reject.")
            return web.Response(status=200, text="ok")

    try:
        data = json.loads(body)
    except Exception:
        return web.Response(status=200, text="ok")

    event = data.get("event", "")
    logger.info(f"[Webhook] Event received: {event}")

    # Sirf ye dono events handle karo
    if event not in ("payment_link.paid", "payment.captured"):
        return web.Response(status=200, text="ok")

    try:
        payload = data.get("payload", {})

        if event == "payment_link.paid":
            link_entity = payload.get("payment_link", {}).get("entity", {})
            payment_entity = payload.get("payment", {}).get("entity", {})
            link_id = link_entity.get("id", "")
            payment_id = str(payment_entity.get("id", ""))
            notes = link_entity.get("notes", {})

        else:  # payment.captured
            payment_entity = payload.get("payment", {}).get("entity", {})
            payment_id = str(payment_entity.get("id", ""))
            notes = payment_entity.get("notes", {})
            # payment.captured mein link_id nahi hota, order_id use karo
            link_id = str(payment_entity.get("order_id", "") or payment_entity.get("invoice_id", ""))

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
