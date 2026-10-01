"""Final assembly with ffmpeg: shots -> background.mp4 -> + captions, voice, music -> video.mp4.

Three plain ffmpeg passes:

1. Every Shot is normalised into ``workdir/shot_XXX.mp4``: an exact number of frames at
   ``cfg.video`` size and fps ("cover" fit: scale up to fill, then centre-crop), no audio,
   identical x264 settings so the files can be stream-copied together. Videos that are
   too short loop; long ones start at a per-shot offset so a reused stock clip shows
   different footage. Still images get a slow Ken Burns zoom. Shots render in a small
   thread pool; a clip ffmpeg cannot read becomes a plain dark shot instead of a failure.
2. The shots are joined with the concat demuxer (``-c copy``) into ``background.mp4``.
3. The final pass burns the ASS captions in (libass), loudness-normalises the narration,
   mixes it with optional looped, faded and ducked background music, and encodes the
   upload-ready MP4.

ffmpeg runs with ``cwd=workdir`` and filter arguments only ever name files there by bare
relative names ("captions.ass", "fonts"), so they never contain drive letters,
backslashes, spaces or quotes that filter-graph escaping would trip over on Windows.
The ``*_filter`` / ``build_*`` helpers are pure functions so the graphs are unit-testable.
"""
from __future__ import annotations

import os
import random
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePath
from typing import Sequence

from .config import Config
from .models import ClipAsset, Narration, RenderResult
from .utils import AutoShortsError, ensure_dir, log, media_duration, run_ffmpeg
from .visuals import Shot

TAIL_SECONDS = 0.4  # the video keeps running this long after the voice ends
# TikTok's Creator Rewards only count videos longer than one minute (the reason the default
# video.target_seconds is 65). When the target is over a minute but the voice-over ends
# just short of it, the last shot and the music run on (a short outro) to reach this.
MONETIZE_SECONDS = 61.0
MAX_OUTRO_PAD = 4.0  # never add more than this; a much shorter video stays short
FIRST_FADE_SECONDS = 0.25  # fade in from black at the very start of the video
MUSIC_FADE_OUT = 1.5
SHOT_PRESET = "veryfast"  # intermediates only; the final pass uses cfg.video.preset/crf
SHOT_CRF = 18
MAX_WORKERS = 3
KEN_BURNS_ZOOM = 0.12  # stills zoom 1.00 -> 1.12 (or back) over the length of the shot
LOOP_MARGIN = 0.1  # seconds kept clear of a source clip's end when picking an offset
FALLBACK_COLOR = "0x15171c"  # background for shots whose clip cannot be decoded
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
FONT_SUFFIXES = {".ttf", ".otf", ".ttc"}
SUBTITLE_NAME = "captions.ass"  # bare names inside the workdir, see module docstring
FONTS_NAME = "fonts"
AUDIO_FORMAT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
# Gentle ducking: the music drops roughly 6 dB while the voice is speaking.
DUCK = "sidechaincompress=threshold=0.05:ratio=4:attack=20:release=350"
LIMITER = "alimiter=limit=0.95:level=0"  # level=0: limit peaks, don't re-normalise to 0 dBFS
# Every TTS engine speaks at its own level (espeak ends up around -21 LUFS, quiet next to
# other Shorts). The voice is normalised to about -14 LUFS, the usual level on YouTube and
# TikTok, before mixing, so music.volume really is "relative to the voice". loudnorm works
# at 192 kHz internally, hence the resample back to 48 kHz.
VOICE_LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
COLOR_ARGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


# --------------------------------------------------------------------------- timing


def video_duration(cfg: Config, narration_seconds: float) -> float:
    """Length of the final video: narration plus a short tail, capped at cfg.video.max_seconds.

    With video.target_seconds over 60, a video that would end less than MAX_OUTRO_PAD
    seconds short of MONETIZE_SECONDS is stretched to it (see MONETIZE_SECONDS).
    """
    total = narration_seconds + TAIL_SECONDS
    if cfg.video.target_seconds > 60 and MONETIZE_SECONDS - MAX_OUTRO_PAD <= total < MONETIZE_SECONDS:
        total = MONETIZE_SECONDS
    return round(min(total, float(cfg.video.max_seconds)), 3)


