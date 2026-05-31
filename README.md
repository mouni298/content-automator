# content-automator

Automated daily AI history & mythology Shorts: research a topic from Wikipedia,
write a narrated script with Claude, assemble an animated image-frame video
(Ken Burns motion + voiceover + burned-in captions + music), send it to you on
Telegram for one-tap approval, then publish to YouTube (Instagram = phase 2).

## Why image frames, not AI video

For history/mythology, real public-domain art, maps, and artifacts look more
*authentic* than generated video, cost ~$0.05/clip instead of $1-5, and never
hallucinate anachronisms. FFmpeg adds pan/zoom/crossfades so it feels like video.

> **2026 reality check:** YouTube demonetized thousands of faceless AI channels
> under its "inauthentic content" policy. Only low-effort, mass-produced content
> is penalized. This pipeline is built for the opposite: **1 high-effort,
> original, well-researched, human-reviewed video per day.**

## Agentic pipeline (Google ADK)

Built on **Google ADK** (Agent Development Kit). LLM agency is concentrated and
guard-railed: the Director decides, deterministic code renders, and a human (you)
approves. Every LLM step has a fallback so a flaky model never blocks a video.

```
cron (daily) -> python -m src.main
  0. series?      - if a series (e.g. Mahabharata) is active, make its NEXT part
                    with continuity (story_so_far); else topic_picker (On This Day/queue)
  1. DirectorAgent - ADK LlmAgent: tool-calls get_strategy_hints / get_series_state,
                     commits a StyleProfile via set_style_profile (tone, voice, music
                     mood, visual_strategy). Mythology -> AI art; history -> real images.
  2. ProductionAgent (deterministic) renders:
       research   - Wikipedia -> Gemini script (+ style/continuity/feedback steering)
       assets     - real Wikimedia images OR free AI art (Pollinations/FLUX)
       tts        - edge-tts natural voice (style voice/rate)
       captions   - faster-whisper timing -> Pillow PNG overlays (no libass)
       assemble   - FFmpeg Ken Burns + mood music + captions -> 1080x1920 mp4
  3. review        - Telegram: Approve / Reject(+feedback)   <-- human gate
  4. publish       - on Approve -> YouTube Short (+researched trending tags);
                     series continuity advances; analytics later feeds learning
```

Feedback loop: Reject → reply with notes → it classifies (wording/visuals/both),
regenerates (reusing images for wording-only), and re-sends. Capped by `max_regens`.

Learning loop (`src/analytics.py`, dormant until you have views): pulls per-video
retention via the YouTube Analytics API → `strategy_memory` → the Director biases
future choices toward what performs.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# system deps
brew install ffmpeg
# Piper TTS voice: download a voice .onnx + .json into assets/voices/
#   https://github.com/rhasspy/piper  (e.g. en_US-lessac-medium)

cp .env.example .env   # fill in keys
```

Required keys (see `.env.example`):
- `GEMINI_API_KEY` - script generation (Gemini 2.0 Flash, free tier). Get one at
  https://aistudio.google.com/app/apikey . Swappable via `src/research.py`.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - review gate
- YouTube OAuth client-secret JSON - phase 1 publish

## Run

```bash
# generate one video, stops at the Telegram review gate
python -m src.main

# the Telegram bot's Approve button triggers publishing
python -m src.review   # long-running listener that publishes approved videos
```

Cron (daily 6 AM):
```
0 6 * * * /path/to/content-automator/scripts/run_daily.sh >> /path/to/logs/daily.log 2>&1
```

### Series (post a big topic in daily parts)
```bash
python -m src.series seed --slug mahabharata --title "The Mahabharata" \
    --wiki "Mahabharata" --parts 12
python -m src.series list
```
While a series is `active`, the daily run generates its next part with continuity,
advancing only when you Approve. Omit `--parts` for an open-ended series.

### Learning loop (turn on after you have views)
Set `analytics.enabled: true` in `config.yaml`, then weekly:
```bash
python -m src.analytics run    # first run opens a one-time analytics OAuth consent
```

## Cost (target <$30/mo, realistic ~$5)

| Item | Cost |
|------|------|
| Gemini 2.0 Flash scripts | $0 (free tier) |
| Piper TTS | $0 (local) |
| Wikimedia images | $0 |
| faster-whisper captions | $0 (local) |
| Hosting | $0 (your Mac) or ~$5 VPS |

## Roadmap

- [x] v1: YouTube Shorts, natural edge-tts voice, Telegram review
- [x] AI image generation (Pollinations/FLUX) for image-poor topics (mythology)
- [x] Agentic rebuild on Google ADK: Creative Director, tool-calling
- [x] Series memory (multi-part topics with continuity)
- [x] Feedback → regenerate loop in Telegram
- [x] Reach: researched trending hashtags + mood-matched music
- [x] Learning loop scaffold (YouTube Analytics → strategy memory; dormant)
- [ ] Activate learning loop once the channel has views
- [ ] Daily scheduling as a service (launchd/cron + keep review listener alive)
- [ ] Instagram Reels (Graph API - needs Business acct + FB Page + app review)
- [ ] Add royalty-free tracks under assets/music/{epic,somber,mysterious,triumphant}/

## Reference projects (mined, not copied)

- MoneyPrinterTurbo (harry0703) - pipeline, TTS handling, caption burn
- ShortGPT (RayVentura) - modular editing engine
