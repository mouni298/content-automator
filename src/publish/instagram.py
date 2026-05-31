"""Instagram Reels publishing - PHASE 2 (stub).

Requires: Instagram Business/Creator account linked to a Facebook Page, a Meta
app with the instagram_content_publish permission (app review), a long-lived
access token, and the video hosted at a public HTTPS URL (Graph API pulls by URL,
it does not accept a file upload).

Flow when implemented:
  1. POST /{ig-user-id}/media         (media_type=REELS, video_url=..., caption=...)
  2. poll  /{container-id}?fields=status_code  until FINISHED
  3. POST /{ig-user-id}/media_publish (creation_id=container-id)
"""
from __future__ import annotations

from pathlib import Path


def upload(video_path: Path, caption: str) -> str:  # noqa: ARG001
    raise NotImplementedError(
        "Instagram publishing is phase 2. Set publish.instagram.enabled once a "
        "Business account + Graph API access token are configured."
    )
