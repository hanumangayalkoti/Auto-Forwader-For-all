"""
Filter processing logic.
All functions are pure / sync — called from the async forwarder.
"""
import re
from typing import Optional
from models import Task


def _text_of(message) -> str:
    """Extract plain text from a Telethon message."""
    return message.text or message.caption or ""


# ─────────────────────────────────────────
# 1. BLACKLIST
# ─────────────────────────────────────────
def check_blacklist(task: Task, message) -> bool:
    """Return True if message should be BLOCKED (blacklist hit)."""
    words = task.blacklist_words or []
    if not words:
        return False
    text = _text_of(message).lower()
    for w in words:
        if w.lower() in text:
            return True
    return False


# ─────────────────────────────────────────
# 2. WHITELIST
# ─────────────────────────────────────────
def check_whitelist(task: Task, message) -> bool:
    """Return True if message should be BLOCKED (whitelist miss)."""
    words = task.whitelist_words or []
    if not words:
        return False
    text = _text_of(message).lower()
    for w in words:
        if w.lower() in text:
            return False   # found → allow
    return True            # none found → block


# ─────────────────────────────────────────
# 3. REGEX FILTER
# ─────────────────────────────────────────
def check_regex(task: Task, message) -> bool:
    """Return True if message should be BLOCKED (regex doesn't match)."""
    pattern = task.regex_pattern
    if not pattern:
        return False
    text = _text_of(message)
    try:
        if not re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            return True
    except re.error:
        pass
    return False


# ─────────────────────────────────────────
# 4. MEDIA FILTER
# ─────────────────────────────────────────
def check_media_filter(task: Task, message) -> bool:
    """Return True if message should be BLOCKED by media filter."""
    mf = task.media_filter
    if not mf:
        return False

    from telethon.tl.types import (
        MessageMediaPhoto, MessageMediaDocument,
        MessageMediaWebPage,
    )
    import mimetypes

    media = message.media

    if media is None:
        # pure text
        return False

    if isinstance(media, MessageMediaPhoto):
        return not mf.get("images", True)

    if isinstance(media, MessageMediaWebPage):
        return not mf.get("links", True)

    if isinstance(media, MessageMediaDocument):
        doc = media.document
        mime = getattr(doc, "mime_type", "") or ""
        if mime.startswith("video/"):
            return not mf.get("videos", True)
        if mime.startswith("audio/"):
            return not mf.get("audio", True)
        if mime == "application/x-tgsticker" or mime == "image/webp":
            return not mf.get("stickers", True)
        # documents / files
        return not mf.get("documents", True)

    return False


# ─────────────────────────────────────────
# 5. PINNED ONLY
# ─────────────────────────────────────────
def check_pinned_only(task: Task, message) -> bool:
    """Return True if message should be BLOCKED (not pinned when pinned_only ON)."""
    if not task.pinned_only:
        return False
    return not getattr(message, "pinned", False)


# ─────────────────────────────────────────
# MASTER FILTER CHECK
# ─────────────────────────────────────────
def should_skip(task: Task, message) -> tuple[bool, str]:
    """
    Returns (skip, reason).
    True  → do NOT forward this message.
    False → forward it.
    """
    if check_blacklist(task, message):
        return True, "blacklist"
    if check_whitelist(task, message):
        return True, "whitelist"
    if check_regex(task, message):
        return True, "regex"
    if check_media_filter(task, message):
        return True, "media_filter"
    if check_pinned_only(task, message):
        return True, "pinned_only"
    return False, ""


# ─────────────────────────────────────────
# MESSAGE MODIFICATION
# ─────────────────────────────────────────

URL_PATTERN = re.compile(
    r"https?://\S+|www\.\S+",
    re.IGNORECASE,
)


def apply_word_replace(text: str, pairs: list[dict]) -> str:
    if not pairs:
        return text
    for pair in pairs:
        frm = pair.get("from", "")
        to = pair.get("to", "")
        if frm:
            text = text.replace(frm, to)
    return text


def apply_link_replace(text: str, replacers) -> str:
    """Replace specific original links with new links (Pro+)."""
    if not replacers:
        return text
    for lr in replacers:
        if lr.original_link in text:
            text = text.replace(lr.original_link, lr.new_link)
    return text


def apply_remove_links(text: str) -> str:
    """Remove all URLs from text."""
    return URL_PATTERN.sub("", text).strip()


def build_modified_text(task: Task, original_text: str, replacers=None) -> str:
    """Apply word replace, link replace, remove links, header, footer."""
    text = original_text or ""

    # Word replace
    pairs = task.word_replace_pairs or []
    text = apply_word_replace(text, pairs)

    # Link replacer (Pro+)
    if replacers:
        text = apply_link_replace(text, replacers)

    # Remove links toggle
    if task.remove_links:
        text = apply_remove_links(text)

    # Header + Footer
    header = (task.header_text or "").strip()
    footer = (task.footer_text or "").strip()
    if header:
        text = header + "\n" + text
    if footer:
        text = text + "\n" + footer

    return text


def build_modified_caption(task: Task, original_caption: str, replacers=None) -> str:
    """Build caption for media messages."""
    if task.custom_caption:
        cap = task.custom_caption.replace("{original_caption}", original_caption or "")
    else:
        cap = original_caption or ""

    cap = apply_word_replace(cap, task.word_replace_pairs or [])

    if replacers:
        cap = apply_link_replace(cap, replacers)

    if task.remove_links:
        cap = apply_remove_links(cap)

    header = (task.header_text or "").strip()
    footer = (task.footer_text or "").strip()
    if header:
        cap = header + "\n" + cap
    if footer:
        cap = cap + "\n" + footer

    return cap.strip()
