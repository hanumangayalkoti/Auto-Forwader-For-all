from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey,
    Integer, String, Text, Float, func, JSON,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────
# USER
# ─────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    plan: Mapped[str] = mapped_column(String(16), default="free")  # free/basic/pro/business
    plan_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # None = free forever
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    referred_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    join_date: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    session: Mapped["TelethonSession | None"] = relationship(
        "TelethonSession", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(
        "Task", back_populates="user", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(
        "Payment", back_populates="user", cascade="all, delete-orphan"
    )
    affiliate: Mapped["Affiliate | None"] = relationship(
        "Affiliate", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    daily_count: Mapped["DailyMsgCount | None"] = relationship(
        "DailyMsgCount", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────
# TELETHON SESSION
# ─────────────────────────────────────────
class TelethonSession(Base):
    __tablename__ = "telethon_sessions"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), primary_key=True)
    session_string: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship("User", back_populates="session")


# ─────────────────────────────────────────
# TASK  (formerly ForwardingGroup)
# ─────────────────────────────────────────
class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    name: Mapped[str] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # ── Message Tools ──
    header_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    footer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    remove_links: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Advanced Settings ──
    delay_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    delay_random_min: Mapped[float] = mapped_column(Float, default=0.0)
    delay_random_max: Mapped[float] = mapped_column(Float, default=0.0)
    delay_mode: Mapped[str] = mapped_column(String(8), default="none")  # none/fixed/random
    skip_duplicates: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned_only: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Schedule ──
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_days: Mapped[str | None] = mapped_column(String(20), nullable=True)   # "0,1,2,3,4" (Mon-Fri)
    schedule_start: Mapped[str | None] = mapped_column(String(5), nullable=True)   # "09:00"
    schedule_end: Mapped[str | None] = mapped_column(String(5), nullable=True)     # "21:00"
    schedule_miss_action: Mapped[str] = mapped_column(String(8), default="skip")   # skip/queue

    # ── Filters ──
    blacklist_words: Mapped[list | None] = mapped_column(JSON, nullable=True)     # ["casino","spam"]
    whitelist_words: Mapped[list | None] = mapped_column(JSON, nullable=True)
    regex_pattern: Mapped[str | None] = mapped_column(String(512), nullable=True)
    media_filter: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {"images": True, "videos": True, "documents": True, "audio": True, "stickers": False, "links": True}

    # ── Word Replace ──
    word_replace_pairs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # [{"from": "Amazon", "to": "My Store"}, ...]

    # ── Image Watermark (Business) ──
    watermark_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    watermark_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    watermark_position: Mapped[str] = mapped_column(String(12), default="bottom_right")
    # top_left / top_right / bottom_left / bottom_right / center

    user: Mapped["User"] = relationship("User", back_populates="tasks")
    channels: Mapped[list["Channel"]] = relationship(
        "Channel", back_populates="task", cascade="all, delete-orphan"
    )
    link_replacers: Mapped[list["LinkReplacer"]] = relationship(
        "LinkReplacer", back_populates="task", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────
# CHANNEL  (source / target)
# ─────────────────────────────────────────
class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"))
    channel_id: Mapped[int] = mapped_column(BigInteger)
    channel_name: Mapped[str] = mapped_column(String(128), default="")
    type: Mapped[str] = mapped_column(String(8))   # source / target

    task: Mapped["Task"] = relationship("Task", back_populates="channels")


# ─────────────────────────────────────────
# LINK REPLACER  (Pro+)
# ─────────────────────────────────────────
class LinkReplacer(Base):
    __tablename__ = "link_replacers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"))
    original_link: Mapped[str] = mapped_column(Text)
    new_link: Mapped[str] = mapped_column(Text)

    task: Mapped["Task"] = relationship("Task", back_populates="link_replacers")


# ─────────────────────────────────────────
# PAYMENT
# ─────────────────────────────────────────
class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    razorpay_order_id: Mapped[str] = mapped_column(String(64))
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan: Mapped[str] = mapped_column(String(16))            # basic/pro/business
    billing: Mapped[str] = mapped_column(String(8))          # monthly/annual
    amount_inr: Mapped[int] = mapped_column(Integer)         # in paise
    amount_usd: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="pending")   # pending/paid/failed
    is_first_payment: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="payments")


# ─────────────────────────────────────────
# AFFILIATE
# ─────────────────────────────────────────
class Affiliate(Base):
    __tablename__ = "affiliates"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    balance_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_earned_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_referred: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="affiliate")
    withdrawal_requests: Mapped[list["WithdrawalRequest"]] = relationship(
        "WithdrawalRequest", back_populates="affiliate", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────
# WITHDRAWAL REQUEST
# ─────────────────────────────────────────
class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("affiliates.user_id"))
    amount_usd: Mapped[float] = mapped_column(Float)
    payment_method: Mapped[str] = mapped_column(String(16))   # upi / bank
    payment_details: Mapped[str] = mapped_column(Text)        # UPI ID or bank details
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/done/rejected
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    affiliate: Mapped["Affiliate"] = relationship("Affiliate", back_populates="withdrawal_requests")


# ─────────────────────────────────────────
# FORWARDED MESSAGES  (skip duplicates)
# ─────────────────────────────────────────
class ForwardedMessage(Base):
    __tablename__ = "forwarded_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"))
    message_id: Mapped[int] = mapped_column(BigInteger)
    source_channel_id: Mapped[int] = mapped_column(BigInteger)
    forwarded_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ─────────────────────────────────────────
# MESSAGE QUEUE  (schedule missed messages)
# ─────────────────────────────────────────
class MessageQueue(Base):
    __tablename__ = "message_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"))
    source_channel_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    queued_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ─────────────────────────────────────────
# SIMI CONVERSATIONS
# ─────────────────────────────────────────
class SimiConversation(Base):
    __tablename__ = "simi_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(16))    # user / assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ─────────────────────────────────────────
# DAILY MESSAGE COUNT  (free plan 60/day)
# ─────────────────────────────────────────
class DailyMsgCount(Base):
    __tablename__ = "daily_msg_count"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    date_ist: Mapped[str] = mapped_column(String(10))   # "2026-05-21"

    user: Mapped["User"] = relationship("User", back_populates="daily_count")
