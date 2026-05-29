#!/usr/bin/env python3
"""
Claude Code Monitor — Relay Server

Routes Telegram permission prompts between Claude Code hooks and Telegram users.
Deploy on Railway: set BOT_TOKEN and SERVER_SECRET env vars, expose port 8000.
"""
import asyncio, hashlib, hmac, json, logging, os, re, time, urllib.request
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Claude Code Monitor Relay")

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
SERVER_SECRET = os.environ.get("SERVER_SECRET", "changeme-set-this-in-railway")
PUBLIC_URL    = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
if PUBLIC_URL and not PUBLIC_URL.startswith("http"):
    PUBLIC_URL = f"https://{PUBLIC_URL}"

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── In-memory state ───────────────────────────────────────────────────────────
pending:    dict = {}   # req_id → asyncio.Queue (one decision per pending prompt)
mute_state: dict = {}   # chat_id → {muted: bool, until: float}

# ── Auth — deterministic per-user token ──────────────────────────────────────
def make_token(chat_id: str) -> str:
    """HMAC of chat_id+server_secret → stable token that survives restarts."""
    return hmac.new(SERVER_SECRET.encode(), str(chat_id).encode(),
                    hashlib.sha256).hexdigest()[:32]

def verify_token(chat_id: str, token: str) -> bool:
    return hmac.compare_digest(make_token(str(chat_id)), token)

