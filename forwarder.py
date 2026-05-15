import os
import asyncio
import logging
import tempfile
from typing import Optional

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Channel as TLChannel, Chat as TLChat, User as TLUser

from config import API_ID, API_HASH
from database import (
    get_all_sessions, save_session, delete_session,
    get_user_groups, get_channels, get_all_active_groups,
    set_group_active, set_group_active_for_user,
)

logger = logging.getLogger(__name__)

# user_id → TelegramClient
user_clients: dict[int, TelegramClient] = {}

# user_id → [(chat_id, name), ...]
user_dialogs: dict[int, list[tuple[int, str]]] = {}

# In-memory active group cache: user_id → [{group_id, incoming_ids, outgoing_ids}]
_group_cache: dict[int, list[dict]] = {}

# Bot reference for user error notifications
_bot_ref: Optional[object] = None

# Track which clients already have a handler registered (prevents duplicate forwarding)
_handler_registered: set[int] = set()

# Track consecutive failures per (user_id, group_id, target_id)
_fail_counts: dict[str, int] = {}
FAIL_NOTIFY_THRESHOLD = 3

DIALOG_LIMIT = 100
FORWARD_DELAY = 0.4


def set_bot(bot) -> None:
    global _bot_ref
    _bot_ref = bot


def _normalize_id(entity) -> int:
    if isinstance(entity, TLChannel):
        return int(f"-100{entity.id}")
    elif isinstance(entity, TLChat):
        return -entity.id
    elif isinstance(entity, TLUser):
        return entity.id
    return getattr(entity, 'id', 0)


# FIX: BytesIO ki jagah temp file use karo — badi files ke liye "file parts invalid" error fix
async def _copy_with_download(client: TelegramClient, target_id: int, message):
    suffix = ""
    if message.file and message.file.name:
        _, suffix = os.path.splitext(message.file.name)
    if not suffix and message.file and message.file.mime_type:
        mime_map = {
            "video/mp4": ".mp4", "video/mpeg": ".mpeg", "video/quicktime": ".mov",
            "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/flac": ".flac",
            "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
            "image/webp": ".webp", "application/pdf": ".pdf",
        }
        suffix = mime_map.get(message.file.mime_type, "")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        await message.download_media(file=tmp_path)
        await client.send_file(
            target_id,
            file=tmp_path,
            caption=message.message or "",
            force_document=False,
        )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


async def _refresh_group_cache(uid: int):
    groups = await get_user_groups(uid)
    cache = []
    for g in groups:
        if not g.is_active:
            continue
        inc = {ch.channel_id for ch in g.channels if ch.type == "incoming"}
        out = [ch.channel_id for ch in g.channels if ch.type == "outgoing"]
        if inc and out:
            cache.append({"id": g.id, "name": g.name, "incoming": inc, "outgoing": out})
    _group_cache[uid] = cache


async def _notify_user_error(uid: int, grp_name: str, tgt_id: int, err_msg: str):
    """User ko bot message bhejo jab forwarding fail ho — threshold ke baad."""
    if not _bot_ref:
        return
    fail_key = f"{uid}:{tgt_id}"
    count = _fail_counts.get(fail_key, 0) + 1
    _fail_counts[fail_key] = count

    if count == FAIL_NOTIFY_THRESHOLD:
        try:
            await _bot_ref.send_message(
                uid,
                f"⚠️ *Forwarding Error*\n\n"
                f"Group: *{grp_name}*\n"
                f"Target ID: `{tgt_id}`\n\n"
                f"*Error:* {err_msg}\n\n"
                "Possible reasons:\n"
                "— Bot/account ko target chat se hata diya gaya\n"
                "— Send permission revoke ho gayi\n"
                "— Account temporarily restricted hai\n\n"
                "Fix karne ke baad /groups se group restart karo.",
                parse_mode="Markdown",
            )
        except Exception as notify_err:
            logger.warning(f"[Forwarder] User {uid} ko notify nahi kar sake: {notify_err}")


