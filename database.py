from datetime import datetime, timedelta
from typing import Optional

from cryptography.fernet import Fernet
from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from config import DATABASE_URL, FERNET_KEY, TRIAL_DAYS, SUBSCRIPTION_DAYS
from models import Base, User, TelethonSession, ForwardingGroup, Channel, Payment

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
_fernet = Fernet(FERNET_KEY) if FERNET_KEY else None


def _encrypt(text: str) -> str:
    if not _fernet:
        return text
    return _fernet.encrypt(text.encode()).decode()


def _decrypt(text: str) -> str:
    if not _fernet:
        return text
    return _fernet.decrypt(text.encode()).decode()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ---- USER CRUD ----

async def get_user(user_id: int) -> Optional[User]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(User).where(User.user_id == user_id)
        )
        return result.scalar_one_or_none()


async def get_or_create_user(user_id: int, username: str, full_name: str) -> User:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if user:
            user.username = username
            user.full_name = full_name
            await s.commit()
            return user
        now = datetime.utcnow()
        user = User(
            user_id=user_id,
            username=username,
            full_name=full_name,
            join_date=now,
            trial_end=now + timedelta(days=TRIAL_DAYS),
            created_at=now,
        )
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user


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


async def give_days(user_id: int, days: int):
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return False
        now = datetime.utcnow()
        base = user.sub_end if user.sub_end and user.sub_end > now else now
        user.sub_end = base + timedelta(days=days)
        await s.commit()
        return True


async def extend_subscription(user_id: int, days: int = SUBSCRIPTION_DAYS):
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return
        now = datetime.utcnow()
        base = user.sub_end if user.sub_end and user.sub_end > now else now
        user.sub_end = base + timedelta(days=days)
        await s.commit()


async def get_users_expiring_in_days(days: int) -> list[User]:
    async with AsyncSessionLocal() as s:
        now = datetime.utcnow()
        target_start = now + timedelta(days=days) - timedelta(hours=12)
        target_end = now + timedelta(days=days) + timedelta(hours=12)
        result = await s.execute(
            select(User).where(
                User.is_banned == False,
                User.sub_end.between(target_start, target_end),
            )
        )
        return list(result.scalars().all())


async def get_trial_users_expiring_in_days(days: int) -> list[User]:
    async with AsyncSessionLocal() as s:
        now = datetime.utcnow()
        target_start = now + timedelta(days=days) - timedelta(hours=12)
        target_end = now + timedelta(days=days) + timedelta(hours=12)
        result = await s.execute(
            select(User).where(
                User.is_banned == False,
                User.sub_end == None,
                User.trial_end.between(target_start, target_end),
            )
        )
        return list(result.scalars().all())


async def get_expired_users() -> list[User]:
    async with AsyncSessionLocal() as s:
        now = datetime.utcnow()
        result = await s.execute(
            select(User).where(
                User.is_banned == False,
                User.sub_end != None,
                User.sub_end < now,
            )
        )
        return list(result.scalars().all())


async def get_expired_trial_users() -> list[User]:
    async with AsyncSessionLocal() as s:
        now = datetime.utcnow()
        result = await s.execute(
            select(User).where(
                User.is_banned == False,
                User.sub_end == None,
                User.trial_end < now,
            )
        )
        return list(result.scalars().all())


# ---- ACCESS CHECK ----

def check_access(user: User) -> tuple[bool, str]:
    if user.is_banned:
        return False, "banned"
    now = datetime.utcnow()
    if user.sub_end and user.sub_end > now:
        return True, "subscribed"
    if user.trial_end and user.trial_end > now:
        remaining = (user.trial_end - now).days + 1
        return True, f"trial:{remaining}"
    return False, "expired"


# ---- SESSION CRUD ----

async def save_session(user_id: int, session_string: str):
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(TelethonSession).where(TelethonSession.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        encrypted = _encrypt(session_string)
        if row:
            row.session_string = encrypted
            row.updated_at = datetime.utcnow()
        else:
            row = TelethonSession(
                user_id=user_id,
                session_string=encrypted,
                updated_at=datetime.utcnow(),
            )
            s.add(row)
        await s.commit()


async def load_session(user_id: int) -> Optional[str]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(TelethonSession).where(TelethonSession.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return _decrypt(row.session_string)


async def delete_session(user_id: int):
    async with AsyncSessionLocal() as s:
        await s.execute(
            delete(TelethonSession).where(TelethonSession.user_id == user_id)
        )
        await s.commit()


async def get_all_sessions() -> list[tuple[int, str]]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(TelethonSession))
        rows = result.scalars().all()
        return [(r.user_id, _decrypt(r.session_string)) for r in rows]


# ---- GROUPS CRUD ----

async def get_user_groups(user_id: int) -> list[ForwardingGroup]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(ForwardingGroup)
            .where(ForwardingGroup.user_id == user_id)
            .options(selectinload(ForwardingGroup.channels))
        )
        return list(result.scalars().all())


