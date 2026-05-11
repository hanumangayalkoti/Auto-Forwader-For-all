import io
import asyncio
import logging
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

# FIX: Bot reference for user error notifications
# Set karo main.py se taaki forward fail hone pe user ko notify kar sakein
_bot_ref: Optional[object] = None

# Track consecutive failures per (user_id, group_id, target_id) taaki spam na ho
_fail_counts: dict[str, int] = {}
FAIL_NOTIFY_THRESHOLD = 3

DIALOG_LIMIT = 20
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


async def _copy_with_download(client: TelegramClient, target_id: int, message):
    buf = io.BytesIO()
    await message.download_media(file=buf)
    buf.seek(0)
    fname = "file"
    if message.file and message.file.name:
        fname = message.file.name
    buf.name = fname
    await client.send_file(
        target_id,
        file=buf,
        caption=message.message or "",
        force_document=False,
    )


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

    # FIX: Sirf threshold hit hone pe notify karo, har baar nahi
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
                    if m.media:
                        try:
                            await client.send_file(tgt_id, file=m.media, caption=m.message or "")
                        except Exception:
                            await _copy_with_download(client, tgt_id, m)
                    elif m.message:
                        await client.send_message(tgt_id, m.message)

                    # FIX: Success hone pe fail counter reset karo
                    _fail_counts.pop(fail_key, None)
                    await asyncio.sleep(FORWARD_DELAY)

                except Exception as err:
                    err_str = str(err)
                    if hasattr(err, 'seconds'):
                        # FloodWait — back off aur retry
                        wait = err.seconds + 5
                        logger.warning(f"[FloodWait] user={uid} waiting {wait}s")
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            f"[Forward Error] user={uid} grp={grp['name']} → {tgt_id}: {err_str}"
                        )
                        # FIX: User ko meaningful error message bhejo (threshold ke baad)
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
        all_dialogs = await client.get_dialogs(limit=100)
        pinned = [d for d in all_dialogs if d.pinned]
        non_pinned = [d for d in all_dialogs if not d.pinned]
        sorted_dialogs = pinned + non_pinned
        top_dialogs = sorted_dialogs[:DIALOG_LIMIT]

        result = []
        for d in top_dialogs:
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
        try:
            await user_clients[uid].disconnect()
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
                # FIX: Broadcast channel — need post_messages admin right
                # Pehle membership check karo, phir permission check karo
                perms = await client.get_permissions(entity)
                if not getattr(perms, 'post_messages', False):
                    return False, f"no_permission_channel:{name}"
            else:
                # Supergroup — send_messages check karo
                perms = await client.get_permissions(entity)
                if not getattr(perms, 'send_messages', True):
                    return False, f"no_permission:{name}"
        elif isinstance(entity, TLChat):
            # Regular group — check if kicked/restricted
            perms = await client.get_permissions(entity)
            if not getattr(perms, 'send_messages', True):
                return False, f"no_permission:{name}"
        # TLUser (DM) — always allowed

        return True, name

    except Exception as e:
        err = str(e)
        entity_name = getattr(entity, 'title', str(channel_id)) if entity is not None else str(channel_id)
        if "not a member" in err.lower() or "kicked" in err.lower() or "banned" in err.lower():
            return False, f"not_member:{entity_name}"
        if "private" in err.lower() or "access" in err.lower():
            return False, f"not_member:{entity_name}"
        return False, f"error:{entity_name}:{err}"
