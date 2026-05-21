import asyncio
import logging
import os
import tempfile
from typing import Optional

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Channel as TLChannel, Chat as TLChat, User as TLUser

from config import API_ID, API_HASH, FORWARD_DELAY, FAIL_NOTIFY_THRESHOLD, get_plan_limits
from filters import should_skip, build_modified_text, build_modified_caption
from image_tools import add_watermark, is_image_bytes
import database as db

logger = logging.getLogger(__name__)

# user_id → TelegramClient
user_clients: dict[int, TelegramClient] = {}

# user_id → [(chat_id, name), ...]
user_dialogs: dict[int, list[tuple[int, str]]] = {}

# Bot reference for notifications
_bot_ref: Optional[object] = None

# Track which clients have handler registered
_handler_registered: set[int] = set()

# Consecutive fail counter: (user_id, task_id, target_id) → count
_fail_counts: dict[str, int] = {}

# Login flow clients: user_id → TelegramClient (not yet saved)
_login_clients: dict[int, TelegramClient] = {}


def set_bot(bot):
    global _bot_ref
    _bot_ref = bot


def _normalize_id(entity) -> int:
    if isinstance(entity, TLChannel):
        return int(f"-100{entity.id}")
    elif isinstance(entity, TLChat):
        return -entity.id
    elif isinstance(entity, TLUser):
        return entity.id
    return getattr(entity, "id", 0)


# ══════════════════════════════════════════════
# LOGIN FLOW
# ══════════════════════════════════════════════

async def create_client_for_login(user_id: int) -> TelegramClient:
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    _login_clients[user_id] = client
    return client


async def finalize_login(user_id: int) -> bool:
    """After successful OTP/2FA — save session, register handler."""
    client = _login_clients.pop(user_id, None)
    if not client:
        return False
    session_string = client.session.save()
    await db.save_session(user_id, session_string)
    user_clients[user_id] = client
    await _load_dialogs(user_id)
    await _register_handler(user_id)
    return True


async def logout_user(user_id: int):
    client = user_clients.pop(user_id, None)
    _handler_registered.discard(user_id)
    user_dialogs.pop(user_id, None)
    if client:
        try:
            await client.log_out()
        except Exception:
            pass
        try:
            await client.disconnect()
        except Exception:
            pass
    await db.delete_session(user_id)
    await db.set_all_tasks_active(user_id, False)


def is_user_logged_in(user_id: int) -> bool:
    return user_id in user_clients and user_clients[user_id].is_connected()


# ══════════════════════════════════════════════
# DIALOGS
# ══════════════════════════════════════════════

async def load_dialogs(user_id: int) -> list[tuple[int, str]]:
    await _load_dialogs(user_id)
    return user_dialogs.get(user_id, [])


async def _load_dialogs(user_id: int):
    client = user_clients.get(user_id)
    if not client:
        return
    try:
        dialogs = await asyncio.wait_for(
            client.get_dialogs(limit=100),
            timeout=25,
        )
        result = []
        for d in dialogs:
            eid = _normalize_id(d.entity)
            name = d.name or str(eid)
            result.append((eid, name))
        user_dialogs[user_id] = result
    except asyncio.TimeoutError:
        try:
            dialogs = await asyncio.wait_for(
                client.get_dialogs(limit=30),
                timeout=15,
            )
            result = []
            for d in dialogs:
                eid = _normalize_id(d.entity)
                name = d.name or str(eid)
                result.append((eid, name))
            user_dialogs[user_id] = result
        except Exception as e:
            logger.error(f"[Forwarder] Dialog load failed for user {user_id}: {e}")
    except Exception as e:
        logger.error(f"[Forwarder] Dialog load error user {user_id}: {e}")


async def resolve_channel(user_id: int, identifier: str) -> Optional[tuple[int, str]]:
    """
    Resolve a channel by @username or https://t.me/... link.
    Returns (channel_id, name) or None.
    """
    client = user_clients.get(user_id)
    if not client:
        return None
    try:
        entity = await client.get_entity(identifier)
        eid = _normalize_id(entity)
        name = getattr(entity, "title", None) or getattr(entity, "username", None) or str(eid)
        # Add to dialogs cache
        dialogs = user_dialogs.get(user_id, [])
        if not any(d[0] == eid for d in dialogs):
            dialogs.insert(0, (eid, name))
            user_dialogs[user_id] = dialogs
        return eid, name
    except Exception as e:
        logger.warning(f"[Forwarder] resolve_channel failed '{identifier}': {e}")
        return None