def shot_frames(shots: Sequence[Shot], total: float, fps: int) -> list[tuple[Shot, int]]:
    """Frame count per shot so that the shots tile [0, total] exactly.

    Boundaries are absolute start times rounded to frames once, so per-shot rounding never
    drifts. The first shot starts at 0 and the last one runs to ``total`` (covering the
    tail); a gap belongs to the shot before it. Shots starting past ``total`` or rounding
    to zero frames are dropped.
    """
    ordered = sorted(shots, key=lambda s: s.start)
    total_frames = max(1, round(total * fps))
    bounds = [0]
    for shot in ordered[1:]:
        bounds.append(min(max(round(shot.start * fps), bounds[-1]), total_frames))
    bounds.append(total_frames)
    return [(shot, end - start) for shot, start, end in zip(ordered, bounds, bounds[1:]) if end > start]


def clip_offset(index: int, clip_name: str, seconds: float, src_duration: float) -> float:
    """Deterministic start offset inside a long source clip.

    Seeded by the shot index (and file name), so the same stock clip used twice in a video
    shows different footage, while re-rendering a job gives identical output.
    """
    room = src_duration - seconds - LOOP_MARGIN
    if room <= 0:
        return 0.0
    return round(random.Random(f"{index}:{clip_name}").uniform(0.0, room), 3)


# --------------------------------------------------------------------------- filters


def is_image(clip: ClipAsset) -> bool:
    return clip.kind == "image" or Path(clip.path).suffix.lower() in IMAGE_SUFFIXES


def cover_filter(width: int, height: int) -> str:
    """Scale up until the frame is filled, then centre-crop to exactly width x height."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:out_range=tv,"
        f"crop={width}:{height},setsar=1"
    )


def ken_burns_filter(width: int, height: int, fps: int, frames: int, zoom_in: bool = True) -> str:
    """Slow centred zoom (in or out) over ``frames`` frames for a single still image.

    The picture is first scaled/cropped to twice the output size, so zoompan's integer
    crop positions move in half-pixel steps of the output and the motion doesn't jitter.
    The RGB -> YUV conversion happens once, up front, with the BT.709 matrix; zoompan then
    works on yuv420p (about 2.5x faster than yuv444p, no visible difference at 2x).
    """
    w2, h2 = width * 2, height * 2
    span = max(frames - 1, 1)
    zoom = (
        f"1+{KEN_BURNS_ZOOM}*on/{span}" if zoom_in
        else f"{1 + KEN_BURNS_ZOOM:g}-{KEN_BURNS_ZOOM}*on/{span}"
    )
    return (
        f"scale={w2}:{h2}:force_original_aspect_ratio=increase:out_color_matrix=bt709:out_range=tv,"
        f"crop={w2}:{h2},format=yuv420p,"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps={fps},"
        "setsar=1"
    )


def fade_in_filter(fps: int, seconds: float = FIRST_FADE_SECONDS) -> str:
    """Frame-based fade from black (independent of the input's timestamps)."""
    return f"fade=t=in:s=0:n={max(1, round(seconds * fps))}"


def shot_filter(
    image: bool, width: int, height: int, fps: int, frames: int, *, zoom_in: bool = True, fade_in: bool = False
) -> str:
    """-vf chain that turns one source into a ``frames``-long, output-sized yuv420p shot."""
    if image:
        parts = [ken_burns_filter(width, height, fps, frames, zoom_in)]
    else:
        # tpad freezes the last frame if the source ends early (wrong duration metadata),
        # so every shot really has ``frames`` frames and later shots never drift.
        pad = f"tpad=stop_mode=clone:stop_duration={frames / fps + 1:.3f}"
        parts = [f"fps={fps}", cover_filter(width, height), pad]
    if fade_in:
        parts.append(fade_in_filter(fps))
    parts.append("format=yuv420p")
    return ",".join(parts)


def final_video_filter(ass_name: str | None = SUBTITLE_NAME, fonts_dir: str | None = None) -> str:
    """[0:v] -> [vout]: freeze-pad the end as a safety net (-t cuts it), burn in captions.

    ``ass_name``/``fonts_dir`` must be bare names relative to ffmpeg's cwd (no escaping is done).
    """
    parts = ["tpad=stop_mode=clone:stop_duration=1"]
    if ass_name:
        parts.append(f"subtitles={ass_name}" + (f":fontsdir={fonts_dir}" if fonts_dir else ""))
    parts.append("format=yuv420p")
    return f"[0:v:0]{','.join(parts)}[vout]"


def final_audio_filter(duration: float, *, music_volume: float | None = None, duck: bool = False) -> str:
    """Filter graph text producing [aout] from the narration (input 1) and music (input 2).

    The narration is loudness-normalised (VOICE_LOUDNESS) and padded with silence to ``duration``. Music
    (input 2, looped by ``-stream_loop -1``) is trimmed to ``duration``, set to
    ``music_volume``, optionally ducked under the voice (sidechain compression), and
    faded out at the end. ``music_volume=None`` means no music input.
    """
    d = f"{duration:.3f}"
    voice = f"[1:a:0]{VOICE_LOUDNESS},{AUDIO_FORMAT},apad=whole_dur={d}"
    if music_volume is None:
        return f"{voice},{LIMITER}[aout]"
    fade_len = min(MUSIC_FADE_OUT, duration)
    fade = f"afade=t=out:st={max(duration - fade_len, 0.0):.3f}:d={fade_len:.3f}"
    music = f"[2:a:0]{AUDIO_FORMAT},atrim=duration={d},asetpts=PTS-STARTPTS,volume={music_volume:g}"
    mix = f"[voice][music]amix=inputs=2:duration=first:normalize=0,{LIMITER}[aout]"
    if duck:
        return ";".join([
            f"{voice},asplit=2[voice][sidechain]",
            f"{music}[bgm]",
            f"[bgm][sidechain]{DUCK},{fade}[music]",
            mix,
        ])
    return ";".join([f"{voice}[voice]", f"{music},{fade}[music]", mix])


# --------------------------------------------------------------------------- commands


def _shot_encode_args(fps: int) -> list[str]:
    # Identical for every shot (and the fallback) so the concat demuxer can stream-copy.
    return [
        "-an", "-sn", "-dn",
        "-c:v", "libx264", "-preset", SHOT_PRESET, "-crf", str(SHOT_CRF),
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps), *COLOR_ARGS,
    ]


