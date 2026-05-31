"""Source public-domain/free images for the video.

Strategy (most-relevant first, with fallbacks so we rarely come up short):
  1. images used in the Wikipedia article itself (topical + already free-licensed)
  2. Wikimedia Commons keyword search on the LLM's image queries
  3. broadened fallback queries (topic / wikipedia title)
Images are de-duped and downloaded until we have `images_per_video`.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import requests

from .config import cfg

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "content-automator/0.1 (history shorts pipeline)"}


def _get(url: str, *, params=None, timeout=20, tries=3) -> requests.Response | None:
    """GET with small backoff retries — survives transient Wikimedia rate-limits."""
    for attempt in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
            if r.status_code == 429:
                raise requests.RequestException("429 rate limited")
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
    return None

# skip non-content files (icons, logos, audio, low-value chrome)
SKIP = re.compile(r"(icon|logo|symbol|wiki|commons-|\.svg|\.ogg|\.oga|\.mid|"
                  r"edit-|button|flag of|sound|\.tif)", re.I)


def _wiki_api(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def _imageinfo_urls(api: str, file_titles: list[str], width: int) -> list[str]:
    """Resolve File: titles to scaled image URLs via imageinfo."""
    urls = []
    for i in range(0, len(file_titles), 20):          # API caps titles per call
        batch = file_titles[i:i + 20]
        r = _get(api, params={
            "action": "query", "format": "json", "titles": "|".join(batch),
            "prop": "imageinfo", "iiprop": "url|size|mime", "iiurlwidth": width,
        })
        if r is None:
            continue
        for p in r.json().get("query", {}).get("pages", {}).values():
            info = (p.get("imageinfo") or [{}])[0]
            if not info.get("mime", "").startswith("image/"):
                continue
            url = info.get("thumburl") or info.get("url")
            if url:
                urls.append(url)
    return urls


def _images_from_article(title: str, lang: str, min_width: int) -> list[str]:
    api = _wiki_api(lang)
    r = _get(api, params={
        "action": "query", "format": "json", "prop": "images",
        "titles": title, "imlimit": 40,
    })
    if r is None:
        return []
    files = []
    for p in r.json().get("query", {}).get("pages", {}).values():
        for img in p.get("images", []):
            t = img.get("title", "")
            if t.lower().startswith("file:") and not SKIP.search(t):
                files.append(t)
    return _imageinfo_urls(api, files, cfg()["video"]["width"])


def _search_commons(query: str, width: int) -> list[str]:
    r = _get(COMMONS_API, params={
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 10,
        "prop": "imageinfo", "iiprop": "url|size|mime", "iiurlwidth": width,
    })
    if r is None:
        return []
    urls = []
    for p in r.json().get("query", {}).get("pages", {}).values():
        t = p.get("title", "")
        if SKIP.search(t):
            continue
        info = (p.get("imageinfo") or [{}])[0]
        if not info.get("mime", "").startswith("image/"):
            continue
        url = info.get("thumburl") or info.get("url")
        if url:
            urls.append(url)
    return urls


def fetch_images(queries: list[str], work_dir: Path,
                 wikipedia_title: str | None = None, *, style=None) -> list[Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    width = cfg()["video"]["width"]
    min_width = cfg()["assets"]["min_image_width"]
    want = cfg()["video"]["images_per_video"]
    lang = cfg()["topic"]["language"]

    # When the creative director chose AI art (e.g. mythology with no real
    # photos), generate straight away and skip web sourcing. Falls through to the
    # download loop below only via the AI top-up; a Commons minimum-count safety
    # net still applies so a video is never blocked.
    visual_strategy = getattr(style, "visual_strategy", None) if style else None
    art_prompt = getattr(style, "art_style_prompt", None) if style else None

    paths: list[Path] = []
    if visual_strategy == "ai-art":
        for i, q in enumerate(queries[:want]):
            img = _generate_ai_image(q, work_dir, len(paths), style_prompt=art_prompt)
            if img:
                paths.append(img)
            if len(paths) >= want:
                break
        if len(paths) >= 3:
            return paths
        # AI thin -> fall through to Commons as a safety net

    # build a de-duplicated candidate URL list, most-relevant first
    candidates: list[str] = []
    seen = set()

    def add(urls):
        for u in urls:
            if u not in seen:
                seen.add(u)
                candidates.append(u)

    if wikipedia_title:
        add(_images_from_article(wikipedia_title, lang, min_width))
    for q in queries:
        add(_search_commons(q, width))
    # broadened fallbacks if the niche queries were thin
    if wikipedia_title:
        add(_search_commons(wikipedia_title, width))

    # download until we have enough (keeping any AI-art images already collected)
    for url in candidates:
        resp = _get(url, timeout=30)
        if resp is None:
            continue
        ctype = resp.headers.get("content-type", "")
        if not ctype.startswith("image/") or len(resp.content) < 15000:
            continue
        ext = ".jpg" if "jpeg" in ctype else ".png"
        out = work_dir / f"img_{len(paths):02d}{ext}"
        out.write_bytes(resp.content)
        paths.append(out)
        if len(paths) >= want:
            break

    # AI fallback: top up with Gemini-generated images when Commons came up short
    if len(paths) < want and cfg()["assets"].get("ai_fallback"):
        needed = want - len(paths)
        prompts = (queries or [])[:needed] or [wikipedia_title or "history"]
        # cycle prompts if we still need more than we have queries for
        prompts = (prompts * needed)[:needed]
        for q in prompts:
            img = _generate_ai_image(q, work_dir, len(paths), style_prompt=art_prompt)
            if img:
                paths.append(img)
            if len(paths) >= want:
                break

    if len(paths) < 3:
        raise RuntimeError(
            f"Only found {len(paths)} usable images for '{wikipedia_title}' / {queries!r}. "
            "Need at least 3 (Commons thin and AI fallback unavailable/failed)."
        )
    return paths


def _generate_ai_image(prompt: str, work_dir: Path, idx: int,
                       style_prompt: str | None = None) -> Path | None:
    """Generate one image via Pollinations.ai (free, keyless, FLUX). Best-effort:
    returns None on failure so the pipeline proceeds with what it has.
    style_prompt (from the creative director) overrides the default config style."""
    from urllib.parse import quote

    style = style_prompt or cfg()["assets"].get("ai_style", "")
    full = f"{prompt}. {style}".strip()
    W, H = cfg()["video"]["width"], cfg()["video"]["height"]
    url = (f"https://image.pollinations.ai/prompt/{quote(full)}"
           f"?width={W}&height={H}&nologo=true&model=flux&seed={idx}")
    resp = _get(url, timeout=90, tries=2)   # generation can take ~10-30s
    if resp is None:
        print(f"  [ai-image] failed: {prompt[:40]}...")
        return None
    ctype = resp.headers.get("content-type", "")
    if not ctype.startswith("image/") or len(resp.content) < 5000:
        return None
    out = work_dir / f"img_{idx:02d}_ai.jpg"
    out.write_bytes(resp.content)
    return out
