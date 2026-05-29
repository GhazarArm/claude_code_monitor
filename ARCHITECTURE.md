# Architecture — Claude Code Monitor

Full technical walkthrough of how every piece connects.

---

## Overview

Claude Code Monitor intercepts tool calls made by Claude Code, shows a permission prompt **simultaneously** in a native macOS dialog and a Telegram message, and lets you approve or deny from either place. First response wins.

There are two deployment modes:

| Mode | How it works | Best for |
|------|-------------|----------|
| **Relay** | Your machine talks to a hosted relay server; the relay server owns the bot | Shared bot — multiple users, no local daemon needed |
| **Direct** | Your machine polls the Telegram Bot API directly | Single user, own bot token |

The recommended and default mode is **relay**.

---

## System Components

```
┌─────────────────────────────────────────────────────────────────┐
│  Your Machine                                                   │
│                                                                 │
│  Claude Code CLI                                                │
│       │                                                         │
│       │  PreToolUse hook (every tool call)                      │
│       ▼                                                         │
│  telegram-permission.py  ◄── reads ~/.claude/telegram.conf     │
│       │                                                         │
│       ├──► osascript (macOS dialog)   ──────────────────┐       │
│       │                                                 │       │
│       └──► POST /v1/prompt ──────────────────────────── │ ──►  │
│                │                                        │       │
│                │  (relay server)                        │       │
│                ▼                                        │       │
│  ┌─────────────────────────────────┐                   │       │
│  │  Railway Relay Server           │                   │       │
│  │  claudecodemonitor-*.railway.app│                   │       │
│  │                                 │                   │       │
│  │  POST /webhook  ◄── Telegram    │                   │       │
│  │  POST /v1/prompt                │   first           │       │
│  │  GET  /v1/wait/{req_id}  ◄──────┼── wins ──────────►│       │
│  │  POST /v1/edit                  │                   │       │
│  └─────────────────────────────────┘                   │       │
│                │                                        │       │
│                ▼                                        │       │
│           Telegram Bot API                              │       │
│                │                                        │       │
│                ▼                                        │       │
│          Your Telegram    ──── tap button ─────────────►│       │
│                                                         │       │
│                              Claude unblocks ◄──────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Files

### On your machine (`~/.claude/`)

| File | Purpose |
|------|---------|
| `telegram-permission.py` | PreToolUse hook — the core permission logic |
| `telegram.conf` | Credentials: chat ID, relay URL, relay token |
| `telegram-mute.json` | Current mute state (written by relay server commands) |
| `settings.local.json` | "Allow always" entries added when you tap that button |
| `telegram-poll.lock` | PID lock preventing 409 conflicts (direct mode only) |
| `telegram-permission.log` | Per-call log for debugging |
| `session-tracker.py` | Tracks open Claude sessions (for `/sessions` bot command) |
| `sessions.json` | Live session data written by session-tracker.py |
| `telegram-notify.sh` | Stop/Notification hook — sends task-done messages |
| `telegram-bot-listener.py` | Direct mode only — background daemon that polls Telegram |

### Relay server (`relay/main.py`)

Runs on Railway. Single Python process serving all users.

---

## Full Request Flow (Relay Mode)

### 1. Claude tries to run `rm -rf ./build`

Claude Code calls the PreToolUse hook with:
```json
{
  "tool_name": "Bash",
  "tool_input": { "command": "rm -rf ./build" }
}
```

### 2. `telegram-permission.py` wakes up

- Reads `~/.claude/telegram.conf` → gets `CHAT_ID`, `RELAY_URL`, `RELAY_TOKEN`
- Detects `rm -rf` → `bash_danger()` returns `(True, "Recursive delete")`
- Checks mute state via `GET /v1/muted/{chat_id}` → not muted
- Generates a unique `req_id = "{timestamp}_{pid}"`

### 3. Two threads race

**Thread A — macOS dialog:**
```
osascript → display dialog "rm -rf ./build" buttons {Deny, Mute 30m, Allow}
```
Blocks until you click a button (or Telegram wins and kills the process).

**Thread B — relay wait:**
```
POST /v1/prompt  →  relay server sends Telegram message with inline buttons
GET  /v1/wait/{req_id}  →  blocks (up to 600s) until button is tapped
```

### 4. You respond (either place)

**If you tap in Telegram:**
- Telegram sends a `callback_query` to `POST /webhook` on the relay server
- Relay extracts `decision|req_id` from `callback_data`
- Relay puts `decision` into `asyncio.Queue` for that `req_id`
- `GET /v1/wait/{req_id}` returns `{"decision": "allow"}`
- Thread B fires `done_event`
- Thread A's dialog process is killed

**If you click the macOS dialog:**
- `osascript` returns the button label
- Thread A fires `done_event`
- Thread B's long-poll is abandoned (server cleans up the queue after 600s)

### 5. Decision is acted on

- `approve()` → prints `{"decision": "approve"}` to stdout → Claude continues
- `block(reason)` → prints `{"decision": "block", "reason": "..."}` → Claude stops
- Relay message is updated via `POST /v1/edit` to show the final state (✅ / ❌ / 🔕)

---

## Relay Server API

Base URL: `https://claudecodemonitor-production.up.railway.app`

All user-facing endpoints require `chat_id` + `token` (HMAC-derived, no database).