def _make_handler(uid: int, client: TelegramClient):
    client_id = id(client)
    if client_id in _handler_registered:
        return
    _handler_registered.add(client_id)

    @client.on(events.NewMessage)
    async def forwarder(event):
        cached = _group_cache.get(uid, [])
        for grp in cached:
            if event.chat_id not in grp["incoming"]:
                continue
            for tgt_id in grp["outgoing"]:
                fail_key = f"{uid}:{tgt_id}"
                try:
                    m = event.message

                    # FIX: Forwarding strategy — 3 levels:
                    # 1. forward_messages() — Telegram ka native forward, sabse reliable
                    #    koi download/upload nahi, media type kuch bhi ho chalega
                    # 2. send_file(m.media) — agar forward block ho (e.g. no-forward channel)
                    # 3. _copy_with_download() — last resort, temp file se (BytesIO wala bug fix)

                    forwarded = False

                    # Level 1: Native forward
                    try:
                        await client.forward_messages(tgt_id, m)
                        forwarded = True
                    except Exception as fwd_err:
                        logger.debug(
                            f"[Forward L1] user={uid} grp={grp['name']} → {tgt_id}: "
                            f"native forward failed ({fwd_err}), trying L2"
                        )

                    # Level 2: send_file with media object (text message bhi handle karo)
                    if not forwarded:
                        if m.media:
                            try:
                                await client.send_file(tgt_id, file=m.media, caption=m.message or "")
                                forwarded = True
                            except Exception as sf_err:
                                logger.debug(
                                    f"[Forward L2] user={uid} grp={grp['name']} → {tgt_id}: "
                                    f"send_file failed ({sf_err}), trying L3"
                                )
                        elif m.message:
                            await client.send_message(tgt_id, m.message)
                            forwarded = True

                    # Level 3: Download to temp file and re-upload
                    if not forwarded and m.media:
                        await _copy_with_download(client, tgt_id, m)
                        forwarded = True

                    if forwarded:
                        _fail_counts.pop(fail_key, None)

                    await asyncio.sleep(FORWARD_DELAY)

                except Exception as err:
                    err_str = str(err)
                    if hasattr(err, 'seconds'):
                        wait = err.seconds + 5
                        logger.warning(f"[FloodWait] user={uid} waiting {wait}s")
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            f"[Forward Error] user={uid} grp={grp['name']} → {tgt_id}: {err_str}"
                        )
                        await _notify_user_error(uid, grp["name"], tgt_id, err_str)

    return forwarder


async def load_dialogs(uid: int) -> list[tuple[int, str]]:
    client = user_clients.get(uid)
    if not client:
        logger.warning(f"[Dialog Load] user={uid}: No client found")
        return []

    if not client.is_connected():
        try:
            await client.connect()
        except Exception as err:
            logger.error(f"[Dialog Load] user={uid}: Reconnect failed: {err}")
            return []

    try:
        all_dialogs = await client.get_dialogs(limit=DIALOG_LIMIT)
        pinned = [d for d in all_dialogs if d.pinned]
        non_pinned = [d for d in all_dialogs if not d.pinned]
        sorted_dialogs = pinned + non_pinned

        result = []
        for d in sorted_dialogs:
            try:
                entity = d.entity
                name = (
                    getattr(entity, 'title', None)
                    or getattr(entity, 'first_name', None)
                    or d.name
                    or "Unnamed"
                )
                chat_id = _normalize_id(entity)
                if chat_id != 0:
                    pin_mark = "📌 " if d.pinned else ""
                    result.append((chat_id, pin_mark + name))
            except Exception as e:
                logger.debug(f"[Dialog Load] user={uid}: Skipping dialog: {e}")
                continue

        user_dialogs[uid] = result
        logger.info(f"[Dialog Load] user={uid}: Loaded {len(result)} dialogs (pinned: {len(pinned)})")
        return result

    except Exception as err:
        logger.error(f"[Dialog Load Error] user={uid}: {err}")
        return []


async def connect_user(uid: int, session_string: str) -> bool:
    try:
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False
        user_clients[uid] = client
        _make_handler(uid, client)
        await load_dialogs(uid)
        await _refresh_group_cache(uid)
        logger.info(f"[Forwarder] User {uid} connected. Dialogs: {len(user_dialogs.get(uid, []))}")
        return True
    except Exception as err:
        logger.error(f"[Connect Error] user={uid}: {err}")
        return False


async def create_client_for_login(uid: int) -> TelegramClient:
    if uid in user_clients:
        old_client = user_clients[uid]
        _handler_registered.discard(id(old_client))
        try:
            await old_client.disconnect()
        except Exception:
            pass
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    user_clients[uid] = client
    return client


async def finalize_login(uid: int) -> Optional[str]:
    client = user_clients.get(uid)
    if not client:
        return None
    try:
        session_string = client.session.save()
        await save_session(uid, session_string)
        _make_handler(uid, client)
        await load_dialogs(uid)
        await _refresh_group_cache(uid)
        return session_string
    except Exception as err:
        logger.error(f"[Finalize Login Error] user={uid}: {err}")
        return None


async def logout_user(uid: int):
    client = user_clients.get(uid)
    if client:
        _handler_registered.discard(id(client))
        try:
            await client.log_out()
        except Exception:
            pass
        try:
            await client.disconnect()
        except Exception:
            pass
        del user_clients[uid]
    if uid in user_dialogs:
        del user_dialogs[uid]
    _group_cache.pop(uid, None)
    _fail_counts_clear_user(uid)
    await delete_session(uid)


def _fail_counts_clear_user(uid: int):
    prefix = f"{uid}:"
    keys_to_del = [k for k in _fail_counts if k.startswith(prefix)]
    for k in keys_to_del:
        del _fail_counts[k]


async def is_user_logged_in(uid: int) -> bool:
    client = user_clients.get(uid)
    if not client:
        return False
    try:
        if not client.is_connected():
            await client.connect()
        return await client.is_user_authorized()
    except Exception:
        return False


