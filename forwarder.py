import io
import asyncio
from typing import Optional

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from config import API_ID, API_HASH
from database import (
    get_all_sessions, save_session, delete_session,
    get_user_groups, get_channels, get_all_active_groups,
    set_group_active,
)

# user_id → TelegramClient
user_clients: dict[int, TelegramClient] = {}

# user_id → [(chat_id, name), ...]
user_dialogs: dict[int, list[tuple[int, str]]] = {}


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
    if not client or not client.is_connected():
        return []
    try:
        dialogs = await client.get_dialogs()
        result = []
        for d in dialogs:
            name = d.name or getattr(d, "title", None) or "Unnamed"
            result.append((d.id, name))
        user_dialogs[uid] = result
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
        return client.is_connected() and await client.is_user_authorized()
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