# ══════════════════════════════════════════════
# STARTUP RECONNECT
# ══════════════════════════════════════════════

async def startup_connect_all():
    sessions = await db.get_all_sessions()
    for sess in sessions:
        try:
            session_string = db._decrypt(sess.session_string)
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            await client.connect()
            if await client.is_user_authorized():
                user_clients[sess.user_id] = client
                await _load_dialogs(sess.user_id)
                await _register_handler(sess.user_id)
                logger.info(f"[Forwarder] Reconnected user {sess.user_id}")
            else:
                await client.disconnect()
                logger.warning(f"[Forwarder] Session unauthorized: user {sess.user_id}")
        except Exception as e:
            logger.error(f"[Forwarder] Reconnect failed for user {sess.user_id}: {e}")


# ══════════════════════════════════════════════
# HANDLER REGISTRATION
# ══════════════════════════════════════════════

async def _register_handler(user_id: int):
    if user_id in _handler_registered:
        return
    client = user_clients.get(user_id)
    if not client:
        return

    @client.on(events.NewMessage())
    async def _on_message(event):
        await _process_message(user_id, event)

    _handler_registered.add(user_id)


async def refresh_cache_for_user(user_id: int):
    """Called after task changes — reload active task cache."""
    pass   # Cache is loaded fresh from DB on each forward


# ══════════════════════════════════════════════
# FORWARD PROCESSING
# ══════════════════════════════════════════════

async def _process_message(user_id: int, event):
    try:
        message = event.message
        if not message:
            return

        source_id = _normalize_id(event.chat)
        tasks = await db.get_user_tasks(user_id)

        for task in tasks:
            if not task.is_active:
                continue

            source_ids = {ch.channel_id for ch in task.channels if ch.type == "source"}
            target_ids = [ch.channel_id for ch in task.channels if ch.type == "target"]

            if source_id not in source_ids:
                continue
            if not target_ids:
                continue

            # ── Plan limits ──
            user = await db.get_user(user_id)
            if not user:
                continue

            _, access_reason = db.check_access(user)
            plan = user.plan

            # Free plan: daily message limit
            limits = get_plan_limits(plan)
            if limits["msgs_per_day"] > 0:
                count = await db.increment_daily_count(user_id)
                if count > limits["msgs_per_day"]:
                    # Limit hit — skip silently
                    continue

            # ── Skip duplicates ──
            if task.skip_duplicates:
                already = await db.is_message_forwarded(task.id, source_id, message.id)
                if already:
                    continue

            # ── Filters ──
            skip, reason = should_skip(task, message)
            if skip:
                continue

            # ── Delay ──
            await _apply_delay(task)

            # ── Schedule check ──
            if task.schedule_enabled:
                if not _in_schedule_window(task):
                    if task.schedule_miss_action == "queue":
                        await db.message_queue_add(task.id, source_id, message.id)
                    continue

            # ── Forward to each target ──
            for target_id in target_ids:
                await _forward_one(user_id, task, message, target_id, source_id)

            # ── Mark forwarded ──
            if task.skip_duplicates:
                await db.mark_message_forwarded(task.id, source_id, message.id)

    except Exception as e:
        logger.error(f"[Forwarder] _process_message error user {user_id}: {e}", exc_info=True)


