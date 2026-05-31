"""Reach optimization (free, ToS-safe).

trending_tags(): mine the tags of recent top-performing videos in the niche via
the official YouTube Data API (search.list ordered by viewCount → videos.list
tags). NOT trending audio — the API cannot attach YouTube's trending sounds; that
is an in-app-only feature, so it is deliberately out of scope.

Quota note: search.list costs 100 units (10k/day free), so cap niche queries.
"""
from __future__ import annotations

from collections import Counter

from .config import cfg
from .publish import youtube


def trending_tags(niche_queries: list[str] | None = None, *, max_videos: int = 25,
                  max_tags: int = 8, published_after: str | None = None) -> list[str]:
    """Return the most common tags among recent top videos for the niche queries.
    Best-effort: returns [] on any API/quota error so publishing never blocks."""
    rc = cfg().get("reach", {}).get("trending", {})
    if not rc.get("enabled", True):
        return []
    queries = niche_queries or rc.get("niche_queries", [])
    cap = rc.get("max_query_calls", 2)          # quota guard
    max_videos = rc.get("max_videos", max_videos)
    max_tags = rc.get("max_extra_tags", max_tags)

    try:
        svc = youtube.data_service()
    except Exception as e:
        print(f"  [reach] data API unavailable ({e})")
        return []

    tally: Counter = Counter()
    for q in queries[:cap]:
        try:
            params = dict(q=q, part="id", type="video", order="viewCount",
                          maxResults=min(max_videos, 50))
            if published_after:
                params["publishedAfter"] = published_after
            sr = svc.search().list(**params).execute()
            ids = [it["id"]["videoId"] for it in sr.get("items", []) if it["id"].get("videoId")]
            if not ids:
                continue
            vr = svc.videos().list(part="snippet", id=",".join(ids)).execute()
            for it in vr.get("items", []):
                for tag in it["snippet"].get("tags", []):
                    if 2 < len(tag) <= 30:
                        tally[tag.lower().strip()] += 1
        except Exception as e:
            print(f"  [reach] query {q!r} failed ({e})")
            continue

    return [t for t, _ in tally.most_common(max_tags)]
