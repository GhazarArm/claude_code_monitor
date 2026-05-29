#!/usr/bin/env python3
"""
Claude Code Monitor — Relay Server
Routes Telegram permission prompts between Claude Code hooks and Telegram users.
"""
import asyncio, concurrent.futures, hashlib, hmac, json, logging, os, re, time, urllib.request
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Claude Code Monitor Relay")

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
SERVER_SECRET = os.environ.get("SERVER_SECRET", "changeme")
PUBLIC_URL    = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
if PUBLIC_URL and not PUBLIC_URL.startswith("http"):
    PUBLIC_URL = f"https://{PUBLIC_URL}"

BASE     = f"https://api.telegram.org/bot{BOT_TOKEN}"
pending:    dict = {}   # req_id → asyncio.Queue
mute_state: dict = {}   # chat_id → {muted, until}
executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)

# ── Auth ──────────────────────────────────────────────────────────────────────
def make_token(chat_id: str) -> str:
    return hmac.new(SERVER_SECRET.encode(), str(chat_id).encode(),
                    hashlib.sha256).hexdigest()[:32]

def verify_token(chat_id: str, token: str) -> bool:
    return hmac.compare_digest(make_token(str(chat_id)), token)

# ── Telegram (sync, runs in thread pool) ──────────────────────────────────────
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

async def tg_async(method: str, payload: dict) -> dict:
    """Run Telegram API call in thread pool — never blocks the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: tg(method, payload))

async def send_msg(chat_id: str, text: str, keyboard: Optional[dict] = None) -> Optional[int]:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = keyboard
    result = await tg_async("sendMessage", payload)
    return result.get("result", {}).get("message_id")

async def edit_msg(chat_id: str, msg_id: int, text: str):
    await tg_async("editMessageText", {
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

# ── Update handler (runs in background, after webhook returns 200) ────────────
async def handle_update(update: dict):
    try:
        msg     = update.get("message", {})
        text    = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if chat_id and text:
            token = make_token(chat_id)
            relay = PUBLIC_URL or "https://claudecodemonitor-production.up.railway.app"
            repo  = "https://github.com/GhazarArm/claude_code_monitor"

            if text.startswith("/start"):
                await send_msg(chat_id, (
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
                    f"Send /help for available commands."
                ))

            elif text in ("/help", "help"):
                await send_msg(chat_id, (
                    "🤖 <b>Claude Code Monitor</b>\n\n"
                    "/mute — pause prompts for 30 min\n"
                    "/mute 2h — pause for 2 hours\n"
                    "/mute 1d — pause for 1 day\n"
                    "/unmute — re-enable prompts\n"
                    "/status — show current state"
                ))

            elif text.startswith("/mute"):
                parts = text.split(None, 1)
                secs  = parse_duration(parts[1]) if len(parts) > 1 else 30 * 60
                until = time.time() + secs
                mute_state[chat_id] = {"muted": True, "until": until}
                expires = time.strftime("%H:%M", time.localtime(until))
                await send_msg(chat_id,
                    f"🔕 <b>Muted for {fmt_duration(secs)}</b>\n"
                    f"⏰ Prompts resume at <b>{expires}</b> — /unmute to re-enable early.")

            elif text in ("/unmute", "unmute"):
                mute_state[chat_id] = {"muted": False, "until": 0}
                await send_msg(chat_id, "🔔 <b>Unmuted</b> — permission prompts are active again.")

            elif text in ("/status", "status"):
                state = mute_state.get(chat_id, {})
                if state.get("muted") and (not state.get("until") or time.time() < state["until"]):
                    until = state.get("until", 0)
                    rem   = max(0, int(until - time.time()))
                    exp   = time.strftime("%H:%M", time.localtime(until))
                    await send_msg(chat_id, f"🔕 <b>Muted</b> until {exp} ({fmt_duration(rem)} remaining)")
                else:
                    await send_msg(chat_id, "🔔 <b>Active</b> — permission prompts are enabled.")

        # Callback query (button tap)
        cb    = update.get("callback_query", {})
        cb_id = cb.get("id", "")
        data  = cb.get("data", "")
        if cb_id:
            await tg_async("answerCallbackQuery", {"callback_query_id": cb_id})
        if "|" in data:
            decision, req_id = data.split("|", 1)
            log.info(f"tap: decision={decision!r} req_id={req_id}")
            q = pending.get(req_id)
            if q:
                await q.put(decision)

    except Exception as e:
        log.error(f"handle_update error: {e}", exc_info=True)

# ── Webhook — returns 200 immediately, processes in background ────────────────
@app.post("/webhook")
async def webhook(request: Request):
    update = await request.json()
    log.info(f"update: {str(update)[:150]}")
    asyncio.create_task(handle_update(update))   # don't await — return fast
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
    msg_id = await send_msg(req.chat_id, req.text, req.keyboard)
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
        return {"decision": decision}
    except asyncio.TimeoutError:
        pending.pop(req_id, None)
        return {"decision": None}

@app.post("/v1/edit")
async def edit_message(req: EditReq):
    if not verify_token(req.chat_id, req.token):
        raise HTTPException(403, "Invalid token")
    await edit_msg(req.chat_id, req.msg_id, req.text)
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

@app.on_event("startup")
async def on_startup():
    # Use configured URL or fall back to the known Railway URL
    webhook_base = PUBLIC_URL or "https://claudecodemonitor-production.up.railway.app"
    if BOT_TOKEN:
        result = tg("setWebhook", {
            "url": f"{webhook_base}/webhook",
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        })
        log.info(f"webhook → {webhook_base}/webhook ok={result.get('ok')} desc={result.get('description','')}")
    else:
        log.warning("BOT_TOKEN not set — webhook not registered")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