async def count_user_groups(user_id: int) -> int:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(func.count()).where(ForwardingGroup.user_id == user_id)
        )
        return result.scalar() or 0


async def create_group(user_id: int, name: str) -> ForwardingGroup:
    async with AsyncSessionLocal() as s:
        g = ForwardingGroup(user_id=user_id, name=name, is_active=False)
        s.add(g)
        await s.commit()
        await s.refresh(g)
        return g


async def get_group(group_id: int) -> Optional[ForwardingGroup]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(ForwardingGroup)
            .where(ForwardingGroup.id == group_id)
            .options(selectinload(ForwardingGroup.channels))
        )
        return result.scalar_one_or_none()


async def rename_group(group_id: int, name: str):
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(ForwardingGroup)
            .where(ForwardingGroup.id == group_id)
            .values(name=name)
        )
        await s.commit()


async def set_group_active(group_id: int, active: bool):
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(ForwardingGroup)
            .where(ForwardingGroup.id == group_id)
            .values(is_active=active)
        )
        await s.commit()


# FIX: Group Delete Bug — pehle channels delete karo ORM se taaki FK constraint na tute
# Raw bulk DELETE bypass karta hai ORM cascade, isliye channels automatically delete nahi hote
# Ab hum group ko ORM se load karke delete karte hain — cascade="all, delete-orphan" fire hoga
async def delete_group(group_id: int):
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(ForwardingGroup)
            .where(ForwardingGroup.id == group_id)
            .options(selectinload(ForwardingGroup.channels))
        )
        g = result.scalar_one_or_none()
        if g:
            await s.delete(g)
            await s.commit()


async def set_group_active_for_user(user_id: int, active: bool):
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(ForwardingGroup)
            .where(ForwardingGroup.user_id == user_id)
            .values(is_active=active)
        )
        await s.commit()


# ---- CHANNELS CRUD ----

async def get_channels(group_id: int, ch_type: str) -> list[Channel]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Channel).where(
                Channel.group_id == group_id,
                Channel.type == ch_type,
            )
        )
        return list(result.scalars().all())


async def set_channels(group_id: int, ch_type: str, channel_list: list[tuple[int, str]]):
    async with AsyncSessionLocal() as s:
        await s.execute(
            delete(Channel).where(Channel.group_id == group_id, Channel.type == ch_type)
        )
        for ch_id, ch_name in channel_list:
            s.add(Channel(group_id=group_id, channel_id=ch_id, channel_name=ch_name, type=ch_type))
        await s.commit()


async def get_all_active_groups() -> list[ForwardingGroup]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(ForwardingGroup)
            .where(ForwardingGroup.is_active == True)
            .options(selectinload(ForwardingGroup.channels))
        )
        return list(result.scalars().all())


# ---- PAYMENTS CRUD ----

async def create_payment(user_id: int, order_id: str, amount: int) -> Payment:
    async with AsyncSessionLocal() as s:
        p = Payment(user_id=user_id, razorpay_order_id=order_id, amount=amount, status="pending")
        s.add(p)
        await s.commit()
        await s.refresh(p)
        return p


async def confirm_payment(order_id: str, payment_id: str):
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Payment).where(Payment.razorpay_order_id == order_id)
        )
        p = result.scalar_one_or_none()
        if p:
            p.razorpay_payment_id = payment_id
            p.status = "success"
            await s.commit()
            return p.user_id
        return None


async def get_payment_stats():
    async with AsyncSessionLocal() as s:
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        total_result = await s.execute(
            select(func.sum(Payment.amount)).where(Payment.status == "success")
        )
        month_result = await s.execute(
            select(func.sum(Payment.amount)).where(
                Payment.status == "success",
                Payment.created_at >= month_start,
            )
        )
        count_result = await s.execute(
            select(func.count()).where(Payment.status == "success")
        )
        total = total_result.scalar() or 0
        month = month_result.scalar() or 0
        count = count_result.scalar() or 0
        return total, month, count


async def get_expiring_users_list(days: int) -> list[User]:
    async with AsyncSessionLocal() as s:
        now = datetime.utcnow()
        cutoff = now + timedelta(days=days)
        result = await s.execute(
            select(User).where(
                User.is_banned == False,
                User.sub_end != None,
                User.sub_end <= cutoff,
                User.sub_end > now,
            )
        )
        return list(result.scalars().all())
