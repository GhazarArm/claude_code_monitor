#!/usr/bin/env bash
# Claude Code → Telegram notifier
# Reads credentials from ~/.claude/telegram.conf

set -euo pipefail

CONF="$HOME/.claude/telegram.conf"
if [[ ! -f "$CONF" ]]; then
  echo "[telegram-notify] Config not found: $CONF" >&2
  exit 0
fi
source "$CONF"

# ── helpers ────────────────────────────────────────────────────────────────────

send() {
  local text="$1"
  curl -s -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" \
    -d parse_mode="HTML" \
    -d text="$text" \
    > /dev/null
}

truncate_text() {
  local text="$1"
  local max="${2:-300}"
  if [[ ${#text} -gt $max ]]; then
    echo "${text:0:$max}…"
  else
    echo "$text"
  fi
}

# ── read hook input (JSON on stdin) ───────────────────────────────────────────

INPUT="$(cat)"
HOOK_EVENT="${HOOK_EVENT_NAME:-unknown}"
SESSION_ID="${CLAUDE_SESSION_ID:-}"
CWD="${PWD:-}"
PROJECT="${CWD##*/}"   # last path component as project name
TIME="$(date '+%H:%M')"

# ── build message based on hook type ──────────────────────────────────────────

case "$HOOK_EVENT" in

  Stop)
    # stop_hook_active: 1 = stopped before idle, 0 = natural stop
    STOP_REASON="$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('interrupted' if d.get('stop_hook_active') else 'finished')" 2>/dev/null || echo 'finished')"
    MSG="$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('message', ''))
" 2>/dev/null || echo '')"

    if [[ -n "$MSG" ]]; then
      SUMMARY="$(truncate_text "$MSG" 280)"
    else
      SUMMARY="Task complete."
    fi

    ICON="✅"
    [[ "$STOP_REASON" == "interrupted" ]] && ICON="⏹️"

    send "${ICON} <b>${PROJECT}</b>  <code>${TIME}</code>
${SUMMARY}"
    ;;

  Notification)
    NOTIF="$(echo "$INPUT" | python3 -c "
import sys, json, re
d = json.load(sys.stdin)
msg = d.get('message', d.get('title', ''))

# Skip generic idle pings — only let real questions through
skip_patterns = [
    r'(?i)waiting for your input',
    r'(?i)^claude is waiting',
    r'(?i)^waiting\b',
]
for pat in skip_patterns:
    if re.search(pat, msg.strip()):
        sys.exit(1)   # signal: skip this notification

print(msg.strip() or 'Claude needs your attention')
" 2>/dev/null)"

    # python3 exited 1 → generic idle ping, skip silently
    [[ $? -ne 0 || -z "$NOTIF" ]] && exit 0

    SUMMARY="$(truncate_text "$NOTIF" 280)"

    send "🔔 <b>${PROJECT}</b>  <code>${TIME}</code>
${SUMMARY}"
    ;;

  PostToolUse)
    TOOL="$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null || echo '')"
    # Only notify for tools you care about — edit this list freely
    case "$TOOL" in
      Bash|Write|Edit)
        OUTPUT="$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
out = d.get('tool_response', {})
if isinstance(out, dict):
    text = out.get('output', out.get('content', ''))
elif isinstance(out, str):
    text = out
else:
    text = ''
print(str(text)[:200])
" 2>/dev/null || echo '')"
        if [[ -n "$OUTPUT" ]]; then
          send "🛠 <b>${PROJECT}</b> › <code>${TOOL}</code>  <code>${TIME}</code>
<pre>$(truncate_text "$OUTPUT" 250)</pre>"
        fi
        ;;
    esac
    ;;

esac

exit 0
