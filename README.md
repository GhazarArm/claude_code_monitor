# Claude Code → Telegram Hook

> Interactive permission prompts via native dialog + Telegram — simultaneously. First response wins.

---

## 🎯 What it does

- 🔐 Every dangerous Bash command (`rm -rf`, `git push --force`, `git reset --hard`, etc.) triggers a **native dialog + Telegram message** at the same time
- 📁 Every file access outside your current project triggers **Allow / Allow always / Deny**
- ⚡ **First response wins** — dialog click OR Telegram tap — Claude continues instantly
- 🔇 **Mute prompts** for 30m / 2h / 1d when you want uninterrupted work
- 📊 `/sessions` command shows all open Claude Code sessions with live status
- ✅ Everything else **auto-approves silently**

---

## 📋 Requirements

| Requirement | Version |
|-------------|---------|
| Python | 3.8+ |
| curl | any recent version |
| macOS | 12+ (for native dialog) |
| Linux | any distro with `zenity` |
| Claude Code CLI | latest |

---

## 🚀 Quick Install

```bash
git clone https://github.com/YOUR_USERNAME/claude-telegram-hook.git
cd claude-telegram-hook
./install.sh
```

The installer will prompt you for your **Bot Token** and **Chat ID**.

> **Getting your Chat ID:** Send any message to your bot, then run:
> ```bash
> curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" \
>   | python3 -c "import sys,json; u=json.load(sys.stdin)['result']; print(u[-1]['message']['chat']['id'])"
> ```

---

## 🔍 What triggers a prompt

| Tool | Condition | Prompt type |
|------|-----------|-------------|
| `Bash` | `rm -rf`, `git push --force`, `git reset --hard`, `chmod`, `sudo`, `curl \| bash` | Allow / Deny |
| `Read` | File outside current project directory | Allow / Allow always / Deny |
| `Write` | File outside current project directory | Allow / Allow always / Deny |
| `Edit` | File outside current project directory | Allow / Allow always / Deny |
| `Bash` | Any command when muted | Silent auto-approve |
| Everything else | — | Silent auto-approve |

---

## 🤖 Bot commands

| Command | Effect |
|---------|--------|
| `/mute 30m` | Silence all prompts for 30 minutes |
| `/mute 2h` | Silence all prompts for 2 hours |
| `/mute 1d` | Silence all prompts for 1 day |
| `/unmute` | Re-enable prompts immediately |
| `/status` | Show current mute status and active sessions |
| `/sessions` | List all open Claude Code sessions with live status |
| `/help` | Show all available commands |

---

## 🏗 How it works

```
Claude Code
    │
    ├── PreToolUse hook ──► session-tracker.py   (registers session)
    │                  └──► telegram-permission.py (intercepts dangerous ops)
    │                            │
    │                            ├──► macOS osascript dialog  ┐
    │                            └──► Telegram inline buttons ┘ first click wins
    │
    ├── Stop hook ────────► telegram-notify.sh    (task complete notification)
    │                  └──► session-tracker.py --stop
    │
    └── Notification hook ► telegram-notify.sh    (pass-through notifications)

telegram-bot-listener.py  (long-polls Telegram, routes /commands and button callbacks)
    └── runs as LaunchAgent (macOS) or systemd user service (Linux)
```

- `telegram-permission.py` — PreToolUse hook. Checks if the tool/command needs approval, fires both dialog and Telegram in parallel, blocks until one responds.
- `telegram-bot-listener.py` — Background daemon. Long-polls the Telegram Bot API, handles `/commands` and inline keyboard callbacks (Allow/Deny buttons).
- `session-tracker.py` — Tracks open Claude Code sessions in `sessions.json` for the `/sessions` command.
- `telegram-notify.sh` — Sends Stop/Notification events to Telegram.

---

## 🔇 Muting

When you need uninterrupted work, mute prompts directly from Telegram:

```
/mute 30m   → no prompts for 30 minutes (all auto-approved)
/mute 2h    → no prompts for 2 hours
/mute 1d    → no prompts for 1 day
/unmute     → restore prompts immediately
```

Mute state is stored in `~/.claude/telegram-mute.json` and respected by all running Claude Code sessions simultaneously.

---

## 🗑 Uninstall

```bash
./uninstall.sh
```

This removes the LaunchAgent/systemd service, all scripts from `~/.claude/`, and cleans the hooks from `~/.claude/settings.json`.

---

## 📄 License

MIT — see [LICENSE](LICENSE).
