#!/usr/bin/env bash
# Claude Code → Telegram Hook — Installer
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/ghazar-tkhachatryan/claude-telegram-hook/main"
CLAUDE_DIR="$HOME/.claude"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Use local files if running from a cloned repo, otherwise download
if [[ -d "$SCRIPT_DIR/scripts" ]]; then
    SRC="$SCRIPT_DIR/scripts"
    TMPL="$SCRIPT_DIR/templates"
    USE_LOCAL=true
else
    USE_LOCAL=false
fi

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}ℹ  $*${NC}"; }
success() { echo -e "${GREEN}✅ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠  $*${NC}"; }
die()     { echo -e "${RED}❌ $*${NC}" >&2; exit 1; }

echo -e "${BOLD}"
cat << 'BANNER'
  ╔════════════════════════════════════════════╗
  ║   Claude Code → Telegram Hook              ║
  ║   Interactive permission prompts via       ║
  ║   native dialog + Telegram simultaneously  ║
  ╚════════════════════════════════════════════╝
BANNER
echo -e "${NC}"

# ── 1. Dependencies ───────────────────────────────────────────────────────────
info "Checking dependencies..."
command -v python3 >/dev/null 2>&1 || die "python3 is required but not found. Install Python 3.8+."
command -v curl    >/dev/null 2>&1 || die "curl is required but not found."
PYTHON="$(command -v python3)"
success "python3 found at $PYTHON"

# ── 2. Telegram credentials ───────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Telegram Bot Credentials${NC}"
echo "  Don't have a bot yet? Open @BotFather on Telegram, send /newbot, and follow the steps."
echo ""

CONF="$CLAUDE_DIR/telegram.conf"
if [[ -f "$CONF" ]]; then
    source "$CONF" 2>/dev/null || true
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    read -r -p "  Bot token (from @BotFather):  " TELEGRAM_BOT_TOKEN
fi
if [[ -z "${TELEGRAM_CHAT_ID:-}" ]]; then
    echo "  Chat ID — send any message to your bot, then run:"
    echo "    curl -s \"https://api.telegram.org/bot\${TELEGRAM_BOT_TOKEN}/getUpdates\" | python3 -c \"import sys,json; u=json.load(sys.stdin)['result']; print(u[-1]['message']['chat']['id'] if u else 'No messages yet — send a message to the bot first')\""
    read -r -p "  Your chat ID:  " TELEGRAM_CHAT_ID
fi

# ── 3. Test credentials ───────────────────────────────────────────────────────
info "Testing bot credentials..."
BOT_NAME="$(curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result']['username'] if d.get('ok') else '')" 2>/dev/null || echo "")"
if [[ -z "$BOT_NAME" ]]; then
    die "Could not reach bot. Check your TELEGRAM_BOT_TOKEN."
fi
success "Connected to @${BOT_NAME}"

# ── 4. Install scripts ────────────────────────────────────────────────────────
info "Installing scripts to $CLAUDE_DIR ..."
mkdir -p "$CLAUDE_DIR"

SCRIPTS=(telegram-permission.py telegram-bot-listener.py session-tracker.py)
SHELL_SCRIPTS=(telegram-notify.sh)

copy_or_download() {
    local name="$1"
    local dest="$CLAUDE_DIR/$name"
    if [[ "$USE_LOCAL" == "true" ]]; then
        if [[ "$name" == *.sh ]]; then
            cp "$TMPL/../scripts/$name" "$dest" 2>/dev/null || cp "$SRC/$name" "$dest"
        else
            cp "$SRC/$name" "$dest"
        fi
    else
        curl -fsSL "$REPO_RAW/scripts/$name" -o "$dest"
    fi
}

for s in "${SCRIPTS[@]}";       do copy_or_download "$s"; done
for s in "${SHELL_SCRIPTS[@]}"; do copy_or_download "$s"; chmod +x "$CLAUDE_DIR/$s"; done

success "Scripts installed"

# ── 5. Write credentials ──────────────────────────────────────────────────────
cat > "$CONF" << CONFEOF
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID}"
CONFEOF
chmod 600 "$CONF"
success "Credentials written to $CONF"

# ── 6. Merge settings.json hooks ──────────────────────────────────────────────
info "Updating ~/.claude/settings.json ..."
python3 - "$CLAUDE_DIR" << 'PYEOF'
import json, os, sys, tempfile

