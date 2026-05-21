import asyncio
import hashlib
import hmac
import json
import logging

import razorpay
from aiohttp import web

from config import (
    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET,
    PLAN_INFO, USD_TO_INR,
)
import database as db

logger = logging.getLogger(__name__)
bot_instance = None


def set_bot(bot):
    global bot_instance
    bot_instance = bot


def _get_client() -> razorpay.Client:
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


async def create_order(
    user_id: int, plan: str, billing: str
) -> tuple[str, str, int, float]:
    """
    Returns (order_id, payment_link_url, amount_inr_paise, amount_usd)
    billing: 'monthly' | 'annual'
    """
    info = PLAN_INFO[plan]
    if billing == "annual":
        amount_usd = info["annual_usd"]
        amount_inr = info["annual_inr"]
    else:
        amount_usd = info["monthly_usd"]
        amount_inr = info["monthly_inr"]

    amount_paise = amount_inr * 100   # Razorpay paise mein

    plan_label = info["name"]
    billing_label = "Monthly" if billing == "monthly" else "Annual"

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "description": f"Forward Bot — {plan_label} {billing_label}",
        "customer": {
            "name": f"User {user_id}",
        },
        "notes": {
            "user_id": str(user_id),
            "plan": plan,
            "billing": billing,
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

    await db.create_payment(user_id, link_id, plan, billing, amount_paise, amount_usd)
    return link_id, link_url, amount_paise, amount_usd


async def webhook_handler(request: web.Request) -> web.Response:
    body = await request.read()
    sig = request.headers.get("X-Razorpay-Signature", "")

    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, sig):
        logger.warning("[Razorpay] Webhook signature mismatch")
        return web.Response(status=400, text="Bad signature")

    try:
        payload = json.loads(body)
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    event = payload.get("event", "")

    if event == "payment_link.paid":
        await _handle_payment_paid(payload)
    elif event == "payment.failed":
        logger.info("[Razorpay] Payment failed event received")

    return web.Response(text="OK")


async def _handle_payment_paid(payload: dict):
    try:
        pl = payload["payload"]["payment_link"]["entity"]
        payment_entity = payload["payload"]["payment"]["entity"]
        order_id = pl["id"]
        payment_id = payment_entity["id"]
        notes = pl.get("notes", {})
        user_id = int(notes.get("user_id", 0))
        plan = notes.get("plan", "basic")
        billing = notes.get("billing", "monthly")

        if not user_id:
            logger.error("[Razorpay] user_id missing in webhook notes")
            return

        payment = await db.confirm_payment(order_id, payment_id)
        if not payment:
            logger.error(f"[Razorpay] Payment not found in DB: {order_id}")
            return

        info = PLAN_INFO[plan]
        days = info["days_annual"] if billing == "annual" else info["days_monthly"]

        await db.set_user_plan(user_id, plan, days)

        # Affiliate commission (first payment only)
        if payment.is_first_payment:
            user = await db.get_user(user_id)
            if user and user.referred_by:
                commission = await db.credit_affiliate_commission(
                    user.referred_by, payment.amount_usd
                )
                if bot_instance:
                    try:
                        await bot_instance.send_message(
                            user.referred_by,
                            f"🎉 *Referral Commission!*\n\n"
                            f"Tumhare referral ne subscribe kiya!\n"
                            f"*+${commission:.2f}* tumhare wallet mein add ho gaya!\n\n"
                            f"_Note: Commission sirf pehli payment pe milti hai._",
                            parse_mode="Markdown",
                        )
                    except Exception:
                        pass

        # Notify user
        if bot_instance:
            plan_name = info["name"]
            billing_label = "Annual" if billing == "annual" else "Monthly"
            try:
                await bot_instance.send_message(
                    user_id,
                    f"✅ *Payment Successful!*\n\n"
                    f"Plan: {plan_name} ({billing_label})\n"
                    f"Duration: {days} days\n\n"
                    f"Forwarding enjoy karo! 🚀",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"[Razorpay] User notify failed: {e}")

        logger.info(f"[Razorpay] Payment confirmed: user={user_id} plan={plan} billing={billing}")

    except Exception as e:
        logger.error(f"[Razorpay] Webhook processing error: {e}", exc_info=True)


def create_webhook_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/webhook", webhook_handler)
    return app
