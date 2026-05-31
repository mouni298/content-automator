"""Upload a finished Short to YouTube via the Data API (OAuth desktop flow)."""
from __future__ import annotations

from pathlib import Path

from ..config import cfg, env, ROOT

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",   # needed to read channel name
]


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_file = ROOT / env("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json")
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
    return build("youtube", "v3", credentials=creds)


def data_service():
    """Authenticated YouTube Data API client (reuses the cached OAuth token).
    Shared by reach.py for trending-tag research."""
    return _service()


def channel_title() -> str:
    """Return the authenticated channel's name. Triggers the OAuth browser flow on
    first call, so it doubles as the 'which channel did I connect to?' check."""
    svc = _service()
    resp = svc.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items", [])
    return items[0]["snippet"]["title"] if items else "(no channel on this account!)"


def upload(video_path: Path, title: str, description: str,
           extra_tags: list[str] | None = None) -> str:
    """Upload as a Short. #Shorts in the title/description + 9:16 <60s = Short.
    extra_tags (e.g. researched trending tags) are merged with the config tags.
    Returns the YouTube video id."""
    from googleapiclient.http import MediaFileUpload

    yt = cfg()["publish"]["youtube"]
    svc = _service()
    tags = list(dict.fromkeys([*yt["tags"], *(extra_tags or [])]))[:30]  # dedupe, cap
    body = {
        "snippet": {
            "title": title[:100],
            "description": (description + "\n\n#Shorts")[:4900],
            "tags": tags,
            "categoryId": str(yt["category_id"]),
        },
        "status": {"privacyStatus": yt["privacy"], "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    req = svc.videos().insert(part="snippet,status", body=body, media_body=media)

    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]