def shot_input_args(clip: ClipAsset, index: int, seconds: float, src_duration: float | None) -> list[str]:
    """Input options: seek to an offset in long videos, loop short (or unknown-length) ones."""
    path = str(clip.path)
    if is_image(clip):
        # image2 without a pattern reads exactly this file, even if its name contains '%'.
        return ["-f", "image2", "-pattern_type", "none", "-i", path]
    if src_duration is not None and src_duration >= seconds + LOOP_MARGIN:
        offset = clip_offset(index, Path(clip.path).name, seconds, src_duration)
        return (["-ss", f"{offset:.3f}"] if offset > 0 else []) + ["-i", path]
    return ["-stream_loop", "-1", "-i", path]


def build_shot_command(
    clip: ClipAsset,
    out_path: str | Path,
    *,
    index: int,
    frames: int,
    width: int,
    height: int,
    fps: int,
    src_duration: float | None = None,
    fade_in: bool = False,
) -> list[str]:
    """ffmpeg args (without 'ffmpeg') rendering one shot of exactly ``frames`` frames."""
    vf = shot_filter(is_image(clip), width, height, fps, frames, zoom_in=index % 2 == 0, fade_in=fade_in)
    return [
        *shot_input_args(clip, index, frames / fps, src_duration),
        "-map", "0:v:0", "-vf", vf, "-frames:v", str(frames),
        *_shot_encode_args(fps), str(out_path),
    ]


def build_fallback_command(
    out_path: str | Path, *, frames: int, width: int, height: int, fps: int, fade_in: bool = False
) -> list[str]:
    """A plain dark shot, encoded exactly like the others (used when a clip is unusable)."""
    vf = ",".join(["setsar=1", *([fade_in_filter(fps)] if fade_in else []), "format=yuv420p"])
    return [
        "-f", "lavfi", "-i", f"color=c={FALLBACK_COLOR}:s={width}x{height}:r={fps}",
        "-vf", vf, "-frames:v", str(frames), *_shot_encode_args(fps), str(out_path),
    ]


def concat_list_text(files: Sequence[str | PurePath]) -> str:
    """Concat-demuxer list: one ``file '...'`` line per file, forward slashes, quotes escaped."""
    lines = ["ffconcat version 1.0"]
    for f in files:
        name = (f if isinstance(f, PurePath) else PurePath(f)).as_posix()
        lines.append("file '" + name.replace("'", "'\\''") + "'")
    return "\n".join(lines) + "\n"


