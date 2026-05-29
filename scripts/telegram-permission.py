#!/usr/bin/env python3
"""
Claude Code → macOS Dialog + Telegram Permission Handler  (PreToolUse, all tools)

Supports two modes (auto-detected from config):
  relay  → uses hosted relay server (recommended — no local bot polling)
  direct → polls Telegram Bot API directly (legacy)

Bash (dangerous patterns)        → ⚠️  Allow / Deny / Mute 30m
Read / Write / Edit outside CWD  → 🔐  Allow / Allow always / Deny
Everything else                  → auto-approve instantly
"""
import sys, json, time, os, re, html, shlex, tempfile, urllib.request, threading, subprocess

# ── Paths ─────────────────────────────────────────────────────────────────────
LOG            = os.path.expanduser("~/.claude/telegram-permission.log")
CONF           = os.path.expanduser("~/.claude/telegram.conf")
MUTE_FILE      = os.path.expanduser("~/.claude/telegram-mute.json")
SETTINGS_LOCAL = os.path.expanduser("~/.claude/settings.local.json")
POLL_LOCK      = os.path.expanduser("~/.claude/telegram-poll.lock")

def log(msg):
    with open(LOG, "a") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

# ── Credentials ───────────────────────────────────────────────────────────────
TOKEN       = None   # direct mode
CHAT_ID     = None
RELAY_URL   = None   # relay mode
RELAY_TOKEN = None
try:
    with open(CONF) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                TOKEN       = line.split("=",1)[1].strip("\"'")
            elif line.startswith("TELEGRAM_CHAT_ID="):
                CHAT_ID     = line.split("=",1)[1].strip("\"'")
            elif line.startswith("RELAY_URL="):
                RELAY_URL   = line.split("=",1)[1].strip("\"'")
            elif line.startswith("RELAY_TOKEN="):
                RELAY_TOKEN = line.split("=",1)[1].strip("\"'")
except Exception as e:
    log(f"config error: {e}")

USE_RELAY = bool(RELAY_URL and RELAY_TOKEN and CHAT_ID)
log(f"mode={'relay' if USE_RELAY else 'direct'} chat_id={CHAT_ID!r}")

# ── Mute ──────────────────────────────────────────────────────────────────────
def is_muted():
    # Relay mode: check relay server for mute state
    if USE_RELAY:
        try:
            url = f"{RELAY_URL}/v1/muted/{CHAT_ID}?token={RELAY_TOKEN}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read()).get("muted", False)
        except Exception as e:
            log(f"is_muted relay error: {e}")
    # Direct mode (or relay failed): read local file
    try:
        with open(MUTE_FILE) as f:
            s = json.load(f)
        if not s.get("muted"): return False
        until = s.get("muted_until", 0)
        if until == 0 or time.time() < until: return True
        with open(MUTE_FILE,"w") as f: json.dump({"muted":False,"muted_until":0,"muted_at":0},f)
        return False
    except: return False

# ── Decisions ─────────────────────────────────────────────────────────────────
def approve():
    log("→ APPROVE")
    print(json.dumps({"decision": "approve"}))
    sys.exit(0)

def block(reason="Denied"):
    log(f"→ BLOCK ({reason})")
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)

# ── Danger detection for Bash ─────────────────────────────────────────────────
def bash_danger(cmd):
    if '\n' in cmd or '<<' in cmd: return False, ""
    first = re.split(r'(?<!\|)\|(?!\|)|&&|\|\||;', cmd.strip())[0].strip()
    try:    tokens = shlex.split(first)
    except: tokens = first.split()
    if not tokens: return False, ""
    prog = os.path.basename(tokens[0])
    args = tokens[1:]
    if prog in ("rm","unlink"):
        has_r = ("--recursive" in args or any(
            re.search(r"[rR]", a.lstrip("-"))
            for a in args if a.startswith("-") and not a.startswith("--")
        ))
        if has_r: return True, "Recursive delete (rm -r/-rf)"
    elif prog == "git":
        sub, rest = (args[0] if args else ""), args[1:]
        if sub == "push" and any(a in ("-f","--force","--force-with-lease") for a in rest):
            return True, "Force push"
        if sub == "reset" and "--hard" in rest:
            return True, "Hard reset"
        if sub == "clean" and any(re.search(r"[fdx]",a.lstrip("-")) for a in rest
                                  if a.startswith("-") and not a.startswith("--")):
            return True, "Git clean"
    elif prog.startswith("mkfs"): return True, "Format filesystem"
    elif prog == "dd" and any(a.startswith("of=/dev/") for a in args):
        return True, "DD write to device"
    elif prog == "sudo":
        inner = " ".join(args)
        if inner: return bash_danger(inner)
    return False, ""

