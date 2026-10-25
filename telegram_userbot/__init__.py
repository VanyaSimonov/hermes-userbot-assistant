"""Telegram userbot platform plugin for Hermes Agent.

Logs in as a real Telegram user account (MTProto / Telethon) so the assistant
can participate in ordinary group chats. See ``adapter.py`` for the gateway
adapter and ``login.py`` for the one-time interactive sign-in that produces the
session.
"""

from .adapter import register

__all__ = ["register"]