def build_final_command(
    cfg: Config,
    *,
    background: str | Path,
    narration_audio: str | Path,
    out_path: str | Path,
    duration: float,
    ass_name: str | None = SUBTITLE_NAME,
    fonts_dir: str | None = None,
    music_path: str | Path | None = None,
) -> list[str]:
    """ffmpeg args (without 'ffmpeg') for the final pass; meant to run with cwd=workdir.

    Inputs: 0 = background video, 1 = narration, 2 = music (looped), if any.
    """
    v = cfg.video
    fps = int(round(v.fps))
    music_volume = cfg.music.volume if music_path and cfg.music.volume > 0 else None
    inputs = ["-i", str(background), "-i", str(narration_audio)]
    if music_volume is not None:
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]
    graph = ";".join([
        final_video_filter(ass_name, fonts_dir),
        final_audio_filter(duration, music_volume=music_volume, duck=cfg.music.duck),
    ])
    return [
        *inputs,
        "-filter_complex", graph,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", str(v.preset), "-crf", str(v.crf),
        "-profile:v", "high", "-level:v", "4.2", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-g", str(fps * 2), *COLOR_ARGS,
        "-c:a", "aac", "-b:a", str(v.audio_bitrate), "-ar", "48000", "-ac", "2",
        "-t", f"{duration:.3f}",
        "-max_muxing_queue_size", "4096",
        "-movflags", "+faststart",
        "-f", "mp4", str(out_path),
    ]


# --------------------------------------------------------------------------- passes


def _source_duration(clip: ClipAsset) -> float | None:
    """Real length of a video file (probed; provider metadata can be off), None if unknown."""
    if is_image(clip):
        return None
    try:
        return media_duration(clip.path)
    except AutoShortsError:
        return float(clip.duration) if clip.duration and clip.duration > 0 else None


def _render_shot(cfg: Config, index: int, clip: ClipAsset, frames: int, out_path: Path) -> Path:
    v = cfg.video
    common = dict(frames=frames, width=v.width, height=v.height, fps=int(round(v.fps)), fade_in=index == 0)
    src = Path(clip.path)
    if src.is_file():
        args = build_shot_command(clip, out_path, index=index, src_duration=_source_duration(clip), **common)
        try:
            run_ffmpeg(args, desc=f"shot {index} ({src.name})")
            return out_path
        except AutoShortsError as exc:
            log.warning("shot %d: could not use %s, using a plain background instead. %s", index, src, exc)
    else:
        log.warning("shot %d: clip file not found (%s), using a plain background", index, src)
    run_ffmpeg(build_fallback_command(out_path, **common), desc=f"shot {index} (fallback)")
    return out_path


def render_shots(cfg: Config, plan: Sequence[tuple[Shot, int]], workdir: Path) -> list[Path]:
    """Render every (shot, frames) pair into workdir/shot_XXX.mp4, a few at a time."""
    outs = [workdir / f"shot_{i:03d}.mp4" for i in range(len(plan))]
    workers = max(1, min(MAX_WORKERS, len(plan), (os.cpu_count() or 2) // 2 or 1))
    log.info("rendering %d shot(s) at %dx%d %s fps (%d in parallel)",
             len(plan), cfg.video.width, cfg.video.height, cfg.video.fps, workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_render_shot, cfg, i, shot.clip, frames, out)
            for i, ((shot, frames), out) in enumerate(zip(plan, outs))
        ]
        try:
            for fut in futures:
                fut.result()
        except BaseException:
            for fut in futures:
                fut.cancel()
            raise
    return outs


def concat_shots(files: Sequence[Path], out_path: Path) -> Path:
    """Join same-format shots without re-encoding (concat demuxer, run inside out_path's folder)."""
    folder = out_path.parent
    names: list[str | PurePath] = [
        f.name if f.parent.resolve() == folder.resolve() else f.resolve() for f in files
    ]
    list_path = folder / "concat.txt"
    list_path.write_text(concat_list_text(names), encoding="utf-8", newline="\n")
    run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", list_path.name, "-map", "0:v:0", "-c", "copy", "-an", out_path.name],
        desc="concat shots", cwd=folder,
    )
    return out_path


