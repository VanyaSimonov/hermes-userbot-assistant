#!/usr/bin/env bash
#
# Installer for the Hermes Telegram Userbot platform plugin.
#
# What it does:
#   1. Locates HERMES_HOME (default ~/.hermes) and the `hermes` CLI.
#   2. Links (or copies) the `telegram_userbot` package into
#      HERMES_HOME/plugins/ so Hermes discovers it as a directory plugin.
#   3. Enables the plugin and runs `hermes pm repair`, which installs the
#      declared python_dependencies (telethon) INTO THE MANAGED HERMES ENV.
#      This is the supported path: a manual `pip install` into the interpreter
#      does NOT survive Hermes' env sync, which prunes anything not tracked by
#      PM. `pm repair` installs telethon in a way that persists.
#   4. Prints the remaining manual steps (API creds, one-time login, start).
#
# Usage:
#   ./install.sh            # symlink the plugin (recommended; `git pull` updates it)
#   ./install.sh --copy     # copy instead of symlink (for read-only checkouts)
#   ./install.sh --no-enable  # install files only; don't enable or repair
#   HERMES_HOME=/path ./install.sh
#
# Re-running is safe (idempotent).

set -euo pipefail

MODE="link"
DO_ENABLE=1
for arg in "$@"; do
  case "$arg" in
    --copy) MODE="copy" ;;
    --link) MODE="link" ;;
    --no-enable) DO_ENABLE=0 ;;
    -h|--help)
      sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_SRC="$SCRIPT_DIR/telegram_userbot"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="$HERMES_HOME/plugins"
DEST="$PLUGINS_DIR/telegram_userbot"
PLUGIN_NAME="telegram-userbot-platform"

HERMES_BIN="$(command -v hermes || echo "$HOME/.local/bin/hermes")"

echo "==> Hermes Telegram Userbot installer"
echo "    HERMES_HOME : $HERMES_HOME"
echo "    plugin src  : $PKG_SRC"
echo "    install to  : $DEST  (mode: $MODE)"
echo "    hermes CLI  : $HERMES_BIN"
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
if [ ! -x "$HERMES_BIN" ]; then
  echo "ERROR: 'hermes' CLI not found. Install Hermes, or ensure 'hermes' is on PATH." >&2
  exit 1
fi

# --- 1. Place the plugin -----------------------------------------------------
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

if [ "$DO_ENABLE" -eq 0 ]; then
  echo "==> --no-enable: files in place; enable it yourself with:"
  echo "      hermes plugins enable $PLUGIN_NAME && hermes pm repair"
  exit 0
fi

# --- 2. Enable + install declared deps via PM (the sync-proof path) ----------
echo "==> Enabling plugin: $PLUGIN_NAME"
"$HERMES_BIN" plugins enable "$PLUGIN_NAME" || {
  echo "WARNING: enable failed — check 'hermes plugins list'." >&2; }

echo "==> Installing declared python_dependencies (telethon) via 'hermes pm repair'..."
echo "    (this installs into the managed Hermes env so env sync won't prune it)"
"$HERMES_BIN" pm repair || {
  echo "WARNING: 'hermes pm repair' did not complete cleanly. Re-run it, then" >&2
  echo "         verify with: hermes plugins doctor" >&2; }

echo
echo "==> Verify discovery + deps:"
echo "      hermes plugins list --plain | grep telegram-userbot   # expect: enabled"
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
   (produces HERMES_HOME/telegram-userbot-auth/userbot.session, chmod 0600)

3) Start the gateway and confirm it connected:

     hermes gateway status
     hermes gateway run          # foreground, or:
     hermes gateway install && hermes gateway start   # as a background service

4) Add the userbot account to a group, then @mention it. It replies only when
   mentioned/replied to, and only in allowed chats.

SECURITY: a userbot runs on a live account — that is against the spirit of
Telegram's ToS and risks a ban. The session file grants full account control;
it is stored 0600 under HERMES_HOME/telegram-userbot-auth/.
=============================================================================
NEXT
