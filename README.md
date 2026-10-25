# Hermes Telegram Userbot

A **platform plugin for [Hermes Agent](https://hermes-agent.nousresearch.com)**
that connects the assistant to Telegram as a **real user account** (MTProto, via
[Telethon](https://docs.telethon.dev/)) — not a BotFather bot.

Why a userbot? A Bot-API bot in a group is limited by Telegram's privacy mode,
can't read the thread freely, and is visibly a bot. A **userbot is an ordinary
group member**, so you can drop a corporate assistant straight into group chats
— including chats with external partners — and it participates like a human
would.

> Hermes already ships a `telegram` platform on the **Bot API**. This plugin is
> the **userbot** alternative and installs alongside it as a separate platform
> (`telegram_userbot`).

## Safety first — read this

- **Telegram ToS / ban risk.** Automating a live account is against the spirit
  of Telegram's Terms of Service and can get the account limited or banned. Use
  a **dedicated account / phone number**, not your personal one.
- **It can see everything in a group.** So it is **mention-gated by default**
  (acts only when `@`-mentioned or replied to) and supports an explicit
  **chat allowlist** — set `TELEGRAM_USERBOT_ALLOWED_CHATS` so it stays silent
  in every chat you have not opted in.
- **The session file is a credential.** Whoever holds it controls the account.
  It is stored `0600` under `HERMES_HOME/telegram-userbot-auth/`.
- **Consent.** Tell partners an AI participant is in the chat where that is
  expected or required.

## Install

One command — the plugin lives in the `telegram_userbot/` subdirectory, so
install it with the `owner/repo/subdir` shorthand:

```bash
hermes plugins install VanyaSimonov/hermes-userbot-assistant/telegram_userbot --yes-deps --enable
```

This clones the repo, installs the declared `telethon` dependency **into the
managed Hermes env** (the only way that survives Hermes' env sync — a plain
`pip install` gets pruned), records provenance, and enables the plugin.

There is also a wrapper script if you prefer running one from a clone (it just
calls the command above, and auto-detects the repo from your git origin so
forks work):

```bash
git clone https://github.com/VanyaSimonov/hermes-userbot-assistant.git
cd hermes-userbot-assistant && ./install.sh        # --no-enable / --ref <sha> supported
```

Verify:

```bash
hermes plugins list --plain | grep telegram-userbot   # expect: enabled
```

Verify discovery:

```bash
hermes plugins list    # expect: telegram-userbot-platform
```

## Configure

Get an API app at <https://my.telegram.org/apps>, then add to `~/.hermes/.env`:

```bash
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Recommended for partner groups (silent everywhere else):
TELEGRAM_USERBOT_ALLOWED_CHATS=@yourpartnerchat,-1001234567890
# Who may DM the assistant:
TELEGRAM_USERBOT_ALLOWED_USERS=123456789
```

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `TELEGRAM_API_ID` | ✅ | — | API ID from my.telegram.org |
| `TELEGRAM_API_HASH` | ✅ | — | API hash from my.telegram.org |
| `TELEGRAM_USERBOT_SESSION_STRING` | — | file session | Telethon StringSession (env-based secret storage) |
| `TELEGRAM_USERBOT_ALLOWED_CHATS` | — | all chats | Comma-separated chat ids / `@usernames` the bot may act in |
| `TELEGRAM_USERBOT_ALLOWED_USERS` | — | none | User ids allowed to DM the bot |
| `TELEGRAM_USERBOT_ALLOW_ALL_USERS` | — | `false` | Allow any DM (dev only) |
| `TELEGRAM_USERBOT_REQUIRE_MENTION` | — | `true` | In groups, act only on `@mention`/reply |
| `TELEGRAM_USERBOT_HOME_CHANNEL` | — | none | Default chat for cron / notifications |

## Sign in (once)

Interactive — phone number, the login code Telegram sends, and your 2FA
password if set. **Use the dedicated account here.**

```bash
TELEGRAM_API_ID=... TELEGRAM_API_HASH=... python -m telegram_userbot.login
# add --string to also print a StringSession for TELEGRAM_USERBOT_SESSION_STRING
```

## Run

Plugins are opt-in, so enable it once, then start the gateway:

```bash
hermes plugins enable telegram-userbot-platform
hermes gateway status
hermes gateway start        # or: hermes gateway restart
```

Add the userbot account to a group and `@mention` it. It replies only when
addressed, and only in allowed chats.

## How it fits Hermes

A Hermes platform is a directory plugin (`plugin.yaml` + a `register(ctx)`
entry point) whose adapter subclasses `BasePlatformAdapter`. This plugin:

- `telegram_userbot/adapter.py` — the Telethon adapter: `connect` / `disconnect`
  / `send` / `get_chat_info`, turning Telethon `NewMessage` events into Hermes
  `MessageEvent`s and dispatching them with `handle_message`; registers the
  platform via `ctx.register_platform(...)`.
- `telegram_userbot/gating.py` — pure inbound-gating policy (DM/chat allowlists,
  mention gating, echo filter), unit-tested on its own.
- `telegram_userbot/login.py` — the one-time interactive sign-in.
- `install.sh` — attaches the plugin to any Hermes install.

## Development

```bash
pip install telethon pytest
pytest tests/          # gating policy tests (no Hermes needed)
```

The adapter imports `gateway.*` from the host Hermes, so it only runs inside a
Hermes environment; the gating tests run anywhere.

## License

MIT — see [LICENSE](LICENSE).