# ── "Allow always" → settings.local.json ─────────────────────────────────────
def add_to_allowlist(entry: str):
    try:
        with open(SETTINGS_LOCAL) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    perms = cfg.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    if entry not in allow:
        allow.append(entry)
    dir_ = os.path.dirname(SETTINGS_LOCAL)
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".settings-local-")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, SETTINGS_LOCAL)
    log(f"added to allowlist: {entry}")

# ── Poll lock (prevents 409 conflicts with bot listener daemon) ───────────────
def acquire_poll_lock():
    try:
        with open(POLL_LOCK, 'w') as f: f.write(str(os.getpid()))
    except Exception: pass

def release_poll_lock():
    try: os.unlink(POLL_LOCK)
    except Exception: pass

# ══════════════════════════════════════════════════════════════════════════════
#  RELAY MODE helpers
# ══════════════════════════════════════════════════════════════════════════════
def relay_post(path, payload):
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{RELAY_URL}{path}", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            log(f"  relay POST {path} → {str(result)[:100]}")
            return result
    except Exception as e:
        log(f"  relay POST {path} ERROR: {e}")
        return {}

def relay_get(path, params=None):
    url = f"{RELAY_URL}{path}"
    if params:
        qs  = "&".join(f"{k}={urllib.request.quote(str(v))}" for k,v in params.items())
        url = f"{url}?{qs}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=610) as r:   # server waits up to 600s
            result = json.loads(r.read())
            log(f"  relay GET {path} → {str(result)[:100]}")
            return result
    except Exception as e:
        log(f"  relay GET {path} ERROR: {e}")
        return {}

def relay_edit_msg(msg_id, text):
    if not msg_id: return
    relay_post("/v1/edit", {
        "chat_id": CHAT_ID,
        "token":   RELAY_TOKEN,
        "msg_id":  msg_id,
        "text":    text,
    })

