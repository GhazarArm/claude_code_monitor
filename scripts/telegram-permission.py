#!/usr/bin/env python3
"""
Claude Code → Native Dialog + Telegram Permission Handler  (PreToolUse, all tools)

Shows permission prompts as BOTH a native dialog AND a Telegram message simultaneously.
First response (dialog click or Telegram tap) wins.

Supports two modes (auto-detected from telegram.conf):
  relay  — uses a shared relay server (public bot, no bot token needed)
  direct — polls Telegram directly with your own bot token

Bash (dangerous patterns)        → ⚠️  Allow / Deny / Mute 30m
Read / Write / Edit outside CWD  → 🔐  Allow / Allow always / Deny
Everything else                  → auto-approve instantly
"""
import sys, json, time, os, re, html, shlex, tempfile, urllib.request, threading, subprocess, platform

# ── Paths ─────────────────────────────────────────────────────────────────────
CLAUDE_DIR     = os.path.dirname(os.path.abspath(__file__))
LOG            = os.path.join(CLAUDE_DIR, "telegram-permission.log")
CONF           = os.path.join(CLAUDE_DIR, "telegram.conf")
MUTE_FILE      = os.path.join(CLAUDE_DIR, "telegram-mute.json")
SETTINGS_LOCAL = os.path.expanduser("~/.claude/settings.local.json")
POLL_LOCK      = os.path.join(CLAUDE_DIR, "telegram-poll.lock")

SYSTEM = platform.system()   # "Darwin" | "Linux"

def log(msg):
    with open(LOG, "a") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

# ── Credentials (relay or direct mode) ───────────────────────────────────────
TOKEN      = None   # direct mode
CHAT_ID    = None
RELAY_URL  = None   # relay mode
RELAY_TOKEN = None

try:
    with open(CONF) as f:
        for line in f:
            line = line.strip()
            k, _, v = line.partition("=")
            v = v.strip("\"'")
            if k == "TELEGRAM_BOT_TOKEN": TOKEN       = v
            elif k == "TELEGRAM_CHAT_ID": CHAT_ID     = v
            elif k == "RELAY_URL":        RELAY_URL   = v
            elif k == "RELAY_TOKEN":      RELAY_TOKEN = v
except Exception as e:
    log(f"config error: {e}")

RELAY_MODE = bool(RELAY_URL and CHAT_ID and RELAY_TOKEN)
log(f"mode={'relay' if RELAY_MODE else 'direct'}")

# ── Mute ──────────────────────────────────────────────────────────────────────
def is_muted():
    if RELAY_MODE:
        try:
            url = f"{RELAY_URL}/v1/muted/{CHAT_ID}?token={RELAY_TOKEN}"
            with urllib.request.urlopen(url, timeout=5) as r:
                return json.loads(r.read()).get("muted", False)
        except Exception:
            return False
    try:
        with open(MUTE_FILE) as f:
            s = json.load(f)
        if not s.get("muted"): return False
        until = s.get("muted_until", 0)
        if until == 0 or time.time() < until: return True
        with open(MUTE_FILE, "w") as f:
            json.dump({"muted": False, "muted_until": 0, "muted_at": 0}, f)
        return False
    except:
        return False

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
    if prog in ("rm", "unlink"):
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
        if sub == "clean" and any(re.search(r"[fdx]", a.lstrip("-")) for a in rest
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
    os.makedirs(dir_, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".settings-local-")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, SETTINGS_LOCAL)
    log(f"added to allowlist: {entry}")

# ── Poll lock (direct mode only — prevents 409 with bot listener) ─────────────
def acquire_poll_lock():
    if RELAY_MODE: return
    try:
        with open(POLL_LOCK, "w") as f: f.write(str(os.getpid()))
    except Exception: pass

def release_poll_lock():
    if RELAY_MODE: return
    try: os.unlink(POLL_LOCK)
    except Exception: pass

