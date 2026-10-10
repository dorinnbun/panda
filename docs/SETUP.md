# Setting up Panda: from zero to talking

This is the full path from an empty VPS to a Panda that knows you and talks with you on your laptop and phone. Plan on about an hour, most of it in the Cloudflare and Anthropic dashboards.

## How the pieces connect

```
 You (voice)                                                  
   │  microphone → browser speech recognition → text
   ▼
 Browser: Panda dashboard ──── encrypted WebSocket (wss://panda.yourdomain.com) ────┐
   ▲                                                                              │
   │  text → browser or ElevenLabs voice → speaker                                ▼
   │                                                    Cloudflare edge (HTTPS, Access login)
   │                                                                              │
   │                                                     outbound tunnel (no open ports)
   │                                                                              ▼
   └──────────── streamed reply ◄────────────  VPS: cloudflared → Panda app (Docker)
                                                         │            │
                                                 SQLite memory    Claude API
                                                 (data/panda.db)  (thinking, web search)
```

- **Speech to text happens in your browser**, so audio never leaves your device. Only the recognised text is sent to your server.
- **Memory lives on your VPS** in one SQLite file. Each turn, the relevant memories are added to what Claude sees, and Claude saves new ones through a tool.
- **Speech out** uses your device's built-in voice by default, or ElevenLabs if you add a key.

---

## Phase 1: Accounts and keys (15 min)