async def startup_connect_all():
    sessions = await get_all_sessions()
    logger.info(f"[Forwarder] Found {len(sessions)} saved sessions. Connecting...")
    tasks = [connect_user(uid, sess) for uid, sess in sessions]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = sum(1 for r in results if r is True)
    logger.info(f"[Forwarder] Connected {ok}/{len(sessions)} users.")


async def reconnect_all_disconnected() -> dict:
    sessions = await get_all_sessions()
    stats = {"checked": len(sessions), "ok": 0, "reconnected": 0, "failed": 0}

    for uid, session_string in sessions:
        client = user_clients.get(uid)

        still_alive = False
        if client:
            try:
                if not client.is_connected():
                    await client.connect()
                still_alive = await client.is_user_authorized()
            except Exception:
                still_alive = False

        if still_alive:
            if uid not in _group_cache:
                await _refresh_group_cache(uid)
            stats["ok"] += 1
            continue

        if client:
            _handler_registered.discard(id(client))
            try:
                await client.disconnect()
            except Exception:
                pass
            user_clients.pop(uid, None)
            _group_cache.pop(uid, None)

        logger.info(f"[Reconnect] user={uid} reconnecting...")
        try:
            success = await connect_user(uid, session_string)
            if success:
                stats["reconnected"] += 1
                logger.info(f"[Reconnect] user={uid} reconnected successfully.")
                if _group_cache.get(uid):
                    if _bot_ref:
                        try:
                            await _bot_ref.send_message(
                                uid,
                                "🔄 *Forwarding Reconnected*\n\n"
                                "Connection drop hone ke baad automatically reconnect ho gaya.\n"
                                "Forwarding phir se chal rahi hai!",
                                parse_mode="Markdown",
                            )
                        except Exception:
                            pass
            else:
                stats["failed"] += 1
                logger.warning(f"[Reconnect] user={uid} reconnect failed — session may be expired.")
        except Exception as err:
            stats["failed"] += 1
            logger.error(f"[Reconnect] user={uid} error: {err}")

    logger.info(
        f"[Reconnect] Done — checked={stats['checked']} ok={stats['ok']} "
        f"reconnected={stats['reconnected']} failed={stats['failed']}"
    )
    return stats


async def stop_all_for_user(uid: int):
    await set_group_active_for_user(uid, False)
    _group_cache.pop(uid, None)


async def refresh_cache_for_user(uid: int):
    await _refresh_group_cache(uid)


async def get_client(uid: int) -> Optional[TelegramClient]:
    return user_clients.get(uid)


async def check_is_member(uid: int, channel_id: int) -> tuple[bool, str]:
    """Verify the Telethon account can read messages from this channel (is joined)."""
    client = user_clients.get(uid)
    if not client:
        return False, "no_client"
    entity = None
    try:
        entity = await client.get_entity(channel_id)
        name = getattr(entity, "title", None) or getattr(entity, "first_name", str(channel_id))
        await client.get_messages(entity, limit=1)
        return True, name
    except Exception as e:
        err = str(e)
        entity_name = getattr(entity, 'title', str(channel_id)) if entity is not None else str(channel_id)
        if "private" in err.lower() or "access" in err.lower() or "invite" in err.lower():
            return False, "private_channel"
        return False, f"error:{entity_name}"


async def check_can_post(uid: int, channel_id: int) -> tuple[bool, str]:
    """
    Verify the Telethon account can send messages in this chat.
    Returns (True, name) or (False, "error_type:name").

    Error types:
      not_member:<name>              — not joined
      no_permission_channel:<name>   — broadcast channel, not admin/editor
      no_permission:<name>           — group restricted, no send right
      error:<name>:<msg>             — other error
    """
    client = user_clients.get(uid)
    if not client:
        return False, "no_client"
    entity = None
    try:
        entity = await client.get_entity(channel_id)
        name = (
            getattr(entity, "title", None)
            or getattr(entity, "first_name", str(channel_id))
            or str(channel_id)
        )

        if isinstance(entity, TLChannel):
            if entity.broadcast:
                if getattr(entity, 'creator', False):
                    return True, name
                admin_rights = getattr(entity, 'admin_rights', None)
                if admin_rights and getattr(admin_rights, 'post_messages', False):
                    return True, name
                perms = await client.get_permissions(entity)
                if not getattr(perms, 'post_messages', False):
                    return False, f"no_permission_channel:{name}"
            else:
                if getattr(entity, 'creator', False):
                    return True, name
                perms = await client.get_permissions(entity)
                if not getattr(perms, 'send_messages', True):
                    return False, f"no_permission:{name}"
        elif isinstance(entity, TLChat):
            perms = await client.get_permissions(entity)
            if not getattr(perms, 'send_messages', True):
                return False, f"no_permission:{name}"

        return True, name

    except Exception as e:
        err = str(e)
        entity_name = getattr(entity, 'title', str(channel_id)) if entity is not None else str(channel_id)
        if "not a member" in err.lower() or "kicked" in err.lower() or "banned" in err.lower():
            return False, f"not_member:{entity_name}"
        if "private" in err.lower() or "access" in err.lower():
            return False, f"not_member:{entity_name}"
        return False, f"error:{entity_name}:{err}"
