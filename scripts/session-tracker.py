#!/usr/bin/env python3
"""
Claude Code — Session Tracker (PreToolUse hook, all tools)
Writes a lightweight heartbeat to ~/.claude/sessions.json on every tool use.
Fast: no network calls, pure file I/O.
"""
import sys, json, os, time, tempfile

SESSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.json")
MAX_AGE_HOURS = 24   # prune sessions older than this
IS_STOP       = "--stop" in sys.argv

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
    sessions[session_id] = {
        "id":          session_id,
        "cwd":         cwd or prev.get("cwd", ""),
        "project":     project or prev.get("project", "unknown"),
        "last_tool":   tool_name or prev.get("last_tool", ""),
        "last_cmd":    last_cmd or prev.get("last_cmd", ""),
        "last_active": now,
        "started_at":  prev.get("started_at", now),
        "stopped_at":  now if IS_STOP else None,
    }

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

except Exception:
    pass   # never crash, never block Claude

# Exit 0, no stdout → Claude Code passes the tool use through to next hooks
sys.exit(0)