| What | Where | Notes |
|---|---|---|
| Claude API key | [platform.claude.com](https://platform.claude.com) → API keys | Add billing credit and **set a monthly spend limit** under Limits. |
| Cloudflare account + a domain | [dash.cloudflare.com](https://dash.cloudflare.com) | The domain's nameservers must point at Cloudflare. |
| ElevenLabs (optional) | [elevenlabs.io](https://elevenlabs.io) | For a cinematic voice. Copy an API key and a voice ID. |
| ntfy app (optional) | Play Store / App Store | Phone push for reminders. |

**Cost.** Your VPS bill doesn't change, and the tunnel and Access are free for one person. Claude is billed per use. The default model (Claude Opus 5.5 at `low` effort) suits a voice assistant: the fixed instructions are cached between turns, so a normal spoken exchange costs a few cents or less. Web searches and long answers cost more. Watch the usage page during your first week, and keep the spend limit on. To cut costs, set `PANDA_MODEL=claude-sonnet-5-5`, which costs about half as much.

## Phase 2: Prepare the VPS (10 min)

```bash
# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

# Firewall: only SSH in. Panda is reached through the tunnel, never a public port.
sudo ufw default deny incoming && sudo ufw allow OpenSSH && sudo ufw enable
```

Panda needs very little: 1 vCPU, 1 GB RAM and about 1 GB of disk is plenty.

## Phase 3: Get the code (5 min)

Merge this work into `main` on GitHub first, then on the VPS:

```bash
git clone https://github.com/dorinnbun/panda.git ~/panda
cd ~/panda
cp .env.example .env
mkdir -p data && sudo chown 1000:1000 data
```

## Phase 4: Configure (5 min)

Edit `.env` (`nano .env`):

```ini
ANTHROPIC_API_KEY=sk-ant-...
ACCESS_PASSWORD=<a long passphrase you can type on your phone>
SECRET_KEY=<output of: python3 -c "import secrets; print(secrets.token_hex(32))">

ASSISTANT_NAME=Panda          # also the wake word
OWNER_NAME=Dorinn
OWNER_TITLE=sir               # how Panda addresses you
TZ=Asia/Phnom_Penh            # your IANA timezone, used for reminders
DEFAULT_LOCATION=Phnom Penh   # weather panel and briefings
```

The timezone matters: "remind me at 9" means 9 o'clock in `TZ`.

## Phase 5: Cloudflare Tunnel (10 min)

1. **Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared**, and name it `panda`.
2. Copy the token from the install command (the long string after `--token`) into `.env` as `CLOUDFLARE_TUNNEL_TOKEN=`. Skip the install command; Docker runs cloudflared for you.
3. **Public hostname**: subdomain `panda`, your domain, type `HTTP`, URL `panda:8000`. Save.

## Phase 6: Lock it down with Cloudflare Access (5 min)

Panda holds your personal memories, so keep strangers from even reaching the login page:

1. **Zero Trust → Access → Applications → Add an application → Self-hosted.**
2. Application domain: `panda.yourdomain.com`. Session duration: 1 month.
3. Policy: **Allow**, include **Emails** = your email address.

Opening Panda now first asks for a one-time code sent to your email, then your passphrase.

## Phase 7: Launch (2 min)

```bash
docker compose up -d --build
docker compose logs -f panda        # look for "Application startup complete"
```

Open `https://panda.yourdomain.com`, pass Access, and enter your passphrase. The status at the top should read **UPLINK SECURE**.

## Phase 8: Teach it who you are (15 min, the important part)

Panda learns two ways: it saves things as you mention them, and it can interview you.

1. **Press Calibrate.** Panda interviews you one question at a time (who you are, your work, projects, people, routine, preferences) and saves what it learns. Answer by voice or text. Stop whenever you like; you can resume later by pressing Calibrate again.
2. **Give it standing instructions.** Say them plainly and Panda stores them as high-importance memories that are always in mind:
   - "Always give me prices in US dollars."
   - "When I ask for a summary, keep it to three points."
   - "My working hours are nine to six, Phnom Penh time."
3. **Check the Memories panel.** Everything it saved appears there. Delete anything wrong with *forget*, or say "That's wrong, I actually prefer…" and it updates the memory.
4. **Test it.** Press **New session** (this clears the chat but keeps memory) and ask "What do you know about me?"

How it remembers:
- Importance 4-5 memories (identity, standing instructions) go into every conversation.
- Other memories are pulled in when your words relate to them, using full-text search.
- Conversations survive restarts. After about 400 messages a fresh conversation starts automatically, and the memories carry over.

## Phase 9: Voice on each device (10 min)

**Laptop (Chrome or Edge, recommended)**
1. Click the mic once and allow microphone access.
2. Pick a voice in the Voice panel. British male voices are ranked first, such as "Microsoft Ryan Online (Natural)" on Edge or "Google UK English Male" on Chrome.
3. Turn **Wake word** on. Then say *"Panda, what's on my list?"*, or just *"Panda"*, wait for the chime, and speak.
4. Hold **Space** for push-to-talk. Press **Esc**, or click the eye, to make it stop talking.
5. Allow notifications when asked, so reminders reach you even when the tab is in the background.

**Android phone (Chrome)**: open the URL, then use ⋮ → **Add to Home screen**. Tap the mic or the eye to talk. The wake word works while the screen is on and the page is open.

**iPhone (Safari)**: open the URL, then Share → **Add to Home Screen**. Use tap-to-talk; Safari stops always-on listening when the page isn't active, so don't rely on the wake word there. Panda speaks with the system voices; Settings → Accessibility → Spoken Content → Voices lets you download better ones.

**Premium voice (optional)**: add `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` to `.env`, run `docker compose up -d`, and choose **Premium (server)** in the Voice panel.

**Reminders on your phone (optional)**: in the ntfy app, subscribe to a long random topic such as `panda-8f3k2m9x`. Set `NTFY_TOPIC_URL=https://ntfy.sh/panda-8f3k2m9x` and restart. Reminders now buzz your phone even when no dashboard is open.

## Phase 10: Living with it

Things to try:

- "Panda, brief me." (or the **Briefing** button)
- "Remind me in 40 minutes to check the deployment."
- "Add a high-priority task: renew the domain, due Friday."
- "Take a note: meeting with Sokha. Budget approved, launch moves to March."
- "What did I note about the Sokha meeting?"
- "How is the server doing?"
- "Search the web for the latest Cloudflare Tunnel release notes and summarise them."
- "Forget what I said about my old job."

### Backups

All your data is in `~/panda/data/panda.db`. Take a safe copy while it runs:

```bash
docker compose exec panda python -c "import sqlite3; sqlite3.connect('/data/panda.db').backup(sqlite3.connect('/data/backup.db'))"
cp data/backup.db ~/panda-backup-$(date +%F).db
```

To make this a nightly job, add it to `crontab -e`, and copy the file off the server now and then.

### Updates

```bash
cd ~/panda && git pull && docker compose up -d --build
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| Status says OFFLINE | `docker compose ps` and `docker compose logs panda cloudflared` |
| Cloudflare error 502 / 1033 | Tunnel hostname must point at `panda:8000` (not localhost) when using Docker Compose |
| "API key has been rejected" | Check `ANTHROPIC_API_KEY`, then `docker compose up -d` to reload `.env` |
| Mic button does nothing | Only works over HTTPS; check the site's microphone permission in the browser |
| Wake word misses you | Speak the name clearly at the start; Chrome/Edge on desktop work best. Push-to-talk always works |
| Robotic voice | Pick another voice in the Voice panel, or add ElevenLabs |
| Reminder at the wrong time | `TZ` in `.env` must be your timezone |
| Logged out after a restart | Set a fixed `SECRET_KEY` in `.env` |