### `POST /v1/prompt`
Send a permission prompt to the user's Telegram.
```json
{
  "chat_id":  "1164900113",
  "token":    "83c4c64a9e65e1434a62254e978e1972",
  "req_id":   "1780049551_92841",
  "text":     "<b>Permission Request</b>\n...",
  "keyboard": { "inline_keyboard": [[{"text": "✅ Allow", "callback_data": "allow|1780049551_92841"}]] }
}
```
Returns: `{"msg_id": 192}`

### `GET /v1/wait/{req_id}?chat_id=...&token=...`
Long-polls until the user taps a button. Blocks up to 600 seconds.

Returns: `{"decision": "allow"}` (or `null` on timeout)

### `POST /v1/edit`
Update the Telegram message after a decision is made.
```json
{
  "chat_id": "1164900113",
  "token":   "83c4c64a9e65e1434a62254e978e1972",
  "msg_id":  192,
  "text":    "✅ <b>Allowed</b>  <code>14:12</code>..."
}
```

### `GET /v1/muted/{chat_id}?token=...`
Check if the user has muted prompts.

Returns: `{"muted": false}` or `{"muted": true, "until": 1780100000}`

### `POST /webhook`
Telegram webhook endpoint. Returns `200 OK` immediately; processes update in background via `asyncio.create_task()`.

### `GET /health`
Liveness check. Returns `{"ok": true, "pending_prompts": 3}`.

### `GET /debug/webhook`
Shows current Telegram webhook registration status (safe — does not expose the token).

---

## Security Model

### HMAC tokens

Each user's token is derived deterministically:

```python
token = hmac.new(SERVER_SECRET.encode(), str(chat_id).encode(), sha256).hexdigest()[:32]
```

- No database needed — tokens survive server restarts
- `SERVER_SECRET` is set once in Railway environment variables
- A user's token is only valid for their own `chat_id`
- Tokens are compared with `hmac.compare_digest` (constant-time, no timing attacks)

### What the relay server can see

The relay server sees:
- Your `chat_id` (a public Telegram identifier)
- The permission prompt text (tool name, file path, or command)
- Your mute preference

It does **not** see:
- Your actual code or file contents
- Your `BOT_TOKEN` (never sent to the relay)
- Anything beyond what you'd see in the Telegram message itself

---

## Configuration File (`~/.claude/telegram.conf`)

```bash
# Direct mode credentials (used as fallback if no relay configured)
TELEGRAM_BOT_TOKEN="1234567890:ABCdef..."
TELEGRAM_CHAT_ID="1164900113"

# Relay mode (takes precedence when both RELAY_URL and RELAY_TOKEN are set)
RELAY_URL="https://claudecodemonitor-production.up.railway.app"
RELAY_TOKEN="83c4c64a9e65e1434a62254e978e1972"
```

If both relay and direct credentials are present, **relay mode is used**.

---

## Deploying Your Own Relay Server

1. Fork `https://github.com/GhazarArm/claude_code_monitor`
2. Create a new project on [Railway](https://railway.app)
3. Connect your fork
4. Set environment variables:
   - `BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
   - `SERVER_SECRET` — any random string (e.g. `openssl rand -hex 32`)
5. Deploy. Railway auto-detects `requirements.txt` and `railway.toml`.
6. On startup the server registers its webhook with Telegram automatically.
7. Users send `/start` to your bot to get their credentials.

---

## `telegram-permission.py` Decision Logic

```
stdin: {"tool_name": "...", "tool_input": {...}}
         │
         ├── Bash?
         │     ├── bash_danger() → (True, reason) ?
         │     │         ├── not muted?  → ask_both() → approve/block
         │     │         └── muted?      → approve silently
         │     └── safe command          → approve silently
         │
         ├── Read / Write / Edit / MultiEdit?
         │     ├── inside project dir?   → approve silently
         │     ├── in allowlist?         → approve silently (Claude handles this)
         │     └── outside project?
         │               ├── not muted? → ask_both() → approve/block/always
         │               └── muted?     → approve silently
         │
         └── everything else             → approve silently
```

### Dangerous Bash patterns detected

| Pattern | Reason shown |
|---------|-------------|
| `rm -r`, `rm -rf` | Recursive delete |
| `git push -f`, `git push --force` | Force push |
| `git reset --hard` | Hard reset |
| `git clean -fd`, `git clean -fx` | Git clean |
| `mkfs.*` | Format filesystem |
| `dd of=/dev/...` | DD write to device |
| `sudo <any above>` | Inherits inner command reason |

---

## `ask_both()` — Race Between Dialog and Telegram

```python
done_event = threading.Event()

Thread A: osascript dialog   ──► result_holder[0] = ("dialog", decision); done_event.set()
Thread B: relay /v1/wait     ──► result_holder[0] = ("telegram", decision); done_event.set()

done_event.wait()   # blocks forever — no timeout

# Winner is in result_holder[0]
# Loser's subprocess/request is killed/abandoned
```

The dialog subprocess is a separate `osascript` process — killing it dismisses the dialog without any user-visible error.

---

## Mute State

### Relay mode
- Mute is stored in-memory on the relay server (`mute_state` dict, per `chat_id`)
- `/mute 2h` command → relay sets `{"muted": True, "until": timestamp}`
- Permission hook checks `GET /v1/muted/{chat_id}` before every prompt
- Survives across Claude Code restarts (relay server holds the state)
- Lost only on relay server restart (Railway redeploy)

### Direct mode
- Mute is stored locally in `~/.claude/telegram-mute.json`
- Persists across everything, including machine reboots
