#!/usr/bin/env python3
"""
Claude Code — Telegram Bot Listener (background daemon)
Handles bot commands from Telegram at any time, independently of Claude sessions.

Supported commands (send these to the bot):
  /mute           → mute for 30 min (default)
  /mute 30m       → mute for 30 minutes
  /mute 2h        → mute for 2 hours
  /mute 1d        → mute for 1 day
  /unmute         → unmute immediately
  /status         → show current mute state

Run via LaunchAgent (auto-start on login, auto-restart on crash).
"""
import json
import os
import re
import sys
import time
import urllib.request

# ── Paths ─────────────────────────────────────────────────────────────────────
CLAUDE_DIR = os.path.dirname(os.path.abspath(__file__))
CONF      = os.path.join(CLAUDE_DIR, "telegram.conf")
MUTE_FILE = os.path.join(CLAUDE_DIR, "telegram-mute.json")
LOG_FILE  = os.path.join(CLAUDE_DIR, "telegram-bot-listener.log")
POLL_LOCK = os.path.join(CLAUDE_DIR, "telegram-poll.lock")

# ── Logging ───────────────────────────────────────────────────────────────────
def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass

# ── Load credentials ──────────────────────────────────────────────────────────
TOKEN = CHAT_ID = None
try:
    with open(CONF) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                TOKEN = line.split("=", 1)[1].strip("\"'")
            elif line.startswith("TELEGRAM_CHAT_ID="):
                CHAT_ID = line.split("=", 1)[1].strip("\"'")
except Exception as e:
    log(f"ERROR loading config: {e}")
    sys.exit(1)

if not TOKEN or not CHAT_ID:
    log("ERROR: TOKEN or CHAT_ID missing in telegram.conf")
    sys.exit(1)

BASE = f"https://api.telegram.org/bot{TOKEN}"

# ── Telegram API ──────────────────────────────────────────────────────────────
def tg(method: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{BASE}/{method}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read())
    except Exception as e:
        log(f"tg.{method} error: {e}")
        return {}

def send(text: str):
    tg("sendMessage", {
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    })

# ── Permission script lock (avoid 409 conflicts) ──────────────────────────────
def permission_script_active() -> bool:
    """Return True if the permission script is currently holding the poll lock."""
    try:
        with open(POLL_LOCK) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)   # raises OSError if process doesn't exist
        return True
    except Exception:
        return False

# ── Mute helpers ──────────────────────────────────────────────────────────────
def _write_mute(muted: bool, until: float):
    with open(MUTE_FILE, "w") as f:
        json.dump({"muted": muted, "muted_until": until, "muted_at": time.time()}, f)

def is_muted() -> tuple[bool, float]:
    """Returns (is_muted, muted_until). muted_until=0 means indefinite."""
    try:
        with open(MUTE_FILE) as f:
            state = json.load(f)
        if not state.get("muted"):
            return False, 0
        until = state.get("muted_until", 0)
        if until == 0 or time.time() < until:
            return True, until
        _write_mute(False, 0)
        return False, 0
    except Exception:
        return False, 0

def set_muted(seconds: int):
    until = time.time() + seconds if seconds > 0 else 0
    _write_mute(True, until)
    log(f"muted for {seconds}s, until={time.strftime('%H:%M', time.localtime(until)) if until else 'forever'}")

def do_unmute():
    _write_mute(False, 0)
    log("unmuted")

# ── Duration parser: "30m", "2h", "1d", "90" → seconds ──────────────────────
def parse_duration(text: str) -> int:
    text = text.strip().lower()
    m = re.match(r'^(\d+)\s*(m|min|mins|h|hr|hrs|d|day|days)?$', text)
    if not m:
        return 30 * 60          # default: 30 minutes
    n    = int(m.group(1))
    unit = (m.group(2) or "m")[0]
    if unit == "d": return n * 86400
    if unit == "h": return n * 3600
    return n * 60

