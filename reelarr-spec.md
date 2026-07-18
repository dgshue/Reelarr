# Build Spec: Reelarr

Self-hosted *arr-family app. Share a social video link (Instagram Reel, TikTok, Facebook video) via
Telegram, Discord, Slack, or WhatsApp → AI identifies the movie/show → adds it to Radarr or Sonarr (or
routes to Overseerr/Jellyseerr for approval). Low confidence → inline-button (or numbered-reply)
confirmation on whichever channel the request came from, before adding.

This supersedes `media-share-pipeline-spec.md`. That spec's identification logic (tiers, prompt,
metadata/transcript/frame extraction) carries forward — what changes is the shape of the app: instead
of n8n orchestrating a thin FastAPI helper, Reelarr is a single self-contained Servarr-styled
application, matching how Radarr/Sonarr/Prowlarr/Bazarr are built and used.

## Why not n8n

n8n was fine as a quick orchestrator but left the project with no real UI (Telegram inline buttons
were the only interface), no persisted history, and workflow logic that's awkward to version/test
compared to code. Building it as a standalone app puts Reelarr on equal footing with the rest of the
stack: a proper settings UI, a request/history database, and something that looks and behaves like
"one more arr app" rather than a bolt-on automation.

## Existing infrastructure (do NOT recreate — integrate)

- Ollama (running; `http://ollama:11434` on the shared Docker network) — default/local AI backend
- Radarr (`http://radarr:7878`, API v3) and Sonarr (`http://sonarr:8989`, API v3)
- Overseerr/Jellyseerr — optional; not assumed running, but supported as an alternate fulfillment
  target if the user has one (see §5.5)
- All containers share one Docker network; use container-name DNS

---

## 1. Look and feel — cloning the Servarr UI language

Radarr/Sonarr/Prowlarr/Bazarr share no actual UI component library (confirmed — no such package
exists), but they converge on the same conventions. Reelarr should hit these deliberately rather than
inventing its own idiom:

