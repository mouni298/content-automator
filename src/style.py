"""Shared StyleProfile: the creative director's per-video decision, threaded
through research/assets/tts/assemble. Kept tiny and JSON-friendly so it can live
in ADK session.state and SQLite alike."""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class StyleProfile:
    genre: str = ""
    tone: str = ""
    voice: str = ""            # edge-tts voice id; "" -> language default
    voice_rate: str = ""       # e.g. "-10%"; "" -> config default
    music_mood: str = ""       # epic|somber|mysterious|triumphant
    visual_strategy: str = "mixed"   # real | ai-art | mixed
    art_style_prompt: str = ""
    pacing: str = ""           # slow|medium|fast
    profile_id: int | None = None    # FK to style_profiles row, set on persist

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "StyleProfile | None":
        if not d:
            return None
        known = {k: d.get(k, getattr(cls, k, "")) for k in cls.__dataclass_fields__}
        return cls(**known)
