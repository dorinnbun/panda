# Panda

A voice-first personal AI assistant in the spirit of J.A.R.V.I.S. from Iron Man, with a holographic HUD dashboard, long-term memory, and a calm, dry-witted butler personality. It runs on your own VPS and is published to the internet through a Cloudflare Tunnel.

```
 Browser (desktop / phone)                VPS (Docker)                       Cloud
 ┌──────────────────────────┐   wss    ┌────────────────────────┐  HTTPS  ┌──────────────┐
 │ HUD dashboard            │◄────────►│ FastAPI app            │────────►│ Claude API   │
 │ - arc-reactor visual     │          │ - conversation engine  │         │ (+ web search)│
 │ - wake word / push-talk  │          │ - tools: memory, tasks,│         └──────────────┘
 │ - speech synthesis       │          │   reminders, notes,    │         ┌──────────────┐
 └──────────▲───────────────┘          │   weather, server stats│────────►│ Open-Meteo   │
            │                          │ - SQLite memory store  │         │ ElevenLabs*  │
            │ https://panda.you.com   └──────────▲─────────────┘         │ ntfy push*   │
            │                                     │ http://panda:8000    └──────────────┘
      ┌─────┴─────────────────────────────────────┴──┐
      │ Cloudflare edge  ◄── outbound tunnel ──  cloudflared container │
      │ (TLS, Access login, DDoS protection)         │                 * optional
      └──────────────────────────────────────────────┘
```

## What it can do

**Talks like a superintelligent butler**
- Composed, precise, dryly witty. Calls you "sir" (or whatever you set). Leads with the answer, keeps spoken replies short, and only formats with lists or code when you ask for something to read.
- Speaks while it thinks: the reply is streamed and spoken one sentence at a time, so it starts talking within a second or two.
- Notices things on its own: overdue tasks, clashing reminders, weather that affects your plans.

**Voice**
- Wake word: turn it on and say *"Panda, what's on my list today?"*, or just *"Panda"*, wait for the chime, then talk.
- Push-to-talk: click the mic or the reactor, or hold **Space**. **Esc** or a click on the reactor stops it talking.
- British voice chosen automatically from the voices your device has (Microsoft Ryan, Google UK English Male, Daniel...). For a film-quality voice, add an ElevenLabs key; "Premium (server)" then appears in the voice list.
- Voice input uses the browser's speech recognition (Chrome, Edge, Safari). Typing always works.

**Remembers you**
- Long-term memory in SQLite with full-text search. It saves facts about you on its own (preferences, people, projects, routines, standing instructions) and recalls relevant ones on every turn.
- Important memories (importance 4-5) are always in mind; the rest are pulled in when the conversation touches them.
- You can see and delete every memory in the Memory Core panel, or just say "forget that".
- Conversations persist across restarts. "New session" clears the chat but keeps the memory.