# ── Relay API ─────────────────────────────────────────────────────────────────
def relay_post(endpoint: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(f"{RELAY_URL}{endpoint}", data=body,
                                   headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            log(f"  relay{endpoint} ok")
            return result
    except Exception as e:
        log(f"  relay{endpoint} ERROR: {e}")
        return {}

def relay_get(endpoint: str) -> dict:
    try:
        with urllib.request.urlopen(f"{RELAY_URL}{endpoint}", timeout=620) as r:
            return json.loads(r.read())
    except Exception as e:
        log(f"  relay GET {endpoint} ERROR: {e}")
        return {}

# ── Direct Telegram API ───────────────────────────────────────────────────────
BASE = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""

def tg(method, payload):
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(f"{BASE}/{method}", data=body,
                                   headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            log(f"  tg.{method} ok={result.get('ok')}")
            return result
    except Exception as e:
        log(f"  tg.{method} ERROR: {e}")
        return {}

def latest_offset():
    resp    = tg("getUpdates", {"limit": 1, "offset": -1})
    updates = resp.get("result", [])
    off     = (updates[-1]["update_id"] + 1) if updates else 0
    log(f"  latest_offset → {off}")
    return off

# ── macOS dialog worker ───────────────────────────────────────────────────────
def _esc(s):
    return s.replace("\\","\\\\").replace('"','\\"').replace("\r","").replace("\n"," ")

def macos_dialog_worker(dialog_title, detail, options, result_holder, done_event, proc_holder):
    try:
        labels       = [label for label, _ in options]
        label_to_val = {label: val for label, val in options}
        btn_str      = ", ".join(f'"{_esc(l)}"' for l in labels)
        script = (
            f'try\n'
            f'  set r to button returned of '
            f'(display dialog "{_esc(detail[:300])}" with title "{_esc(dialog_title)}" '
            f'buttons {{{btn_str}}} default button "{_esc(labels[-1])}" with icon caution)\n'
            f'  return r\non error\n  return ""\nend try'
        )
        proc = subprocess.Popen(["/usr/bin/osascript", "-e", script],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc_holder[0] = proc
        stdout, _ = proc.communicate()
        if done_event.is_set(): return
        clicked = stdout.decode().strip()
        log(f"dialog: {clicked!r}")
        if clicked and clicked in label_to_val:
            result_holder[0] = ("dialog", label_to_val[clicked])
            done_event.set()
        else:
            log("dialog: cancelled — deferring to Telegram")
    except Exception as e:
        log(f"macos_dialog_worker error: {e}")

# ── Linux dialog worker ───────────────────────────────────────────────────────
def linux_dialog_worker(dialog_title, detail, options, result_holder, done_event, proc_holder):
    labels       = [label for label, _ in options]
    label_to_val = {label: val for label, val in options}
    for tool in ("zenity", "yad"):
        try:
            subprocess.run([tool, "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        try:
            cmd = ([
                "zenity", "--list", "--title", dialog_title, "--text", detail[:300],
                "--column", "Action", "--height=220", "--width=480", "--hide-header",
            ] if tool == "zenity" else [
                "yad", "--list", "--title", dialog_title, "--text", detail[:300],
                "--column", "Action", "--height=220", "--width=480", "--no-headers",
            ]) + labels
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc_holder[0] = proc
            stdout, _ = proc.communicate()
            if done_event.is_set(): return
            clicked = stdout.decode().strip().rstrip("|")
            log(f"linux dialog ({tool}): {clicked!r}")
            if clicked and clicked in label_to_val:
                result_holder[0] = ("dialog", label_to_val[clicked])
                done_event.set()
            return
        except Exception as e:
            log(f"linux dialog {tool}: {e}")
    log("no dialog tool found — Telegram only")

# ── Telegram polling worker (direct mode) ─────────────────────────────────────
def telegram_poll_worker(valid, offset_start, result_holder, done_event):
    offset = offset_start
    try:
        while not done_event.is_set():
            resp = tg("getUpdates", {
                "offset": offset, "timeout": 20,
                "allowed_updates": ["callback_query", "message"],
            })
            for upd in resp.get("result", []):
                uid    = upd.get("update_id", 0)
                offset = max(offset, uid + 1)
                cb     = upd.get("callback_query", {})
                data_  = cb.get("data", "")
                if data_ in valid:
                    decision = valid[data_]
                    tg("answerCallbackQuery", {"callback_query_id": cb.get("id","")})
                    log(f"telegram tap: {decision!r}")
                    if not done_event.is_set():
                        result_holder[0] = ("telegram", decision)
                        done_event.set()
                    return
                msg_text = upd.get("message",{}).get("text","").strip().lower()
                if msg_text in ("/unmute","unmute"):
                    try:
                        with open(MUTE_FILE,"w") as f:
                            json.dump({"muted":False,"muted_until":0,"muted_at":0},f)
                        tg("sendMessage",{"chat_id":CHAT_ID,"text":"🔔 <b>Unmuted</b>","parse_mode":"HTML"})
                    except Exception: pass
    except Exception as e:
        log(f"telegram_poll_worker error: {e}")

# ── Relay polling worker ──────────────────────────────────────────────────────
def relay_poll_worker(req_id, result_holder, done_event):
    """Long-poll the relay server for a decision."""
    try:
        result = relay_get(f"/v1/wait/{req_id}?chat_id={CHAT_ID}&token={RELAY_TOKEN}")
        decision = result.get("decision")
        log(f"relay decision: {decision!r}")
        if decision and not done_event.is_set():
            result_holder[0] = ("telegram", decision)
            done_event.set()
    except Exception as e:
        log(f"relay_poll_worker error: {e}")

# ── Combined dual prompt ──────────────────────────────────────────────────────
def ask_both(tg_title, detail, reason, tg_buttons, dialog_title, dialog_options):
    project = os.path.basename(os.environ.get("PWD","") or "")
    ts      = time.strftime("%H:%M")
    req_id  = f"{int(time.time())}_{os.getpid()}"
    log(f"req_id={req_id}")

    # Build Telegram message
    tg_text = (
        f"{tg_title}  <code>{ts}</code>\n"
        f"📁 <b>Project:</b> <code>{html.escape(project)}</code>\n"
        f"🏷 <b>Type:</b> <code>{html.escape(reason)}</code>\n\n"
        f"<pre>{html.escape(str(detail)[:400])}</pre>\n\n"
        f"📲 <i>Reply here or click the dialog</i>"
    )

    # Build keyboard — relay mode uses | separator, direct mode uses _
    sep = "|" if RELAY_MODE else "_"
    keyboard = {"inline_keyboard": [
        [{"text": lbl, "callback_data": f"{sfx}{sep}{req_id}"} for lbl, sfx in row]
        for row in tg_buttons
    ]}

    # Send message
    msg_id = None
    if RELAY_MODE:
        result = relay_post("/v1/prompt", {
            "chat_id": CHAT_ID, "token": RELAY_TOKEN,
            "req_id": req_id, "text": tg_text, "keyboard": keyboard,
        })
        msg_id = result.get("msg_id")
    else:
        acquire_poll_lock()
        offset = latest_offset()
        resp   = tg("sendMessage", {"chat_id": CHAT_ID, "text": tg_text,
                                     "parse_mode": "HTML", "reply_markup": keyboard})
        msg_id = resp.get("result", {}).get("message_id")

    log(f"msg_id={msg_id}")

    result_holder = [None]
    done_event    = threading.Event()
    proc_holder   = [None]

    # Dialog worker (platform-specific)
    if SYSTEM == "Darwin":
        dialog_fn = macos_dialog_worker
    elif SYSTEM == "Linux":
        dialog_fn = linux_dialog_worker
    else:
        dialog_fn = None

    if dialog_fn:
        threading.Thread(target=dialog_fn,
            args=(dialog_title, detail, dialog_options, result_holder, done_event, proc_holder),
            daemon=True).start()

    # Telegram/relay worker
    if RELAY_MODE:
        threading.Thread(target=relay_poll_worker,
            args=(req_id, result_holder, done_event), daemon=True).start()
    else:
        valid = {f"{sfx}_{req_id}": sfx for _, sfx in [btn for row in tg_buttons for btn in row]}
        threading.Thread(target=telegram_poll_worker,
            args=(valid, offset, result_holder, done_event), daemon=True).start()

    done_event.wait()   # no timeout — wait forever
    if not RELAY_MODE:
        release_poll_lock()

    # Kill dialog if still open
    proc = proc_holder[0]
    if proc and proc.poll() is None:
        try: proc.kill()
        except Exception: pass

    result   = result_holder[0]
    decision = result[1] if result else None
    source   = result[0] if result else "unknown"
    log(f"decision={decision!r} source={source!r}")
    return decision, msg_id, req_id

# ── Edit helper ───────────────────────────────────────────────────────────────
def edit_message(msg_id, chat_id_or_none, text):
    if not msg_id: return
    if RELAY_MODE:
        relay_post("/v1/edit", {
            "chat_id": CHAT_ID, "token": RELAY_TOKEN, "msg_id": msg_id, "text": text
        })
    else:
        tg("editMessageText", {
            "chat_id": chat_id_or_none or CHAT_ID,
            "message_id": msg_id, "text": text,
            "parse_mode": "HTML", "reply_markup": {"inline_keyboard": []},
        })

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
    command            = tool_input.get("command", "")
    dangerous, reason  = bash_danger(command)
    log(f"bash dangerous={dangerous} reason={reason!r}")

    if not dangerous or not CHAT_ID:
        approve()
    if is_muted():
        log("muted → auto-approve"); approve()

    cmd_preview = command.strip()
    if len(cmd_preview) > 500: cmd_preview = cmd_preview[:500] + "\n..."

    decision, msg_id, req_id = ask_both(
        tg_title   = "⚠️ <b>Permission Request</b>",
        detail     = cmd_preview,
        reason     = reason,
        tg_buttons = [
            [("✅ Allow","allow"), ("❌ Deny","deny")],
            [("🔕 Mute 30m","mute30"), ("🔕 Mute 2h","mute120")],
        ],
        dialog_title   = f"⚠️ Dangerous Bash — {reason}",
        dialog_options = [("Deny","deny"), ("Mute 30m","mute30"), ("Allow","allow")],
    )

    ts        = time.strftime("%H:%M")
    project   = os.path.basename(cwd or "")
    cmd_short = html.escape(command.strip()[:300])

    if decision in ("mute30","mute120"):
        secs    = 30*60 if decision=="mute30" else 2*60*60
        label   = "30 minutes" if decision=="mute30" else "2 hours"
        expires = time.strftime("%H:%M", time.localtime(time.time()+secs))
        if not RELAY_MODE:
            try:
                with open(MUTE_FILE,"w") as f:
                    json.dump({"muted":True,"muted_until":time.time()+secs,"muted_at":time.time()},f)
            except Exception: pass
        edit_message(msg_id, project,
            f"🔕 <b>Muted for {label}</b>  <code>{ts}</code>\n"
            f"📁 <code>{html.escape(project)}</code>  🏷 <code>{html.escape(reason)}</code>\n\n"
            f"<pre>{cmd_short}</pre>\n\n"
            f"⏰ <i>Prompts resume at {expires} · /unmute to re-enable early</i>")
        approve()

    if decision == "allow":
        edit_message(msg_id, None,
            f"✅ <b>Allowed</b>  <code>{ts}</code>\n"
            f"📁 <code>{html.escape(project)}</code>  🏷 <code>{html.escape(reason)}</code>\n\n"
            f"<pre>{cmd_short}</pre>")
        approve()

    edit_message(msg_id, None,
        f"❌ <b>Denied</b>  <code>{ts}</code>\n"
        f"📁 <code>{html.escape(project)}</code>  🏷 <code>{html.escape(reason)}</code>\n\n"
        f"<pre>{cmd_short}</pre>")
    block(f"Denied ({reason})")

# ══════════════════════════════════════════════════════════════════════════════
#  READ / WRITE / EDIT — cross-project path check
# ══════════════════════════════════════════════════════════════════════════════
if tool_name in ("Read","Write","Edit","MultiEdit"):
    file_path = (tool_input.get("file_path") or tool_input.get("new_file_path") or "")

    if not file_path or (cwd and file_path.startswith(cwd)):
        approve()

    log(f"cross-project access: {file_path!r}")

    if not CHAT_ID:
        approve()
    if is_muted():
        log("muted → auto-approve"); approve()

    allowlist_entry = (f"{tool_name}({file_path}/**)" if os.path.isdir(file_path)
                       else f"{tool_name}({os.path.dirname(file_path)}/**)")
    icons  = {"Read":"📖","Write":"✏️","Edit":"✏️","MultiEdit":"✏️"}
    icon   = icons.get(tool_name,"🛠")

    decision, msg_id, req_id = ask_both(
        tg_title   = f"{icon} <b>Permission Request</b>",
        detail     = file_path,
        reason     = f"{tool_name} outside project",
        tg_buttons = [[("✅ Allow","allow"),("✅ Allow always","always"),("❌ Deny","deny")]],
        dialog_title   = f"🔐 {tool_name} outside project",
        dialog_options = [("Deny","deny"),("Allow always","always"),("Allow once","allow")],
    )

    ts      = time.strftime("%H:%M")
    project = os.path.basename(cwd or "")
    path_s  = html.escape(file_path[:300])

    if decision == "always":
        add_to_allowlist(allowlist_entry)
        edit_message(msg_id, None,
            f"✅ <b>Allowed always</b>  <code>{ts}</code>\n"
            f"📁 <code>{html.escape(project)}</code>\n\n"
            f"<pre>{path_s}</pre>\n\n<i>Added to allowlist — won't ask again</i>")
        approve()

    if decision == "allow":
        edit_message(msg_id, None,
            f"✅ <b>Allowed once</b>  <code>{ts}</code>\n"
            f"📁 <code>{html.escape(project)}</code>\n\n<pre>{path_s}</pre>")
        approve()

    edit_message(msg_id, None,
        f"❌ <b>Denied</b>  <code>{ts}</code>\n"
        f"📁 <code>{html.escape(project)}</code>\n\n<pre>{path_s}</pre>")
    block(f"Denied ({tool_name} outside project)")

approve()