**Navigation** (mapped from Radarr/Sonarr's actual routes):

| Servarr tab | Reelarr equivalent |
|---|---|
| Movies / Series (primary grid) | **Library** — grid/table of identified clips, poster art once TMDB-matched |
| Calendar | *(skip — not applicable to this domain)* |
| Activity → Queue / History / Blocklist | **Activity** → Queue (in-flight identifications) / History (resolved) / Blocklist (dismissed links) |
| Wanted → Missing / Cutoff Unmet | **Pending Confirmation** — low-confidence items awaiting a reply on whichever channel they came in on |
| Settings → Media Management, Profiles, Quality, Indexers, Download Clients, Import Lists, Connect, Metadata, Tags, General, UI | Settings → **Fulfillment** (Radarr/Sonarr direct connection, *or* Overseerr/Jellyseerr connection — §5.5), **Identification** (AI pipeline config — §5), **Sources** (Telegram, Discord, Slack, WhatsApp intake channels — §4), **Connect** (outbound notifications, same idiom as Radarr), **Metadata** (TMDB key), Tags, General, UI |
| System → Status, Tasks, Backup, Updates, Events, Log Files | **System** → same six sub-pages, same behavior |

**Design tokens** (pulled from Radarr's actual theme source, `Styles/Themes/dark.js`):
- Dark theme default (with Light + Auto options, plus a color-impaired mode toggle) — Settings → UI
- Background `#202020`, panels `#2a2a2a`, text `#ccc`, success `#27c24c`, danger `#f05050`, warning `#ffa500`
- Reelarr claims its **own accent color** (not `#5d9cec` blue/Radarr, not Sonarr's blue, not Prowlarr's purple) — pick something distinct, e.g. a coral/teal, for brand identity within the stack
- Font stack: `Roboto, "Open Sans", "Helvetica Neue", Helvetica, Arial, sans-serif`; monospace `"Ubuntu Mono", Menlo, Monaco, Consolas, monospace` for logs
- Icons: Font Awesome (solid set), 12–16px inline glyphs for status/action

**UX patterns to replicate exactly** (these read as "native" more than colors do):
- **Test button** on every external connection form — see §2 for the full per-integration breakdown, including which ones live-populate dependent dropdowns the way Seerr's Radarr/Sonarr test does.
- **No toast notifications** — health/config issues show as a colored count badge on the System sidebar item, linking to System → Status.
- **API key**: plain-text read-only field, auto-select on focus, Copy button, red "Reset" behind a confirm modal, "requires restart" note.
- **Auth**: Forms login (default) with a "disabled for local/LAN addresses" toggle; API key auth for programmatic/webhook calls regardless of UI auth mode.
- **URL Base** setting for reverse-proxy subpath deployment; document the WebSocket-upgrade-header gotcha for real-time updates through nginx/Caddy.
- **Backup**: manual "Backup Now" (kept forever) + scheduled backups (folder/interval/retention), restore via upload.
- **Updates**: since this ships Docker-first, skip the built-in self-updater — health check just flags "newer image available," matching how Docker deployments of the *arr apps are typically run.
- **Logging**: Info/Debug/Trace level dropdown, rotating log files capped and counted like Radarr's, separate Events tab for structured activity events.

---

## 2. Setup wizard & validation

Priority: someone who isn't the person who built this should be able to stand it up without reading
source code. The bar is Seerr's (Overseerr/Jellyseerr's successor) first-run wizard, confirmed against
its actual source — this is the strongest "easy setup" precedent in the ecosystem and Reelarr should
copy its mechanics directly, not just its spirit.

**Seerr's pattern, verified from source** (`RadarrModal/index.tsx`, `server/routes/settings/radarr.ts`):
a single **Test** call does double duty — it validates the connection *and*, on success, returns
dependent data (quality profiles, root folders, tags) in the same response payload, which populates
previously-disabled `<select>` dropdowns. There is no free-text entry for anything the target service
can enumerate itself. **Save stays disabled until `isValidated === true`.** By contrast, Radarr/Sonarr's
*own* download-client/indexer onboarding is more primitive — plain pass/fail Test, free-text category
fields — which is worth improving on, not copying.

**Wizard flow for Reelarr**, following Seerr's hard-gate/soft-gate split (auth-critical steps block
progress; optional integrations don't):

1. **AI backend** (hard gate — nothing works without this): confirm the LiteLLM proxy is reachable and
   the configured local model is actually pulled in Ollama (a very common failure mode is "model
   configured but never `ollama pull`ed" — surface that distinctly from "can't reach Ollama at all").
2. **TMDB key** (hard gate): validate against a lightweight endpoint.
3. **Fulfillment target** (hard gate — at least one required): Radarr/Sonarr direct, with Test
   live-populating root folder and quality profile dropdowns exactly like Seerr's Radarr modal; or
   Overseerr/Jellyseerr, with Test validating connectivity/auth/version only (Seerr owns its own
   downstream config, nothing to populate on Reelarr's side).
4. **At least one Source** (hard gate — at least one intake channel required, see §4): each channel's
   Test does a real round-trip appropriate to that channel (Telegram/Discord: fetch bot identity +
   let the user pick a channel/chat from a live-fetched list; Slack: `auth.test` + channel list; WhatsApp:
   surface the Evolution API QR code in-wizard and confirm the session reaches "paired").
5. **Connect notification targets** (soft gate, skippable): configure later from Settings → Connect.

No elaborate "you're all set!" completion screen — Seerr doesn't have one and it isn't missed; finish
the last hard-gated step and drop straight into the Library view.

**Test-button behavior by integration** (what Test actually checks, and what it live-populates):

| Integration | Test validates | Live-populates |
|---|---|---|
| TMDB | API key auth | — |
| Radarr / Sonarr | connectivity, auth, version compatibility | root folders, quality profiles, tags |
| Overseerr / Jellyseerr | connectivity, auth, version compatibility | — |
| Ollama / LiteLLM | connectivity + presence check for the configured model | available model list (dropdown instead of free-text model name) |
| Commercial LLM key (via LiteLLM) | auth via a minimal request | model list, where the provider exposes one |
| STT backend (Speaches/whisper wrapper, or commercial) | connectivity + model loaded | — |
| Telegram | bot token valid (`getMe`) | — (chat ID allowlist is entered directly; there's no "list of chats" to fetch for a bot that hasn't been messaged yet) |
| Discord | bot token valid | guild list → channel dropdown per guild |
| Slack | token valid (`auth.test`) | workspace channel list |
| WhatsApp (Evolution API) | instance reachable, session paired (post-QR-scan) | — (session status only) |
| Connect notification target | sends a real test notification | — |
| Instagram cookies file | present + parses; best-effort live check via a known public reel | — |

---

## 3. Tech stack

Backend language doesn't need to match Radarr's C#/.NET — the "native" feel comes from the frontend
and UX conventions above, not the server language. Given this is an AI/ML-heavy service (yt-dlp,
ffmpeg, faster-whisper, LLM calls, several chat-bot SDKs), Python is the better fit and lets the
existing `media-share-pipeline-spec.md` groundwork carry forward directly.

- **Backend**: Python 3.12, FastAPI, SQLite (via SQLAlchemy) for request/history/config storage
- **Frontend**: React + TypeScript, CSS custom properties matching the token set in §1, Font Awesome icons
- **Real-time updates**: WebSocket (FastAPI native) or Socket.IO — Radarr/Sonarr use SignalR, which is
  .NET-specific; a plain WebSocket channel pushing queue/history state changes to the UI covers the
  same UX (live queue updates without polling)
- **Chat-channel SDKs**: `python-telegram-bot`, `discord.py`, `slack-bolt` (Socket Mode — no public
  HTTPS endpoint required, matches a homelab setup with no reverse proxy), and an `httpx` client
  against a self-hosted **Evolution API** instance for WhatsApp (§4)
- **Packaging**: single Docker image (or two: `reelarr` app + optional bundled `reelarr-worker` for the
  heavier yt-dlp/whisper/ffmpeg jobs if we want to isolate that load — decide during implementation
  planning, not now)
- **Distribution**: Docker Compose service on the existing network, following the same pattern as
  Radarr/Sonarr. Evolution API ships as its own optional compose service/profile (`--profile whatsapp`)
  since it's only needed if WhatsApp is enabled — see §4 for why it's opt-in.

---

## 4. Sources — multi-channel intake

Telegram alone was the original spec's assumption. Reelarr should treat "where a link came from" as a
pluggable `IntakeChannel` abstraction — receive(url, sender ref) → normalized event; send_text(chat
ref); send_confirmation(chat ref, candidates) → a channel-appropriate interactive prompt — with
multiple channels active simultaneously, each independently configured in **Settings → Sources** with
its own allowlist (chat ID / user ID / channel ID / phone number), generalizing the original spec's
`TELEGRAM_ALLOWED_CHAT_ID` pattern per channel. **Botdarr** is direct prior art for one bot core serving
Telegram/Discord/Slack/Matrix simultaneously — though it has no dedicated server or settings UI, which
is exactly what Reelarr adds on top of that shape.

**Telegram** — unchanged from the original spec. Bot token via BotFather, native inline-keyboard
buttons for confirmation, no practical limits.

**Discord** — official Bot API via the Developer Portal, no ban risk. Native message components
(buttons, up to 5 per row / 25 total) handle the confirmation flow cleanly. Test fetches the bot's
guild list live, so setup picks a channel from a dropdown instead of typing an ID.

**Slack** — official Bot API. Recommend **Socket Mode**, which doesn't require a publicly reachable
HTTPS endpoint — the right call for a homelab box behind no reverse proxy. Block Kit interactive
buttons handle confirmation (10 actions per block, no practical limit here either). Test does
`auth.test` and fetches the channel list.

**WhatsApp — opt-in, explicitly marked "advanced/unofficial" in the UI.** WhatsApp has no open bot
protocol; the official Meta Cloud API is Meta-hosted (not self-hosted in any real sense) and requires a
Business Manager account and phone verification. For "full local control," the pragmatic homelab
choice is **Evolution API** — a self-hosted, Apache-2.0, Docker-native wrapper around the unofficial
Baileys library, with a dual-engine design (can also proxy the official Cloud API per instance if a
user prefers that route). It exposes a webhook for inbound messages (`messages.upsert` events) and a
simple REST send API — a clean fit for the `IntakeChannel` abstraction, except it's session-based (QR
pairing) rather than a stateless bot token, so the channel needs a re-pair/health-check path the others
don't.

Two things worth being explicit about in the README and setup UI, since this path is meaningfully
riskier than the other three:
- **Ban risk is real and reportedly getting worse** — accounts stable for years have been banned in
  clusters; mitigations are a dedicated secondary number, conservative send rates, and avoiding known
  trigger endpoints (Evolution API's bulk-lookup call specifically). This is why WhatsApp should default
  **off** and ship as an opt-in compose profile, not bundled into the base stack like the other three.
- **Native interactive buttons are unreliable on the unofficial (Baileys) engine** — WhatsApp's
  button-message format isn't well-supported outside unaudited community forks (a trojanized fork,
  `lotusbail`, was caught exfiltrating credentials in Dec 2025 — only use Evolution's canonical
  package). Confirmation on WhatsApp should fall back to **numbered text replies** ("Reply 1, 2, or 3")
  rather than relying on native buttons that may silently fail to render.

---

## 5. AI provider architecture

Requirement: fully local/self-hosted must work end-to-end with **zero paid APIs** for all real
testing, while allowing commercial providers as optional, per-component alternatives.

**Recommendation: run a self-hosted [LiteLLM](https://github.com/BerriAI/litellm) proxy** in front of
Ollama plus any configured commercial keys. Reelarr's own code speaks exactly one dialect —
OpenAI-style `/v1/chat/completions` (including vision content blocks) and `/v1/audio/transcriptions` —
against LiteLLM's single endpoint. LiteLLM already normalizes the two real incompatibilities in this
space (Anthropic's `x-api-key` auth header, Deepgram's non-OpenAI transcription shape), supports
local-first/commercial-fallback chains, and gives Reelarr's settings UI the same
"base URL + API key + model name + Test button" idiom as every other Servarr connection form —
consistent with §1/§2's UX conventions, not a special case.

**Add `litellm` (proxy mode) as a new service to the Docker network.**

**Default local models** (via Ollama, already running):
- Text reasoning/classification: **Qwen3** 4B–8B
- Vision (Tier 3 fallback): **Qwen2.5-VL 7B** (or `qwen3-vl:8b`)
- Speech-to-text: **faster-whisper**, fronted by an OpenAI-compatible wrapper (**Speaches**, or
  whisper.cpp's `--inference-path` remap) so it's just another LiteLLM backend like everything else —
  this keeps the all-local test path and the optional-commercial path running through identical code.

**Cheapest/easiest optional commercial add-ons per component** (all OpenAI-compatible, minimal
integration cost via LiteLLM):
- Text: **Groq** (fast, cheap) or **Gemini Flash**
- Vision: **Gemini Flash** or **Mistral Pixtral/Large 3**
- STT: **Groq-hosted Whisper large-v3-turbo** — ~9x cheaper than OpenAI's own Whisper API, notably fast, zero adapter work

Keep a thin internal interface (`TextLLMClient`, `VisionLLMClient`, `SttClient`) so a single component
could bypass LiteLLM later if ever needed, without a rewrite.

**Identification pipeline** — unchanged from the original spec:
1. **Tier 1 (metadata)**: yt-dlp metadata (caption, hashtags, top comments) → text LLM
2. **Tier 2 (transcript)**: if UNKNOWN or confidence < high → ffmpeg audio extraction → STT → re-prompt with transcript + metadata
3. **Tier 3 (frames, feature-flagged)**: if still unresolved and `ENABLE_VISION=true` → extract N evenly-spaced frames → vision LLM
4. TMDB `/search/multi` lookup (+ TVDB external-ID resolution for TV, since Sonarr needs `tvdbId`)
5. **Identification-confidence gate**: high + single exact match → proceed straight to fulfillment; otherwise → confirmation prompt (inline buttons or numbered reply, per §4) on the originating channel with the top 3 matches + "None of these" — this gate answers "is this the right title," independent of §5.5's fulfillment gate
6. **Fulfillment** — see §5.5, routes to either Radarr/Sonarr directly or Overseerr/Jellyseerr for approval

System prompt, JSON contract, and defensive parsing carry forward unchanged from
`media-share-pipeline-spec.md` §"LLM identification prompt", except for the
multi-title extension in §5.4.

### 5.4 Multi-title clips (listicles / "top 10" posts)

A large share of film-related social content isn't one clip from one film — it's
a countdown ("5 mind-bending movies you need to watch", "top 10 horror films of
the decade"), a versus post, or a slideshow. These are arguably the *highest*
value content type for Reelarr, since one shared link can become several library
adds. Measured behavior of the original single-title contract on these (real
qwen3:8b runs, 2026-07-18):

| Input | Result under single-title contract |
|---|---|
| "5 mind-bending movies: Inception, Shutter Island, Memento, Donnie Darko, Primer" | `title: null, confidence: low` → gives up entirely |
| "This scene from Heat is unmatched" + comments naming Sicario, The Town | correctly picked **Heat**, ignored the distractors ✅ |
| "Blade Runner vs Blade Runner 2049 — which ending hits harder?" | `title: null, confidence: low` → gives up |

The distractor case is handled well and must not regress. The failure mode is
*safe* (never a wrong add) but unhelpful: the JSON contract has exactly one
`title` slot, so a listicle has no way to express itself.

**Contract change**: identification returns a *list* of candidate
identifications rather than a single one. Outcomes become:

- **1 title, high confidence** → auto-add (today's behavior, unchanged)
- **N titles** → multi-select confirmation on the originating channel (below)
- **0 titles** → today's "couldn't identify — reply with the title" path

**Use the caption's own stated count as a prior.** If the title/caption says
"top 10" / "5 movies" / "part 3 of ranking every…", that's a strong signal for
how many identifications to expect, and a way to tell the user "the post claims
10, I could only identify 7" — far more useful than silently returning 7. Feed
the stated count into the prompt and surface any shortfall in the reply.

**Cap** the number of titles offered (default ~10) so a "top 50" post can't dump
50 adds into Radarr; state plainly when results were truncated rather than
silently dropping them (per §1's "no silent caps" principle).

**Multi-select UX differs by platform** — the `IntakeChannel` abstraction (§4)
grows a `send_multi_select()` alongside `send_confirmation()`:

- **Discord**: native support — a string select menu (`min_values`/`max_values`,
  max 25 options) gives real checkbox semantics in a single interaction, plus a
  confirm button.
- **Telegram**: no native multi-select. Simulate with toggle buttons that edit
  the message in place (`☐ Inception` → tap → `☑ Inception`) and an
  "➕ Add selected (N)" button. Requires holding per-request selection state
  between callbacks — persist it on the request row, not in memory, so it
  survives a restart.
- Both get **Add all** and **None of these**. "Add all" writes several items at
  once, so it re-prompts for confirmation before firing.
- **WhatsApp** (when enabled): falls back to numbered replies ("Reply 1,3,5"),
  consistent with §4's note that native buttons are unreliable there.

### 5.5 Fulfillment target — Radarr/Sonarr direct, or via Overseerr/Jellyseerr

Two independent gates exist in this flow, and they shouldn't be conflated:

- **Identification confidence** (§5 step 5) — *is this the right movie/show?* Always Reelarr's job,
  regardless of fulfillment target or which channel the request came from.
- **Approval to add** — *should this be added to the library at all?* Optional, and delegated entirely
  to Overseerr/Jellyseerr when that target is selected, rather than reimplemented in Reelarr.

Configured once in **Settings → Fulfillment**, with the same connection-form + Test-button idiom as
every other Servarr integration (§1/§2):

- **Direct** (default, matches the original spec): once identification is confirmed, `POST
  /api/v3/movie` (Radarr) or `POST /api/v3/series` (Sonarr), handling "already exists" gracefully.
  Requires the Radarr/Sonarr URL + API key, with root folder/quality profile/tags live-populated per
  §2's Test-button table.
- **Via Seerr**: once identification is confirmed, `POST /api/v1/request` on the configured
  Overseerr/Jellyseerr instance (`mediaType`, `tmdbId`, and `seasons` for TV), instead of touching
  Radarr/Sonarr directly. Seerr already owns the Radarr/Sonarr wiring, root folders, and quality
  profiles on its side, plus its own approval queue and per-user request permissions — so this mode
  only needs a Seerr URL + API key in Reelarr, nothing else duplicated. Handle "already
  requested"/"already available" responses the same way as Radarr/Sonarr's "already exists."

This makes Seerr a first-class fulfillment peer of Radarr/Sonarr, not a bolt-on: households running a
shared bot can require admin approval before anything lands in the library, while a single-user setup
can skip straight to Direct. (v2 possibility, not required for v1: let this be set per media type —
e.g. movies go direct, TV requires approval — rather than one global toggle.)

---

## 6. Integrations

Derived from surveying the *arr companion ecosystem (Bazarr, Seerr/Overseerr/Jellyseerr, Requestrr,
Doplarr, Searcharr, Autobrr, Huntarr, Notifiarr, Recyclarr, Unpackerr, Cleanuparr, Buildarr, Prowlarr,
Tautulli, Botdarr) for what "feeling like part of the stack" actually requires.

**v1 — must have:**
- Fulfillment layer with two peer targets: Radarr/Sonarr direct-add, or Overseerr/Jellyseerr (Seerr)
  for approval-gated requests — see §5.5.
- Multi-channel intake: **Telegram, Discord, Slack** as first-class, always-available Sources; **WhatsApp**
  as an opt-in/advanced Source behind its own compose profile — see §4.
- Connect notification targets (outbound, separate config from the Sources above even where the
  platform overlaps, e.g. Discord/Slack/Telegram are both a Source *and* independently configurable as
  a Connect target): **Discord, generic Webhook, Pushover, Slack, ntfy, Gotify**
- **Apprise** passthrough as a single Connect target — covers 100+ additional services (SMS gateways,
  Matrix, dozens of push services) without maintaining bespoke connectors per service; Huntarr and
  Notifiarr both lean on this instead of building N integrations

**v1 — nice to have:**
- Post-add library-refresh trigger to Plex/Jellyfin/Emby (the "Autopulse" pattern) — mainly relevant
  to the Direct fulfillment path, since Seerr triggers its own refresh once it fulfills the request
- Role/permission-based request quotas if multiple people share a channel

**v2 — differentiators (no existing ecosystem precedent — genuine white space):**
- Inbound generic webhook trigger, so Home Assistant / n8n / iOS Shortcuts / a browser bookmarklet can
  feed Reelarr a link as an additional Source
- Browser extension / mobile share-sheet intake (precedent exists for IMDb/TMDb links via
  Pulsarr/Magnetarr; nobody's done it for social-clip URLs)
- Signal intake via signal-cli-rest-api (flagged in the original spec as future — confirmed there's
  zero ecosystem precedent for this, so it'd be a genuine first)
- Streaming-availability dedup before adding (Searcharr-Plus does this for manual text requests;
  nobody does it for AI-identified clips)

---

## 7. Deliverables

1. **`reelarr/`** — the self-contained app: FastAPI backend (identification pipeline, Radarr/Sonarr
   clients, Overseerr/Jellyseerr client, Telegram/Discord/Slack bots + Evolution API client, Connect
   notification dispatch, SQLite models) + React frontend (Servarr-styled UI per §1, setup wizard per §2)
2. **`docker-compose.yml`** snippet adding `reelarr` (+ `litellm` proxy, + optional `evolution-api`
   under a `whatsapp` profile) to the existing network, named volumes for cookie files, temp workdir,
   and the SQLite db
3. **`README.md`** — setup steps: BotFather token, Discord app + bot token, Slack app (Socket Mode)
   setup, Evolution API WhatsApp pairing (with the ban-risk callout from §4), TMDB API key, Radarr/Sonarr
   API keys, Instagram cookie export, LiteLLM/provider configuration, env var list
4. **`.env.example`** with every variable
5. Basic tests for the identification pipeline and Radarr/Sonarr clients (mock yt-dlp, mock LiteLLM
   endpoint, mock each Source's SDK) — must pass fully against local Ollama with zero paid API calls,
   since that's the only path CI will exercise

## Constraints & notes (carried forward)

- Local-only testing must produce a fully working system — zero required paid APIs
- Instagram frequently requires cookies; must work without cookies for TikTok
- Temp downloads deleted after each request; cap disk use
- Write basic tests for the identification pipeline (mock yt-dlp / mock LLM endpoint)
- Repo/folder is currently `Findarr` on disk — needs renaming to `Reelarr` before first commit, since
  "Findarr" collides with an unrelated existing GitHub project (`Shiqan/Findarr`). Attempted and
  currently **blocked**: both shells report the folder is in use by another process (likely File
  Explorer, an editor/IDE, or a sync client with it open) — retry once that's closed.
