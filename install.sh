#!/usr/bin/env bash
#
# Installer for the Hermes Telegram Userbot platform plugin.
#
# What it does:
#   1. Locates HERMES_HOME (default ~/.hermes).
#   2. Installs `telethon` into the SAME Python environment Hermes runs in.
#   3. Links (or copies) the `telegram_userbot` package into
#      HERMES_HOME/plugins/ so Hermes discovers it as a directory plugin.
#   4. Prints the remaining manual steps (API creds, one-time login, enable).
#
# Usage:
#   ./install.sh            # symlink the plugin (recommended; `git pull` updates it)
#   ./install.sh --copy     # copy instead of symlink (for read-only checkouts)
#   HERMES_HOME=/path ./install.sh
#
# Re-running is safe (idempotent).

set -euo pipefail

MODE="link"
for arg in "$@"; do
  case "$arg" in
    --copy) MODE="copy" ;;
    --link) MODE="link" ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_SRC="$SCRIPT_DIR/telegram_userbot"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="$HERMES_HOME/plugins"
DEST="$PLUGINS_DIR/telegram_userbot"

echo "==> Hermes Telegram Userbot installer"
echo "    HERMES_HOME : $HERMES_HOME"
echo "    plugin src  : $PKG_SRC"
echo "    install to  : $DEST  (mode: $MODE)"
echo

if [ ! -f "$PKG_SRC/plugin.yaml" ]; then
  echo "ERROR: $PKG_SRC/plugin.yaml not found — run this from the repo root." >&2
  exit 1
fi
if [ ! -d "$HERMES_HOME" ]; then
  echo "ERROR: HERMES_HOME ($HERMES_HOME) does not exist. Install Hermes first," >&2
  echo "       or pass HERMES_HOME=/path/to/.hermes ./install.sh" >&2
  exit 1
fi

# --- 1. Find the Python that Hermes uses, to install telethon into it --------
# Prefer the interpreter inside the Hermes checkout's venv if present; fall back
# to `hermes pm env`-reported python, then to the current `python3`.
HERMES_PY=""
for cand in \
  "$HERMES_HOME/hermes-agent/.venv/bin/python" \
  "$HERMES_HOME/hermes-agent/.venv/bin/python3"; do
  if [ -x "$cand" ]; then HERMES_PY="$cand"; break; fi
done
if [ -z "$HERMES_PY" ]; then
  HERMES_PY="$(command -v python3 || command -v python || true)"
fi
if [ -z "$HERMES_PY" ]; then
  echo "ERROR: no python interpreter found to install telethon into." >&2
  exit 1
fi
echo "==> Installing telethon into: $HERMES_PY"
if ! "$HERMES_PY" -c "import telethon" >/dev/null 2>&1; then
  "$HERMES_PY" -m pip install --quiet --disable-pip-version-check telethon \
    || { echo "ERROR: 'pip install telethon' failed for $HERMES_PY" >&2; exit 1; }
  echo "    telethon installed."
else
  echo "    telethon already present."
fi

# --- 2. Place the plugin -----------------------------------------------------
mkdir -p "$PLUGINS_DIR"
if [ -e "$DEST" ] || [ -L "$DEST" ]; then
  echo "==> Removing existing $DEST"
  rm -rf "$DEST"
fi
if [ "$MODE" = "copy" ]; then
  cp -R "$PKG_SRC" "$DEST"
  echo "==> Copied plugin to $DEST"
else
  ln -s "$PKG_SRC" "$DEST"
  echo "==> Symlinked plugin: $DEST -> $PKG_SRC"
fi

echo
echo "==> Plugin files in place. Verify discovery with:"
echo "      hermes plugins list      # expect: telegram-userbot-platform"
echo
cat <<'NEXT'
================================  NEXT STEPS  ================================
1) Create a Telegram API app (once) at  https://my.telegram.org/apps
   and set, in  ~/.hermes/.env :

     TELEGRAM_API_ID=123456
     TELEGRAM_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
     # recommended for partner groups — the bot is silent everywhere else:
     TELEGRAM_USERBOT_ALLOWED_CHATS=@yourpartnerchat,-1001234567890
     # who may DM it:
     TELEGRAM_USERBOT_ALLOWED_USERS=123456789

2) Sign in ONCE (phone + code, optional 2FA). Use a DEDICATED account/number:

     cd "$(dirname "$0")"
     TELEGRAM_API_ID=... TELEGRAM_API_HASH=... \
       python -m telegram_userbot.login            # or add --string

3) (Re)start the gateway and confirm it connected:

     hermes gateway status
     hermes gateway start        # or: hermes gateway restart

4) Add the userbot account to a group, then @mention it. It replies only when
   mentioned/replied to, and only in allowed chats.

SECURITY: a userbot runs on a live account — that is against the spirit of
Telegram's ToS and risks a ban. The session file grants full account control;
it is stored 0600 under HERMES_HOME/telegram-userbot-auth/.
=============================================================================
NEXT
