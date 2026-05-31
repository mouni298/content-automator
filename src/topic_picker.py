"""Pick today's topic: Wikipedia 'On This Day' or the curated CSV queue, deduped."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass

import requests

from .config import cfg, ROOT
from . import db

WIKI_ONTHISDAY = "https://api.wikimedia.org/feed/v1/wikipedia/{lang}/onthisday/events/{mm}/{dd}"
HEADERS = {"User-Agent": "content-automator/0.1 (history shorts pipeline)"}

HISTORY_HINTS = re.compile(
    r"\b(empire|war|battle|king|queen|pharaoh|dynasty|ancient|temple|myth|god|"
    r"goddess|civili[sz]ation|emperor|revolt|founded|conquer|treaty)\b",
    re.I,
)


@dataclass
class Topic:
    topic: str
    angle: str
    wikipedia_title: str

    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.topic.lower()).strip("-")


def _from_queue() -> Topic | None:
    csv_path = ROOT / "data" / "topics.csv"
    if not csv_path.exists():
        return None
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            t = Topic(row["topic"].strip(), row["angle"].strip(), row["wikipedia_title"].strip())
            if not db.already_used(t.slug()):
                return t
    return None


def _from_onthisday(mm: int, dd: int) -> Topic | None:
    lang = cfg()["topic"]["language"]
    url = WIKI_ONTHISDAY.format(lang=lang, mm=f"{mm:02d}", dd=f"{dd:02d}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        return None

    events = r.json().get("events", [])
    # prefer history/mythology-flavoured events with a usable wiki page, oldest first
    events.sort(key=lambda e: e.get("year", 9999))
    for ev in events:
        text = ev.get("text", "")
        pages = ev.get("pages", [])
        if not pages:
            continue
        if not HISTORY_HINTS.search(text):
            continue
        page = pages[0]
        title = page.get("titles", {}).get("normalized") or page.get("title", "")
        t = Topic(topic=title, angle=text, wikipedia_title=title)
        if title and not db.already_used(t.slug()):
            return t
    return None


def pick(today_mm: int, today_dd: int) -> Topic:
    """today_mm/today_dd supplied by caller (no clock access inside library code)."""
    source = cfg()["topic"]["source"]
    chosen = None
    if source in ("onthisday", "auto"):
        chosen = _from_onthisday(today_mm, today_dd)
    if chosen is None and source in ("queue", "auto"):
        chosen = _from_queue()
    if chosen is None:
        raise RuntimeError(
            "No fresh topic available (queue exhausted and On This Day returned nothing). "
            "Add rows to data/topics.csv."
        )
    return chosen