def _stage_subtitles(ass_path: str | Path | None, workdir: Path) -> str | None:
    """Put the captions into the workdir under a bare name the subtitles filter can use."""
    if ass_path is None:
        return None
    src = Path(ass_path)
    if not src.is_file():
        raise AutoShortsError(f"captions file not found: {src}")
    dest = workdir / SUBTITLE_NAME
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    return SUBTITLE_NAME


def _stage_fonts(cfg: Config, workdir: Path) -> str | None:
    """Copy cfg.captions.fonts_dir's font files (flattened) to workdir/fonts for libass."""
    if not cfg.captions.fonts_dir:
        return None
    src = cfg.path(cfg.captions.fonts_dir)
    if not src.is_dir():
        return None
    fonts = sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in FONT_SUFFIXES)
    if not fonts:
        return None
    dest = ensure_dir(workdir / FONTS_NAME)
    for font in fonts:
        shutil.copy2(font, dest / font.name)
    return FONTS_NAME


def render_video(
    cfg: Config,
    narration: Narration,
    shots: Sequence[Shot],
    ass_path: str | Path | None,
    out_path: str | Path,
    music_path: str | Path | None = None,
    workdir: str | Path | None = None,
) -> RenderResult:
    """Render the finished short: background shots + burned-in captions + voice (+ music).

    Intermediates go to ``workdir`` (left for the caller to clean up); without one a
    temporary folder is used and removed afterwards unless ``cfg.keep_intermediate``.
    The video is written to a temporary name and renamed to ``out_path`` on success.
    """
    if not shots:
        raise AutoShortsError("nothing to render: no shots were planned")
    voice = Path(narration.audio_path).resolve()
    if not voice.is_file():
        raise AutoShortsError(f"narration audio not found: {voice}")
    if ass_path is not None and not Path(ass_path).is_file():
        raise AutoShortsError(f"captions file not found: {ass_path}")
    music = Path(music_path).resolve() if music_path else None
    if music is not None and not music.is_file():
        log.warning("music file not found, rendering without music: %s", music)
        music = None

    out_path = Path(out_path).resolve()
    ensure_dir(out_path.parent)
    tmp_out = out_path.with_name(f"{out_path.stem}.part.mp4")
    total = video_duration(cfg, narration.duration)
    if total > narration.duration + TAIL_SECONDS + 0.01:
        log.info("adding a %.1f s outro so the video is over one minute (TikTok Creator Rewards)",
                 total - narration.duration)
    if narration.duration + TAIL_SECONDS > total + 0.01:  # total is rounded to milliseconds
        log.warning("narration is %.1f s; the video is capped at %.1f s (video.max_seconds)",
                    narration.duration, total)

    own_work = workdir is None
    work = Path(tempfile.mkdtemp(prefix="autoshorts-render-")) if own_work else ensure_dir(workdir)
    try:
        plan = shot_frames(shots, total, int(round(cfg.video.fps)))
        background = concat_shots(render_shots(cfg, plan, work), work / "background.mp4")
        ass_name = _stage_subtitles(ass_path, work)
        fonts = _stage_fonts(cfg, work) if ass_name else None
        args = build_final_command(
            cfg, background=background.name, narration_audio=voice, out_path=tmp_out,
            duration=total, ass_name=ass_name, fonts_dir=fonts, music_path=music,
        )
        log.info("final render: %.1f s, captions=%s, music=%s", total, bool(ass_name),
                 music.name if music else "none")
        run_ffmpeg(args, desc="final render", cwd=work)
        os.replace(tmp_out, out_path)
    finally:
        tmp_out.unlink(missing_ok=True)
        if own_work and not cfg.keep_intermediate:
            shutil.rmtree(work, ignore_errors=True)
        elif own_work:
            log.info("render intermediates kept in %s", work)
    return RenderResult(video_path=out_path, duration=media_duration(out_path))


def make_thumbnail(video_path: str | Path, out_path: str | Path, at: float = 1.0) -> Path:
    """Save one frame of the video as a JPEG, taken at ``at`` seconds (or mid-video if shorter)."""
    video_path, out_path = Path(video_path), Path(out_path)
    ensure_dir(out_path.parent)
    try:
        duration = media_duration(video_path)
    except AutoShortsError:
        duration = 0.0
    when = max(0.0, min(at, duration / 2)) if duration > 0 else 0.0
    run_ffmpeg(
        ["-ss", f"{when:.3f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(out_path)],
        desc="thumbnail",
    )
    return out_path
