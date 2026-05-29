#!/usr/bin/env bash
# Claude Code Monitor — Installer
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/GhazarArm/claude_code_monitor/main"
CLAUDE_DIR="$HOME/.claude"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USE_LOCAL=false
[[ -d "$SCRIPT_DIR/scripts" ]] && USE_LOCAL=true

# ── Colors ────────────────────────────────────────────────────────────────────
GRN='\033[0;32m'; YLW='\033[1;33m'; BLU='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BLU}ℹ  $*${NC}"; }
success() { echo -e "${GRN}✅ $*${NC}"; }
warn()    { echo -e "${YLW}⚠  $*${NC}"; }

echo -e "${BOLD}"
cat << 'BANNER'
  ╔════════════════════════════════════════════╗
  ║   Claude Code Monitor                      ║
  ║   Permission prompts via Telegram          ║
  ╚════════════════════════════════════════════╝
BANNER
echo -e "${NC}"

# ── Parse args ────────────────────────────────────────────────────────────────
RELAY_URL=""
CHAT_ID=""
RELAY_TOKEN_ARG=""
BOT_TOKEN=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --relay)     RELAY_URL="$2";        shift 2 ;;
        --chat-id)   CHAT_ID="$2";          shift 2 ;;
        --token)     RELAY_TOKEN_ARG="$2";  shift 2 ;;
        --bot-token) BOT_TOKEN="$2";        shift 2 ;;
        *) shift ;;
    esac
done

# ── Dependencies ──────────────────────────────────────────────────────────────
info "Checking dependencies..."
command -v python3 >/dev/null 2>&1 || { echo "python3 required"; exit 1; }
command -v curl    >/dev/null 2>&1 || { echo "curl required";    exit 1; }
PYTHON="$(command -v python3)"
success "python3 at $PYTHON"

# ── Mode selection ────────────────────────────────────────────────────────────
echo ""
if [[ -n "$RELAY_URL" && -n "$CHAT_ID" && -n "$RELAY_TOKEN_ARG" ]]; then
    MODE="relay"
    info "Using relay mode (args provided)"
else
    echo -e "${BOLD}Choose mode:${NC}"
    echo "  1. Public bot  (use shared relay — just send /start to the bot)"
    echo "  2. Private bot (create your own Telegram bot)"
    echo ""
    read -r -p "  Choice [1/2]: " MODE_CHOICE
    [[ "$MODE_CHOICE" == "2" ]] && MODE="direct" || MODE="relay"
fi

# ── Relay mode setup ──────────────────────────────────────────────────────────
if [[ "$MODE" == "relay" ]]; then
    if [[ -z "$RELAY_URL" ]]; then
        DEFAULT_RELAY="https://claude-monitor-production.up.railway.app"
        echo ""
        echo "  1. Open Telegram → search @ClaudeCodeMonitorBot → send /start"
        echo "  2. Copy the Chat ID, Token, and Relay URL from the reply"
        echo ""
        read -r -p "  Relay URL [${DEFAULT_RELAY}]: " RELAY_URL
        [[ -z "$RELAY_URL" ]] && RELAY_URL="$DEFAULT_RELAY"
    fi
    if [[ -z "$CHAT_ID" ]]; then
        read -r -p "  Your Chat ID (from /start):  " CHAT_ID
    fi
    if [[ -z "$RELAY_TOKEN_ARG" ]]; then
        read -r -p "  Your Token (from /start):    " RELAY_TOKEN_ARG
    fi

    # Test relay
    info "Testing relay connection..."
    STATUS="$(curl -sf "${RELAY_URL}/health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',''))" 2>/dev/null || echo "")"
    [[ "$STATUS" == "True" ]] && success "Relay is online" || warn "Relay not reachable — check the URL"

    CONF_CONTENT="RELAY_URL=\"${RELAY_URL}\"
TELEGRAM_CHAT_ID=\"${CHAT_ID}\"
RELAY_TOKEN=\"${RELAY_TOKEN_ARG}\""

