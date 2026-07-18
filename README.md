# Reelarr

Self-hosted *arr-family app: share a social video link (Instagram Reel, TikTok, Facebook video) via
Telegram (or Discord / Slack / WhatsApp) → AI identifies the movie/show → adds it to Radarr or
Sonarr, or routes it through Overseerr/Jellyseerr for approval. Low confidence → the bot asks you to
confirm on whichever channel the link came from.

Runs entirely on local AI (Ollama + faster-whisper via a LiteLLM proxy) — zero paid APIs required.
Commercial providers (Groq, Gemini) are optional per-component drop-ins.

## Architecture at a glance

```
Telegram/Discord/Slack/WhatsApp ──> IntakeChannel ──> Identification pipeline
                                                        1. yt-dlp metadata  -> text LLM
                                                        2. audio -> STT     -> text LLM
                                                        3. frames -> vision LLM (OCR + actor
                                                           evidence) -> TMDB credit verification
                                                        4. TMDB lookup (+ TVDB id for TV)
                                                        5. confidence gate ──> confirm on channel
                                                        6. fulfillment ──> Radarr/Sonarr direct
                                                                        └─> or Overseerr/Jellyseerr
```

- **Backend**: Python 3.12 / FastAPI / SQLAlchemy (SQLite), `backend/`
- **Frontend**: React + TypeScript, Servarr-styled dark UI, `frontend/`
- **AI**: one OpenAI-compatible dialect against a LiteLLM proxy (`deploy/litellm-config.yaml`)
- **Web UI**: port `7979`

## Quick start (local dev)

```bash
cp .env.example .env       # fill in at least TMDB_API_KEY + TELEGRAM_BOT_TOKEN
docker compose up --build  # builds the image, starts reelarr + litellm
# UI at http://localhost:7979
```

Without Docker:

```bash
# backend (needs Python 3.12+, ffmpeg on PATH)
cd backend
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn reelarr.main:app --port 7979 --reload

# frontend (dev server proxies /api to :7979)
cd frontend
npm install
npm run dev

# tests — all mocked, no live services or paid APIs needed
cd backend && pytest
```

## Setup checklist

### 1. Ollama models (local AI)

Ollama is assumed already running (container `ollama-nvidia` on the shared network). Pull the
default models:

```bash
docker exec ollama-nvidia ollama pull qwen3:8b       # text identification
docker exec ollama-nvidia ollama pull qwen2.5vl:7b   # vision (Tier 3, only if ENABLE_VISION=true)
```

> The single most common failure mode is "model configured but never pulled" — the Identification
> settings Test button checks for exactly this and lists what the proxy actually serves.

### 2. Speech-to-text

The default LiteLLM config points at an existing OpenAI-compatible **Speaches** (faster-whisper)
instance at `http://ollama-speaches:8000/v1` with model
`Systran/faster-distil-whisper-small.en`. Any OpenAI-compatible STT endpoint works — change the
`reelarr-stt` entry in `deploy/litellm-config.yaml`, or set `STT_BASE_URL` to bypass LiteLLM
entirely.

> **The model must actually be installed on the Speaches instance** or every Tier 2 call 404s:
> `curl -X POST http://ollama-speaches:8000/v1/models/Systran/faster-distil-whisper-small.en`.
> If that download itself fails with a `PermissionError` on `/home/ubuntu/.cache/huggingface`,
> the container's HF cache volume is owned by the wrong UID — fix ownership on the host.
> Note the default model is **English-only** (`.en`); for clips with non-English dialogue swap in
> a multilingual model such as `Systran/faster-whisper-small`.

### 3. LiteLLM proxy

Ships as a compose service (`reelarr-litellm`, port 4000) configured by
`deploy/litellm-config.yaml`. Set `LITELLM_MASTER_KEY` in `.env` to any secret string. To add
commercial fallbacks, uncomment the Groq/Gemini blocks in the config and set the matching env keys.

### 4. TMDB API key (required)

Free: sign up at themoviedb.org → Settings → API → create a Developer key. Put it in
`TMDB_API_KEY`.

### 5. Telegram bot (priority intake channel)

1. Message **@BotFather** → `/newbot` → pick a name/username → copy the token into
   `TELEGRAM_BOT_TOKEN`.
2. Find your numeric chat ID (message **@userinfobot**, or check
   `https://api.telegram.org/bot<TOKEN>/getUpdates` after messaging your bot) and put it in
   `TELEGRAM_ALLOWED_CHAT_IDS` (comma-separated for multiple people). The allowlist is closed by
   default — no ID, no access.
3. Share any Instagram/TikTok/Facebook link with the bot.

### 6. Radarr / Sonarr / Overseerr API keys

- Radarr: Settings → General → Security → API Key → `RADARR_API_KEY`
- Sonarr: same path → `SONARR_API_KEY`
- Overseerr/Jellyseerr (only if `FULFILLMENT_TARGET=seerr`): Settings → General → API Key →
  `SEERR_API_KEY`

