"""ADK tools — plain functions the agents can call. Docstrings drive when/how the
model uses them, so keep them clear and accurate. These are read-only memory
lookups (SQLite); side-effecting tools (set_style_profile) live with their agent.
"""
from __future__ import annotations

from . import db


def get_strategy_hints() -> dict:
    """Return which creative choices have performed best historically, so you can
    bias toward what works. Includes per-dimension top performers (genre,
    visual_strategy, hook_style, voice) by score, plus recent reviewer feedback.
    Returns a note when there is no performance data yet (new channel)."""
    hints: dict = {}
    for dim in ("genre", "visual_strategy", "hook_style", "voice"):
        rows = db.top_strategies(dim, 3)
        if rows:
            hints[dim] = [{"key": r["key"], "score": round(r["score"], 3)} for r in rows]
    fb = db.recent_feedback(5)
    if fb:
        hints["recent_reviewer_feedback"] = fb
    return hints or {"note": "No performance data yet — use genre best practices."}


def get_series_state(slug: str) -> dict:
    """Return the state of an ongoing multi-part series by its slug (e.g.
    'mahabharata'): how many parts are done, the total, and the locked style
    profile id to reuse for visual/tonal consistency. Use this for big topics
    posted in daily parts. Returns exists=False if there is no such series."""
    row = db.get_series(slug)
    if not row:
        return {"exists": False}
    return {
        "exists": True,
        "current_part": row["current_part"],
        "total_parts": row["total_parts"],
        "locked_style_profile_id": row["style_profile_id"],
    }
