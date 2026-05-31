"""Creative Director — an ADK LlmAgent that decides the per-video style.

It genuinely uses tools: it calls get_strategy_hints / get_series_state to inform
its choice, then commits the decision by calling set_style_profile (which persists
the profile and writes it to session.state["style"]). A deterministic fallback
guarantees a style is always set even if the model misbehaves.
"""
from __future__ import annotations

import json
import re

from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext

from .config import cfg
from . import db
from .style import StyleProfile
from .tools import get_strategy_hints, get_series_state

INSTRUCTION = """You are the creative director for a faceless world-history & \
mythology YouTube Shorts channel. From the topic in the user message, decide the \
best creative approach for ONE short.

Steps (use the tools):
1. Call get_strategy_hints to see what has performed well; bias toward it.
2. If the topic looks like part of a multi-part series (e.g. an epic like the \
Mahabharata), call get_series_state with a slug guess to reuse the locked style.
3. Call set_style_profile EXACTLY ONCE with your final decision, then stop.

Guidance:
- Mythology / legends / epics (gods, Ramayana, Mahabharata, Norse/Greek myth): \
there are no real photographs, so visual_strategy='ai-art', music_mood='epic', a \
grand dramatic tone, voice_rate around '-10%'.
- CRITICAL — art_style_prompt MUST match the topic's OWN culture and era. Never \
apply one culture's art to another. Examples:
    * Hindu epics (Mahabharata, Ramayana, Puranas) -> 'classical Indian miniature \
painting, Pichwai/Tanjore style, rich detail'
    * Greek/Roman myth -> 'classical Greco-Roman fresco / red-figure pottery style'
    * Norse/Germanic -> 'Norse saga manuscript / medieval Scandinavian art'
    * Egyptian -> 'ancient Egyptian tomb painting style'
  If unsure of the culture, use 'period-accurate painting faithful to the culture \
of the topic' rather than guessing a specific (possibly wrong) tradition.
- Documented history (real people, places, events with surviving art/photos): \
visual_strategy='real', music_mood 'somber' or 'mysterious', measured tone.
- voice: an edge-tts voice id; default 'en-US-ChristopherNeural'.
- pacing: one of slow|medium|fast.
Output no prose — your deliverable is the tool calls."""


def set_style_profile(genre: str, tone: str, voice: str, voice_rate: str,
                      music_mood: str, visual_strategy: str, art_style_prompt: str,
                      pacing: str, tool_context: ToolContext) -> dict:
    """Commit the final creative decision for this video. Call this exactly once
    after researching. visual_strategy must be 'real', 'ai-art', or 'mixed'."""
    prof = StyleProfile(genre=genre, tone=tone, voice=voice, voice_rate=voice_rate,
                        music_mood=music_mood, visual_strategy=visual_strategy,
                        art_style_prompt=art_style_prompt, pacing=pacing)
    iso = tool_context.state.get("iso_now", "")
    topic_name = (tool_context.state.get("topic") or {}).get("topic", "")
    pid = db.insert_style_profile(
        scope=f"topic:{topic_name}", created_at=iso, genre=genre, tone=tone,
        voice=voice, voice_rate=voice_rate, music_mood=music_mood,
        visual_strategy=visual_strategy, art_style_prompt=art_style_prompt,
        pacing=pacing, profile_json=json.dumps(prof.to_dict()))
    prof.profile_id = pid
    tool_context.state["style"] = prof.to_dict()
    return {"status": "ok", "profile_id": pid, "visual_strategy": visual_strategy}


# ---- deterministic fallback (never let a missing/failed decision block a video) ----

# substring matches (no trailing \b — it would fail on "Mahabharat-a", "Ramayan-a")
_MYTH = re.compile(r"(myth|legend|deity|goddess|\bgod\b|ramayan|mahabharat|purana|"
                   r"norse|odin|thor|zeus|olympu|valhalla|pantheon|krishna|arjuna|"
                   r"pandava|kaurava|hindu epic|\bepic\b|saga|folklore|\bgods\b)", re.I)


def _guess_genre(topic_name: str) -> str:
    return "mythology" if _MYTH.search(topic_name or "") else "documented-history"


def _fallback_profile(genre: str, iso_now: str = "") -> dict:
    """Build a deterministic genre style and persist it, so every video records a
    style_profile_id (the learning loop attributes performance by profile)."""
    fb = cfg().get("director", {}).get("fallbacks", {})
    base = fb.get(genre) or fb.get("documented-history") or {}
    prof = StyleProfile(genre=genre, tone=base.get("tone", ""),
                        voice=base.get("voice", "en-US-ChristopherNeural"),
                        voice_rate=base.get("voice_rate", "-8%"),
                        music_mood=base.get("music_mood", ""),
                        visual_strategy=base.get("visual_strategy", "mixed"),
                        art_style_prompt=base.get("art_style_prompt", ""),
                        pacing=base.get("pacing", "medium"))
    try:
        prof.profile_id = db.insert_style_profile(
            scope=f"genre:{genre}", created_at=iso_now, genre=genre, tone=prof.tone,
            voice=prof.voice, voice_rate=prof.voice_rate, music_mood=prof.music_mood,
            visual_strategy=prof.visual_strategy, art_style_prompt=prof.art_style_prompt,
            pacing=prof.pacing, profile_json=json.dumps(prof.to_dict()))
    except Exception:
        pass  # persistence is best-effort; a missing id never blocks a video
    return prof.to_dict()


def _ensure_style(callback_context):
    """after_agent_callback: guarantee session.state['style'] exists even if the
    model never called set_style_profile."""
    st = callback_context.state
    if not st.get("style"):
        genre = _guess_genre((st.get("topic") or {}).get("topic", ""))
        st["style"] = _fallback_profile(genre, st.get("iso_now", ""))
        print(f"  [director] fallback style applied (genre={genre})")


def build_director_agent():
    return LlmAgent(
        name="director",
        model=cfg()["director"]["model"],
        instruction=INSTRUCTION,
        tools=[get_strategy_hints, get_series_state, set_style_profile],
        after_agent_callback=_ensure_style,
    )
