import hashlib
import hmac
import json
import logging
import uuid

import aiohttp
from aiohttp import web

from config import (
    CASHFREE_APP_ID, CASHFREE_SECRET_KEY, CASHFREE_ENV,
    CASHFREE_WEBHOOK_SECRET, SUBSCRIPTION_PRICE, SUBSCRIPTION_DAYS
)
from database import create_payment, confirm_payment, extend_subscription

logger = logging.getLogger(__name__)

# Cashfree API base URL — TEST ya PROD ke hisaab se
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

bot_instance = None


def set_bot(bot):
    global bot_instance
    bot_instance = bot


async def create_order(user_id: int) -> tuple[str, str]:
    link_id = f"user_{user_id}_{uuid.uuid4().hex[:8]}"

    payload = {
        "link_id": link_id,
        "link_amount": SUBSCRIPTION_PRICE,
        "link_currency": "INR",
        "link_purpose": "DealsKoti Bot — 30 din ka access",
        "customer_details": {
            "customer_phone": "9999999999",  # Cashfree requires this field
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
    order_id = link_id  # link_id as order reference

    await create_payment(user_id, order_id, int(SUBSCRIPTION_PRICE * 100))  # DB mein paise store karo
    return order_id, link_url


def _verify_signature(body: bytes, timestamp: str, signature: str) -> bool:
    if not CASHFREE_WEBHOOK_SECRET:
        logger.error(
            "[Payments] CASHFREE_WEBHOOK_SECRET environment variable set nahi hai! "
            "Webhook reject ho raha hai — Railway dashboard mein secret set karo."
        )
        return False
    if not signature or not timestamp:
        logger.warning("[Payments] Webhook received bina signature/timestamp ke — reject kar rahe hain.")
        return False

    # Cashfree signature = HMAC-SHA256(timestamp + raw_body, secret)
    message = timestamp.encode() + body
    expected = hmac.new(
        CASHFREE_WEBHOOK_SECRET.encode(),
        message,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def webhook_handler(request: web.Request) -> web.Response:
    body = await request.read()
    timestamp = request.headers.get("x-webhook-timestamp", "")
    signature = request.headers.get("x-webhook-signature", "")

    if not _verify_signature(body, timestamp, signature):
        logger.warning("[Payments] Webhook signature invalid — ignoring.")
        return web.Response(status=200, text="ok")

    try:
        data = json.loads(body)
    except Exception:
        return web.Response(status=200, text="ok")

    event = data.get("type", "")

    # Cashfree events: PAYMENT_SUCCESS_WEBHOOK, PAYMENT_LINK_EVENT
    if event not in ("PAYMENT_SUCCESS_WEBHOOK", "PAYMENT_LINK_EVENT"):
        return web.Response(status=200, text="ok")

    try:
        payment_data = data.get("data", {})
        payment = payment_data.get("payment", {})
        link = payment_data.get("link", {})

        payment_id = payment.get("cf_payment_id", "")
        payment_status = payment.get("payment_status", "")

        # Link se notes/user_id nikalo
        notes = link.get("link_notes", {})
        user_id_str = notes.get("user_id", "")
        order_id = link.get("link_id", "")

        if payment_status != "SUCCESS":
            logger.info(f"[Payments] Payment status '{payment_status}' — ignoring.")
            return web.Response(status=200, text="ok")

        if not order_id or not user_id_str:
            logger.warning(f"[Payments] order_id ya user_id nahi mila — skip.")
            return web.Response(status=200, text="ok")

        uid = await confirm_payment(order_id, str(payment_id))
        if uid:
            await extend_subscription(uid, SUBSCRIPTION_DAYS)
            if bot_instance:
                await bot_instance.send_message(
                    uid,
                    "✅ *Payment Successful!*\n\n"
                    "₹69 receive ho gaya.\n"
                    "30 din ka access mil gaya hai!\n\n"
                    "Ab /start karo aur forwarding enjoy karo! 🎉",
                    parse_mode="Markdown",
                )
    except Exception as err:
        logger.exception(f"[Webhook Error] {err}")

    return web.Response(status=200, text="ok")


def create_webhook_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/webhook", webhook_handler)
    app.router.add_get("/health", lambda r: web.Response(text="ok"))
    return app
