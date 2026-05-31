"""Orchestrator entry point: pick today's topic, drive the ADK generation agent
to render one video, then hand off to the Telegram review gate.

Run:  python -m src.main            (uses today's date for On This Day)
      python -m src.main --date 07-04   (override mm-dd for testing)

This process reads the clock once and passes iso_now down; library code never
calls the clock. After this finishes, `python -m src.review` handles Approve/Publish.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from . import topic_picker, runner, review, series, db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="override mm-dd (e.g. 07-04) for On This Day")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    if args.date:
        mm, dd = (int(x) for x in args.date.split("-"))
    else:
        mm, dd = now.month, now.day
    iso_now = now.isoformat()

    # An active series takes priority: generate its next part (with continuity).
    active = db.get_active_series()
    try:
        if active:
            ctx = series.next_part(active)
            print(f"Series '{active['slug']}' -> part {ctx.part_no}: {ctx.topic.topic}")
            # reuse the series' locked style for consistency; if none yet, the
            # Director decides on part 1 and we lock that choice for later parts.
            locked = runner.load_style_by_profile(active["style_profile_id"])
            video_id = runner.run_generation(
                ctx.topic, iso_now=iso_now, series_id=active["id"],
                part_no=ctx.part_no, story_so_far=ctx.story_so_far, style=locked)
            if not active["style_profile_id"] and video_id:
                v = db.get_video(video_id)
                if v and v["style_profile_id"]:
                    db.update_series(active["id"], style_profile_id=v["style_profile_id"])
                    print(f"  locked series style -> profile {v['style_profile_id']}")
        else:
            topic = topic_picker.pick(mm, dd)
            print(f"Topic: {topic.topic}  ({topic.slug()})")
            video_id = runner.run_generation(topic, iso_now=iso_now)
    except Exception as e:
        print(f"Generation failed: {e}", file=sys.stderr)
        raise SystemExit(1)

    if video_id:
        review.send_for_review(video_id)
        print(f"Sent video #{video_id} to Telegram for review.")
        raise SystemExit(0)
    print("No video produced.", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
