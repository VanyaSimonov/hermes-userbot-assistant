"""Unit tests for the pure gating helpers (no Hermes / Telethon needed).

``gating.py`` is loaded directly by file path so importing it does not trigger
``telegram_userbot/__init__.py`` (which pulls in the adapter and the host's
``gateway.*`` modules). These tests therefore run anywhere.
"""

import importlib.util
import os

_GATING = os.path.join(os.path.dirname(__file__), "..", "telegram_userbot", "gating.py")
_spec = importlib.util.spec_from_file_location("telegram_userbot_gating", _GATING)
gating = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gating)

parse_csv = gating.parse_csv
is_truthy = gating.is_truthy
coerce_peer = gating.coerce_peer
dm_allowed = gating.dm_allowed
chat_allowed = gating.chat_allowed
is_addressed = gating.is_addressed
strip_self_mention = gating.strip_self_mention


def test_parse_csv():
    assert parse_csv("a, b ,,c") == ["a", "b", "c"]
    assert parse_csv("") == []
    assert parse_csv(None) == []


def test_is_truthy():
    assert is_truthy("true") and is_truthy("YES") and is_truthy("1") and is_truthy("on")
    assert not is_truthy("false") and not is_truthy("") and not is_truthy(None)


def test_coerce_peer():
    assert coerce_peer("-1001234567890") == -1001234567890
    assert coerce_peer("123") == 123
    assert coerce_peer("@partners") == "@partners"
    assert coerce_peer("partners") == "partners"


def test_dm_allowed():
    allowed = {"111", "222"}
    assert dm_allowed(111, allowed, False)
    assert not dm_allowed(333, allowed, False)
    assert dm_allowed(333, allowed, True)            # allow_all overrides
    assert not dm_allowed(None, allowed, False)


def test_chat_allowed_empty_allowlist_passes():
    assert chat_allowed(-100123, "anychat", set()) is True


def test_chat_allowed_by_id_and_username():
    allow = {"-100123", "@partners"}
    assert chat_allowed(-100123, None, allow)
    assert chat_allowed(999, "partners", allow)      # matches @username
    assert chat_allowed(999, "Partners", allow)      # case-insensitive
    assert not chat_allowed(999, "randoms", allow)


def test_is_addressed():
    assert is_addressed(True, "hi", "bot", False)                 # mentioned flag
    assert is_addressed(False, "hey @bot help", "bot", False)     # literal @username
    assert is_addressed(False, "hey @BOT help", "bot", False)     # case-insensitive
    assert not is_addressed(False, "just chatting", "bot", False)
    assert not is_addressed(False, "reply", "bot", True)          # bare reply is not enough


def test_strip_self_mention():
    assert strip_self_mention("@bot what is up", "bot") == "what is up"
    assert strip_self_mention("hello", "bot") == "hello"
    assert strip_self_mention("@bot", "bot") == ""