claude_dir = sys.argv[1]
path = os.path.join(claude_dir, "settings.json")

try:
    with open(path) as f:
        cfg = json.load(f)
except Exception:
    cfg = {}

hooks = cfg.setdefault("hooks", {})

def has_hook(lst, fragment):
    for entry in lst:
        for h in entry.get("hooks", []):
            if fragment in h.get("command", ""):
                return True
    return False

pre  = hooks.setdefault("PreToolUse",   [])
stop = hooks.setdefault("Stop",         [])
notif = hooks.setdefault("Notification", [])

if not has_hook(pre, "session-tracker.py"):
    pre.append({"matcher": "", "hooks": [{"type": "command", "command": f"python3 {claude_dir}/session-tracker.py"}]})
if not has_hook(pre, "telegram-permission.py"):
    pre.append({"matcher": "", "hooks": [{"type": "command", "command": f"python3 {claude_dir}/telegram-permission.py"}]})
if not has_hook(stop, "telegram-notify.sh"):
    stop.append({"matcher": "", "hooks": [{"type": "command", "command": f"HOOK_EVENT_NAME=Stop bash {claude_dir}/telegram-notify.sh"}]})
if not has_hook(stop, "session-tracker.py --stop"):
    stop.append({"matcher": "", "hooks": [{"type": "command", "command": f"python3 {claude_dir}/session-tracker.py --stop"}]})
if not has_hook(notif, "Notification bash"):
    notif.append({"matcher": "", "hooks": [{"type": "command", "command": f"HOOK_EVENT_NAME=Notification bash {claude_dir}/telegram-notify.sh"}]})

cfg["skipAutoPermissionPrompt"] = True

os.makedirs(claude_dir, exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=claude_dir, prefix=".settings-")
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2)
os.replace(tmp, path)
print("  settings.json updated")
PYEOF
success "settings.json configured"

# ── 7. Platform daemon ────────────────────────────────────────────────────────
OS="$(uname -s)"

if [[ "$OS" == "Darwin" ]]; then
    info "Setting up macOS LaunchAgent..."
    PLIST_DIR="$HOME/Library/LaunchAgents"
    PLIST="$PLIST_DIR/com.claude.telegram-bot.plist"
    mkdir -p "$PLIST_DIR"

    # Stop existing if loaded
    launchctl unload "$PLIST" 2>/dev/null || true

    cat > "$PLIST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>             <string>com.claude.telegram-bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${CLAUDE_DIR}/telegram-bot-listener.py</string>
    </array>
    <key>RunAtLoad</key>         <true/>
    <key>KeepAlive</key>         <true/>
    <key>ThrottleInterval</key>  <integer>10</integer>
    <key>StandardOutPath</key>   <string>${CLAUDE_DIR}/telegram-bot-listener.log</string>
    <key>StandardErrorPath</key> <string>${CLAUDE_DIR}/telegram-bot-listener.log</string>
</dict>
</plist>
PLISTEOF

    launchctl load "$PLIST"
    success "LaunchAgent installed and started"

elif [[ "$OS" == "Linux" ]]; then
    info "Setting up systemd user service..."
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SYSTEMD_DIR"

    cat > "$SYSTEMD_DIR/claude-telegram-bot.service" << SVCEOF
[Unit]
Description=Claude Code Telegram Bot Listener
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
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
    success "systemd service installed and started"
else
    warn "Unknown OS ($OS) — skipping daemon setup. Run manually: python3 $CLAUDE_DIR/telegram-bot-listener.py &"
fi

# ── 8. Send test message ──────────────────────────────────────────────────────
info "Sending test message..."
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" \
    -d parse_mode="HTML" \
    -d text="🟢 <b>Claude Code Telegram Hook installed!</b>

✅ Permission prompts will now appear here.
Send /help to see available commands." > /dev/null
success "Test message sent to Telegram"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}Installation complete!${NC}"
echo ""
echo "  📱 Check Telegram — you should have received a test message."
echo "  🔐 Permission prompts will appear as a macOS dialog + Telegram simultaneously."
echo "  🤖 Bot commands: /mute  /unmute  /status  /sessions  /help"
echo ""
echo "  To uninstall: ./uninstall.sh"
echo ""
