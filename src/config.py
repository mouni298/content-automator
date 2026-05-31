"""Load config.yaml + .env into a single accessor."""
from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Google ADK reads GOOGLE_API_KEY and decides Vertex vs AI Studio from
# GOOGLE_GENAI_USE_VERTEXAI. We run on the AI Studio Gemini key, so mirror it
# and force AI-Studio mode unless the user explicitly configured otherwise.
if os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")


@lru_cache(maxsize=1)
def cfg() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def env(key: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(key, default)
    if required and not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def path(key: str) -> Path:
    """Resolve a config 'paths.*' entry to an absolute Path, creating dirs as needed."""
    rel = cfg()["paths"][key]
    p = ROOT / rel
    if key.endswith("_dir"):
        p.mkdir(parents=True, exist_ok=True)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p
