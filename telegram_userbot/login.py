"""One-time interactive sign-in for the Telegram userbot.

Telethon needs a real login the first time (phone number → code → optional
2FA password). That is interactive, so it is kept OUT of the gateway adapter
(which must never block on input) and lives here instead.

Run it once on the machine that will host the gateway:

    python -m telegram_userbot.login            # writes a file session
    python -m telegram_userbot.login --string   # also prints a StringSession

Credentials are read from the environment (the same names the adapter uses):
    TELEGRAM_API_ID, TELEGRAM_API_HASH
from https://my.telegram.org/apps. The file session is written to
``HERMES_HOME/telegram-userbot-auth/userbot.session`` with 0600 permissions —
anyone holding that file controls the account, so guard it like a password.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import stat
import sys
from pathlib import Path


def _auth_dir() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser()
    d = home / "telegram-userbot-auth"
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(stat.S_IRWXU)  # 0700
    except OSError:
        pass
    return d


def _session_path() -> Path:
    return _auth_dir() / "userbot.session"


def _lock_down(path: Path) -> None:
    """chmod 0600 the session file and its -wal/-shm sidecars."""
    for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"),
              Path(str(path) + "-journal")):
        if p.exists():
            try:
                p.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
            except OSError:
                pass


async def _run(make_string: bool) -> int:
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("telethon is not installed. Run: pip install telethon", file=sys.stderr)
        return 2

    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not (api_id and api_hash):
        print("Set TELEGRAM_API_ID and TELEGRAM_API_HASH first "
              "(get them at https://my.telegram.org/apps).", file=sys.stderr)
        return 2

    session_file = _session_path()
    client = TelegramClient(str(session_file).removesuffix(".session"), int(api_id), api_hash)

    print("Signing in to Telegram. You will be asked for the phone number of the "
          "account the userbot will run as, then the login code Telegram sends you "
          "(and your 2FA password, if set).\n")
    # start() drives the interactive phone/code/2FA prompts.
    await client.start()

    me = await client.get_me()
    uname = getattr(me, "username", None)
    print(f"\nSigned in as: {getattr(me, 'first_name', '')} "
          f"(@{uname}) id={getattr(me, 'id', '?')}")
    _lock_down(session_file)
    print(f"File session saved: {session_file} (chmod 0600)")

    if make_string:
        string = StringSession.save(client.session)
        print("\nStringSession (store as TELEGRAM_USERBOT_SESSION_STRING; treat as a secret):\n")
        print(string)
        print("\nWith the StringSession set in the environment you can delete the "
              "file session if you prefer env-only secret storage.")

    await client.disconnect()
    print("\nDone. Enable the platform and (re)start your Hermes gateway.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive Telegram userbot login (Telethon).")
    parser.add_argument("--string", action="store_true",
                        help="Also print a StringSession for env-based secret storage.")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args.string))
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