# ── Direct mode setup ─────────────────────────────────────────────────────────
else
    CONF="$CLAUDE_DIR/telegram.conf"
    [[ -f "$CONF" ]] && source "$CONF" 2>/dev/null || true
    [[ -z "$BOT_TOKEN" ]] && BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"

    if [[ -z "$BOT_TOKEN" ]]; then
        echo ""
        echo "  Create a bot: open @BotFather on Telegram → /newbot"
        echo ""
        read -r -p "  Bot token (from @BotFather): " BOT_TOKEN
    fi
    if [[ -z "$CHAT_ID" ]]; then
        CHAT_ID="${TELEGRAM_CHAT_ID:-}"
    fi
    if [[ -z "$CHAT_ID" ]]; then
        echo ""
        echo "  Send any message to your bot, then run:"
        echo "    curl -s \"https://api.telegram.org/bot${BOT_TOKEN}/getUpdates\" | python3 -c \"import sys,json; u=json.load(sys.stdin)['result']; print(u[-1]['message']['chat']['id'] if u else 'send a message to the bot first')\""
        echo ""
        read -r -p "  Your Chat ID: " CHAT_ID
    fi

    info "Testing bot..."
    BOT_NAME="$(curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getMe" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result']['username'] if d.get('ok') else '')" 2>/dev/null || echo "")"
    [[ -n "$BOT_NAME" ]] && success "Connected to @${BOT_NAME}" || { echo "Bot token invalid"; exit 1; }

    CONF_CONTENT="TELEGRAM_BOT_TOKEN=\"${BOT_TOKEN}\"
TELEGRAM_CHAT_ID=\"${CHAT_ID}\""
fi

# ── Install scripts ───────────────────────────────────────────────────────────
info "Installing scripts to $CLAUDE_DIR ..."
mkdir -p "$CLAUDE_DIR"

copy_file() {
    local name="$1" dest="$CLAUDE_DIR/$name"
    if [[ "$USE_LOCAL" == "true" ]]; then
        cp "$SCRIPT_DIR/scripts/$name" "$dest"
    else
        curl -fsSL "$REPO_RAW/scripts/$name" -o "$dest"
    fi
}

for f in telegram-permission.py telegram-bot-listener.py session-tracker.py; do
    copy_file "$f"
done
copy_file "telegram-notify.sh"
chmod +x "$CLAUDE_DIR/telegram-notify.sh"
success "Scripts installed"

# ── Write credentials ─────────────────────────────────────────────────────────
printf '%s\n' "$CONF_CONTENT" > "$CLAUDE_DIR/telegram.conf"
chmod 600 "$CLAUDE_DIR/telegram.conf"
success "Credentials saved to $CLAUDE_DIR/telegram.conf"

# ── Merge settings.json ───────────────────────────────────────────────────────
info "Updating ~/.claude/settings.json ..."
python3 - "$CLAUDE_DIR" << 'PYEOF'
import json, os, sys, tempfile
d = sys.argv[1]
p = os.path.join(d, "settings.json")
try:
    with open(p) as f: cfg = json.load(f)
except: cfg = {}
hooks = cfg.setdefault("hooks", {})
def has(lst, frag):
    return any(frag in h.get("command","") for e in lst for h in e.get("hooks",[]))
pre  = hooks.setdefault("PreToolUse",   [])
stop = hooks.setdefault("Stop",         [])
notif = hooks.setdefault("Notification",[])
if not has(pre,  "session-tracker.py"):     pre.append({"matcher":"","hooks":[{"type":"command","command":f"python3 {d}/session-tracker.py"}]})
if not has(pre,  "telegram-permission.py"): pre.append({"matcher":"","hooks":[{"type":"command","command":f"python3 {d}/telegram-permission.py"}]})
if not has(stop, "telegram-notify.sh"):     stop.append({"matcher":"","hooks":[{"type":"command","command":f"HOOK_EVENT_NAME=Stop bash {d}/telegram-notify.sh"}]})
if not has(stop, "session-tracker.py --stop"): stop.append({"matcher":"","hooks":[{"type":"command","command":f"python3 {d}/session-tracker.py --stop"}]})
if not has(notif,"Notification bash"):      notif.append({"matcher":"","hooks":[{"type":"command","command":f"HOOK_EVENT_NAME=Notification bash {d}/telegram-notify.sh"}]})
cfg["skipAutoPermissionPrompt"] = True
fd,tmp = tempfile.mkstemp(dir=d, prefix=".settings-")
with os.fdopen(fd,"w") as f: json.dump(cfg,f,indent=2)
os.replace(tmp, p)
PYEOF
success "settings.json configured"

