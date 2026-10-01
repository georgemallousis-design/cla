"""Procedural backgrounds rendered with FFmpeg: the provider that never comes back empty.

Each query maps to a deterministic seed, which picks a style, a dark low-contrast palette
(white captions stay readable) and the motion parameters:

* ``gradient`` - FFmpeg's ``gradients`` source (linear or radial), slowly rotating.
* ``blobs``    - two or three soft colour blobs drifting over a dark base.
* ``nebula``   - a blurred cloud texture drifting over a gradient, with slow hue cycling.

Everything is rendered at a fraction of the output size, then scaled up (the images are
smooth, so nothing is lost) and encoded with x264 ``ultrafast``: about 1-2 s for 4 s of
1080x1920 on four cores. Results are cached as ``cache_dir/generated_<hash>.mp4``.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..models import ClipAsset
from ..utils import AutoShortsError, ensure_dir, log, run_ffmpeg
from . import VisualProvider

STYLE_VERSION = 1  # bump when the look of a style changes, so old cache files are not reused
STYLES = ("gradient", "blobs", "nebula")
FALLBACK_STYLE = "blobs"  # no "gradients" source (FFmpeg >= 4.4): works on old builds
MIN_SECONDS = 4.0
MAX_VARIANTS = 8  # per search call; each variant costs one render
BLOB_ALPHA = 1.0  # opacity at a blob's centre

# (base, accent, accent, accent): dark, desaturated-ish colours; accents stay below ~60% brightness.
PALETTES: tuple[tuple[str, str, str, str], ...] = (
    ("0b132b", "1c2541", "3a506b", "2a6f97"),  # deep ocean
    ("081c24", "0f4c5c", "1f7a6d", "5b2a86"),  # aurora
    ("1a1033", "4a1f6b", "7b2d6e", "2d3a8c"),  # plum night
    ("1a0f0f", "6b2414", "8c4a1c", "4a1f3d"),  # ember
    ("0b1a12", "1f4d2e", "2e6b4f", "3d5a1f"),  # forest
    ("1f1020", "6b2d4a", "8c3b5e", "3b2a6b"),  # rose dusk
    ("0a1030", "1a2f8c", "2d5aa8", "5a2d8c"),  # cobalt
    ("10141f", "1f6b6b", "8c5a2d", "6b2d3d"),  # teal sunset
    ("121212", "3d3522", "6b5a2d", "2d3d4a"),  # graphite gold
    ("120f24", "3d1f6b", "1f6b5a", "2d2d8c"),  # violet mint
)


@dataclass(frozen=True)
class Look:
    """Everything that decides how a generated background looks (all derived from seed)."""

    style: str
    palette: tuple[str, ...]
    seed: int
    speed: float  # overall motion speed multiplier, ~0.6-1.4
    variant: int  # style sub-type (gradient type, blob count...)


class GeneratedProvider(VisualProvider):
    name = "generated"
    spares = 0  # every extra candidate would be another render

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.cache_dir = cfg.path(cfg.visuals.cache_dir)

    def search(self, query: str, min_seconds: float, count: int = 1) -> list[ClipAsset]:
        width, height = _even(self.cfg.video.width), _even(self.cfg.video.height)
        fps = max(1, int(round(self.cfg.video.fps)))
        duration = clip_seconds(min_seconds)
        clips = []
        for variant in range(max(1, min(count, MAX_VARIANTS))):
            seed = seed_for(query, variant)
            path = self.cache_dir / f"generated_{cache_key(query, variant, width, height, fps, duration)}.mp4"
            if not (path.exists() and path.stat().st_size > 0):
                render_look(choose_look(seed), path, width, height, fps, duration)
            clips.append(ClipAsset(
                path=path, kind="video", duration=duration, source="generated",
                query=query, attribution="", width=width, height=height,
            ))
        return clips


# --------------------------------------------------------------------------- looks


def normalize_query(query: str) -> str:
    return " ".join((query or "").lower().split())


def seed_for(query: str, variant: int = 0) -> int:
    """Stable 31-bit seed (same on every machine and Python run, unlike hash())."""
    digest = hashlib.sha256(f"{normalize_query(query)}#{variant}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**31 - 1)


def cache_key(query: str, variant: int, width: int, height: int, fps: int, duration: float) -> str:
    data = {"v": STYLE_VERSION, "q": normalize_query(query), "n": variant,
            "w": width, "h": height, "fps": fps, "d": duration}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def clip_seconds(min_seconds: float) -> float:
    """max(min_seconds, 4) rounded up to half a second (better cache reuse)."""
    seconds = max(float(min_seconds or 0), MIN_SECONDS)
    return math.ceil(seconds * 2 - 1e-9) / 2


def choose_look(seed: int) -> Look:
    rng = random.Random(seed)
    style = STYLES[seed % len(STYLES)]
    palette = PALETTES[rng.randrange(len(PALETTES))]
    if rng.random() < 0.5:  # vary accent order so one palette yields several looks
        palette = (palette[0], *rng.sample(palette[1:], len(palette) - 1))
    return Look(style=style, palette=palette, seed=seed, speed=round(rng.uniform(0.6, 1.4), 3),
                variant=rng.randrange(3))


# --------------------------------------------------------------------------- rendering


def render_look(look: Look, out_path: Path, width: int, height: int, fps: int, duration: float) -> Path:
    """Render ``look`` to ``out_path`` (atomically). Falls back to the most portable style."""
    ensure_dir(out_path.parent)
    tmp = out_path.with_name(f"{out_path.stem}.{os.getpid()}.tmp.mp4")
    styles = [look.style] + ([FALLBACK_STYLE] if look.style != FALLBACK_STYLE else [])
    error: AutoShortsError | None = None
    for style in styles:
        args = ffmpeg_args(Look(style, look.palette, look.seed, look.speed, look.variant),
                           width, height, fps, duration, tmp)
        try:
            run_ffmpeg(args, desc=f"generated background ({style})", timeout=max(120.0, duration * 20))
        except AutoShortsError as exc:
            error = exc
            log.warning("generated background style '%s' failed, trying a simpler one: %s",
                        style, str(exc).splitlines()[0])
            continue
        os.replace(tmp, out_path)
        log.debug("generated %s background %s (%.1fs)", style, out_path.name, duration)
        return out_path
    tmp.unlink(missing_ok=True)
    assert error is not None
    raise error


def ffmpeg_args(look: Look, width: int, height: int, fps: int, duration: float, out_path: Path) -> list[str]:
    """Full ffmpeg argument list (without 'ffmpeg') for one look."""
    frames = max(1, round(duration * fps))
    builder = {"gradient": _gradient, "blobs": _blobs, "nebula": _nebula}[look.style]
    inputs, graph = builder(look, width, height, fps, duration, frames)
    finish = f"format=yuv420p,scale={width}:{height}:flags=bicubic,setsar=1"
    return [
        *inputs,
        "-filter_complex", f"{graph},{finish}[out]",
        "-map", "[out]", "-frames:v", str(frames), "-r", str(fps),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-an", str(out_path),
    ]


def _even(n: int | float, minimum: int = 2) -> int:
    n = max(minimum, int(n))
    return n - (n % 2)


def _lowres(width: int, height: int, factor: int) -> tuple[int, int]:
    return _even(max(16, width // factor)), _even(max(16, height // factor))


def _gradient(look: Look, width: int, height: int, fps: int, duration: float, frames: int) -> tuple[list[str], str]:
    lw, lh = _lowres(width, height, 4)
    # Radial and the 4-colour linear variant include the dark base colour.
    colors = look.palette[1:] if look.variant == 0 else look.palette
    kind = ("linear", "radial", "linear")[look.variant]
    cs = ":".join(f"c{i}=0x{c}" for i, c in enumerate(colors))
    speed = round(0.012 * look.speed, 4)
    src = (f"gradients=s={lw}x{lh}:r={fps}:d={duration}:{cs}:n={len(colors)}:"
           f"speed={speed}:type={kind}:seed={look.seed}")
    return ["-f", "lavfi", "-i", src], "[0:v]format=yuv420p,vignette=angle=PI/5:dither=0"


def _blobs(look: Look, width: int, height: int, fps: int, duration: float, frames: int) -> tuple[list[str], str]:
    # Each blob is one soft Gaussian sprite (geq on a single frame), looped and moved
    # around with overlay x/y expressions: far cheaper than evaluating geq every frame.
    lw, lh = _lowres(width, height, 4)
    rng = random.Random(look.seed + 1)
    n_blobs = 2 + (look.variant % 2)
    alpha = round(255 * BLOB_ALPHA)
    parts = [f"color=c=0x{look.palette[0]}:s={lw}x{lh}:r={fps}:d={duration}[b0]"]
    for i in range(n_blobs):
        color = look.palette[1 + i % (len(look.palette) - 1)]
        size = _even(lw * rng.uniform(0.95, 1.3))
        half, spread = size / 2, round(size * size * 0.06, 1)
        sx, sy = (round(rng.uniform(0.15, 0.45) * look.speed, 3) for _ in range(2))
        px, py = (round(rng.uniform(0, 2 * math.pi), 3) for _ in range(2))
        ax, ay = round(rng.uniform(0.18, 0.32), 3), round(rng.uniform(0.1, 0.2), 3)
        cy = round(0.25 + 0.5 * i / max(1, n_blobs - 1), 3)
        parts.append(
            f"color=c=0x{color}:s={size}x{size}:r={fps}:d={duration},format=rgba,trim=end_frame=1,"
            f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha}*exp(-(pow(X-{half},2)+pow(Y-{half},2))/{spread})',"
            f"loop=loop={frames}:size=1,setpts=N/FRAME_RATE/TB[s{i}]"
        )
        parts.append(
            f"[b{i}][s{i}]overlay=x='W/2-w/2+W*{ax}*sin(t*{sx}+{px})':"
            f"y='H*{cy}-h/2+H*{ay}*cos(t*{sy}+{py})':shortest=1[b{i + 1}]"
        )
    graph = ";".join(parts) + f";[b{n_blobs}]format=yuv420p,vignette=angle=PI/5:dither=0"
    return [], graph


def _nebula(look: Look, width: int, height: int, fps: int, duration: float, frames: int) -> tuple[list[str], str]:
    lw, lh = _lowres(width, height, 4)
    margin = _even(lw // 4)
    rng = random.Random(look.seed + 2)
    cs = ":".join(f"c{i}=0x{c}" for i, c in enumerate(look.palette[1:]))
    gradient = (f"gradients=s={lw}x{lh}:r={fps}:d={duration}:{cs}:n={len(look.palette) - 1}:"
                f"speed={round(0.008 * look.speed, 4)}:type={('linear', 'radial', 'linear')[look.variant]}:"
                f"seed={look.seed}")
    nw, nh = max(4, lw // 35), max(4, lh // 35)
    sigma = max(2, lw // 20)
    ax, ay = round(rng.uniform(0.2, 0.4) * look.speed, 3), round(rng.uniform(0.15, 0.35) * look.speed, 3)
    px, py = round(rng.uniform(0, 6.28), 3), round(rng.uniform(0, 6.28), 3)
    hue0, hue_rate = round(rng.uniform(-0.5, 0.5), 3), round(rng.uniform(0.1, 0.3) * look.speed, 3)
    # A single static noise frame, upscaled and blurred into soft clouds, looped and panned.
    clouds = (
        f"color=c=0xa8a8a8:s={nw}x{nh}:r={fps}:d={duration},trim=end_frame=1,format=gray,"
        f"noise=alls=90:allf=u:all_seed={look.seed},"
        f"scale={lw + 2 * margin}:{lh + 2 * margin}:flags=bicubic,gblur=sigma={sigma},"
        f"loop=loop={frames}:size=1,setpts=N/FRAME_RATE/TB,"
        f"crop={lw}:{lh}:x='{margin}+{margin}*sin(t*{ax}+{px})':y='{margin}+{margin}*cos(t*{ay}+{py})',"
        f"format=gbrp"
    )
    graph = (
        f"{clouds}[c];[0:v]format=gbrp[g];[g][c]blend=all_mode=multiply:shortest=1,"
        f"hue=H='{hue0}+t*{hue_rate}',format=yuv420p,vignette=angle=PI/5:dither=0"
    )
    return ["-f", "lavfi", "-i", gradient], graph
