#!/usr/bin/env bash
#
# Installer for the Hermes Telegram Userbot platform plugin.
#
# This is a thin wrapper around the canonical, supported install path:
#
#     hermes plugins install <owner/repo>/telegram_userbot --yes-deps --enable
#
# `hermes plugins install` clones the repo, resolves the `telegram_userbot`
# subdirectory (where plugin.yaml lives), installs the declared python
# dependency (telethon) INTO THE MANAGED HERMES ENV via PM — the only way that
# survives Hermes' env sync — records provenance, and enables the plugin.
#
# A manual symlink + `pip install` does NOT register the dependency with PM, so
# the gateway starts with telethon missing and silently skips the platform.
# That is why this wrapper uses `hermes plugins install` rather than copying
# files around.
#
# Usage (run from a checkout, or anywhere):
#   ./install.sh                 # install from this repo's origin, enabled
#   ./install.sh --no-enable     # install disabled; enable later yourself
#   ./install.sh --ref <sha>     # pin an exact commit
#   REPO=owner/name ./install.sh # override the owner/repo (e.g. a fork)
#
# Re-running is safe (idempotent; use --force semantics via hermes if needed).

set -euo pipefail

SUBDIR="telegram_userbot"
DEFAULT_REPO="VanyaSimonov/hermes-userbot-assistant"
ENABLE_FLAG="--enable"
REF_ARGS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-enable) ENABLE_FLAG="--no-enable" ;;
    --ref) shift; REF_ARGS=(--ref "${1:?--ref needs a commit SHA}") ;;
    -h|--help) sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

HERMES_BIN="$(command -v hermes || echo "$HOME/.local/bin/hermes")"
if [ ! -x "$HERMES_BIN" ]; then
  echo "ERROR: 'hermes' CLI not found. Install Hermes, or put 'hermes' on PATH." >&2
  exit 1
fi

# Derive owner/repo from this checkout's git origin when available (fork-friendly),
# else fall back to the canonical repo. REPO env var overrides both.
REPO="${REPO:-}"
if [ -z "$REPO" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  origin="$(git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)"
  case "$origin" in
    *github.com[:/]*)
      REPO="$(printf '%s' "$origin" | sed -E 's#.*github\.com[:/]##; s#\.git$##')" ;;
  esac
fi
[ -z "$REPO" ] && REPO="$DEFAULT_REPO"

IDENTIFIER="$REPO/$SUBDIR"
echo "==> Installing Hermes Telegram Userbot plugin"
echo "    identifier : $IDENTIFIER"
echo "    hermes CLI : $HERMES_BIN"
echo "    enable     : $ENABLE_FLAG"
echo

"$HERMES_BIN" plugins install "$IDENTIFIER" --yes-deps "$ENABLE_FLAG" "${REF_ARGS[@]}"

echo
echo "==> Verify:"
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

2) Sign in ONCE (phone + code, optional 2FA). Use a DEDICATED account/number.
   You need the plugin's login helper; the simplest way is from a clone:

     git clone https://github.com/VanyaSimonov/hermes-userbot-assistant.git
     cd hermes-userbot-assistant
     TELEGRAM_API_ID=... TELEGRAM_API_HASH=... \
       python -m telegram_userbot.login            # or add --string
   (writes HERMES_HOME/telegram-userbot-auth/userbot.session, chmod 0600)

3) Start the gateway and confirm it connected:

     hermes gateway status
     hermes gateway run          # foreground, or:
     hermes gateway install && hermes gateway start   # as a background service

   Look for:  "✓ telegram_userbot connected" and "Gateway running with N platform(s)".

4) Add the userbot account to a group, then @mention it. It replies only when
   mentioned/replied to, and only in allowed chats.

SECURITY: a userbot runs on a live account — that is against the spirit of
Telegram's ToS and risks a ban. The session file grants full account control;
it is stored 0600 under HERMES_HOME/telegram-userbot-auth/.
=============================================================================
NEXT
