"""ADK runner glue. Builds the generation agent graph and drives it to completion,
returning the produced videos.id. Entry points (main.py / review.py) call the
synchronous wrappers; clock is passed in as iso_now.

Phase 0: root = SequentialAgent([ProductionAgent]). Phase 1 inserts DirectorAgent
in front; Phase 2 adds a regeneration LoopAgent.
"""
from __future__ import annotations

import asyncio

from google.adk.agents import SequentialAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from .config import cfg
from . import db
from .agents import ProductionAgent
from .topic_picker import Topic

APP_NAME = "content-automator"


def build_generation_agent():
    subs = []
    # Phase 1: DirectorAgent is prepended here when director.enabled.
    try:
        if cfg().get("director", {}).get("enabled"):
            from .director import build_director_agent
            subs.append(build_director_agent())
    except Exception as e:  # director optional; never block generation
        print(f"  [director] disabled ({e})")
    subs.append(ProductionAgent(name="production"))
    return SequentialAgent(name="generation", sub_agents=subs)


async def _drive(root, state: dict) -> int | None:
    runner = InMemoryRunner(agent=root, app_name=APP_NAME)
    await runner.session_service.create_session(
        app_name=APP_NAME, user_id="local", session_id="gen", state=state)
    msg = types.Content(role="user", parts=[types.Part(text="generate")])
    video_id = state.get("video_id")
    async for ev in runner.run_async(user_id="local", session_id="gen", new_message=msg):
        delta = getattr(ev.actions, "state_delta", None) if ev.actions else None
        if delta and delta.get("video_id"):
            video_id = delta["video_id"]
    return video_id


def _topic_state(topic: Topic) -> dict:
    return {"topic": topic.topic, "angle": topic.angle,
            "wikipedia_title": topic.wikipedia_title}


def _production_only_agent():
    return SequentialAgent(name="generation", sub_agents=[ProductionAgent(name="production")])


def run_generation(topic: Topic, *, iso_now: str, series_id: int | None = None,
                   part_no: int | None = None, story_so_far: str = "",
                   style: dict | None = None) -> int | None:
    state = {
        "topic": _topic_state(topic),
        "iso_now": iso_now,
        "story_so_far": story_so_far,
        "series_id": series_id,
        "part_no": part_no,
        "style": style,
    }
    # A locked style (e.g. a series' established look) skips the Director entirely
    # for visual/tonal consistency across parts.
    if style is not None:
        return asyncio.run(_drive(_production_only_agent(), state))
    try:
        return asyncio.run(_drive(build_generation_agent(), state))
    except Exception as e:
        # Director or its model may be transiently down (e.g. 503). Never block a
        # video: fall back to a deterministic genre-based style + production only.
        print(f"  [runner] agent run failed ({e}); retrying production-only with fallback style")
        from .director import _fallback_profile, _guess_genre
        state["style"] = _fallback_profile(_guess_genre(topic.topic), iso_now)
        return asyncio.run(_drive(_production_only_agent(), dict(state)))


def load_style_by_profile(profile_id: int | None) -> dict | None:
    """Build a style dict from a stored style_profiles row (e.g. a series' locked
    style), so it can be passed straight to generation without re-running the Director."""
    if not profile_id:
        return None
    from .style import StyleProfile
    sp = db.get_style_profile(profile_id)
    if not sp:
        return None
    return StyleProfile(
        genre=sp["genre"], tone=sp["tone"], voice=sp["voice"], voice_rate=sp["voice_rate"],
        music_mood=sp["music_mood"], visual_strategy=sp["visual_strategy"],
        art_style_prompt=sp["art_style_prompt"], pacing=sp["pacing"],
        profile_id=sp["id"],
    ).to_dict()


def _load_style(video_id: int) -> dict | None:
    """Reload the locked style of an existing video so regeneration keeps identity."""
    v = db.get_video(video_id)
    return load_style_by_profile(v["style_profile_id"]) if v else None


def run_regeneration(video_id: int, topic: Topic, *, feedback: str, iso_now: str,
                     reuse_images: bool = False) -> int | None:
    """Regenerate an existing video with reviewer feedback. Keeps the original
    style (no Director re-run) and reuses images when feedback was about wording.
    Safe to call from a worker thread (uses its own event loop)."""
    state = {
        "topic": _topic_state(topic),
        "iso_now": iso_now,
        "video_id": video_id,
        "feedback": feedback,
        "reuse_images": reuse_images,
        "style": _load_style(video_id),
    }
    return asyncio.run(_drive(_production_only_agent(), state))
