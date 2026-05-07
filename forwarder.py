import io
import asyncio
from typing import Optional

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat, User as TLUser

from config import API_ID, API_HASH
from database import (
    get_all_sessions, save_session, delete_session,
    get_user_groups, get_channels, get_all_active_groups,
    set_group_active, set_group_active_for_user,
)

# user_id → TelegramClient
user_clients: dict[int, TelegramClient] = {}

# user_id → [(chat_id, name), ...]
user_dialogs: dict[int, list[tuple[int, str]]] = {}

DIALOG_LIMIT = 20


def _normalize_id(entity) -> int:
    if isinstance(entity, Channel):
        return int(f"-100{entity.id}")
    elif isinstance(entity, Chat):
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


def _make_handler(uid: int, client: TelegramClient):
    @client.on(events.NewMessage)
    async def forwarder(event):
        groups = await get_all_active_groups()
        for group in groups:
            if group.user_id != uid:
                continue
            if not group.is_active:
                continue
            incoming_ids = {ch.channel_id for ch in group.channels if ch.type == "incoming"}
            outgoing_ids = [ch.channel_id for ch in group.channels if ch.type == "outgoing"]
            if event.chat_id not in incoming_ids:
                continue
            for tgt_id in outgoing_ids:
                try:
                    m = event.message
                    if m.media:
                        try:
                            await client.send_file(tgt_id, file=m.media, caption=m.message or "")
                        except Exception:
                            await _copy_with_download(client, tgt_id, m)
                    elif m.message:
                        await client.send_message(tgt_id, m.message)
                except Exception as err:
                    print(f"[Forward Error] user={uid} group={group.name} → {tgt_id}: {err}")

    return forwarder


async def load_dialogs(uid: int) -> list[tuple[int, str]]:
    client = user_clients.get(uid)
    if not client:
        print(f"[Dialog Load] user={uid}: No client found")
        return []

    if not client.is_connected():
        try:
            await client.connect()
        except Exception as err:
            print(f"[Dialog Load] user={uid}: Reconnect failed: {err}")
            return []

    try:
        # Fetch more dialogs so we can sort properly
        all_dialogs = await client.get_dialogs(limit=100)

        # Sort: pinned first, then by last message time (default order)
        pinned = [d for d in all_dialogs if d.pinned]
        non_pinned = [d for d in all_dialogs if not d.pinned]
        sorted_dialogs = pinned + non_pinned

        # Take top DIALOG_LIMIT
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
                print(f"[Dialog Load] user={uid}: Skipping dialog: {e}")
                continue

        user_dialogs[uid] = result
        print(f"[Dialog Load] user={uid}: Loaded {len(result)} dialogs (pinned: {len(pinned)})")
        return result

    except Exception as err:
        print(f"[Dialog Load Error] user={uid}: {err}")
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
        print(f"[Forwarder] User {uid} connected. Dialogs: {len(user_dialogs.get(uid, []))}")
        return True
    except Exception as err:
        print(f"[Connect Error] user={uid}: {err}")
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
        return session_string
    except Exception as err:
        print(f"[Finalize Login Error] user={uid}: {err}")
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
    await delete_session(uid)


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
    print(f"[Forwarder] Found {len(sessions)} saved sessions. Connecting...")
    tasks = [connect_user(uid, sess) for uid, sess in sessions]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = sum(1 for r in results if r is True)
    print(f"[Forwarder] Connected {ok}/{len(sessions)} users.")


async def stop_all_for_user(uid: int):
    await set_group_active_for_user(uid, False)


async def get_client(uid: int) -> Optional[TelegramClient]:
    return user_clients.get(uid)

async def check_is_member(uid: int, channel_id: int) -> tuple[bool, str]:
    """Verify the Telethon account can read messages from this channel (is joined)."""
    client = user_clients.get(uid)
    if not client:
        return False, "Client nahi mila"
    try:
        entity = await client.get_entity(channel_id)
        name = getattr(entity, "title", None) or getattr(entity, "first_name", str(channel_id))
        msgs = await client.get_messages(entity, limit=1)
        _ = msgs
        return True, name
    except Exception as e:
        err = str(e)
        if "private" in err.lower() or "access" in err.lower() or "invite" in err.lower():
            return False, f"private_channel"
        return False, f"error:{err}"


async def check_can_post(uid: int, channel_id: int) -> tuple[bool, str]:
    """Verify the Telethon account has permission to send messages in this chat."""
    client = user_clients.get(uid)
    if not client:
        return False, "Client nahi mila"
    try:
        entity = await client.get_entity(channel_id)
        name = getattr(entity, "title", None) or getattr(entity, "first_name", str(channel_id))
        perms = await client.get_permissions(entity)
        if not perms.send_messages:
            return False, f"no_permission:{name}"
        return True, name
    except Exception as e:
        err = str(e)
        if "not a member" in err.lower() or "kicked" in err.lower():
            return False, f"not_member:{str(channel_id)}"
        return False, f"error:{err}"
