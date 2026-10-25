"""Pure inbound-gating helpers — no Hermes or Telethon imports, so they are
unit-testable on their own.

A userbot sees every message in every group it belongs to. These functions
decide which of those messages the assistant is actually allowed to act on:
DM allowlist, group/chat allowlist, and mention gating. Keeping them pure (plain
args in, bool/str out) makes the policy easy to test and reason about.
"""

from __future__ import annotations

_TRUTHY = ("1", "true", "yes", "on")


def parse_csv(value: str | None) -> list[str]:
    """Split a comma-separated env value into trimmed, non-empty tokens."""
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def coerce_peer(chat_id: "str | int") -> "str | int":
    """Numeric chat ids must be ``int`` for Telethon; @usernames stay ``str``."""
    s = str(chat_id).strip()
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except ValueError:
            return s
    return s


def dm_allowed(user_id: int | None, allowed_users: set[str], allow_all: bool) -> bool:
    """Whether a DM from ``user_id`` may trigger the bot."""
    if allow_all:
        return True
    return user_id is not None and str(user_id) in allowed_users


def chat_allowed(chat_id: int | None, chat_username: str | None, allowed_chats: set[str]) -> bool:
    """Whether the bot may act in this chat.

    Empty allowlist = any chat passes (still mention-gated upstream). Otherwise
    the chat must match by numeric id or by @username (with or without the ``@``).
    """
    if not allowed_chats:
        return True
    candidates: set[str] = set()
    if chat_id is not None:
        candidates.add(str(chat_id))
    if chat_username:
        u = chat_username.lower()
        candidates.add(u)
        candidates.add("@" + u)
    norm_allowed = {c.lower() for c in allowed_chats}
    return bool(candidates & norm_allowed)


def is_addressed(mentioned: bool, text: str, me_username: str | None, has_reply: bool) -> bool:
    """True if the message mentions us (Telethon ``mentioned`` flag, which also
    covers replies-to-us) or contains our ``@username`` literally."""
    if mentioned:
        return True
    if me_username and ("@" + me_username) in (text or "").lower():
        return True
    # ``has_reply`` alone is not enough (could be a reply to anyone); Telethon's
    # ``mentioned`` already flags replies to our own messages.
    return False


def strip_self_mention(text: str, me_username: str | None) -> str:
    """Remove our ``@username`` from the start/body so the model sees clean text."""
    if me_username and text:
        text = text.replace("@" + me_username, "").replace("@" + me_username.capitalize(), "")
    return text.strip()
