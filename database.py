from datetime import datetime, timedelta, timezone
from typing import Optional
import re

from cryptography.fernet import Fernet
from sqlalchemy import select, update, delete, func, and_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from config import (
    DATABASE_URL, FERNET_KEY,
    AFFILIATE_COMMISSION_PERCENT, AFFILIATE_MIN_WITHDRAW_USD, USD_TO_INR,
    get_plan_limits, PLAN_INFO,
)
from models import (
    Base, User, TelethonSession, Task, Channel, LinkReplacer,
    Payment, Affiliate, WithdrawalRequest,
    ForwardedMessage, MessageQueue, SimiConversation, DailyMsgCount,
)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
_fernet = Fernet(FERNET_KEY) if FERNET_KEY else None

IST = timezone(timedelta(hours=5, minutes=30))


def _encrypt(text: str) -> str:
    if not _fernet:
        return text
    return _fernet.encrypt(text.encode()).decode()


def _decrypt(text: str) -> str:
    if not _fernet:
        return text
    try:
        return _fernet.decrypt(text.encode()).decode()
    except Exception:
        return text


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ══════════════════════════════════════════════
# USER
# ══════════════════════════════════════════════

async def get_user(user_id: int) -> Optional[User]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(User).where(User.user_id == user_id))
        return result.scalar_one_or_none()


async def get_or_create_user(user_id: int, username: str, full_name: str) -> tuple[User, bool]:
    """Returns (user, is_new)"""
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if user:
            user.username = username
            user.full_name = full_name
            await s.commit()
            return user, False
        user = User(
            user_id=user_id,
            username=username,
            full_name=full_name,
            plan="free",
            plan_end=None,
            join_date=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
        s.add(user)
        await s.commit()
        return user, True


async def get_all_users() -> list[User]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(User))
        return list(result.scalars().all())


async def ban_user(user_id: int):
    async with AsyncSessionLocal() as s:
        await s.execute(update(User).where(User.user_id == user_id).values(is_banned=True))
        await s.commit()


async def unban_user(user_id: int):
    async with AsyncSessionLocal() as s:
        await s.execute(update(User).where(User.user_id == user_id).values(is_banned=False))
        await s.commit()


async def give_days(user_id: int, days: int, plan: str = None):
    """Give free days to user. If plan given, also set/upgrade plan."""
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return None
        now = datetime.utcnow()
        current_end = user.plan_end if (user.plan_end and user.plan_end > now) else now
        user.plan_end = current_end + timedelta(days=days)
        if plan:
            user.plan = plan
        await s.commit()
        return user


async def remove_user_access(user_id: int):
    """Immediately expire plan"""
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(User).where(User.user_id == user_id).values(
                plan="free", plan_end=None
            )
        )
        await s.commit()


def check_access(user: User) -> tuple[bool, str]:
    """
    Returns (allowed, reason)
    reason: 'banned' | 'free' | 'subscribed:<plan>' | 'expired'
    """
    if user.is_banned:
        return False, "banned"
    if user.plan == "free":
        return True, "free"
    now = datetime.utcnow()
    if user.plan_end and user.plan_end > now:
        days_left = (user.plan_end - now).days
        return True, f"subscribed:{user.plan}:{days_left}"
    # Expired paid plan → fall back to free
    return True, "free"


async def set_user_plan(user_id: int, plan: str, days: int):
    """Activate or extend a plan"""
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return
        now = datetime.utcnow()
        current_end = user.plan_end if (user.plan_end and user.plan_end > now) else now
        user.plan = plan
        user.plan_end = current_end + timedelta(days=days)
        await s.commit()


async def get_expiring_users(days_ahead: int = 5) -> list[User]:
    async with AsyncSessionLocal() as s:
        now = datetime.utcnow()
        cutoff = now + timedelta(days=days_ahead)
        result = await s.execute(
            select(User).where(
                and_(
                    User.plan != "free",
                    User.plan_end != None,
                    User.plan_end > now,
                    User.plan_end <= cutoff,
                    User.is_banned == False,
                )
            )
        )
        return list(result.scalars().all())


async def get_expired_paid_users() -> list[User]:
    """Users whose paid plan expired — for scheduler"""
    async with AsyncSessionLocal() as s:
        now = datetime.utcnow()
        result = await s.execute(
            select(User).where(
                and_(
                    User.plan != "free",
                    User.plan_end != None,
                    User.plan_end <= now,
                    User.is_banned == False,
                )
            )
        )
        return list(result.scalars().all())


