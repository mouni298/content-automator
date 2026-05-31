"""Caption generation without libass.

faster-whisper gives word timings -> we group them into short lines, then render
each line as a transparent full-frame PNG (white text + black outline). The
assembler overlays these during their time windows. This avoids depending on an
ffmpeg built with libass (the `subtitles` filter), which isn't always present.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import cfg

# common system fonts to try (macOS first, then Linux), bold preferred
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


@dataclass
class Caption:
    start: float
    end: float
    text: str
    path: Path | None = None


def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600); m = int((seconds % 3600) // 60)
    s = int(seconds % 60); ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()   # last resort (small bitmap)


def _chunk_words(words, max_chars: int):
    line, start = [], None
    for w in words:
        if start is None:
            start = w.start
        candidate = " ".join([*[x.word.strip() for x in line], w.word.strip()])
        if len(candidate) > max_chars and line:
            yield Caption(start, line[-1].end, " ".join(x.word.strip() for x in line))
            line, start = [w], w.start
        else:
            line.append(w)
    if line:
        yield Caption(start, line[-1].end, " ".join(x.word.strip() for x in line))


def transcribe(audio_path: Path) -> list[Caption]:
    from faster_whisper import WhisperModel   # heavy import, lazy

    model_name = cfg()["captions"]["whisper_model"]
    max_chars = cfg()["captions"]["max_chars_per_line"]
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), word_timestamps=True)
    words = [w for seg in segments for w in (seg.words or [])]
    if not words:
        raise RuntimeError("Whisper produced no word timings; check the audio.")
    return list(_chunk_words(words, max_chars))


def write_srt(caps: list[Caption], out_srt: Path) -> Path:
    """Write a .srt alongside (handy for debugging / re-use), not used for burn-in."""
    out_srt.parent.mkdir(parents=True, exist_ok=True)
    with open(out_srt, "w", encoding="utf-8") as f:
        for i, c in enumerate(caps, 1):
            f.write(f"{i}\n{_fmt_ts(c.start)} --> {_fmt_ts(c.end)}\n{c.text}\n\n")
    return out_srt


def render_caption_pngs(caps: list[Caption], work_dir: Path) -> list[Caption]:
    """Render each caption as a transparent full-frame PNG with the text placed
    near the bottom-center. Returns the captions with .path populated."""
    v = cfg()["video"]
    W, H = v["width"], v["height"]
    fontsize = max(36, W // 22)            # ~49px at 1080 wide
    margin_v = 150                         # distance from bottom
    stroke = max(3, fontsize // 12)
    font = _load_font(fontsize)
    pad = int(W * 0.08)

    out_dir = work_dir / "captions"
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, c in enumerate(caps):
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        text = _wrap(draw, c.text, font, W - 2 * pad)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, stroke_width=stroke,
                                       align="center", spacing=8)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (W - tw) // 2
        y = H - margin_v - th
        draw.multiline_text((x, y), text, font=font, fill=(255, 255, 255, 255),
                            stroke_width=stroke, stroke_fill=(0, 0, 0, 255),
                            align="center", spacing=8)
        p = out_dir / f"cap_{i:03d}.png"
        img.save(p)
        c.path = p
    return caps


def _wrap(draw, text: str, font, max_w: int) -> str:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return "\n".join(lines)
