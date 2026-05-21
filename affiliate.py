"""
Affiliate / Refer & Earn handlers.
Registered into the main bot dispatcher.
"""
import logging

from aiogram import types, Bot
from aiogram.dispatcher import Dispatcher

import database as db
from config import (
    AFFILIATE_MIN_WITHDRAW_USD, AFFILIATE_COMMISSION_PERCENT,
    USD_TO_INR, OWNER_ID, MAIN_BOT_USERNAME,
)
from keyboards import (
    kb_refer, kb_withdraw_method, kb_withdraw_confirm,
    kb_admin_withdrawal, kb_main_only,
)

logger = logging.getLogger(__name__)

# Temporary state for withdraw flow: user_id → dict
_withdraw_state: dict[int, dict] = {}


def register_affiliate(dp: Dispatcher, bot: Bot):

    # ---- /refer ----
    @dp.message_handler(commands=["refer"])
    async def cmd_refer(msg: types.Message):
        uid = msg.from_user.id
        await _show_refer(msg, uid, bot)

    # ---- CALLBACK: ref ----
    @dp.callback_query_handler(lambda cb: cb.data == "ref")
    async def cb_refer(cb: types.CallbackQuery):
        uid = cb.from_user.id
        await _show_refer_edit(cb, uid, bot)

    # ---- CALLBACK: rst (stats) ----
    @dp.callback_query_handler(lambda cb: cb.data == "rst")
    async def cb_stats(cb: types.CallbackQuery):
        uid = cb.from_user.id
        aff = await db.get_or_create_affiliate(uid)
        text = (
            f"📊 *Referral Stats*\n\n"
            f"Referral Code: `{aff.code}`\n"
            f"Total Referred: {aff.total_referred} log\n"
            f"Total Earned: ${aff.total_earned_usd:.2f}\n"
            f"Current Balance: ${aff.balance_usd:.2f}\n"
            f"Min Withdraw: ${AFFILIATE_MIN_WITHDRAW_USD}\n\n"
            f"Commission: {AFFILIATE_COMMISSION_PERCENT}% — sirf pehli payment pe"
        )
        await cb.message.edit_text(
            text, parse_mode="Markdown",
            reply_markup=kb_refer(
                aff.code, aff.balance_usd, aff.total_earned_usd,
                aff.total_referred, MAIN_BOT_USERNAME
            ),
        )
        await cb.answer()

    # ---- CALLBACK: rws (withdraw start) ----
    @dp.callback_query_handler(lambda cb: cb.data == "rws")
    async def cb_withdraw_start(cb: types.CallbackQuery):
        uid = cb.from_user.id
        aff = await db.get_or_create_affiliate(uid)

        if aff.balance_usd < AFFILIATE_MIN_WITHDRAW_USD:
            await cb.answer(
                f"Minimum ${AFFILIATE_MIN_WITHDRAW_USD} chahiye. "
                f"Abhi: ${aff.balance_usd:.2f}",
                show_alert=True,
            )
            return

        _withdraw_state[uid] = {"step": "choose_method", "amount": aff.balance_usd}
        await cb.message.edit_text(
            f"💰 *Withdrawal Request*\n\n"
            f"Available: *${aff.balance_usd:.2f}*\n\n"
            f"Payment method choose karo:",
            parse_mode="Markdown",
            reply_markup=kb_withdraw_method(),
        )
        await cb.answer()

    # ---- CALLBACK: rwm (method chosen) ----
    @dp.callback_query_handler(lambda cb: cb.data.startswith("rwm:"))
    async def cb_withdraw_method_chosen(cb: types.CallbackQuery):
        uid = cb.from_user.id
        method = cb.data.split(":")[1]   # upi / bank

        if uid not in _withdraw_state:
            await cb.answer("Session expire ho gaya. /refer se dobara try karo.", show_alert=True)
            return

        _withdraw_state[uid]["method"] = method
        _withdraw_state[uid]["step"] = "enter_details"

        if method == "upi":
            prompt = "📱 Apna *UPI ID* type karo:\nExample: yourname@paytm"
        else:
            prompt = (
                "🏦 Apni *Bank Details* type karo:\n"
                "Format:\nBank: HDFC\nAccount: 1234567890\nIFSC: HDFC0001234\nName: Tera Naam"
            )

        await cb.message.edit_text(prompt, parse_mode="Markdown")
        await cb.answer()

    # ---- CALLBACK: rwc (withdrawal confirm) ----
    @dp.callback_query_handler(lambda cb: cb.data == "rwc")
    async def cb_withdraw_confirm(cb: types.CallbackQuery):
        uid = cb.from_user.id
        state = _withdraw_state.get(uid, {})
        if not state or state.get("step") != "confirm":
            await cb.answer("Session expire. /refer se dobara try karo.", show_alert=True)
            return

        amount = state["amount"]
        method = state["method"]
        details = state["details"]

        wr = await db.create_withdrawal_request(uid, amount, method, details)

        user = await db.get_user(uid)
        username = f"@{user.username}" if user and user.username else f"ID:{uid}"
        plan_name = user.plan if user else "free"

        # Notify admin
        await bot.send_message(
            OWNER_ID,
            f"💰 *New Withdrawal Request!*\n\n"
            f"User: {username} (`{uid}`)\n"
            f"Plan: {plan_name}\n"
            f"Amount: *${amount:.2f}* (≈₹{amount * USD_TO_INR:.0f})\n"
            f"Method: {method.upper()}\n"
            f"Details: `{details}`\n\n"
            f"Request ID: #{wr.id}",
            parse_mode="Markdown",
            reply_markup=kb_admin_withdrawal(wr.id),
        )

        _withdraw_state.pop(uid, None)

        await cb.message.edit_text(
            f"✅ *Withdrawal Request Submit Ho Gaya!*\n\n"
            f"Amount: *${amount:.2f}*\n"
            f"Method: {method.upper()}\n\n"
            f"Admin process karega — 1-2 working days mein paise milenge.",
            parse_mode="Markdown",
            reply_markup=kb_main_only(),
        )
        await cb.answer()

    # ---- ADMIN: Withdrawal Done / Reject ----
    @dp.callback_query_handler(lambda cb: cb.data.startswith("wrd:") or cb.data.startswith("wrr:"))
    async def cb_admin_withdrawal_action(cb: types.CallbackQuery):
        if cb.from_user.id != OWNER_ID:
            await cb.answer("Access nahi!", show_alert=True)
            return

        action, wr_id_str = cb.data.split(":")
        wr_id = int(wr_id_str)
        status = "done" if action == "wrd" else "rejected"

        wr = await db.resolve_withdrawal(wr_id, status)
        if not wr:
            await cb.answer("Request nahi mili!", show_alert=True)
            return

        # Notify user
        if status == "done":
            user_msg = (
                f"✅ *Paise Bhej Diye Gaye!*\n\n"
                f"Amount: *${wr.amount_usd:.2f}*\n"
                f"Method: {wr.payment_method.upper()}\n"
                f"Details: `{wr.payment_details}`\n\n"
                f"1-2 din mein account mein aa jaayenge."
            )
        else:
            user_msg = (
                f"❌ *Withdrawal Reject Ho Gaya*\n\n"
                f"Amount: ${wr.amount_usd:.2f}\n"
                f"Reason: {wr.admin_note or 'Admin ne reject kiya'}\n\n"
                f"Koi query ho to support se sampark karo."
            )

        try:
            await bot.send_message(wr.user_id, user_msg, parse_mode="Markdown")
        except Exception:
            pass

        action_label = "Done ✅" if status == "done" else "Rejected ❌"
        await cb.message.edit_text(
            cb.message.text + f"\n\n*Status: {action_label}*",
            parse_mode="Markdown",
        )
        await cb.answer(f"Marked as {status}")

    # ---- TEXT: Withdrawal details input ----
    @dp.message_handler(lambda msg: msg.from_user.id in _withdraw_state
                        and _withdraw_state[msg.from_user.id].get("step") == "enter_details")
    async def text_withdraw_details(msg: types.Message):
        uid = msg.from_user.id
        details = msg.text.strip()
        state = _withdraw_state[uid]
        state["details"] = details
        state["step"] = "confirm"

        amount = state["amount"]
        method = state["method"]

        await msg.answer(
            f"📋 *Confirm Withdrawal*\n\n"
            f"Amount: *${amount:.2f}* (≈₹{amount * USD_TO_INR:.0f})\n"
            f"Method: {method.upper()}\n"
            f"Details: `{details}`\n\n"
            f"Sab sahi hai?",
            parse_mode="Markdown",
            reply_markup=kb_withdraw_confirm(round(amount, 2)),
        )
