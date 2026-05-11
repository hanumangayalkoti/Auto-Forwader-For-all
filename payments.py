import hashlib
import hmac
import json
import logging

import razorpay
from aiohttp import web

from config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET, SUBSCRIPTION_PRICE, SUBSCRIPTION_DAYS
from database import create_payment, confirm_payment, extend_subscription

logger = logging.getLogger(__name__)

rzp_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

bot_instance = None


def set_bot(bot):
    global bot_instance
    bot_instance = bot


async def create_order(user_id: int) -> tuple[str, str]:
    order = rzp_client.order.create({
        "amount": SUBSCRIPTION_PRICE,
        "currency": "INR",
        "notes": {"user_id": str(user_id)},
    })
    order_id = order["id"]
    await create_payment(user_id, order_id, SUBSCRIPTION_PRICE)
    payment_link = rzp_client.payment_link.create({
        "amount": SUBSCRIPTION_PRICE,
        "currency": "INR",
        "description": "DealsKoti Bot — 30 din ka access",
        "notes": {"user_id": str(user_id), "order_id": order_id},
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "callback_url": "",
        "callback_method": "get",
    })
    link_url = payment_link.get("short_url", "")
    return order_id, link_url


# FIX: Security Bug — agar RAZORPAY_WEBHOOK_SECRET set nahi hai toh
# pehle True return hota tha, matlab koi bhi fake payment bhej sakta tha.
# Ab agar secret missing hai toh False return hoga aur webhook reject hoga.
def _verify_signature(body: bytes, signature: str) -> bool:
    if not RAZORPAY_WEBHOOK_SECRET:
        logger.error(
            "[Payments] RAZORPAY_WEBHOOK_SECRET environment variable set nahi hai! "
            "Webhook reject ho raha hai — please secret set karo Railway/Railway dashboard mein."
        )
        return False
    if not signature:
        logger.warning("[Payments] Webhook received bina signature ke — reject kar rahe hain.")
        return False
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def webhook_handler(request: web.Request) -> web.Response:
    body = await request.read()
    signature = request.headers.get("X-Razorpay-Signature", "")

    # FIX: Agar signature verify nahi hoti toh 200 ok return karo
    # (Razorpay retry nahi karta agar non-200 mile, isliye 200 safe hai)
    # lekin payment process mat karo
    if not _verify_signature(body, signature):
        logger.warning("[Payments] Webhook signature invalid — ignoring.")
        return web.Response(status=200, text="ok")

    try:
        data = json.loads(body)
    except Exception:
        return web.Response(status=200, text="ok")

    event = data.get("event", "")
    if event not in ("payment.captured", "payment_link.paid"):
        return web.Response(status=200, text="ok")

    try:
        if event == "payment.captured":
            payload = data["payload"]["payment"]["entity"]
            payment_id = payload["id"]
            order_id = payload.get("order_id", "")
            notes = payload.get("notes", {})
        else:
            payload = data["payload"]["payment_link"]["entity"]
            payment_id = data["payload"].get("payment", {}).get("entity", {}).get("id", "")
            notes = payload.get("notes", {})
            order_id = notes.get("order_id", "")

        if not order_id:
            logger.warning(f"[Payments] order_id nahi mila event={event} mein — skip kar rahe hain.")
            return web.Response(status=200, text="ok")

        uid = await confirm_payment(order_id, payment_id)
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
