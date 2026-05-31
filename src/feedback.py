"""Classify reviewer feedback into a regeneration scope. Deterministic + free
(regex). Keeps the regenerate loop predictable; no LLM needed for routing.

  wording  -> reuse images, rewrite script/voice/captions (tone/length/hook/pace)
  visuals  -> keep nothing visual; refetch/regenerate images (focus/wrong art)
  both     -> full regenerate (ambiguous or mixed feedback)
"""
from __future__ import annotations

import re

_VISUAL = re.compile(
    r"\b(image|images|visual|visuals|picture|photo|art|artwork|background|scene|"
    r"footage|clip|wrong (person|place|face)|looks?|ugly|color|colour)\b", re.I)
_WORDING = re.compile(
    r"\b(long|longer|short|shorter|length|tone|word|words|hook|boring|script|"
    r"say|said|narrat|voice|pace|pacing|slow|slower|fast|faster|caption|"
    r"rephrase|reword|dramatic|formal|casual)\b", re.I)


def classify(text: str) -> str:
    v, w = bool(_VISUAL.search(text)), bool(_WORDING.search(text))
    if v and not w:
        return "visuals"
    if w and not v:
        return "wording"
    return "both"