**Works for you**
- Tasks: add, list, complete, prioritise, due dates.
- Reminders: "remind me in 20 minutes to call Sam". It speaks the reminder on every open dashboard and can push it to your phone through [ntfy](https://ntfy.sh).
- Notes: save and search longer text such as meeting notes, ideas and drafts.
- Live web search and page reading for anything current.
- Weather and a three-day forecast.
- Diagnostics for the server it runs on (CPU, memory, disk, network, uptime), shown live on the HUD.
- Briefing button: time, weather, tasks, reminders and anything else worth knowing, in one go.

**Everywhere at once**: open the dashboard on your laptop and phone together. They share one conversation, and only the device you spoke to answers out loud.

## Cloudflare Tunnel or Cloudflare Workers?

**Use the VPS with a Cloudflare Tunnel.** It suits this project much better:

| | VPS + Cloudflare Tunnel | Cloudflare Workers |
|---|---|---|
| Long-lived WebSocket for streaming voice | Native | Needs Durable Objects |
| Memory database | SQLite file on your disk | Must be rewritten for D1/KV |
| Background jobs (reminders) | Simple loop in the app | Cron triggers + Durable Object alarms |
| Monitoring your own server | Yes, it runs there | Not possible |
| Future tools (run scripts, read files, control home devices) | Yes | Sandboxed, no local access |
| Open ports on the VPS | **None**: the tunnel dials out | n/a |
| Cost | The VPS you already pay for, tunnel is free | Workers Paid plan for Durable Objects |

The tunnel gives you the same benefits as Workers for exposure (Cloudflare TLS, DDoS protection, hidden origin IP) without rewriting the app for a serverless runtime. Workers would only make sense if you had no server at all.

## Deploy to your VPS

Requirements: a Linux VPS (1 vCPU / 1 GB RAM is enough, since the AI runs in the cloud), Docker with the compose plugin, and a domain on Cloudflare.

### 1. Get the code and configure it

```bash
git clone https://github.com/dorinnbun/panda.git
cd panda
cp .env.example .env
nano .env        # fill in ANTHROPIC_API_KEY, ACCESS_PASSWORD, SECRET_KEY, TZ, DEFAULT_LOCATION
mkdir -p data && sudo chown 1000:1000 data   # the container runs as uid 1000
```

Generate a `SECRET_KEY` with `python3 -c "import secrets; print(secrets.token_hex(32))"`.

### 2. Create the Cloudflare Tunnel

1. In the Cloudflare dashboard open **Zero Trust → Networks → Tunnels → Create a tunnel**, choose **Cloudflared**, and name it `panda`.
2. On the install step, copy the token from the shown command (the long string after `--token`) into `CLOUDFLARE_TUNNEL_TOKEN` in `.env`. You don't need to run the command; the compose file runs cloudflared for you.
3. Add a **public hostname**: subdomain `panda`, your domain, service type **HTTP**, URL **`panda:8000`**.

### 3. Start it

```bash
docker compose up -d --build
docker compose logs -f panda
```

Open `https://panda.yourdomain.com`, enter your passphrase, and say hello.

### 4. Recommended: put Cloudflare Access in front

This is your personal assistant and it holds your memories, so add a second lock that runs before anything reaches your server:
**Zero Trust → Access → Applications → Add → Self-hosted**, hostname `panda.yourdomain.com`, policy *Allow → Emails → your email*. You then log in with a one-time code sent to your email, followed by the app passphrase.

### Updating

```bash
git pull && docker compose up -d --build
```

Your data lives in `./data/panda.db`. To back it up, copy that file.

### Without Docker

```bash
sudo useradd -r -m panda && sudo mkdir -p /opt/panda && sudo chown panda /opt/panda
sudo -u panda git clone https://github.com/dorinnbun/panda.git /opt/panda
cd /opt/panda && sudo -u panda python3 -m venv .venv && sudo -u panda .venv/bin/pip install -r requirements.txt
sudo -u panda cp .env.example .env   # then edit it
sudo cp deploy/panda.service /etc/systemd/system/ && sudo systemctl enable --now panda
```

Install `cloudflared` as a service with the command the tunnel page shows, and point the public hostname at `http://localhost:8000`.

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # set ANTHROPIC_API_KEY, ACCESS_PASSWORD and COOKIE_SECURE=false
uvicorn panda.main:app --reload
```

Open http://localhost:8000. Browsers only allow the microphone on `https://` or `localhost`.

## Configuration

All settings are in `.env`; see `.env.example` for the full list.

| Variable | Default | Purpose |
|---|---|---|
| `ASSISTANT_NAME` | `Panda` | Name, wake word, and HUD title |
| `OWNER_NAME` / `OWNER_TITLE` | `User` / `sir` | Who it serves and how it addresses you |
| `PANDA_MODEL` | `claude-opus-5-5` | Claude model |
| `PANDA_EFFORT` | `low` | `low` is fastest for voice. `medium`/`high` think harder but are slower and cost more |
| `PANDA_FALLBACKS` | `default` | If a request is declined by a safety classifier, the API retries it on a fallback model. `off` disables this |
| `PANDA_WEB_SEARCH` | `on` | Live web search and page fetch |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` | empty | Premium voice |
| `NTFY_TOPIC_URL` | empty | e.g. `https://ntfy.sh/your-long-random-topic` for reminders on your phone |

## Project layout

```
panda/
  main.py      web server, login, WebSocket channel, reminder loop
  brain.py     streaming conversation engine and tool loop
  persona.py   personality (system prompt) and per-turn context
  tools.py     memory, tasks, reminders, notes, weather, system status
  db.py        SQLite storage with full-text search
  auth.py      passphrase login and signed session cookie
web/           HUD dashboard (HTML/CSS/JS, no build step)
deploy/        systemd unit for non-Docker installs
```

## Adding your own abilities

Every tool is one decorated function in `panda/tools.py`:

```python
@tool("turn_on_lights", "Turn on the lights in a room.", {"room": {"type": "string"}}, ["room"])
async def _lights(a: dict) -> dict:
    ...  # call Home Assistant, a script, an API
    return {"ok": True}
```

Restart the app and the assistant can use it straight away. Good next ones to add: Google Calendar and Gmail, Home Assistant, GitHub notifications, or a "run this command on the server" tool behind a confirmation step.

## Security notes

- The app listens only on `127.0.0.1` on the host; the only way in is through the tunnel.
- Logins are rate-limited (5 failed attempts per IP every 15 minutes), and sessions are signed HttpOnly, SameSite=Strict cookies.
- Never commit `.env`. It is already in `.gitignore`.