async def _forward_one(user_id: int, task, message, target_id: int, source_id: int):
    client = user_clients.get(user_id)
    if not client:
        return

    fail_key = f"{user_id}:{task.id}:{target_id}"
    try:
        await _do_forward(client, task, message, target_id)
        _fail_counts.pop(fail_key, None)
        await asyncio.sleep(FORWARD_DELAY)

    except Exception as e:
        count = _fail_counts.get(fail_key, 0) + 1
        _fail_counts[fail_key] = count
        logger.warning(f"[Forwarder] Forward fail #{count} task={task.id} target={target_id}: {e}")

        if count >= FAIL_NOTIFY_THRESHOLD and _bot_ref:
            _fail_counts[fail_key] = 0
            try:
                await _bot_ref.send_message(
                    user_id,
                    f"⚠️ *Forward Error*\n\n"
                    f"Task: *{task.name}*\n"
                    f"Target channel ID: `{target_id}`\n"
                    f"Error: {str(e)[:200]}\n\n"
                    f"Task continue kar raha hai.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass


async def _do_forward(client: TelegramClient, task, message, target_id: int):
    """Core forward logic — no 'Forwarded from' tag."""
    replacers = task.link_replacers or []
    has_media = message.media is not None
    original_text = message.text or ""
    original_caption = message.caption or ""

    if not has_media:
        # Text message
        new_text = build_modified_text(task, original_text, replacers)
        if new_text.strip():
            await client.send_message(target_id, new_text)
    else:
        # Media message
        new_caption = build_modified_caption(task, original_caption, replacers)

        # Check if watermark needed (Business plan)
        apply_wm = task.watermark_enabled and task.watermark_text

        if apply_wm:
            await _forward_with_watermark(client, task, message, target_id, new_caption)
        else:
            await _copy_media(client, message, target_id, new_caption)


async def _copy_media(client: TelegramClient, message, target_id: int, caption: str):
    """Download and re-upload media without forward tag."""
    suffix = _guess_suffix(message)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        await message.download_media(file=tmp_path)
        await client.send_file(
            target_id,
            tmp_path,
            caption=caption or None,
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def _forward_with_watermark(client: TelegramClient, task, message, target_id: int, caption: str):
    """Download image, apply watermark, re-upload."""
    tmp_path = None
    wm_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        await message.download_media(file=tmp_path)

        with open(tmp_path, "rb") as f:
            image_bytes = f.read()

        if is_image_bytes(image_bytes):
            wm_bytes = add_watermark(
                image_bytes,
                text=task.watermark_text,
                position=task.watermark_position or "bottom_right",
            )
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp2:
                wm_path = tmp2.name
                tmp2.write(wm_bytes)

            await client.send_file(target_id, wm_path, caption=caption or None)
        else:
            # Not an image — forward without watermark
            await client.send_file(target_id, tmp_path, caption=caption or None)

    finally:
        for path in [tmp_path, wm_path]:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:
                    pass


def _guess_suffix(message) -> str:
    if message.file and message.file.name:
        _, ext = os.path.splitext(message.file.name)
        if ext:
            return ext
    if message.file and message.file.mime_type:
        mime_map = {
            "video/mp4": ".mp4", "video/mpeg": ".mpeg",
            "audio/mpeg": ".mp3", "audio/ogg": ".ogg",
            "image/jpeg": ".jpg", "image/png": ".png",
            "image/gif": ".gif", "image/webp": ".webp",
            "application/pdf": ".pdf",
        }
        return mime_map.get(message.file.mime_type, "")
    return ""


def _in_schedule_window(task) -> bool:
    """Check if current IST time is within the task's schedule window."""
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)

    if task.schedule_days:
        allowed_days = [int(d) for d in task.schedule_days.split(",") if d.strip()]
        if now.weekday() not in allowed_days:
            return False

    if task.schedule_start and task.schedule_end:
        try:
            sh, sm = map(int, task.schedule_start.split(":"))
            eh, em = map(int, task.schedule_end.split(":"))
            start_minutes = sh * 60 + sm
            end_minutes = eh * 60 + em
            now_minutes = now.hour * 60 + now.minute
            if not (start_minutes <= now_minutes <= end_minutes):
                return False
        except Exception:
            pass

    return True


async def _apply_delay(task):
    if task.delay_mode == "fixed" and task.delay_seconds > 0:
        await asyncio.sleep(task.delay_seconds)
    elif task.delay_mode == "random":
        import random
        mn = task.delay_random_min or 0
        mx = task.delay_random_max or 0
        if mx > mn:
            await asyncio.sleep(random.uniform(mn, mx))