# ── Telegram helpers ──────────────────────────────────────────────────────────
def tg(method: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(f"{BASE}/{method}", data=body,
                                   headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        log.error(f"tg.{method}: {e}")
        return {}

def send_msg(chat_id: str, text: str, keyboard: Optional[dict] = None) -> Optional[int]:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = keyboard
    return tg("sendMessage", payload).get("result", {}).get("message_id")

def edit_msg(chat_id: str, msg_id: int, text: str):
    tg("editMessageText", {
        "chat_id": chat_id, "message_id": msg_id,
        "text": text, "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": []},
    })

# ── Duration helpers ──────────────────────────────────────────────────────────
def parse_duration(text: str) -> int:
    m = re.match(r'^(\d+)\s*(m|min|h|hr|d|day)?$', text.strip().lower())
    if not m: return 30 * 60
    n, unit = int(m.group(1)), (m.group(2) or "m")[0]
    if unit == "d": return n * 86400
    if unit == "h": return n * 3600
    return n * 60

def fmt_duration(secs: int) -> str:
    if secs >= 86400: return f"{secs // 86400}d"
    if secs >= 3600:
        h = secs // 3600; m = (secs % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{secs // 60}m"

# ── Telegram webhook ──────────────────────────────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    update  = await request.json()
    log.info(f"update: {str(update)[:200]}")

    msg     = update.get("message", {})
    text    = msg.get("text", "").strip()
    chat_id = str(msg.get("chat", {}).get("id", ""))

    # ── Bot commands ──
    if chat_id and text:
        token = make_token(chat_id)
        relay = PUBLIC_URL or "https://YOUR_RELAY.railway.app"
        repo  = "https://github.com/GhazarArm/claude_code_monitor"

        if text.startswith("/start"):
            send_msg(chat_id, (
                f"👋 <b>Claude Code Monitor</b>\n\n"
                f"Your installation credentials:\n\n"
                f"  <b>Chat ID</b>   <code>{chat_id}</code>\n"
                f"  <b>Token</b>     <code>{token}</code>\n"
                f"  <b>Relay URL</b> <code>{relay}</code>\n\n"
                f"<b>Install on your machine:</b>\n"
                f"<pre>git clone {repo}\ncd claude_code_monitor\n"
                f"./install.sh \\\n"
                f"  --relay {relay} \\\n"
                f"  --chat-id {chat_id} \\\n"
                f"  --token {token}</pre>\n\n"
                f"Send /help to see available commands."
            ))

        elif text in ("/help", "help"):
            send_msg(chat_id, (
                "🤖 <b>Claude Code Monitor — Commands</b>\n\n"
                "/mute — pause prompts for 30 min\n"
                "/mute 2h — pause for 2 hours\n"
                "/mute 1d — pause for 1 day\n"
                "/unmute — re-enable prompts\n"
                "/status — show current state\n"
            ))

        elif text.startswith("/mute"):
            parts = text.split(None, 1)
            secs  = parse_duration(parts[1]) if len(parts) > 1 else 30 * 60
            until = time.time() + secs
            mute_state[chat_id] = {"muted": True, "until": until}
            expires = time.strftime("%H:%M", time.localtime(until))
            send_msg(chat_id,
                f"🔕 <b>Muted for {fmt_duration(secs)}</b>\n"
                f"⏰ Prompts resume at <b>{expires}</b>\n\n"
                f"Send /unmute to re-enable early.")

        elif text in ("/unmute", "unmute"):
            mute_state[chat_id] = {"muted": False, "until": 0}
            send_msg(chat_id, "🔔 <b>Unmuted</b> — permission prompts are active again.")

        elif text in ("/status", "status"):
            state = mute_state.get(chat_id, {})
            if state.get("muted") and (state.get("until", 0) == 0 or time.time() < state["until"]):
                until = state.get("until", 0)
                rem   = max(0, int(until - time.time()))
                exp   = time.strftime("%H:%M", time.localtime(until))
                send_msg(chat_id, f"🔕 <b>Muted</b> until {exp} ({fmt_duration(rem)} remaining)")
            else:
                send_msg(chat_id, "🔔 <b>Active</b> — permission prompts are enabled.")

    # ── Callback query (button tap) ──
    cb     = update.get("callback_query", {})
    cb_id  = cb.get("id", "")
    data   = cb.get("data", "")
    if cb_id:
        tg("answerCallbackQuery", {"callback_query_id": cb_id})
    if "|" in data:
        decision, req_id = data.split("|", 1)
        log.info(f"tap: decision={decision!r} req_id={req_id}")
        q = pending.get(req_id)
        if q:
            await q.put(decision)

    return {"ok": True}

# ── REST API ──────────────────────────────────────────────────────────────────
class PromptReq(BaseModel):
    chat_id: str
    token:   str
    req_id:  str
    text:    str
    keyboard: dict

class EditReq(BaseModel):
    chat_id: str
    token:   str
    msg_id:  int
    text:    str

@app.post("/v1/prompt")
async def send_prompt(req: PromptReq):
    if not verify_token(req.chat_id, req.token):
        raise HTTPException(403, "Invalid token")
    msg_id = send_msg(req.chat_id, req.text, req.keyboard)
    if not msg_id:
        raise HTTPException(502, "Failed to send Telegram message")
    pending[req.req_id] = asyncio.Queue()
    log.info(f"prompt queued: req_id={req.req_id} msg_id={msg_id}")
    return {"msg_id": msg_id}

@app.get("/v1/wait/{req_id}")
async def wait_decision(req_id: str, chat_id: str, token: str):
    if not verify_token(chat_id, token):
        raise HTTPException(403, "Invalid token")
    q = pending.get(req_id)
    if not q:
        raise HTTPException(404, "Unknown req_id")
    try:
        decision = await asyncio.wait_for(q.get(), timeout=600)
        pending.pop(req_id, None)
        log.info(f"resolved: req_id={req_id} decision={decision!r}")
        return {"decision": decision}
    except asyncio.TimeoutError:
        pending.pop(req_id, None)
        return {"decision": None}

@app.post("/v1/edit")
async def edit_message(req: EditReq):
    if not verify_token(req.chat_id, req.token):
        raise HTTPException(403, "Invalid token")
    edit_msg(req.chat_id, req.msg_id, req.text)
    return {"ok": True}

@app.get("/v1/muted/{chat_id}")
async def check_muted(chat_id: str, token: str):
    if not verify_token(chat_id, token):
        raise HTTPException(403, "Invalid token")
    state = mute_state.get(chat_id, {})
    if not state.get("muted"):
        return {"muted": False}
    until = state.get("until", 0)
    if until and time.time() > until:
        mute_state[chat_id] = {"muted": False, "until": 0}
        return {"muted": False}
    return {"muted": True, "until": until}

@app.get("/health")
async def health():
    return {"ok": True, "pending_prompts": len(pending)}

# ── Auto-register webhook on startup ─────────────────────────────────────────
@app.on_event("startup")
async def on_startup():
    if BOT_TOKEN and PUBLIC_URL:
        webhook_url = f"{PUBLIC_URL}/webhook"
        result = tg("setWebhook", {"url": webhook_url, "drop_pending_updates": True})
        log.info(f"webhook registered: {webhook_url} → {result.get('ok')}")
    else:
        log.warning("BOT_TOKEN or PUBLIC_URL not set — webhook not registered")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
