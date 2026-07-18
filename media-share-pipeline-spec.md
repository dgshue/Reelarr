# Build Spec: Social Video → Movie/Show Identifier → Radarr/Sonarr

## Goal
A self-hosted pipeline. I share a video link (Instagram Reel, TikTok, Facebook video) from my phone to a private Telegram bot. The system identifies which movie or TV show the clip is from — using the post's caption/comments first, then audio transcript, then video frames — and adds it to Radarr (movie) or Sonarr (show). Low confidence → ask me to confirm via Telegram inline buttons before adding.

Everything runs in Docker on my existing tower server. Zero paid APIs. Local LLMs via Ollama.

## Existing infrastructure (do NOT recreate — integrate)
- n8n (running, will host the orchestration workflow)
- Ollama (running; assume `http://ollama:11434` on shared Docker network)
- Radarr (`http://radarr:7878`, API v3) and Sonarr (`http://sonarr:8989`, API v3)
- All containers share one Docker network. Use container-name DNS.

## Deliverables
1. **`media-resolver/`** — a new FastAPI service container (Python 3.12) that wraps yt-dlp, ffmpeg, and faster-whisper. This does the heavy lifting; n8n stays thin.
2. **`docker-compose.yml`** snippet adding `media-resolver` to the existing network (with named volume for yt-dlp cookie files and a temp workdir).
3. **`n8n-workflow.json`** — importable n8n workflow implementing the orchestration below.
4. **`README.md`** — setup steps: BotFather token, TMDB API key (free tier), Radarr/Sonarr API keys, Instagram cookies export, env var list.
5. **`.env.example`** with every variable.

## media-resolver API contract
- `POST /metadata` — body `{ "url": "..." }`. Runs `yt-dlp --dump-json --no-download --write-comments` (comments best-effort; don't fail if unavailable). Returns `{ platform, title, description, uploader, hashtags[], top_comments[] (max 25, sorted by likes), duration }`.
- `POST /transcript` — body `{ "url": "..." }`. Downloads video (cap at 720p, max ~5 min), extracts audio with ffmpeg, transcribes with faster-whisper (model size from env `WHISPER_MODEL`, default `small`). Returns `{ transcript, language }`. Clean up temp files after.
- `POST /frames` — body `{ "url": "...", "count": 4 }`. Downloads (or reuses cached download from /transcript within same request TTL), extracts N evenly spaced frames via ffmpeg, returns them base64-encoded JPEGs `{ frames: [b64, ...] }`.
- All endpoints: 120s timeout, structured error JSON, request logging. Support cookies file at `/config/cookies/<platform>.txt` if present (needed for Instagram).

## Orchestration logic (n8n workflow)
1. **Telegram Trigger** — private bot, restrict to my chat ID (env/config).
2. **Extract URL** from message text (regex for instagram.com, tiktok.com, vm.tiktok.com, facebook.com, fb.watch). Invalid → reply with help text, stop.
3. **Reply "🔍 Looking into it..."** immediately.
4. **Tier 1 — metadata:** call `/metadata`. Send caption + hashtags + top comments to Ollama (`OLLAMA_TEXT_MODEL`, default `llama3.2:3b`) with the identification prompt (below). 
5. **Tier 2 — transcript:** if Tier 1 returns UNKNOWN or confidence < high, call `/transcript`, re-prompt LLM with transcript + metadata combined.
6. **Tier 3 — frames (feature-flagged):** if `ENABLE_VISION=true`, call `/frames` and query `OLLAMA_VISION_MODEL` (default `qwen2.5vl:7b`). If `ENABLE_VISION=false`, skip to the manual-confirm path with whatever the best guess is, or "couldn't identify — reply with the title" message.
7. **TMDB lookup:** `GET /search/multi` with the extracted title (+ year if known). Determines movie vs TV and gets `tmdb_id`. For TV, resolve `tvdb_id` via TMDB external IDs endpoint (Sonarr needs tvdbId).
8. **Confidence gate:**
   - High confidence + exact single TMDB match → add directly, then confirm: "✅ Added *Title (Year)* to Radarr/Sonarr".
   - Otherwise → Telegram inline keyboard with top 3 TMDB matches (title, year, movie/TV) + "❌ None of these". Callback handler adds the chosen one.
9. **Add to Radarr:** `POST /api/v3/movie` — tmdbId, qualityProfileId, rootFolderPath from env, `monitored: true`, `addOptions.searchForMovie: true`. Handle 400 "already exists" gracefully ("Already in your library").
   **Add to Sonarr:** `POST /api/v3/series` — tvdbId, same pattern, `addOptions.searchForMissingEpisodes: true`.
10. **Every failure path replies in Telegram** — never fail silently.

## LLM identification prompt (Tier 1/2)
System: "You identify which movie or TV show a social media video clip is from. Respond ONLY with JSON: `{ \"title\": string|null, \"year\": number|null, \"type\": \"movie\"|\"tv\"|null, \"confidence\": \"high\"|\"medium\"|\"low\" }`. Use null title if you cannot identify it. Captions and comments frequently name the title directly — weight explicit mentions heavily. Ignore hashtag spam like #fyp #movie #film unless a specific title is named."
User content: caption, hashtags, top comments (and transcript in Tier 2), clearly labeled.
Parse defensively: strip markdown fences, fall back to UNKNOWN on parse failure.

## Config (env vars)
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`, `TMDB_API_KEY`, `RADARR_URL`, `RADARR_API_KEY`, `RADARR_ROOT_FOLDER`, `RADARR_QUALITY_PROFILE_ID`, `SONARR_URL`, `SONARR_API_KEY`, `SONARR_ROOT_FOLDER`, `SONARR_QUALITY_PROFILE_ID`, `OLLAMA_URL`, `OLLAMA_TEXT_MODEL`, `OLLAMA_VISION_MODEL`, `WHISPER_MODEL`, `ENABLE_VISION`.

## Constraints & notes
- Cost: $0/month. No OpenAI/Anthropic/cloud APIs. TMDB free tier only.
- Instagram frequently requires cookies — document the browser-export process in README; service must work without cookies for TikTok.
- Temp downloads deleted after each request; cap disk use.
- Keep n8n workflow modular: each tier is a distinct branch so tiers can be tested independently.
- Write basic tests for media-resolver endpoints (mock yt-dlp).
- Future (do not build yet, but don't preclude): Signal via signal-cli-rest-api as an alternate trigger.
