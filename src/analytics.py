"""Learning loop (ships DORMANT — set analytics.enabled: true once the channel
has views). Pulls per-video performance from the YouTube Analytics API and
aggregates it into strategy_memory, which the Creative Director reads (via the
get_strategy_hints tool) to bias future creative choices toward what performs.

Uses its OWN OAuth token + scope (secrets/youtube_analytics_token.json) so it
never disturbs the upload token. First run opens a one-time browser consent.

CLI:  python -m src.analytics pull       (fetch performance for published videos)
      python -m src.analytics recompute  (rebuild strategy_memory)
      python -m src.analytics run        (pull + recompute)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta

from .config import cfg, env, ROOT
from . import db

SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]
TOKEN_FILE = "secrets/youtube_analytics_token.json"


def _enabled() -> bool:
    return bool(cfg().get("analytics", {}).get("enabled"))


def _analytics_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_file = ROOT / TOKEN_FILE
    client_file = ROOT / env("YOUTUBE_CLIENT_SECRET_FILE", required=True)
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json())
    return build("youtubeAnalytics", "v2", credentials=creds)


def pull_recent(iso_now: str) -> int:
    """Fetch analytics for published videos older than min_age_hours whose
    performance is missing/stale. Returns how many were updated."""
    min_age = cfg().get("analytics", {}).get("min_age_hours", 48)
    now = datetime.fromisoformat(iso_now)
    svc = _analytics_service()

    with db.conn() as c:
        rows = c.execute(
            "SELECT id, youtube_id, created_at FROM videos "
            "WHERE status='published' AND youtube_id IS NOT NULL"
        ).fetchall()

    updated = 0
    for r in rows:
        try:
            created = datetime.fromisoformat(r["created_at"])
        except Exception:
            continue
        if now - created < timedelta(hours=min_age):
            continue
        start = created.date().isoformat()
        end = now.date().isoformat()
        try:
            resp = svc.reports().query(
                ids="channel==MINE", startDate=start, endDate=end,
                metrics="views,estimatedMinutesWatched,averageViewPercentage,likes,comments",
                filters=f"video=={r['youtube_id']}",
            ).execute()
        except Exception as e:
            print(f"  [analytics] {r['youtube_id']} failed: {e}")
            continue
        data = resp.get("rows") or []
        if not data:
            continue
        v = data[0]
        db.upsert_performance(
            r["id"], youtube_id=r["youtube_id"], views=int(v[0]),
            avg_view_sec=None, avg_view_pct=float(v[2]),
            likes=int(v[3]), comments=int(v[4]), pulled_at=iso_now)
        updated += 1
    print(f"  [analytics] updated performance for {updated} video(s)")
    return updated


def recompute_strategy(iso_now: str):
    """Aggregate performance by creative dimension into strategy_memory. Score =
    mean averageViewPercentage (retention), the most format-agnostic signal."""
    dims = {
        "genre": "sp.genre",
        "visual_strategy": "sp.visual_strategy",
        "voice": "sp.voice",
    }
    with db.conn() as c:
        for dim, col in dims.items():
            rows = c.execute(
                f"SELECT {col} AS k, AVG(p.avg_view_pct) AS score, COUNT(*) AS n "
                f"FROM performance p JOIN videos v ON v.id=p.video_id "
                f"JOIN style_profiles sp ON sp.id=v.style_profile_id "
                f"WHERE {col} IS NOT NULL AND {col} != '' "
                f"GROUP BY {col}", ).fetchall()
            for row in rows:
                if row["score"] is None:
                    continue
                db.upsert_strategy(dim, row["k"], float(row["score"]),
                                   int(row["n"]), iso_now)
    print("  [analytics] strategy_memory recomputed")


def main():
    ap = argparse.ArgumentParser(prog="src.analytics")
    ap.add_argument("cmd", choices=["pull", "recompute", "run"])
    args = ap.parse_args()
    if not _enabled():
        print("analytics.enabled is false in config.yaml — enable it once the "
              "channel has views, then re-run.")
        return
    iso_now = datetime.now(timezone.utc).isoformat()
    if args.cmd in ("pull", "run"):
        pull_recent(iso_now)
    if args.cmd in ("recompute", "run"):
        recompute_strategy(iso_now)


if __name__ == "__main__":
    main()