def relay_wait_worker(req_id, result_holder, done_event):
    """Block on relay server until user taps a button."""
    try:
        resp     = relay_get(f"/v1/wait/{req_id}", {"chat_id": CHAT_ID, "token": RELAY_TOKEN})
        decision = resp.get("decision")
        if decision and not done_event.is_set():
            log(f"relay_wait: decision={decision!r}")
            result_holder[0] = ("telegram", decision)
            done_event.set()
    except Exception as e:
        log(f"relay_wait_worker error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
#  DIRECT MODE helpers
# ══════════════════════════════════════════════════════════════════════════════
BASE = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""

def tg(method, payload):
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(f"{BASE}/{method}", data=body,
                                   headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            log(f"  tg.{method} ok={result.get('ok')}")
            return result
    except Exception as e:
        log(f"  tg.{method} ERROR: {e}")
        return {}

def latest_offset():
    resp = tg("getUpdates", {"limit":1,"offset":-1})
    updates = resp.get("result",[])
    off = (updates[-1]["update_id"]+1) if updates else 0
    log(f"  latest_offset → {off}")
    return off

def telegram_poll_worker(valid, offset_start, result_holder, done_event):
    offset = offset_start
    try:
        while not done_event.is_set():
            resp = tg("getUpdates", {
                "offset":          offset,
                "timeout":         20,
                "allowed_updates": ["callback_query", "message"],
            })
            for upd in resp.get("result", []):
                uid    = upd.get("update_id", 0)
                offset = max(offset, uid + 1)
                cb    = upd.get("callback_query", {})
                data_ = cb.get("data", "")
                if data_ in valid:
                    decision = valid[data_]
                    tg("answerCallbackQuery", {"callback_query_id": cb.get("id","")})
                    log(f"telegram tap: {decision!r}")
                    if not done_event.is_set():
                        result_holder[0] = ("telegram", decision)
                        done_event.set()
                    return
                msg_text = upd.get("message", {}).get("text", "").strip().lower()
                if msg_text in ("/unmute", "unmute"):
                    try:
                        with open(MUTE_FILE, "w") as f:
                            json.dump({"muted":False,"muted_until":0,"muted_at":0}, f)
                        tg("sendMessage", {"chat_id":CHAT_ID,"text":"🔔 <b>Unmuted</b>",
                                           "parse_mode":"HTML"})
                    except Exception: pass
    except Exception as e:
        log(f"telegram_poll_worker error: {e}")

# ── macOS native dialog worker ────────────────────────────────────────────────
def _esc(s):
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', ' ')

def macos_dialog_worker(dialog_title, detail, options, result_holder, done_event, proc_holder):
    try:
        labels       = [label for label, _ in options]
        label_to_val = {label: val for label, val in options}
        btn_str      = ", ".join(f'"{_esc(l)}"' for l in labels)
        script = (
            f'try\n'
            f'  set r to button returned of '
            f'(display dialog "{_esc(detail[:300])}" with title "{_esc(dialog_title)}" '
            f'buttons {{{btn_str}}} default button "{_esc(labels[-1])}" '
            f'with icon caution)\n'
            f'  return r\n'
            f'on error\n'
            f'  return ""\n'
            f'end try'
        )
        proc = subprocess.Popen(
            ["/usr/bin/osascript", "-e", script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        proc_holder[0] = proc
        stdout, _ = proc.communicate()
        if done_event.is_set(): return
        clicked = stdout.decode().strip()
        log(f"dialog result: {clicked!r}")
        if clicked and clicked in label_to_val and not done_event.is_set():
            result_holder[0] = ("dialog", label_to_val[clicked])
            done_event.set()
        else:
            log("dialog: cancelled — deferring to Telegram")
    except Exception as e:
        log(f"macos_dialog_worker error: {e}")

# ── Combined dual prompt ──────────────────────────────────────────────────────
def ask_both(tg_title, detail, reason, tg_buttons, dialog_title, dialog_options):
    """Show prompt in macOS dialog AND Telegram simultaneously. First wins."""
    project = os.path.basename(os.environ.get("PWD", "") or "")
    ts      = time.strftime("%H:%M")
    req_id  = f"{int(time.time())}_{os.getpid()}"
    log(f"ask_both mode={'relay' if USE_RELAY else 'direct'} req_id={req_id}")

    tg_text = (
        f"{tg_title}  <code>{ts}</code>\n"
        f"📁 <b>Project:</b> <code>{html.escape(project)}</code>\n"
        f"🏷 <b>Type:</b> <code>{html.escape(reason)}</code>\n\n"
        f"<pre>{html.escape(str(detail)[:400])}</pre>\n\n"
        f"📲 <i>Reply here or click the macOS dialog</i>"
    )

    result_holder = [None]
    done_event    = threading.Event()
    proc_holder   = [None]

    if USE_RELAY:
        # ── relay mode ────────────────────────────────────────────────────────
        keyboard = {"inline_keyboard": [
            [{"text": lbl, "callback_data": f"{sfx}|{req_id}"} for lbl, sfx in row]
            for row in tg_buttons
        ]}
        resp   = relay_post("/v1/prompt", {
            "chat_id":  CHAT_ID,
            "token":    RELAY_TOKEN,
            "req_id":   req_id,
            "text":     tg_text,
            "keyboard": keyboard,
        })
        msg_id = resp.get("msg_id")
        log(f"relay prompt sent msg_id={msg_id}")

        t_tg = threading.Thread(
            target=relay_wait_worker,
            args=(req_id, result_holder, done_event),
            daemon=True,
        )
    else:
        # ── direct mode ───────────────────────────────────────────────────────
        keyboard = {"inline_keyboard": [
            [{"text": lbl, "callback_data": f"{sfx}_{req_id}"} for lbl, sfx in row]
            for row in tg_buttons
        ]}
        acquire_poll_lock()
        offset = latest_offset()
        resp   = tg("sendMessage", {"chat_id":CHAT_ID, "text":tg_text,
                                     "parse_mode":"HTML", "reply_markup":keyboard})
        msg_id = resp.get("result", {}).get("message_id")
        log(f"direct msg_id={msg_id}")

        valid = {f"{sfx}_{req_id}": sfx for _, sfx in [btn for row in tg_buttons for btn in row]}
        t_tg  = threading.Thread(
            target=telegram_poll_worker,
            args=(valid, offset, result_holder, done_event),
            daemon=True,
        )

    t_dialog = threading.Thread(
        target=macos_dialog_worker,
        args=(dialog_title, detail, dialog_options, result_holder, done_event, proc_holder),
        daemon=True,
    )
    t_dialog.start()
    t_tg.start()

    done_event.wait()   # forever

    if not USE_RELAY:
        release_poll_lock()

    # Kill dialog if Telegram won
    proc = proc_holder[0]
    if proc and proc.poll() is None:
        try: proc.kill()
        except Exception: pass

    result   = result_holder[0]
    decision = result[1] if result else None
    source   = result[0] if result else "timeout"
    log(f"decision={decision!r} source={source!r}")
    return decision, msg_id, req_id

# ── Edit helper (works in both modes) ────────────────────────────────────────
def edit_result_msg(msg_id, text):
    if not msg_id: return
    if USE_RELAY:
        relay_edit_msg(msg_id, text)
    else:
        tg("editMessageText", {"chat_id":CHAT_ID,"message_id":msg_id,
            "text":text,"parse_mode":"HTML","reply_markup":{"inline_keyboard":[]}})

# ── Read stdin ────────────────────────────────────────────────────────────────
try:
    raw  = sys.stdin.read()
    log(f"stdin bytes: {len(raw)}")
    data = json.loads(raw)
except Exception as e:
    log(f"stdin error: {e}")
    approve()

tool_name  = data.get("tool_name", "")
tool_input = data.get("tool_input", {})
cwd        = os.environ.get("PWD", "")
log(f"tool={tool_name!r}")

# ══════════════════════════════════════════════════════════════════════════════
#  BASH — dangerous command check
# ══════════════════════════════════════════════════════════════════════════════
if tool_name == "Bash":
    command  = tool_input.get("command", "")
    dangerous, reason = bash_danger(command)
    log(f"bash dangerous={dangerous} reason={reason!r}")

    if not dangerous or not CHAT_ID:
        approve()

    if is_muted():
        log("muted → auto-approve")
        approve()

    cmd_preview = command.strip()
    if len(cmd_preview) > 500: cmd_preview = cmd_preview[:500] + "\n..."

    decision, msg_id, req_id = ask_both(
        tg_title   = "⚠️ <b>Permission Request</b>",
        detail     = cmd_preview,
        reason     = reason,
        tg_buttons = [
            [("✅ Allow", "allow"), ("❌ Deny", "deny")],
            [("🔕 Mute 30m", "mute30"), ("🔕 Mute 2h", "mute120")],
        ],
        dialog_title   = f"⚠️ Dangerous Bash — {reason}",
        dialog_options = [
            ("Deny",      "deny"),
            ("Mute 30m",  "mute30"),
            ("Allow",     "allow"),
        ],
    )

    ts        = time.strftime("%H:%M")
    project   = os.path.basename(cwd or "")
    cmd_short = html.escape(command.strip()[:300])

    if decision in ("mute30", "mute120"):
        secs    = 30*60 if decision == "mute30" else 2*60*60
        label   = "30 minutes" if decision == "mute30" else "2 hours"
        expires = time.strftime("%H:%M", time.localtime(time.time() + secs))
        if not USE_RELAY:
            try:
                with open(MUTE_FILE, "w") as f:
                    json.dump({"muted":True,"muted_until":time.time()+secs,"muted_at":time.time()}, f)
            except Exception: pass
        edit_result_msg(msg_id,
            f"🔕 <b>Muted for {label}</b>  <code>{ts}</code>\n"
            f"📁 <code>{html.escape(project)}</code>  🏷 <code>{html.escape(reason)}</code>\n\n"
            f"<pre>{cmd_short}</pre>\n\n"
            f"⏰ <i>Prompts resume at {expires} · /unmute to re-enable early</i>")
        approve()

    if decision == "allow":
        edit_result_msg(msg_id,
            f"✅ <b>Allowed</b>  <code>{ts}</code>\n"
            f"📁 <code>{html.escape(project)}</code>  🏷 <code>{html.escape(reason)}</code>\n\n"
            f"<pre>{cmd_short}</pre>")
        approve()

    edit_result_msg(msg_id,
        f"❌ <b>Denied</b>  <code>{ts}</code>\n"
        f"📁 <code>{html.escape(project)}</code>  🏷 <code>{html.escape(reason)}</code>\n\n"
        f"<pre>{cmd_short}</pre>")
    block(f"Denied ({reason})")

# ══════════════════════════════════════════════════════════════════════════════
#  READ / WRITE / EDIT — cross-project path check
# ══════════════════════════════════════════════════════════════════════════════
if tool_name in ("Read", "Write", "Edit", "MultiEdit"):
    file_path = (tool_input.get("file_path") or
                 tool_input.get("new_file_path") or "")

    if not file_path or (cwd and file_path.startswith(cwd)):
        approve()

    log(f"cross-project access: {file_path!r}")

    if not CHAT_ID:
        approve()

    if is_muted():
        log("muted → auto-approve")
        approve()

    if os.path.isdir(file_path):
        allowlist_entry = f"{tool_name}({file_path}/**)"
    else:
        parent = os.path.dirname(file_path)
        allowlist_entry = f"{tool_name}({parent}/**)"

    icons = {"Read":"📖","Write":"✏️","Edit":"✏️","MultiEdit":"✏️"}
    icon  = icons.get(tool_name, "🛠")

    decision, msg_id, req_id = ask_both(
        tg_title   = f"{icon} <b>Permission Request</b>",
        detail     = file_path,
        reason     = f"{tool_name} outside project",
        tg_buttons = [[
            ("✅ Allow",        "allow"),
            ("✅ Allow always", "always"),
            ("❌ Deny",         "deny"),
        ]],
        dialog_title   = f"🔐 {tool_name} outside project",
        dialog_options = [
            ("Deny",         "deny"),
            ("Allow always", "always"),
            ("Allow once",   "allow"),
        ],
    )

    ts      = time.strftime("%H:%M")
    project = os.path.basename(cwd or "")
    path_s  = html.escape(file_path[:300])

    if decision == "always":
        add_to_allowlist(allowlist_entry)
        edit_result_msg(msg_id,
            f"✅ <b>Allowed always</b>  <code>{ts}</code>\n"
            f"📁 <code>{html.escape(project)}</code>\n\n"
            f"<pre>{path_s}</pre>\n\n"
            f"<i>Added to allowlist — won't ask again</i>")
        approve()

    if decision == "allow":
        edit_result_msg(msg_id,
            f"✅ <b>Allowed once</b>  <code>{ts}</code>\n"
            f"📁 <code>{html.escape(project)}</code>\n\n"
            f"<pre>{path_s}</pre>")
        approve()

    edit_result_msg(msg_id,
        f"❌ <b>Denied</b>  <code>{ts}</code>\n"
        f"📁 <code>{html.escape(project)}</code>\n\n"
        f"<pre>{path_s}</pre>")
    block(f"Denied ({tool_name} outside project)")

# ══════════════════════════════════════════════════════════════════════════════
#  Everything else → pass through
# ══════════════════════════════════════════════════════════════════════════════
approve()