# ── Daemon (direct mode only) ─────────────────────────────────────────────────
if [[ "$MODE" == "direct" ]]; then
    OS="$(uname -s)"
    if [[ "$OS" == "Darwin" ]]; then
        info "Setting up macOS LaunchAgent..."
        PLIST="$HOME/Library/LaunchAgents/com.claude.telegram-bot.plist"
        launchctl unload "$PLIST" 2>/dev/null || true
        mkdir -p "$(dirname "$PLIST")"
        cat > "$PLIST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key>             <string>com.claude.telegram-bot</string>
    <key>ProgramArguments</key>  <array><string>${PYTHON}</string><string>${CLAUDE_DIR}/telegram-bot-listener.py</string></array>
    <key>RunAtLoad</key>         <true/>
    <key>KeepAlive</key>         <true/>
    <key>ThrottleInterval</key>  <integer>10</integer>
    <key>StandardOutPath</key>   <string>${CLAUDE_DIR}/telegram-bot-listener.log</string>
    <key>StandardErrorPath</key> <string>${CLAUDE_DIR}/telegram-bot-listener.log</string>
</dict></plist>
PLISTEOF
        launchctl load "$PLIST"
        success "LaunchAgent started"
    elif [[ "$OS" == "Linux" ]]; then
        info "Setting up systemd service..."
        SVCDIR="$HOME/.config/systemd/user"
        mkdir -p "$SVCDIR"
        cat > "$SVCDIR/claude-telegram-bot.service" << SVCEOF
[Unit]
Description=Claude Code Telegram Bot Listener
After=network-online.target
[Service]
ExecStart=${PYTHON} ${CLAUDE_DIR}/telegram-bot-listener.py
Restart=always
RestartSec=10
StandardOutput=append:${CLAUDE_DIR}/telegram-bot-listener.log
StandardError=append:${CLAUDE_DIR}/telegram-bot-listener.log
[Install]
WantedBy=default.target
SVCEOF
        systemctl --user daemon-reload
        systemctl --user enable --now claude-telegram-bot.service
        success "systemd service started"
    fi
else
    info "Relay mode — no local daemon needed ✓"
fi

# ── Test message ──────────────────────────────────────────────────────────────
info "Sending test message..."
if [[ "$MODE" == "relay" ]]; then
    curl -sf -X POST "${RELAY_URL}/v1/edit" \
        -H "Content-Type: application/json" \
        -d "{\"chat_id\":\"${CHAT_ID}\",\"token\":\"${RELAY_TOKEN_ARG}\",\"msg_id\":0,\"text\":\"test\"}" \
        > /dev/null 2>&1 || true
    # Just send directly via the bot (relay handles it)
    BOT_TOKEN_FOR_TEST="$(python3 -c "
import urllib.request, json
url = '${RELAY_URL}/health'
# fallback: use relay health as test
print('ok')
" 2>/dev/null || echo "")"
    warn "Open Telegram — you should see a message from /start if you ran it."
else
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d chat_id="${CHAT_ID}" -d parse_mode="HTML" \
        -d "text=🟢 <b>Claude Code Monitor installed!</b>

✅ Permission prompts will now appear here.
🤖 Send /help for available commands." > /dev/null
    success "Test message sent"
fi

echo ""
echo -e "${GRN}${BOLD}Done!${NC}"
echo ""
echo "  🔐 Dangerous Bash commands → dialog + Telegram"
echo "  📁 Cross-project file access → dialog + Telegram"
echo "  🤖 Bot commands: /mute  /unmute  /status  /help"
echo ""
[[ "$MODE" == "direct" ]] && echo "  📖 Logs: $CLAUDE_DIR/telegram-permission.log"
