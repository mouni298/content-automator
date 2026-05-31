"""SQLite state: topic dedupe + per-video status tracking."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .config import path

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT UNIQUE,          -- stable id derived from topic
    topic         TEXT NOT NULL,
    wikipedia_title TEXT,
    created_at    TEXT NOT NULL,        -- ISO; caller supplies (no Date.now in scripts)
    status        TEXT NOT NULL,        -- generated|approved|rejected|published|failed
    video_path    TEXT,
    caption       TEXT,
    youtube_id    TEXT,
    error         TEXT
);

CREATE TABLE IF NOT EXISTS series (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT UNIQUE,          -- e.g. "mahabharata"
    title         TEXT NOT NULL,
    wikipedia_title TEXT,
    total_parts   INTEGER,              -- NULL = open-ended
    current_part  INTEGER NOT NULL DEFAULT 0,   -- last COMPLETED part
    style_profile_id INTEGER,           -- locked style for cross-part consistency
    story_so_far  TEXT DEFAULT '',      -- running continuity summary (LLM-maintained)
    status        TEXT NOT NULL DEFAULT 'active',  -- active|paused|done
    created_at    TEXT NOT NULL,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS style_profiles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope         TEXT NOT NULL,        -- 'series:<slug>' | 'topic:<slug>' | 'genre:<g>'
    genre         TEXT,
    tone          TEXT,
    voice         TEXT,
    voice_rate    TEXT,
    music_mood    TEXT,
    visual_strategy TEXT,               -- real | ai-art | mixed
    art_style_prompt TEXT,
    pacing        TEXT,
    profile_json  TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS performance (
    video_id      INTEGER PRIMARY KEY,
    youtube_id    TEXT,
    views         INTEGER DEFAULT 0,
    avg_view_pct  REAL,
    avg_view_sec  REAL,
    likes         INTEGER DEFAULT 0,
    comments      INTEGER DEFAULT 0,
    pulled_at     TEXT
);

CREATE TABLE IF NOT EXISTS strategy_memory (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension     TEXT NOT NULL,        -- genre|visual_strategy|hook_style|hashtag|voice
    key           TEXT NOT NULL,
    score         REAL NOT NULL,
    sample_size   INTEGER NOT NULL DEFAULT 0,
    note          TEXT,
    updated_at    TEXT NOT NULL,
    UNIQUE(dimension, key)
);
"""

# Columns added to tables after their original creation. CREATE TABLE IF NOT
# EXISTS won't alter an existing table, so apply these idempotently.
_ADDED_COLUMNS = {
    "videos": {
        "series_id": "INTEGER",
        "part_no": "INTEGER",
        "style_profile_id": "INTEGER",
        "feedback": "TEXT",
        "regen_count": "INTEGER DEFAULT 0",
        "script": "TEXT",
    },
    "series": {
        "story_so_far": "TEXT DEFAULT ''",
    },
}


def _migrate(c):
    for table, cols in _ADDED_COLUMNS.items():
        existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


@contextmanager
def conn():
    c = sqlite3.connect(path("db_path"))
    c.row_factory = sqlite3.Row
    try:
        c.executescript(SCHEMA)
        _migrate(c)
        yield c
        c.commit()
    finally:
        c.close()


def already_used(slug: str) -> bool:
    with conn() as c:
        row = c.execute("SELECT 1 FROM videos WHERE slug = ?", (slug,)).fetchone()
        return row is not None


def insert_video(slug, topic, wikipedia_title, created_at, status="generated"):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO videos (slug, topic, wikipedia_title, created_at, status) "
            "VALUES (?,?,?,?,?)",
            (slug, topic, wikipedia_title, created_at, status),
        )
        return cur.lastrowid


def update_video(video_id: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE videos SET {cols} WHERE id = ?", (*fields.values(), video_id))


def get_video(video_id: int):
    with conn() as c:
        return c.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()


# ----- series -----

def get_active_series():
    with conn() as c:
        return c.execute(
            "SELECT * FROM series WHERE status = 'active' ORDER BY id LIMIT 1"
        ).fetchone()


def get_series(slug: str):
    with conn() as c:
        return c.execute("SELECT * FROM series WHERE slug = ?", (slug,)).fetchone()


def insert_series(slug, title, wikipedia_title, total_parts, created_at):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO series (slug, title, wikipedia_title, total_parts, created_at) "
            "VALUES (?,?,?,?,?)",
            (slug, title, wikipedia_title, total_parts, created_at),
        )
        return cur.lastrowid


def update_series(series_id: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE series SET {cols} WHERE id = ?", (*fields.values(), series_id))


# ----- style profiles -----

def insert_style_profile(scope, created_at, **fields):
    keys = ["scope", "created_at", *fields.keys()]
    vals = [scope, created_at, *fields.values()]
    placeholders = ",".join("?" * len(keys))
    with conn() as c:
        cur = c.execute(
            f"INSERT INTO style_profiles ({','.join(keys)}) VALUES ({placeholders})", vals
        )
        return cur.lastrowid


def get_style_profile(profile_id: int):
    with conn() as c:
        return c.execute(
            "SELECT * FROM style_profiles WHERE id = ?", (profile_id,)
        ).fetchone()


# ----- performance + strategy memory (learning loop) -----

def upsert_performance(video_id: int, **fields):
    cols = ["video_id", *fields.keys()]
    vals = [video_id, *fields.values()]
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields)
    placeholders = ",".join("?" * len(cols))
    with conn() as c:
        c.execute(
            f"INSERT INTO performance ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(video_id) DO UPDATE SET {updates}",
            vals,
        )


def upsert_strategy(dimension, key, score, sample_size, updated_at, note=None):
    with conn() as c:
        c.execute(
            "INSERT INTO strategy_memory (dimension, key, score, sample_size, note, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(dimension, key) DO UPDATE SET "
            "score=excluded.score, sample_size=excluded.sample_size, "
            "note=excluded.note, updated_at=excluded.updated_at",
            (dimension, key, score, sample_size, note, updated_at),
        )


def top_strategies(dimension: str, limit: int = 5):
    with conn() as c:
        return c.execute(
            "SELECT * FROM strategy_memory WHERE dimension = ? "
            "ORDER BY score DESC LIMIT ?",
            (dimension, limit),
        ).fetchall()


def recent_feedback(limit: int = 10):
    with conn() as c:
        return [
            r["feedback"] for r in c.execute(
                "SELECT feedback FROM videos WHERE feedback IS NOT NULL "
                "AND feedback != '' ORDER BY id DESC LIMIT ?", (limit,)
            )
        ]