async def downgrade_to_free(user_id: int):
    """Move expired user back to free plan"""
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(User).where(User.user_id == user_id).values(plan="free", plan_end=None)
        )
        await s.commit()


async def set_referred_by(user_id: int, referrer_id: int):
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if user and user.referred_by is None:
            user.referred_by = referrer_id
            await s.commit()


# ══════════════════════════════════════════════
# SESSION
# ══════════════════════════════════════════════

async def save_session(user_id: int, session_string: str):
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(TelethonSession).where(TelethonSession.user_id == user_id)
        )
        existing = result.scalar_one_or_none()
        encrypted = _encrypt(session_string)
        if existing:
            existing.session_string = encrypted
            existing.updated_at = datetime.utcnow()
        else:
            s.add(TelethonSession(user_id=user_id, session_string=encrypted))
        await s.commit()


async def get_session(user_id: int) -> Optional[str]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(TelethonSession).where(TelethonSession.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row:
            return _decrypt(row.session_string)
        return None


async def delete_session(user_id: int):
    async with AsyncSessionLocal() as s:
        await s.execute(delete(TelethonSession).where(TelethonSession.user_id == user_id))
        await s.commit()


async def get_all_sessions() -> list[TelethonSession]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(TelethonSession))
        return list(result.scalars().all())


# ══════════════════════════════════════════════
# TASKS
# ══════════════════════════════════════════════

async def get_user_tasks(user_id: int) -> list[Task]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Task)
            .where(Task.user_id == user_id)
            .options(selectinload(Task.channels), selectinload(Task.link_replacers))
        )
        return list(result.scalars().all())


async def get_task(task_id: int) -> Optional[Task]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Task)
            .where(Task.id == task_id)
            .options(selectinload(Task.channels), selectinload(Task.link_replacers))
        )
        return result.scalar_one_or_none()


async def count_user_tasks(user_id: int) -> int:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(func.count()).where(Task.user_id == user_id)
        )
        return result.scalar() or 0


async def create_task(user_id: int, name: str) -> Task:
    async with AsyncSessionLocal() as s:
        task = Task(user_id=user_id, name=name)
        s.add(task)
        await s.commit()
        await s.refresh(task)
        return task


async def rename_task(task_id: int, name: str):
    async with AsyncSessionLocal() as s:
        await s.execute(update(Task).where(Task.id == task_id).values(name=name))
        await s.commit()


async def delete_task(task_id: int):
    async with AsyncSessionLocal() as s:
        await s.execute(delete(Task).where(Task.id == task_id))
        await s.commit()


async def set_task_active(task_id: int, active: bool):
    async with AsyncSessionLocal() as s:
        await s.execute(update(Task).where(Task.id == task_id).values(is_active=active))
        await s.commit()


async def set_all_tasks_active(user_id: int, active: bool):
    async with AsyncSessionLocal() as s:
        await s.execute(update(Task).where(Task.user_id == user_id).values(is_active=active))
        await s.commit()


async def update_task_settings(task_id: int, **kwargs):
    async with AsyncSessionLocal() as s:
        await s.execute(update(Task).where(Task.id == task_id).values(**kwargs))
        await s.commit()


async def get_all_active_tasks() -> list[Task]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Task)
            .where(Task.is_active == True)
            .options(selectinload(Task.channels), selectinload(Task.link_replacers))
        )
        return list(result.scalars().all())


# ══════════════════════════════════════════════
# CHANNELS
# ══════════════════════════════════════════════

async def set_task_channels(task_id: int, channel_type: str, channels: list[tuple[int, str]]):
    """Replace all channels of given type for a task"""
    async with AsyncSessionLocal() as s:
        await s.execute(
            delete(Channel).where(
                and_(Channel.task_id == task_id, Channel.type == channel_type)
            )
        )
        for cid, cname in channels:
            s.add(Channel(task_id=task_id, channel_id=cid, channel_name=cname, type=channel_type))
        await s.commit()


# ══════════════════════════════════════════════
# LINK REPLACERS
# ══════════════════════════════════════════════

async def get_link_replacers(task_id: int) -> list[LinkReplacer]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(LinkReplacer).where(LinkReplacer.task_id == task_id)
        )
        return list(result.scalars().all())


