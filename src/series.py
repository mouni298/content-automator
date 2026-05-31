"""Multi-part series: post a big topic (e.g. the Mahabharata) as daily episodes
that remember continuity via a running 'story so far' summary.

CLI:  python -m src.series seed --slug mahabharata --title "The Mahabharata" \
          --wiki "Mahabharata" --parts 12
      python -m src.series list

next_part(series_row) -> what to generate next (deterministic).
update_summary(series_id, new_script, iso_now) -> fold the approved part into the
running summary and advance current_part. Best-effort: never blocks an approve.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import cfg, env
from . import db
from .topic_picker import Topic


@dataclass
class SeriesContext:
    topic: Topic
    part_no: int
    story_so_far: str
    is_final: bool


def next_part(series_row) -> SeriesContext:
    part_no = (series_row["current_part"] or 0) + 1
    total = series_row["total_parts"]
    is_final = bool(total and part_no >= total)
    angle = (f"Part {part_no}"
             + (f" of {total}" if total else "")
             + " of an ongoing series; continue the narrative.")
    topic = Topic(topic=series_row["title"], angle=angle,
                  wikipedia_title=series_row["wikipedia_title"])
    return SeriesContext(topic=topic, part_no=part_no,
                         story_so_far=series_row["story_so_far"] or "", is_final=is_final)


def update_summary(series_id: int, new_script: str, *, iso_now: str):
    """Fold the just-approved part into story_so_far and advance current_part.
    The summary is best-effort (LLM with a plain-append fallback); the part
    advance always happens so the series never stalls."""
    row = db.get_series(series_id) if False else None  # series_id is the id
    with db.conn() as c:
        row = c.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
    if not row:
        return
    prev = row["story_so_far"] or ""
    new_summary = _summarize(prev, new_script)
    done = bool(row["total_parts"] and (row["current_part"] or 0) + 1 >= row["total_parts"])
    db.update_series(series_id,
                     story_so_far=new_summary,
                     current_part=(row["current_part"] or 0) + 1,
                     status="done" if done else "active",
                     updated_at=iso_now)


def _summarize(prev: str, new_script: str) -> str:
    max_words = cfg().get("series", {}).get("summary_max_words", 200)
    model = cfg().get("series", {}).get("summary_model", "gemini-2.5-flash")
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=env("GEMINI_API_KEY", required=True))
        prompt = (f"Running summary of a history/mythology video series so far:\n{prev or '(none)'}\n\n"
                  f"The newest episode's narration:\n{new_script}\n\n"
                  f"Write an updated 'story so far' in <= {max_words} words that captures "
                  f"everything important for continuity into the next episode. Plain prose, no preamble.")
        resp = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=600, temperature=0.4,
                thinking_config=types.ThinkingConfig(thinking_budget=0)),
        )
        text = (resp.text or "").strip()
        if text:
            return text
    except Exception as e:
        print(f"  [series] summary LLM failed ({e}); using append fallback")
    # fallback: keep it bounded
    merged = (prev + " " + new_script).split()
    return " ".join(merged[-max_words:])


# ---- CLI ----

def _seed(args, iso_now):
    if db.get_series(args.slug):
        print(f"series '{args.slug}' already exists"); return
    sid = db.insert_series(args.slug, args.title, args.wiki, args.parts, iso_now)
    print(f"seeded series #{sid}: {args.title} ({args.parts or 'open-ended'} parts)")


def _list(args, iso_now):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM series ORDER BY id").fetchall()
    if not rows:
        print("(no series)"); return
    for r in rows:
        print(f"#{r['id']} {r['slug']}: part {r['current_part']}/{r['total_parts'] or '?'} "
              f"[{r['status']}]")


def main():
    ap = argparse.ArgumentParser(prog="src.series")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--slug", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--wiki", required=True, help="Wikipedia article title")
    s.add_argument("--parts", type=int, default=None, help="total parts (omit = open-ended)")
    sub.add_parser("list")
    args = ap.parse_args()
    iso_now = datetime.now(timezone.utc).isoformat()
    {"seed": _seed, "list": _list}[args.cmd](args, iso_now)


if __name__ == "__main__":
    main()
