# Claude Code Monitor

> Permission prompts from Claude Code — in your macOS dialog **and** Telegram simultaneously. First tap wins.

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template)

---

## What it does

Every time Claude Code tries to run something potentially destructive, you get asked in **two places at once**:

- 🖥 A native macOS dialog pops up on your screen
- 📱 A Telegram message with inline buttons arrives on your phone

Tap **Allow** or **Deny** from either — the first response wins, Claude continues (or stops) instantly.

```
Claude: "I'll run rm -rf ./build"

  ┌─── macOS ──────────────────────┐    ┌─── Telegram ──────────────────┐
  │  ⚠️ Dangerous Bash             │    │  ⚠️ Permission Request  14:32  │
  │  rm -rf ./build                │    │  📁 Project: picsart-android   │
  │                                │    │  🏷 Type: Recursive delete     │
  │  [Deny] [Mute 30m] [Allow]     │    │                                │
  └────────────────────────────────┘    │  rm -rf ./build                │
                                        │  [✅ Allow] [❌ Deny]           │
                                        │  [🔕 Mute 30m] [🔕 Mute 2h]   │
                                        └────────────────────────────────┘
```

---

## Quick start for users

**1.** Message the bot on Telegram: [@Ghazar_claude_code_bot](https://t.me/Ghazar_claude_code_bot) → send `/start`

You'll get your personal credentials:
```
Chat ID   1164900113
Token     83c4c64a9e65e1434a62254e978e1972
Relay URL https://claudecodemonitor-production.up.railway.app
```

**2.** Run the one-line installer on your machine:
```bash
git clone https://github.com/GhazarArm/claude_code_monitor
cd claude_code_monitor
./install.sh \
  --relay https://claudecodemonitor-production.up.railway.app \
  --chat-id YOUR_CHAT_ID \
  --token YOUR_TOKEN
```

Done. Open Claude Code and try a dangerous command — the prompt appears.

---

## What triggers a prompt

| Tool | Condition | Buttons |
|------|-----------|---------|
| `Bash` | `rm -rf`, `git push --force`, `git reset --hard`, `mkfs`, `dd of=/dev/…` | Allow / Deny / Mute 30m / Mute 2h |
| `Read` | File outside current project directory | Allow / Allow always / Deny |
| `Write` | File outside current project directory | Allow / Allow always / Deny |
| `Edit` | File outside current project directory | Allow / Allow always / Deny |
| Everything else | — | Silent auto-approve |

---

## Bot commands

| Command | Effect |
|---------|--------|
| `/start` | Get your installation credentials |
| `/mute` | Pause prompts for 30 minutes |
| `/mute 2h` | Pause for 2 hours |
| `/mute 1d` | Pause for 1 day |
| `/unmute` | Re-enable prompts immediately |
| `/status` | Show current mute state |
| `/help` | List all commands |

---

## How it works

```
Claude Code
    │ PreToolUse hook (every tool call)
    ▼
telegram-permission.py
    │
    ├──► osascript dialog (macOS) ─────────────────────┐
    │                                                   │ first
    └──► POST /v1/prompt ──► Relay Server               │ wins
              │                    │                    │
              │              Telegram Bot               │
              │                    │                    │
              │              Your phone ── tap ─────────┘
              │
              └── GET /v1/wait/{req_id} ◄── blocks until tap
                        │
                        └── returns decision → Claude unblocks
```

The relay server is a FastAPI app running on Railway. It:
- Owns the Telegram bot (via webhook)
- Queues pending prompts per user
- Routes button taps back to the waiting hook process
- Handles `/mute`, `/unmute`, `/status` commands

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full technical deep-dive.

---

## Deploying your own relay server

You only need to do this once. Your friends use your server — no setup on their end beyond running the installer.

**1.** Fork this repo

**2.** Create a Telegram bot via [@BotFather](https://t.me/botfather) → `/newbot` → copy the token

**3.** Deploy to Railway:
- New project → connect your fork
- Set environment variables:
  ```
  BOT_TOKEN     = <from BotFather>
  SERVER_SECRET = <any random string, e.g. openssl rand -hex 32>
  ```
- Deploy — Railway auto-detects `requirements.txt` and `railway.toml`

**4.** On startup the server registers its Telegram webhook automatically.

**5.** Send `/start` to your bot — it should reply with credentials.

**6.** Share the bot link with friends. They run the installer, enter their credentials, and it works.

---

## Requirements

| | Version |
|-|---------|
| Python | 3.8+ |
| macOS | 12+ (for native dialog) |
| Linux | any distro (uses `zenity` for dialog) |
| Claude Code CLI | latest |

---

## Uninstall

```bash
./uninstall.sh
```

Removes the hook scripts, cleans `~/.claude/settings.json`, and stops any background daemons.

---

## License

MIT