async def add_link_replacer(task_id: int, original: str, new: str) -> LinkReplacer:
    async with AsyncSessionLocal() as s:
        lr = LinkReplacer(task_id=task_id, original_link=original, new_link=new)
        s.add(lr)
        await s.commit()
        await s.refresh(lr)
        return lr


async def delete_link_replacer(lr_id: int):
    async with AsyncSessionLocal() as s:
        await s.execute(delete(LinkReplacer).where(LinkReplacer.id == lr_id))
        await s.commit()


async def delete_all_link_replacers(task_id: int):
    async with AsyncSessionLocal() as s:
        await s.execute(delete(LinkReplacer).where(LinkReplacer.task_id == task_id))
        await s.commit()


# ══════════════════════════════════════════════
# PAYMENTS
# ══════════════════════════════════════════════

async def has_any_payment(user_id: int) -> bool:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(func.count()).where(
                and_(Payment.user_id == user_id, Payment.status == "paid")
            )
        )
        return (result.scalar() or 0) > 0


async def create_payment(
    user_id: int, order_id: str, plan: str, billing: str,
    amount_inr: int, amount_usd: float
) -> Payment:
    is_first = not await has_any_payment(user_id)
    async with AsyncSessionLocal() as s:
        p = Payment(
            user_id=user_id,
            razorpay_order_id=order_id,
            plan=plan,
            billing=billing,
            amount_inr=amount_inr,
            amount_usd=amount_usd,
            status="pending",
            is_first_payment=is_first,
        )
        s.add(p)
        await s.commit()
        await s.refresh(p)
        return p


async def confirm_payment(order_id: str, payment_id: str) -> Optional[Payment]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Payment).where(Payment.razorpay_order_id == order_id)
        )
        p = result.scalar_one_or_none()
        if not p:
            return None
        p.razorpay_payment_id = payment_id
        p.status = "paid"
        await s.commit()
        return p


async def get_payment_stats() -> tuple[int, int, int]:
    """Returns (total_paise, this_month_paise, count)"""
    async with AsyncSessionLocal() as s:
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        total_res = await s.execute(
            select(func.sum(Payment.amount_inr)).where(Payment.status == "paid")
        )
        month_res = await s.execute(
            select(func.sum(Payment.amount_inr)).where(
                and_(Payment.status == "paid", Payment.created_at >= month_start)
            )
        )
        count_res = await s.execute(
            select(func.count()).where(Payment.status == "paid")
        )
        return (
            total_res.scalar() or 0,
            month_res.scalar() or 0,
            count_res.scalar() or 0,
        )


# ══════════════════════════════════════════════
# AFFILIATE
# ══════════════════════════════════════════════

def _generate_code(user_id: int) -> str:
    """Generate short unique referral code from user_id"""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    n = user_id
    code = ""
    while n:
        code = chars[n % len(chars)] + code
        n //= len(chars)
    return (code or "A").ljust(4, "0")[:8]


async def get_or_create_affiliate(user_id: int) -> Affiliate:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Affiliate).where(Affiliate.user_id == user_id)
        )
        aff = result.scalar_one_or_none()
        if aff:
            return aff
        code = _generate_code(user_id)
        aff = Affiliate(user_id=user_id, code=code)
        s.add(aff)
        await s.commit()
        await s.refresh(aff)
        return aff


async def get_affiliate_by_code(code: str) -> Optional[Affiliate]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Affiliate).where(Affiliate.code == code.upper())
        )
        return result.scalar_one_or_none()


async def credit_affiliate_commission(referrer_id: int, payment_amount_usd: float):
    """Credit 80% commission to referrer"""
    commission = round(payment_amount_usd * AFFILIATE_COMMISSION_PERCENT / 100, 2)
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Affiliate).where(Affiliate.user_id == referrer_id)
        )
        aff = result.scalar_one_or_none()
        if aff:
            aff.balance_usd = round(aff.balance_usd + commission, 2)
            aff.total_earned_usd = round(aff.total_earned_usd + commission, 2)
            aff.total_referred += 1
            await s.commit()
    return commission


async def get_total_referral_payouts() -> float:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(func.sum(WithdrawalRequest.amount_usd)).where(
                WithdrawalRequest.status == "done"
            )
        )
        return round(result.scalar() or 0.0, 2)