Container-name URLs (`http://radarr:7878`, `http://sonarr:8989`, `http://overseerr:5055`) are
pre-set for the shared `traefik_default` network. Root folder + quality profile are easiest to set
from the UI: Settings → Fulfillment → Test live-populates the dropdowns.

### 7. Instagram cookies (optional but usually needed for IG)

Instagram frequently requires login cookies for yt-dlp. TikTok works without cookies.

1. Install a "cookies.txt" exporter extension (e.g. *Get cookies.txt LOCALLY*) in a browser where
   you're logged into Instagram.
2. Export in Netscape format and save as `instagram.txt`.
3. Drop it into the cookies volume: `docker cp instagram.txt reelarr:/config/cookies/instagram.txt`
   (or place it in the mounted cookies dir). Same pattern applies for `facebook.txt` if needed.

### 8. Discord / Slack (structural stubs)

The adapters exist with the full interface and working Test endpoints, but message handling is TODO
until tokens exist:

- **Discord**: create an app at discord.com/developers → Bot → token into `DISCORD_BOT_TOKEN`,
  enable the *Message Content* intent.
- **Slack**: create an app → enable **Socket Mode** (no public HTTPS endpoint needed) → bot token
  (`xoxb-…`) into `SLACK_BOT_TOKEN` and app-level token (`xapp-…`, `connections:write`) into
  `SLACK_APP_TOKEN`.

### 9. WhatsApp (opt-in — read this first)

WhatsApp support uses a self-hosted [Evolution API](https://github.com/EvolutionAPI/evolution-api)
instance wrapping the **unofficial** Baileys engine:

- **Ban risk is real** and reportedly getting worse — use a dedicated secondary number, keep send
  rates conservative, and avoid bulk-lookup endpoints. This is why it ships **off by default**
  behind a compose profile.
- Confirmations use **numbered text replies** ("Reply 1, 2, or 3"), not native buttons, which are
  unreliable on the unofficial engine.
- Only use Evolution's canonical package — a trojanized Baileys fork (`lotusbail`) was caught
  stealing credentials in Dec 2025.

Enable with `docker compose --profile whatsapp up -d`, open the Evolution manager, create an
instance named `reelarr`, scan the QR from the secondary phone, then point Evolution's webhook at
`http://reelarr:7979/api/v1/webhooks/whatsapp`. Note Evolution v2 expects Postgres + Redis for
production persistence — the bundled service is a minimal pairing/testing setup.

## Deployment (docker-stacks / Portainer)

`deploy/reelarr.yaml` is a docker-stacks-style stack file (external `traefik_default` network,
`/nvme0/appdata` bind mounts, `dgshue/reelarr:latest` + `pull_policy: always`, commented-out
Traefik labels). Copy it into the docker-stacks repo when ready, copy
`deploy/litellm-config.yaml` to `/nvme0/appdata/reelarr/litellm-config.yaml` on the host, and set
the stack's env vars in Portainer. Building/pushing `dgshue/reelarr:latest` is a manual step for
now:

```bash
docker build -t dgshue/reelarr:latest .
docker push dgshue/reelarr:latest
```

### Reverse proxy note

If you expose Reelarr through nginx/Caddy/Traefik on a subpath (URL Base setting), make sure the
proxy forwards WebSocket upgrade headers (`Upgrade`/`Connection`) for `/ws`, or live queue updates
will silently fall back to nothing.

## Environment variables

Every variable is listed in [.env.example](.env.example) with inline docs. Minimum for an
end-to-end run: `TMDB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`,
`RADARR_API_KEY`/`SONARR_API_KEY` (or `SEERR_API_KEY` with `FULFILLMENT_TARGET=seerr`), plus
`RADARR_ROOT_FOLDER`/`RADARR_QUALITY_PROFILE_ID` (and Sonarr equivalents) for direct adds.

## Project layout

```
backend/reelarr/
  ai/           TextLLMClient / VisionLLMClient / SttClient (OpenAI dialect via LiteLLM)
  api/          FastAPI routers: settings (+ Test buttons), library/activity/pending, system, webhooks
  fulfillment/  Radarr/Sonarr direct clients + Overseerr/Jellyseerr client (common interface)
  intake/       IntakeChannel abstraction; Telegram (real), Discord/Slack/WhatsApp (stubs)
  models/       SQLAlchemy: settings sections, requests/events/blocklist, sources+allowlists
  pipeline/     yt-dlp/ffmpeg wrapper, LLM prompt + JSON contract, TMDB client, 6-step pipeline
  services/     settings merge (env<-db), request processor, Connect notification dispatch
backend/tests/  pytest suite — everything mocked, runs with zero live services
frontend/       React+TS Servarr-styled UI (Library, Activity, Pending, Settings, System)
deploy/         docker-stacks style stack file + LiteLLM proxy config
```
