"""Fetch Wikipedia context and have Claude write a narrated script + assets plan."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import requests

from .config import cfg, env
from .topic_picker import Topic

WIKI_SUMMARY = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_EXTRACT = "https://{lang}.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "content-automator/0.1 (history shorts pipeline)"}

SYSTEM = """You are a meticulous history-documentary scriptwriter for short-form video.
You write tight, vivid, factually accurate narration with a strong opening hook.

ACCURACY RULES (strict):
- Use ONLY facts explicitly stated in the provided source text.
- Do NOT infer, embellish, dramatize, or add details that are not literally in the
  source (e.g. do not say "stoned by a mob" if the source only says "killed").
- Vividness must come from word choice and pacing, never from invented specifics.
- If the source is vague, stay vague. Omit rather than guess.
- In fact_check_notes, explicitly confirm every sentence traces to the source, and
  name any sentence you are unsure about so it can be cut."""

USER_TMPL = """Theme tone: {tone}
Allowed length: {min_words}-{max_words} words of narration (~45 seconds spoken).

TOPIC: {topic}
ANGLE: {angle}

SOURCE TEXT (Wikipedia, treat as ground truth):
\"\"\"
{source}
\"\"\"

Produce a JSON object ONLY (no prose), with this exact shape:
{{
  "hook": "<=12 words, the spoken first line; must make a scroller stop",
  "script": "the full narration, {min_words}-{max_words} words, including the hook as its first sentence",
  "image_queries": ["6 short Wikimedia Commons search queries for period-accurate public-domain visuals"],
  "caption": "an engaging social caption, 1-2 sentences, with a hook",
  "hashtags": ["8-12 relevant hashtags without the # symbol"],
  "fact_check_notes": "1-2 sentences: confirm every claim in the script is supported by the source; flag anything uncertain"
}}"""


@dataclass
class Script:
    topic: Topic
    hook: str
    script: str
    image_queries: list[str]
    caption: str
    hashtags: list[str] = field(default_factory=list)
    fact_check_notes: str = ""

    def full_caption(self) -> str:
        tags = " ".join(f"#{h.lstrip('#')}" for h in self.hashtags)
        return f"{self.caption}\n\n{tags}".strip()


def fetch_source(title: str, lang: str = "en") -> str:
    """Return a few paragraphs of plain-text Wikipedia content for the title."""
    # summary first (clean intro)
    summary = ""
    try:
        r = requests.get(
            WIKI_SUMMARY.format(lang=lang, title=requests.utils.quote(title)),
            headers=HEADERS, timeout=20,
        )
        if r.ok:
            summary = r.json().get("extract", "")
    except requests.RequestException:
        pass

    # longer extract for substance
    body = ""
    try:
        r = requests.get(
            WIKI_EXTRACT.format(lang=lang),
            params={
                "action": "query", "prop": "extracts", "explaintext": 1,
                "exsectionformat": "plain", "titles": title, "format": "json",
                "exchars": 2500,
            },
            headers=HEADERS, timeout=20,
        )
        if r.ok:
            pages = r.json().get("query", {}).get("pages", {})
            body = next(iter(pages.values()), {}).get("extract", "")
    except requests.RequestException:
        pass

    text = (summary + "\n\n" + body).strip()
    if not text:
        raise RuntimeError(f"No Wikipedia source text found for '{title}'.")
    return text


def write_script(topic: Topic, *, style=None, story_so_far: str = "",
                 feedback: str = "") -> Script:
    """style / story_so_far / feedback steer the prompt when present (Phase 1+);
    with all defaults this behaves exactly as the original single-shot writer."""
    from google import genai
    from google.genai import types

    s = cfg()["script"]
    source = fetch_source(topic.wikipedia_title, cfg()["topic"]["language"])

    # Steering blocks appended to the user prompt only when provided.
    steer = ""
    if style is not None:
        tone = getattr(style, "tone", None)
        pacing = getattr(style, "pacing", None)
        if tone:
            steer += f"\nNarration tone: {tone}."
        if pacing:
            steer += f"\nPacing: {pacing}."
    if story_so_far:
        steer += (f"\n\nThis is the next part of an ongoing series. STORY SO FAR:\n"
                  f"{story_so_far}\nContinue from here; end on a hook for the next part.")
    if feedback:
        steer += f"\n\nREVISE per this reviewer feedback (highest priority): {feedback}"
    client = genai.Client(api_key=env("GEMINI_API_KEY", required=True))

    resp = client.models.generate_content(
        model=s["model"],
        contents=USER_TMPL.format(
            tone=s["tone"], min_words=s["min_words"], max_words=s["max_words"],
            topic=topic.topic, angle=topic.angle, source=source[:6000],
        ) + steer,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            max_output_tokens=4096,
            temperature=0.8,
            response_mime_type="application/json",  # forces clean JSON output
            # 2.5 models "think" by default and burn output budget; this is a
            # structured extraction task, so turn thinking off for speed + room.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    raw = (resp.text or "").strip()
    if not raw:
        reason = resp.candidates[0].finish_reason if resp.candidates else "unknown"
        raise RuntimeError(f"Gemini returned no text (finish_reason={reason}).")
    raw = raw[raw.find("{"): raw.rfind("}") + 1]   # tolerate stray prose
    data = json.loads(raw)

    return Script(
        topic=topic,
        hook=data["hook"],
        script=data["script"],
        image_queries=data["image_queries"][: cfg()["video"]["images_per_video"]],
        caption=data["caption"],
        hashtags=data.get("hashtags", []),
        fact_check_notes=data.get("fact_check_notes", ""),
    )
