"""Telegram userbot adapter: MTProto (Telethon) in/out as a real user account.

Design mirrors the bundled platform adapters (``gateway.platforms.base``): one
``BasePlatformAdapter`` subclass with ``connect``/``disconnect``/``send``/
``get_chat_info``, producing normalized ``MessageEvent``s via ``build_source``
and dispatching them with ``handle_message``.

Why a userbot and not the Bot API: a bot added to a group is limited by
Telegram's privacy mode and is visibly a bot; a userbot is an ordinary member,
so it can sit in partner group chats and read the thread as a human would.

Because that means it can see *everything*, inbound is gated:
  * DMs      — allowed only for ``TELEGRAM_USERBOT_ALLOWED_USERS`` (or all, dev).
  * Groups   — only on @mention or reply-to-us (``REQUIRE_MENTION``, default on)
               and only in ``TELEGRAM_USERBOT_ALLOWED_CHATS`` when that list is set.
Our own outgoing messages (``event.out``) are dropped to avoid echo loops.

Config: values come from the profile's Hermes secrets/env (read via
``get_scoped_secret``) and are seeded into ``PlatformConfig.extra`` by
``_env_enablement``. ``config.yaml`` ``platforms.telegram_userbot.extra`` wins
over env.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import uuid
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Optional

try:
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession
    from telethon.tl.custom import Message as _TLMessage  # noqa: F401 (type hint only)
    TELETHON_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only where telethon is absent
    TELETHON_AVAILABLE = False
    TelegramClient = None  # type: ignore[assignment]
    events = None  # type: ignore[assignment]
    StringSession = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType
from gateway.platforms._shared import (
    get_scoped_secret as _get_scoped_secret,
    send_error,
    extra_or_secret as _extra_or_secret,
    seed_extra_from_env as _seed_extra_from_env,
)
from gateway.platforms.helpers import MessageDeduplicator

from .gating import (
    parse_csv as _parse_csv,
    is_truthy as _is_truthy,
    coerce_peer as _coerce_peer,
    dm_allowed as _dm_allowed,
    chat_allowed as _chat_allowed,
    is_addressed as _is_addressed,
    strip_self_mention as _strip_self_mention,
)

logger = logging.getLogger(__name__)

PLATFORM_NAME = "telegram_userbot"
MAX_MESSAGE_LENGTH = 4096  # Telegram per-message text limit
DEDUP_WINDOW_SECONDS = 300
DEDUP_MAX_SIZE = 2000


# --------------------------------------------------------------------------- #
# Config helpers
# --------------------------------------------------------------------------- #

def _auth_dir() -> Path:
    """``HERMES_HOME/telegram-userbot-auth`` (0700), holding the file session."""
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser()
    d = home / "telegram-userbot-auth"
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(stat.S_IRWXU)  # 0700
    except OSError:
        pass
    return d


def _session_file() -> Path:
    """Path to the Telethon file session (used when no StringSession is set)."""
    return _auth_dir() / "userbot.session"


def _parse_target_ref(ref: str) -> str | None:
    """``target_ref_syntax_fn``: a send target is any non-empty chat id / @username.

    Kept local (rather than importing a core helper) so the plugin depends only
    on documented plugin APIs and survives Hermes releases.
    """
    ref = (ref or "").strip()
    return ref or None


def check_requirements() -> bool:
    """Passive probe: telethon importable AND minimally configured (``TELEGRAM_API_ID`` set)."""
    return TELETHON_AVAILABLE and bool(_get_scoped_secret("TELEGRAM_API_ID", "").strip())


def validate_config(config) -> bool:
    """True when api id + hash are available (config.yaml ``extra`` or env)."""
    extra = getattr(config, "extra", {}) or {}
    return bool(
        _extra_or_secret(extra, "api_id", "TELEGRAM_API_ID")
        and _extra_or_secret(extra, "api_hash", "TELEGRAM_API_HASH")
    )


def is_connected(config) -> bool:
    """Configured AND a session exists (string env or file on disk)."""
    if not validate_config(config):
        return False
    if _get_scoped_secret("TELEGRAM_USERBOT_SESSION_STRING", "").strip():
        return True
    return _session_file().exists()


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #

class TelegramUserbotAdapter(BasePlatformAdapter):
    """Telethon userbot adapter — real-account MTProto in, ``send_message`` out."""

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config=config, platform=Platform(PLATFORM_NAME))
        extra = config.extra or {}
        self._api_id_raw: str = str(_extra_or_secret(extra, "api_id", "TELEGRAM_API_ID") or "")
        self._api_hash: str = str(_extra_or_secret(extra, "api_hash", "TELEGRAM_API_HASH") or "")
        self._session_string: str = str(
            _extra_or_secret(extra, "session_string", "TELEGRAM_USERBOT_SESSION_STRING") or ""
        )
        self._allowed_chats: set[str] = set(
            _parse_csv(_extra_or_secret(extra, "allowed_chats", "TELEGRAM_USERBOT_ALLOWED_CHATS"))
        )
        self._allowed_users: set[str] = set(
            _parse_csv(_extra_or_secret(extra, "allowed_users", "TELEGRAM_USERBOT_ALLOWED_USERS"))
        )
        self._allow_all_users: bool = _is_truthy(
            _extra_or_secret(extra, "allow_all_users", "TELEGRAM_USERBOT_ALLOW_ALL_USERS")
        )
        # Mention gating defaults ON; only an explicit false turns it off.
        require_mention_raw = _extra_or_secret(extra, "require_mention", "TELEGRAM_USERBOT_REQUIRE_MENTION")
        self._require_mention: bool = (
            True if require_mention_raw is None or str(require_mention_raw).strip() == ""
            else _is_truthy(str(require_mention_raw))
        )
        self._client: Optional["TelegramClient"] = None
        self._me_id: Optional[int] = None
        self._me_username: Optional[str] = None
        self._dedup = MessageDeduplicator(max_size=DEDUP_MAX_SIZE, ttl_seconds=DEDUP_WINDOW_SECONDS)

    # -- Connection lifecycle ------------------------------------------------ #

    def _build_client(self) -> "TelegramClient":
        api_id = int(self._api_id_raw)
        if self._session_string:
            return TelegramClient(StringSession(self._session_string), api_id, self._api_hash)
        return TelegramClient(str(_session_file()).removesuffix(".session"), api_id, self._api_hash)

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        """Connect using the existing session; never prompts (login is a separate step)."""
        if not TELETHON_AVAILABLE:
            logger.warning("[%s] telethon not installed. Run: pip install telethon", self.name)
            self._set_fatal_error("telethon_missing", "telethon is not installed (pip install telethon)",
                                  retryable=False)
            return False
        if not (self._api_id_raw and self._api_hash):
            logger.warning("[%s] TELEGRAM_API_ID / TELEGRAM_API_HASH not configured", self.name)
            return False
        try:
            self._client = self._build_client()
            await self._client.connect()
            if not await self._client.is_user_authorized():
                logger.error("[%s] No authorized session. Run: python -m telegram_userbot.login", self.name)
                self._set_fatal_error(
                    "telegram_userbot_unauthorized",
                    "Telegram userbot has no authorized session. "
                    "Run `python -m telegram_userbot.login` once to sign in.",
                    retryable=False)
                await self._client.disconnect()
                self._client = None
                return False
            me = await self._client.get_me()
            self._me_id = getattr(me, "id", None)
            self._me_username = (getattr(me, "username", None) or "").lower() or None
            self._client.add_event_handler(self._on_new_message, events.NewMessage(incoming=True))
            self._mark_connected()
            logger.info("[%s] Connected as user id=%s @%s", self.name, self._me_id, self._me_username)
            self._wire_plugin_handlers(None)
            # Keep the MTProto connection serviced for the lifetime of the adapter.
            asyncio.create_task(self._run_until_disconnected())
            return True
        except Exception as e:
            logger.error("[%s] Failed to connect: %s", self.name, e)
            return False

    async def _run_until_disconnected(self) -> None:
        """Await the client's disconnect future so Telethon keeps pumping updates."""
        client = self._client
        if client is None:
            return
        try:
            await client.run_until_disconnected()
        except asyncio.CancelledError:
            pass
        except Exception as e:  # pragma: no cover - network teardown noise
            logger.debug("[%s] run_until_disconnected ended: %s", self.name, e)

    async def disconnect(self) -> None:
        self._running = False
        self._mark_disconnected()
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # pragma: no cover
                pass
            self._client = None
        self._dedup.clear()
        logger.info("[%s] Disconnected", self.name)

    # -- Inbound ------------------------------------------------------------- #

    async def _on_new_message(self, event) -> None:
        """Telethon NewMessage handler → gating → MessageEvent → handle_message."""
        try:
            await self._handle_event(event)
        except Exception as e:  # pragma: no cover - defensive; one bad update must not kill the loop
            logger.warning("[%s] Error handling update: %s", self.name, e, exc_info=True)

    async def _handle_event(self, event) -> None:
        message = event.message
        # Drop our own messages (echo-loop guard).
        if getattr(event, "out", False):
            return
        sender_id = getattr(event, "sender_id", None)
        if self._me_id is not None and sender_id == self._me_id:
            return

        msg_id = str(getattr(message, "id", "") or uuid.uuid4().hex)
        chat_id = getattr(event, "chat_id", None)
        dedup_key = f"{chat_id}:{msg_id}"
        if self._dedup.is_duplicate(dedup_key):
            return

        text = (getattr(message, "message", None) or "").strip()
        if not text:
            return  # media-only; text-only first cut

        is_private = bool(getattr(event, "is_private", False))
        chat = await event.get_chat()
        chat_username = (getattr(chat, "username", None) or "") or None

        if is_private:
            if not _dm_allowed(sender_id, self._allowed_users, self._allow_all_users):
                logger.debug("[%s] DM from %s not allowed, skipping", self.name, sender_id)
                return
            chat_type = "dm"
        else:
            if not _chat_allowed(chat_id, chat_username, self._allowed_chats):
                return
            if self._require_mention and not _is_addressed(
                bool(getattr(event, "mentioned", False)), text, self._me_username,
                bool(getattr(message, "reply_to", None)),
            ):
                return
            chat_type = "channel" if bool(getattr(event, "is_channel", False)) \
                and not bool(getattr(event, "is_group", False)) else "group"
            text = _strip_self_mention(text, self._me_username)
            if not text:
                return

        sender = await event.get_sender()
        user_name = self._display_name(sender)
        chat_name = getattr(chat, "title", None) or chat_username or str(chat_id)

        source = self.build_source(
            chat_id=str(chat_id), chat_name=chat_name, chat_type=chat_type,
            user_id=str(sender_id) if sender_id is not None else None, user_name=user_name,
            message_id=msg_id,
        )
        ts = getattr(message, "date", None) or datetime.now(tz=UTC)
        message_event = MessageEvent(
            text=text, message_type=MessageType.TEXT, source=source, message_id=msg_id,
            raw_message=message, timestamp=ts,
        )
        logger.debug("[%s] %s in %s: %s", self.name, user_name, chat_name, text[:80])
        await self.handle_message(message_event)

    @staticmethod
    def _display_name(sender) -> str:
        if sender is None:
            return "unknown"
        first = getattr(sender, "first_name", None) or ""
        last = getattr(sender, "last_name", None) or ""
        name = (first + " " + last).strip()
        return name or (getattr(sender, "username", None) or "unknown")

    # -- Outbound ------------------------------------------------------------ #

    async def send(
        self, chat_id: str, content: str, reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        if self._client is None:
            return SendResult(success=False, error="Telegram userbot client not connected")
        if len(content) > self.MAX_MESSAGE_LENGTH:
            logger.warning("[%s] Message %d > %d chars; Telethon will split it.",
                           self.name, len(content), self.MAX_MESSAGE_LENGTH)
        reply_int: Optional[int] = None
        if reply_to and str(reply_to).isdigit():
            reply_int = int(reply_to)
        try:
            sent = await self._client.send_message(
                _coerce_peer(chat_id), content, reply_to=reply_int,
            )
            sent_id = getattr(sent, "id", None)
            return SendResult(success=True, message_id=str(sent_id) if sent_id is not None else None)
        except Exception as e:
            logger.error("[%s] Send error: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        if self._client is None:
            return {"name": str(chat_id), "type": "group"}
        try:
            entity = await self._client.get_entity(_coerce_peer(chat_id))
            name = getattr(entity, "title", None) or getattr(entity, "username", None) or str(chat_id)
            if getattr(entity, "broadcast", False):
                ctype = "channel"
            elif getattr(entity, "megagroup", False) or getattr(entity, "title", None):
                ctype = "group"
            else:
                ctype = "dm"
            return {"name": name, "type": ctype}
        except Exception as e:  # pragma: no cover
            logger.debug("[%s] get_chat_info(%s) failed: %s", self.name, chat_id, e)
            return {"name": str(chat_id), "type": "group"}


# --------------------------------------------------------------------------- #
# Plugin registration
# --------------------------------------------------------------------------- #

def _env_enablement() -> dict | None:
    """Seed ``PlatformConfig.extra`` from the profile env; ``None`` when unconfigured."""
    api_id = _get_scoped_secret("TELEGRAM_API_ID", "").strip()
    api_hash = _get_scoped_secret("TELEGRAM_API_HASH", "").strip()
    if not (api_id and api_hash):
        return None
    seed = _seed_extra_from_env((
        ("TELEGRAM_USERBOT_SESSION_STRING", "session_string", None),
        ("TELEGRAM_USERBOT_ALLOWED_CHATS", "allowed_chats", None),
        ("TELEGRAM_USERBOT_ALLOWED_USERS", "allowed_users", None),
        ("TELEGRAM_USERBOT_ALLOW_ALL_USERS", "allow_all_users", None),
        ("TELEGRAM_USERBOT_REQUIRE_MENTION", "require_mention", None),
    ), home_env="TELEGRAM_USERBOT_HOME_CHANNEL", home_default="")
    return {"api_id": api_id, "api_hash": api_hash, **seed}


async def _standalone_send(
    pconfig, chat_id: str, message: str, *,
    thread_id: Optional[str] = None, media_files: Optional[list[str]] = None, force_document: bool = False,
) -> dict[str, Any]:
    """Out-of-process send for cron / send_message_tool when no gateway adapter is live.

    Spins up a short-lived Telethon client from the same session and disconnects.
    ``thread_id``/``media_files`` are signature parity only (text-only first cut).
    """
    if not TELETHON_AVAILABLE:
        return send_error("telegram userbot standalone send: telethon not installed")
    extra = getattr(pconfig, "extra", {}) or {}
    api_id = _extra_or_secret(extra, "api_id", "TELEGRAM_API_ID")
    api_hash = _extra_or_secret(extra, "api_hash", "TELEGRAM_API_HASH")
    session_string = _extra_or_secret(extra, "session_string", "TELEGRAM_USERBOT_SESSION_STRING")
    if not (api_id and api_hash):
        return send_error("telegram userbot standalone send: TELEGRAM_API_ID/HASH not configured")
    target = chat_id or _extra_or_secret(extra, "home_channel", "TELEGRAM_USERBOT_HOME_CHANNEL")
    if not target:
        return send_error("telegram userbot standalone send: no chat_id and no TELEGRAM_USERBOT_HOME_CHANNEL")
    if session_string:
        client = TelegramClient(StringSession(session_string), int(api_id), str(api_hash))
    else:
        client = TelegramClient(str(_session_file()).removesuffix(".session"), int(api_id), str(api_hash))
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return send_error("telegram userbot standalone send: session not authorized (run login)")
        sent = await client.send_message(_coerce_peer(target), message)
        return {"success": True, "platform": PLATFORM_NAME, "chat_id": str(target),
                "message_id": str(getattr(sent, "id", "") or uuid.uuid4().hex)}
    except Exception as e:
        return send_error(f"telegram userbot standalone send failed: {e}")
    finally:
        try:
            await client.disconnect()
        except Exception:  # pragma: no cover
            pass


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system at startup."""
    ctx.register_platform(
        name=PLATFORM_NAME,
        label="Telegram Userbot",
        adapter_factory=lambda cfg: TelegramUserbotAdapter(cfg),
        target_ref_syntax_fn=_parse_target_ref,
        dashboard_description=(
            "Chat with Hermes from a real Telegram account (userbot / MTProto) — "
            "usable inside ordinary group chats, mention-gated by default."
        ),
        docs_url="https://github.com/VanyaSimonov/hermes-userbot-assistant",
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["TELEGRAM_API_ID", "TELEGRAM_API_HASH"],
        install_hint="pip install telethon",
        env_enablement_fn=_env_enablement,
        standalone_sender_fn=_standalone_send,
        cron_deliver_env_var="TELEGRAM_USERBOT_HOME_CHANNEL",
        allowed_users_env="TELEGRAM_USERBOT_ALLOWED_USERS",
        allow_all_env="TELEGRAM_USERBOT_ALLOW_ALL_USERS",
        max_message_length=MAX_MESSAGE_LENGTH,
        emoji="🛰️",
        allow_update_command=True,
        platform_hint=(
            "You are talking through a Telegram USERBOT running on a real user "
            "account, often inside a group chat with several people. Keep replies "
            "concise and address the person who mentioned you. You only see a "
            "message when @-mentioned or replied to. Telegram supports Markdown; "
            "the per-message limit is 4096 characters."
        ),
    )