async def create_withdrawal_request(
    user_id: int, amount_usd: float, method: str, details: str
) -> WithdrawalRequest:
    async with AsyncSessionLocal() as s:
        wr = WithdrawalRequest(
            user_id=user_id,
            amount_usd=amount_usd,
            payment_method=method,
            payment_details=details,
        )
        s.add(wr)
        await s.commit()
        await s.refresh(wr)
        return wr


async def get_pending_withdrawals() -> list[WithdrawalRequest]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.status == "pending")
        )
        return list(result.scalars().all())


async def resolve_withdrawal(wr_id: int, status: str, note: str = ""):
    """Mark withdrawal as done/rejected and deduct balance"""
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.id == wr_id)
        )
        wr = result.scalar_one_or_none()
        if not wr:
            return None
        wr.status = status
        wr.admin_note = note
        wr.resolved_at = datetime.utcnow()
        if status == "done":
            aff_result = await s.execute(
                select(Affiliate).where(Affiliate.user_id == wr.user_id)
            )
            aff = aff_result.scalar_one_or_none()
            if aff:
                aff.balance_usd = max(0.0, round(aff.balance_usd - wr.amount_usd, 2))
        await s.commit()
        return wr


# ══════════════════════════════════════════════
# SKIP DUPLICATES
# ══════════════════════════════════════════════

async def is_message_forwarded(task_id: int, source_channel_id: int, message_id: int) -> bool:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(func.count()).where(
                and_(
                    ForwardedMessage.task_id == task_id,
                    ForwardedMessage.source_channel_id == source_channel_id,
                    ForwardedMessage.message_id == message_id,
                )
            )
        )
        return (result.scalar() or 0) > 0


async def mark_message_forwarded(task_id: int, source_channel_id: int, message_id: int):
    async with AsyncSessionLocal() as s:
        s.add(ForwardedMessage(
            task_id=task_id,
            source_channel_id=source_channel_id,
            message_id=message_id,
        ))
        await s.commit()


# ══════════════════════════════════════════════
# DAILY MESSAGE COUNT (Free plan)
# ══════════════════════════════════════════════

def _ist_today() -> str:
    now_ist = datetime.now(IST)
    return now_ist.strftime("%Y-%m-%d")


async def get_daily_count(user_id: int) -> int:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(DailyMsgCount).where(DailyMsgCount.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return 0
        today = _ist_today()
        if row.date_ist != today:
            return 0
        return row.count


async def increment_daily_count(user_id: int) -> int:
    """Increment and return new count"""
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(DailyMsgCount).where(DailyMsgCount.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        today = _ist_today()
        if not row:
            row = DailyMsgCount(user_id=user_id, count=1, date_ist=today)
            s.add(row)
        elif row.date_ist != today:
            row.count = 1
            row.date_ist = today
        else:
            row.count += 1
        await s.commit()
        return row.count


async def reset_all_daily_counts():
    """Called by scheduler at IST midnight"""
    async with AsyncSessionLocal() as s:
        today = _ist_today()
        await s.execute(
            update(DailyMsgCount).values(count=0, date_ist=today)
        )
        await s.commit()


# ══════════════════════════════════════════════
# SIMI CONVERSATIONS
# ══════════════════════════════════════════════

async def get_simi_history(user_id: int, limit: int = 20) -> list[SimiConversation]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(SimiConversation)
            .where(SimiConversation.user_id == user_id)
            .order_by(SimiConversation.created_at.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
        rows.reverse()
        return rows


async def add_simi_message(user_id: int, role: str, content: str):
    async with AsyncSessionLocal() as s:
        s.add(SimiConversation(user_id=user_id, role=role, content=content))
        await s.commit()
    # Keep only last 20 messages per user
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(SimiConversation.id)
            .where(SimiConversation.user_id == user_id)
            .order_by(SimiConversation.created_at.desc())
            .offset(20)
        )
        old_ids = [r[0] for r in result.all()]
        if old_ids:
            await s.execute(
                delete(SimiConversation).where(SimiConversation.id.in_(old_ids))
            )
            await s.commit()


async def clear_simi_history(user_id: int):
    async with AsyncSessionLocal() as s:
        await s.execute(
            delete(SimiConversation).where(SimiConversation.user_id == user_id)
        )
        await s.commit()
