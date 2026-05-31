"""Text-to-speech behind a swappable interface.

Engines (config: tts.engine):
  edge  - Microsoft Edge neural voices via edge-tts. Free, no API key, natural,
          multilingual (English + Telugu). Needs internet. Default.
  piper - local, offline, robotic. No network. Fallback.

synthesize(text, work_dir) -> Path to the rendered audio file.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from .config import cfg, ROOT


def synthesize(text: str, work_dir: Path, *, style=None) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    engine = cfg()["tts"]["engine"]
    if engine == "edge":
        return _edge(text, work_dir, style=style)
    if engine == "piper":
        return _piper(text, work_dir)
    raise RuntimeError(f"Unknown tts.engine: {engine!r}")


def _voice_for_language() -> str:
    lang = cfg()["topic"]["language"]
    voices = cfg()["tts"]["voices"]
    if lang not in voices:
        raise RuntimeError(f"No tts voice configured for language '{lang}'. "
                           f"Add it under tts.voices in config.yaml.")
    return voices[lang]


def _edge(text: str, work_dir: Path, *, style=None) -> Path:
    import edge_tts  # lazy

    out = work_dir / "voice.mp3"
    # creative director's voice/rate override the language default when present
    voice = getattr(style, "voice", None) if style else None
    voice = voice or _voice_for_language()
    rate = (getattr(style, "voice_rate", None) if style else None) or cfg()["tts"].get("rate", "+0%")

    async def run():
        comm = edge_tts.Communicate(text, voice=voice, rate=rate)
        await comm.save(str(out))

    asyncio.run(run())
    if not out.exists() or out.stat().st_size < 1000:
        raise RuntimeError("edge-tts produced no audio (check network / voice name).")
    return out


def _piper(text: str, work_dir: Path) -> Path:
    if shutil.which("piper") is None:
        raise RuntimeError("piper not found on PATH. Install from "
                           "https://github.com/rhasspy/piper or use tts.engine: edge.")
    voices_dir = ROOT / cfg()["assets"]["voices_dir"]
    name = cfg()["tts"]["piper_voice"]
    model = voices_dir / f"{name}.onnx"
    config = voices_dir / f"{name}.onnx.json"
    if not model.exists():
        raise RuntimeError(f"Piper voice model missing: {model}")

    out = work_dir / "voice.wav"
    proc = subprocess.run(
        ["piper", "--model", str(model), "--config", str(config),
         "--output_file", str(out)],
        input=text.encode("utf-8"), capture_output=True,
    )
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"Piper failed: {proc.stderr.decode('utf-8', 'ignore')}")
    return out
