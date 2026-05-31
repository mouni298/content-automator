"""Assemble the final 9:16 video with FFmpeg: Ken Burns motion per image,
crossfades, voiceover, background music, and burned-in captions."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import cfg, ROOT


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(out.stdout.strip() or 0.0)


def _pick_music(mood: str | None = None) -> Path | None:
    base = ROOT / cfg()["assets"]["music_dir"]
    # prefer a mood-matched subfolder (assets/music/<mood>/), else the flat dir
    dirs = [base / mood, base] if mood else [base]
    for d in dirs:
        if not d.is_dir():
            continue
        tracks = sorted(d.glob("*.mp3")) + sorted(d.glob("*.m4a"))
        if tracks:
            return tracks[len(tracks) // 2]   # deterministic, no clock
    return None


def _kenburns_clip(img: Path, dur: float, idx: int, out: Path):
    """Render one Ken Burns clip. Alternates zoom-in / zoom-out by index so the
    finished video doesn't look pattern-stamped (a 2026 policy-safety concern)."""
    v = cfg()["video"]
    W, H, fps = v["width"], v["height"], v["fps"]
    n = max(int(dur * fps), 1)
    c = "1.30"   # max zoom for pan presets (need headroom to move)
    # 8 motion presets cycled by index -> strong, varied movement so the video
    # reads as motion, not a slideshow. (z=zoom, x/y=window position over frame `on`.)
    presets = [
        # zoom in, center
        ("min(zoom+0.0018,1.35)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        # zoom out, center
        (f"if(eq(on,0),1.35,max(zoom-0.0015,1.0))", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        # pan right
        (c, f"(iw-iw/zoom)*on/{n}", "ih/2-(ih/zoom/2)"),
        # pan left
        (c, f"(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"),
        # pan up
        (c, "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(1-on/{n})"),
        # pan down
        (c, "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*on/{n}"),
        # zoom in toward top-left (diagonal push)
        ("min(zoom+0.0016,1.35)", f"(iw-iw/zoom)*on/{n}", f"(ih-ih/zoom)*on/{n}"),
        # zoom in toward bottom-right (diagonal pull)
        ("min(zoom+0.0016,1.35)", f"(iw-iw/zoom)*(1-on/{n})", f"(ih-ih/zoom)*(1-on/{n})"),
    ]
    z, x, y = presets[idx % len(presets)]
    zoompan = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={fps}"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", str(img),
         "-vf", zoompan, "-t", str(dur), "-r", str(fps),
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )


def build_video(images: list[Path], voice_wav: Path, captions: list,
                work_dir: Path, out_mp4: Path, *, style=None) -> Path:
    # style (creative director) may carry music_mood/pacing; music selection is
    # wired to moods in Phase 4 (reach.pick_music). style is accepted now so the
    # signature is stable and ProductionAgent can pass it through.
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH. brew install ffmpeg")

    v = cfg()["video"]
    voice_dur = _ffprobe_duration(voice_wav)
    per = max(voice_dur / len(images), 2.5)   # split narration evenly across images

    # 1) Ken Burns clip per image
    clips = []
    for i, img in enumerate(images):
        clip = work_dir / f"clip_{i:02d}.mp4"
        _kenburns_clip(img, per, i, clip)
        clips.append(clip)

    # 2) concat clips with crossfade via the concat demuxer (simple, robust)
    concat_list = work_dir / "concat.txt"
    # absolute paths so the concat demuxer resolves regardless of cwd
    concat_list.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    silent_video = work_dir / "video_silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c", "copy", str(silent_video)],
        check=True, capture_output=True,
    )

    # 3) mix voiceover + (optional) ducked background music (mood-matched if set)
    mood = getattr(style, "music_mood", None) if style else None
    music = _pick_music(mood)
    mixed_audio = work_dir / "audio_mixed.m4a"
    if music:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(voice_wav), "-stream_loop", "-1", "-i", str(music),
             "-filter_complex",
             f"[1:a]volume={v['music_volume']}[bg];[0:a][bg]amix=inputs=2:duration=first[a]",
             "-map", "[a]", "-t", str(voice_dur), str(mixed_audio)],
            check=True, capture_output=True,
        )
        audio_in = mixed_audio
    else:
        audio_in = voice_wav

    # 4) overlay caption PNGs (libass-free) during their time windows + attach audio
    caps = [c for c in captions if getattr(c, "path", None)]
    cmd = ["ffmpeg", "-y", "-i", str(silent_video), "-i", str(audio_in)]
    for c in caps:
        cmd += ["-i", str(c.path)]

    # build the overlay chain: video is input 0, audio input 1, captions 2..N+1.
    # final node forces standard tv-range yuv420p for maximum player compatibility.
    fmt = "scale=in_range=full:out_range=tv,format=yuv420p"
    if caps:
        steps, prev = [], "0:v"
        for idx, c in enumerate(caps):
            inp = idx + 2
            out_label = f"v{idx}"
            steps.append(
                f"[{prev}][{inp}:v]overlay=0:0:enable='between(t,{c.start:.2f},{c.end:.2f})'[{out_label}]"
            )
            prev = out_label
        steps.append(f"[{prev}]{fmt}[vout]")
        cmd += ["-filter_complex", ";".join(steps), "-map", "[vout]", "-map", "1:a"]
    else:
        cmd += ["-vf", fmt, "-map", "0:v", "-map", "1:a"]

    cmd += ["-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-color_range", "tv", "-movflags", "+faststart",
            "-c:a", "aac", str(out_mp4)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_mp4
