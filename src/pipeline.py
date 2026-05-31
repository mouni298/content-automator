"""The deterministic render pipeline, extracted so both main.py (fresh generation)
and review.py (feedback regeneration, via the ADK ProductionAgent) can call it.

Pure synchronous Python — research → assets → tts → captions → assemble → db.
No clock access (caller passes iso_now). No Telegram (that's the review gate).
"""
from __future__ import annotations

import sys
import traceback

from .config import path
from . import db, research, assets, tts, captions, assemble
from .topic_picker import Topic


def generate(topic: Topic, *, style=None, story_so_far: str = "", feedback: str = "",
             iso_now: str, video_id: int | None = None,
             series_id: int | None = None, part_no: int | None = None,
             reuse_images: bool = False) -> int:
    """Render one video end-to-end. Returns the videos.id.

    If video_id is given, reuse that row (regeneration); else insert a new row.
    Raises on failure (after marking the row 'failed')."""
    work_root = path("work_dir")
    out_root = path("output_dir")

    if video_id is not None:
        row = db.get_video(video_id)
        slug = row["slug"]
        db.update_video(video_id, status="generating", error=None)
    else:
        slug = f"{topic.slug()}-part-{part_no}" if part_no else topic.slug()
        video_id = db.insert_video(slug, topic.topic, topic.wikipedia_title, iso_now)
        if series_id is not None or part_no is not None:
            db.update_video(video_id, series_id=series_id, part_no=part_no)

    work = work_root / slug
    work.mkdir(parents=True, exist_ok=True)

    try:
        script = research.write_script(topic, style=style,
                                       story_so_far=story_so_far, feedback=feedback)
        print(f"  script: {len(script.script.split())} words | {script.fact_check_notes[:80]}")

        img_dir = work / "images"
        existing = sorted(p for p in img_dir.glob("img_*")) if img_dir.exists() else []
        if reuse_images and len(existing) >= 3:
            images = existing
            print(f"  images: reused {len(images)} (feedback was about wording)")
        else:
            images = assets.fetch_images(script.image_queries, img_dir,
                                         wikipedia_title=topic.wikipedia_title, style=style)
            print(f"  images: {len(images)}")

        voice = tts.synthesize(script.script, work, style=style)
        caps = captions.transcribe(voice)
        captions.write_srt(caps, work / "captions.srt")
        captions.render_caption_pngs(caps, work)
        print(f"  captions: {len(caps)} lines")

        out_mp4 = out_root / f"{slug}.mp4"
        assemble.build_video(images, voice, caps, work, out_mp4, style=style)
        print(f"  assembled: {out_mp4}")

        fields = dict(video_path=str(out_mp4), caption=script.full_caption(),
                      script=script.script, status="generated")
        if style is not None and getattr(style, "profile_id", None):
            fields["style_profile_id"] = style.profile_id
        db.update_video(video_id, **fields)
        return video_id

    except Exception as e:
        db.update_video(video_id, status="failed", error=str(e))
        print(f"  FAILED: {e}", file=sys.stderr)
        traceback.print_exc()
        raise
