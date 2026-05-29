#!/usr/bin/env bash
set -euo pipefail

CLAUDE_DIR="$HOME/.claude"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo -e "${YELLOW}Uninstalling Claude Code Telegram Hook...${NC}"

OS="$(uname -s)"

# Stop and remove daemon
if [[ "$OS" == "Darwin" ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.claude.telegram-bot.plist"
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo -e "${GREEN}✅ LaunchAgent removed${NC}"
elif [[ "$OS" == "Linux" ]]; then
    systemctl --user disable --now claude-telegram-bot.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/claude-telegram-bot.service"
    systemctl --user daemon-reload
    echo -e "${GREEN}✅ systemd service removed${NC}"
fi

# Remove scripts
for f in telegram-permission.py telegram-bot-listener.py telegram-notify.sh session-tracker.py \
          telegram.conf telegram-mute.json telegram-poll.lock sessions.json \
          telegram-permission.log telegram-bot-listener.log; do
    rm -f "$CLAUDE_DIR/$f"
done
echo -e "${GREEN}✅ Scripts removed${NC}"

# Remove hooks from settings.json
python3 - "$CLAUDE_DIR" << 'PYEOF'
import json, os, sys, tempfile

claude_dir = sys.argv[1]
path = os.path.join(claude_dir, "settings.json")
try:
    with open(path) as f:
        cfg = json.load(f)
except Exception:
    print("  settings.json not found, skipping"); sys.exit(0)

hooks = cfg.get("hooks", {})
fragments = ["session-tracker", "telegram-permission", "telegram-notify", "telegram"]

def clean(lst):
    result = []
    for entry in lst:
        new_hooks = [h for h in entry.get("hooks",[])
                     if not any(frag in h.get("command","") for frag in fragments)]
        if new_hooks:
            result.append({**entry, "hooks": new_hooks})
    return result

for key in list(hooks.keys()):
    hooks[key] = clean(hooks[key])
    if not hooks[key]:
        del hooks[key]

cfg.pop("skipAutoPermissionPrompt", None)

fd, tmp = tempfile.mkstemp(dir=claude_dir, prefix=".settings-")
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2)
os.replace(tmp, path)
print("  settings.json cleaned")
PYEOF

echo -e "${GREEN}✅ settings.json cleaned${NC}"
echo -e "${GREEN}Uninstall complete.${NC}"
