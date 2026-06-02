#!/usr/bin/env python3
"""
Claude Code — Session Tracker (PreToolUse + Stop hook, all tools)
Writes a lightweight heartbeat to ~/.claude/sessions.json on every tool use,
and pushes a throttled heartbeat to the relay so the /sessions bot command can
list active sessions. Local write is pure file I/O; the relay push is
best-effort, throttled, and never blocks Claude.
"""
import sys, json, os, time, tempfile, urllib.request

CLAUDE_DIR    = os.path.dirname(os.path.abspath(__file__))
SESSIONS_FILE = os.path.join(CLAUDE_DIR, "sessions.json")
CONF          = os.path.join(CLAUDE_DIR, "telegram.conf")
MAX_AGE_HOURS = 24    # prune sessions older than this
PUSH_THROTTLE = 20    # seconds between relay pushes per session
IS_STOP       = "--stop" in sys.argv

# ── Credentials (relay mode only) ─────────────────────────────────────────────
RELAY_URL = CHAT_ID = RELAY_TOKEN = None
try:
    with open(CONF) as f:
        for line in f:
            line = line.strip()
            if line.startswith("RELAY_URL="):
                RELAY_URL = line.split("=", 1)[1].strip("\"'")
            elif line.startswith("TELEGRAM_CHAT_ID="):
                CHAT_ID = line.split("=", 1)[1].strip("\"'")
            elif line.startswith("RELAY_TOKEN="):
                RELAY_TOKEN = line.split("=", 1)[1].strip("\"'")
except Exception:
    pass
USE_RELAY = bool(RELAY_URL and CHAT_ID and RELAY_TOKEN)

def push_heartbeat(entry, session_id, stopped):
    """Best-effort relay push — short timeout, never raises."""
    if not USE_RELAY:
        return
    try:
        payload = json.dumps({
            "chat_id":    CHAT_ID,
            "token":      RELAY_TOKEN,
            "session_id": session_id,
            "project":    entry.get("project", ""),
            "cwd":        entry.get("cwd", ""),
            "last_tool":  entry.get("last_tool", ""),
            "last_cmd":   entry.get("last_cmd", ""),
            "started_at": entry.get("started_at", 0),
            "stopped":    stopped,
        }).encode()
        req = urllib.request.Request(
            f"{RELAY_URL}/v1/heartbeat", data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass

try:
    raw  = sys.stdin.buffer.read()
    data = json.loads(raw)

    session_id = (
        data.get("session_id")
        or os.environ.get("CLAUDE_SESSION_ID")
        or f"pid-{os.getpid()}"
    )
    tool_name  = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    cwd        = os.environ.get("PWD", "")
    project    = os.path.basename(cwd) if cwd else "unknown"

    # Brief human-readable description of what's happening
    if tool_name == "Bash":
        last_cmd = (tool_input.get("command") or "")[:100]
    elif tool_name in ("Write", "Edit", "Read", "MultiEdit"):
        last_cmd = os.path.basename(tool_input.get("file_path") or "")
    elif tool_name and tool_name.startswith("mcp__"):
        # e.g. mcp__picsart-api-docs__get-swagger-spec → get-swagger-spec
        last_cmd = tool_name.split("__")[-1]
    else:
        last_cmd = ""

    now = time.time()

    # ── Atomic read-modify-write ──────────────────────────────────────────────
    try:
        with open(SESSIONS_FILE) as f:
            sessions = json.load(f)
        if not isinstance(sessions, dict):
            sessions = {}
    except Exception:
        sessions = {}

    # Update this session
    prev = sessions.get(session_id, {})
    entry = {
        "id":          session_id,
        "cwd":         cwd or prev.get("cwd", ""),
        "project":     project or prev.get("project", "unknown"),
        "last_tool":   tool_name or prev.get("last_tool", ""),
        "last_cmd":    last_cmd or prev.get("last_cmd", ""),
        "last_active": now,
        "started_at":  prev.get("started_at", now),
        "stopped_at":  now if IS_STOP else None,
        "last_push":   prev.get("last_push", 0),
    }

    # Decide whether to push to the relay this time (throttled, or always on stop)
    should_push = IS_STOP or (now - entry["last_push"] >= PUSH_THROTTLE)
    if should_push:
        entry["last_push"] = now

    sessions[session_id] = entry

    # Prune old/stale sessions (> MAX_AGE_HOURS since last activity)
    cutoff = now - MAX_AGE_HOURS * 3600
    sessions = {sid: s for sid, s in sessions.items()
                if s.get("last_active", 0) > cutoff}

    # Atomic write via temp-file + rename
    dir_ = os.path.dirname(SESSIONS_FILE)
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".sessions-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(sessions, f, indent=2)
        os.replace(tmp, SESSIONS_FILE)
    except Exception:
        try: os.unlink(tmp)
        except Exception: pass

    # Relay push (after the local write so state is consistent)
    if should_push:
        push_heartbeat(entry, session_id, IS_STOP)

except Exception:
    pass   # never crash, never block Claude

# Exit 0, no stdout → Claude Code passes the tool use through to next hooks
sys.exit(0)
