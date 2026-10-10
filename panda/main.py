"""HTTP + WebSocket server: dashboard, login, live voice/chat channel."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import auth, db, sysinfo, weather
from .brain import Conversation
from .config import ROOT, settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("panda")
WEB = ROOT / "web"


class Hub:
    """Every open dashboard (desktop, phone, tablet) shares one conversation."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def broadcast(self, event: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(event)
            except Exception:
                self.clients.discard(ws)


hub = Hub()
conversation: Conversation


async def _push_phone(text: str) -> None:
    if not settings.ntfy_topic_url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(settings.ntfy_topic_url, content=text.encode(),
                              headers={"Title": f"{settings.assistant_name} reminder", "Priority": "high"})
    except httpx.HTTPError as exc:
        log.warning("ntfy push failed: %s", exc)


async def reminder_loop() -> None:
    while True:
        try:
            for r in db.due_reminders(datetime.now(timezone.utc).isoformat(timespec="seconds")):
                db.set_reminder_status(r["id"], "fired")
                await hub.broadcast({"type": "reminder", "id": r["id"], "text": r["text"]})
                await _push_phone(r["text"])
        except Exception:
            log.exception("reminder loop error")
        await asyncio.sleep(10)


async def telemetry_loop() -> None:
    while True:
        if hub.clients:
            await hub.broadcast({"type": "system", "data": sysinfo.snapshot()})
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global conversation
    conversation = Conversation()
    if not settings.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY is not set; the assistant cannot think until it is.")
    tasks = [asyncio.create_task(reminder_loop()), asyncio.create_task(telemetry_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=WEB), name="static")


def _authed(request: Request) -> bool:
    return auth.valid_token(request.cookies.get(auth.COOKIE))


def _client_ip(request: Request) -> str:
    # Behind Cloudflare the real client address arrives in this header.
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def _require(request: Request) -> None:
    if not _authed(request):
        raise HTTPException(status_code=401)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/")
async def index(request: Request):
    if not _authed(request):
        return RedirectResponse("/login")
    return FileResponse(WEB / "index.html")


@app.get("/login")
async def login_page():
    return FileResponse(WEB / "login.html")


@app.post("/api/login")
async def login(request: Request):
    ip = _client_ip(request)
    if auth.throttled(ip):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
    body = await request.json()
    if not auth.check_password(ip, str(body.get("password", ""))):
        raise HTTPException(status_code=401, detail="Access denied.")
    resp = Response(status_code=204)
    resp.set_cookie(auth.COOKIE, auth.issue_token(), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, secure=settings.cookie_secure, samesite="strict")
    return resp


@app.post("/api/logout")
async def logout():
    resp = Response(status_code=204)
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/api/dashboard")
async def dashboard(request: Request):
    _require(request)
    wx = None
    if settings.default_location:
        try:
            wx = await weather.get_weather(settings.default_location)
        except httpx.HTTPError:
            wx = None
    return {
        "assistant": settings.assistant_name,
        "owner": settings.owner_name,
        "title": settings.owner_title,
        "timezone": settings.timezone,
        "server_tts": bool(settings.elevenlabs_api_key and settings.elevenlabs_voice_id),
        "tasks": db.list_tasks("open"),
        "reminders": db.list_reminders("pending"),
        "memories": db.recent_memories(30),
        "memory_count": db.memory_count(),
        "notes": db.recent_notes(8),
        "system": sysinfo.snapshot(),
        "weather": wx,
        "transcript": conversation.transcript[-40:],
    }


@app.post("/api/tasks/{task_id}/complete")
async def ui_complete_task(task_id: int, request: Request):
    _require(request)
    db.complete_task(task_id)
    await hub.broadcast({"type": "refresh"})
    return {"ok": True}


@app.delete("/api/memories/{memory_id}")
async def ui_forget(memory_id: int, request: Request):
    _require(request)
    db.delete_memory(memory_id)
    await hub.broadcast({"type": "refresh"})
    return {"ok": True}


@app.post("/api/reminders/{reminder_id}/cancel")
async def ui_cancel_reminder(reminder_id: int, request: Request):
    _require(request)
    db.set_reminder_status(reminder_id, "cancelled")
    await hub.broadcast({"type": "refresh"})
    return {"ok": True}


@app.post("/api/tts")
async def tts(request: Request):
    """Optional premium voice through ElevenLabs; the browser voice is the fallback."""
    _require(request)
    if not (settings.elevenlabs_api_key and settings.elevenlabs_voice_id):
        raise HTTPException(status_code=404)
    text = str((await request.json()).get("text", ""))[:2000]
    client = httpx.AsyncClient(timeout=30)
    req = client.build_request(
        "POST",
        f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}/stream",
        headers={"xi-api-key": settings.elevenlabs_api_key, "accept": "audio/mpeg"},
        json={"text": text, "model_id": "eleven_turbo_v2_5",
              "voice_settings": {"stability": 0.55, "similarity_boost": 0.75}},
    )
    upstream = await client.send(req, stream=True)
    if upstream.status_code != 200:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail="TTS provider error")

    async def body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(body(), media_type="audio/mpeg")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not auth.valid_token(ws.cookies.get(auth.COOKIE)):
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.clients.add(ws)
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "say":
                text = str(msg.get("text", "")).strip()[:8000]
                if not text:
                    continue
                await hub.broadcast({"type": "user", "text": text, "client": str(msg.get("client", ""))[:64]})
                asyncio.create_task(conversation.respond(text, hub.broadcast))
            elif kind == "reset":
                async with conversation.lock:
                    conversation.reset()
                await hub.broadcast({"type": "reset"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