def fmt_duration(seconds: int) -> str:
    if seconds >= 86400:
        return f"{seconds // 86400}d"
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{seconds // 60}m"

# ── Command handlers ──────────────────────────────────────────────────────────
def handle_command(text: str):
    parts = text.strip().split(None, 1)
    cmd   = parts[0].lower()
    arg   = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/mute", "mute"):
        seconds = parse_duration(arg) if arg else 30 * 60
        set_muted(seconds)
        label   = fmt_duration(seconds)
        expires = time.strftime("%H:%M", time.localtime(time.time() + seconds))
        send(
            f"🔕 <b>Muted for {label}</b>\n"
            f"⏰ Permission prompts paused until <b>{expires}</b>\n\n"
            f"Send /unmute to re-enable early."
        )

    elif cmd in ("/unmute", "unmute"):
        do_unmute()
        send("🔔 <b>Unmuted</b> — permission prompts are active again.")

    elif cmd in ("/status", "status"):
        muted, until = is_muted()
        if muted:
            if until:
                remaining = max(0, int(until - time.time()))
                expires   = time.strftime("%H:%M", time.localtime(until))
                send(
                    f"🔕 <b>Currently muted</b>\n"
                    f"⏰ Expires at <b>{expires}</b> ({fmt_duration(remaining)} remaining)\n\n"
                    f"Send /unmute to re-enable early."
                )
            else:
                send("🔕 <b>Currently muted</b> (indefinite)\n\nSend /unmute to re-enable.")
        else:
            send("🔔 <b>Active</b> — permission prompts are enabled.")

    elif cmd in ("/sessions", "sessions"):
        handle_sessions()

    elif cmd in ("/help", "help"):
        send(
            "🤖 <b>Claude Code Bot</b>\n\n"
            "<b>Commands:</b>\n"
            "/mute — mute for 30 min\n"
            "/mute 2h — mute for 2 hours\n"
            "/mute 1d — mute for 1 day\n"
            "/unmute — re-enable prompts\n"
            "/status — show mute state\n"
            "/sessions — show open Claude sessions"
        )

    else:
        log(f"unknown command: {text!r}")

# ── /sessions handler ─────────────────────────────────────────────────────────
def handle_sessions():
    SESSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.json")

    try:
        with open(SESSIONS_FILE) as f:
            sessions = json.load(f)
    except Exception:
        send("📭 No session data yet.\n\nSessions are tracked once Claude starts using tools.")
        return

    if not sessions:
        send("📭 No active sessions found.")
        return

    now = time.time()

    def session_status(s):
        """Returns (emoji, label) based on recency."""
        stopped = s.get("stopped_at")
        last    = s.get("last_active", 0)
        age     = now - last

        if stopped and (now - stopped) < 30:   # stopped very recently
            return "⚪", "just stopped"
        if age < 90:                            # active in last 90 seconds
            return "🟢", "active now"
        if age < 10 * 60:                       # active in last 10 min
            return "🟡", fmt_age(age) + " ago"
        return "⚫", fmt_age(age) + " ago"

    def fmt_age(secs):
        if secs < 60:   return f"{int(secs)}s"
        if secs < 3600: return f"{int(secs//60)}m"
        return f"{int(secs//3600)}h {int((secs%3600)//60)}m"

    # Sort: most recently active first
    ordered = sorted(sessions.values(), key=lambda s: s.get("last_active", 0), reverse=True)

    lines = ["📱 <b>Claude Code Sessions</b>\n"]
    for s in ordered:
        emoji, label = session_status(s)
        project      = s.get("project", "unknown")
        cwd          = s.get("cwd", "")
        last_tool    = s.get("last_tool", "")
        last_cmd     = s.get("last_cmd", "")
        started      = s.get("started_at", 0)
        started_str  = time.strftime("%H:%M", time.localtime(started)) if started else "?"

        # Tool + brief command description
        if last_cmd:
            tool_line = f"{last_tool} · <code>{last_cmd[:50]}</code>"
        else:
            tool_line = last_tool or "—"

        lines.append(
            f"{emoji} <b>{project}</b>  <i>{label}</i>\n"
            f"<code>{cwd}</code>\n"
            f"Last: {tool_line}\n"
            f"Started: {started_str}"
        )

    send("\n\n".join(lines))

# ── Main polling loop ─────────────────────────────────────────────────────────
def main():
    log("bot listener started")
    send("🟢 <b>Claude Code bot listener started</b>\n\nSend /help for available commands.")

    offset = 0

    # Advance past any old queued messages on startup
    resp    = tg("getUpdates", {"limit": 1, "offset": -1})
    updates = resp.get("result", [])
    if updates:
        offset = updates[-1]["update_id"] + 1
    log(f"starting at offset={offset}")

    retry_delay = 5   # seconds to wait after an API error

    while True:
        # Back off while permission script holds the poll lock (avoids 409)
        if permission_script_active():
            time.sleep(1)
            continue

        try:
            resp = tg("getUpdates", {
                "offset":          offset,
                "timeout":         5,       # short-poll so we can check the lock often
                "allowed_updates": ["message"],
            })

            if not resp.get("ok"):
                log(f"getUpdates not ok: {resp}")
                time.sleep(retry_delay)
                continue

            retry_delay = 5   # reset on success

            for update in resp.get("result", []):
                uid    = update.get("update_id", 0)
                offset = max(offset, uid + 1)

                msg  = update.get("message", {})
                text = msg.get("text", "").strip()

                # Only process messages from the configured chat
                from_id = str(msg.get("chat", {}).get("id", ""))
                if from_id != str(CHAT_ID):
                    log(f"ignoring message from unknown chat {from_id}")
                    continue

                if text:
                    log(f"received: {text!r}")
                    handle_command(text)

        except KeyboardInterrupt:
            log("listener stopped (KeyboardInterrupt)")
            sys.exit(0)
        except Exception as e:
            log(f"unexpected error: {e}")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)   # exponential back-off, cap at 60s

if __name__ == "__main__":
    main()
