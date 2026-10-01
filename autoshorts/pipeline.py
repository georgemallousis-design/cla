"""Pipeline: topic -> finished job folder (video, thumbnail, script, captions, metadata).

``make_video`` runs every stage for one video::

    <output_dir>/<YYYYMMDD-HHMMSS>-<slug>/
        video.mp4  thumbnail.jpg  script.json  captions.ass
        metadata.json  youtube.txt  tiktok.txt  credits.txt  job.json
        work/        temporary files, deleted unless keep_intermediate
        error.txt    only when the video failed (traceback)

Stage modules are imported lazily inside the small ``stage`` wrappers below, so the
CLI still starts (``--help``, ``doctor``) when one stage cannot be imported, and tests
can replace any stage by monkeypatching the wrapper on this module.
"""
from __future__ import annotations

import json
import shutil
import time
import traceback
from contextlib import contextmanager
from dataclasses import fields, is_dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .config import Config
from .models import ClipAsset, Narration, RenderResult, UploadResult, VideoJob, VideoScript
from .topics import TopicQueue, write_json_atomic
from .utils import AutoShortsError, ensure_dir, log, slugify

PLATFORMS = ("youtube", "tiktok")
PLATFORM_ALIASES = {"yt": "youtube", "youtube_shorts": "youtube", "shorts": "youtube", "tt": "tiktok"}
MAX_SHORTEN_ATTEMPTS = 3  # re-syntheses allowed when the narration is too long
DURATION_MARGIN = 0.5  # seconds kept free below video.max_seconds
MIN_SEGMENTS = 2  # never shorten below hook + ending
SLUG_LENGTH = 50  # keeps folder paths short (Windows MAX_PATH)
SAME_ERROR_LIMIT = 3  # a batch stops when this many videos in a row fail with the same error


# --------------------------------------------------------------------------- stage wrappers
# One thin function per stage. Tests monkeypatch these on the module.


def resolve_format(fmt: str | None) -> str:
    """A validated format name, or "random" left for the script generator to resolve
    (the offline bank then picks the format that best matches the topic)."""
    from .script import resolve_format as _resolve

    if (fmt or "random").strip().lower() == "random":
        return "random"
    return _resolve(fmt)


def generate_script(cfg: Config, topic: str, fmt: str) -> VideoScript:
    from .script import get_generator

    return get_generator(cfg).generate(topic, fmt)


def synthesize_narration(cfg: Config, script: VideoScript, workdir: Path) -> Narration:
    from .tts import synthesize_narration as _synthesize

    return _synthesize(cfg, script, workdir)


def build_captions(cfg: Config, narration: Narration, script: VideoScript, out_path: Path) -> Path:
    from .captions import build_ass

    return build_ass(cfg, narration, script, out_path)


def plan_shots(cfg: Config, narration: Narration, workdir: Path, topic: str = "") -> list:
    from .visuals import plan_shots as _plan

    return _plan(cfg, narration, workdir, topic=topic)


def pick_music(cfg: Config) -> Path | None:
    from .music import pick_track

    return pick_track(cfg)


def render_video(
    cfg: Config, narration: Narration, shots: Sequence, ass_path: Path, out_path: Path,
    music_path: Path | None, workdir: Path,
) -> RenderResult:
    from .render import render_video as _render

    return _render(cfg, narration, shots, ass_path, out_path, music_path=music_path, workdir=workdir)


def make_thumbnail(video_path: Path, out_path: Path) -> Path:
    from .render import make_thumbnail as _thumb

    return _thumb(video_path, out_path)


def build_metadata(cfg: Config, script: VideoScript, clips: list[ClipAsset]) -> dict[str, Any]:
    from .metadata import build_metadata as _build

    return _build(cfg, script, clips)


def write_metadata(folder: Path, meta: dict[str, Any]) -> Path:
    from .metadata import write_metadata as _write

    return _write(folder, meta)


def upload_youtube(cfg: Config, video: Path, meta: dict[str, Any], thumbnail: Path | None) -> UploadResult:
    from .upload import youtube

    return youtube.upload(cfg, video, meta, thumbnail)


def upload_tiktok(cfg: Config, video: Path, meta: dict[str, Any]) -> UploadResult:
    from .upload import tiktok

    return tiktok.upload(cfg, video, meta)


# --------------------------------------------------------------------------- helpers


def parse_platforms(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """"youtube,tiktok" / ["yt", "tiktok"] / "all" -> ("youtube", "tiktok"). "" / "none" -> ()."""
    if value is None:
        return ()
    items = value.split(",") if isinstance(value, str) else [p for v in value for p in str(v).split(",")]
    out: list[str] = []
    for raw in items:
        name = raw.strip().lower().replace("-", "_")
        if not name or name == "none":
            continue
        names = PLATFORMS if name in ("all", "both") else (PLATFORM_ALIASES.get(name, name),)
        for n in names:
            if n not in PLATFORMS:
                raise AutoShortsError(f"unknown upload platform '{raw.strip()}' (use: {', '.join(PLATFORMS)})")
            if n not in out:
                out.append(n)
    return tuple(out)


def default_platforms(cfg: Config) -> tuple[str, ...]:
    """Platforms with ``upload.<platform>.enabled: true`` in the config."""
    return tuple(p for p in PLATFORMS if getattr(cfg.upload, p).enabled)


def _fmt_seconds(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m{secs:02d}s"


class _Stages:
    """Numbered, timed stage logging: "[2/6] Voice-over (edge)..."."""

    def __init__(self, total: int):
        self.total = total
        self.n = 0

    @contextmanager
    def __call__(self, label: str) -> Iterator[None]:
        self.n += 1
        log.info("[%d/%d] %s...", self.n, self.total, label)
        start = time.monotonic()
        yield
        log.info("[%d/%d] %s done in %s", self.n, self.total, label, _fmt_seconds(time.monotonic() - start))


def create_job_folder(cfg: Config, topic: str, now: datetime | None = None) -> Path:
    """<output_dir>/<YYYYMMDD-HHMMSS>-<slug>, with -2, -3... appended if it exists."""
    out_dir = ensure_dir(cfg.path(cfg.output_dir))
    base = f"{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}-{slugify(topic, SLUG_LENGTH)}"
    for n in range(1, 1000):
        folder = out_dir / (base if n == 1 else f"{base}-{n}")
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        return folder
    raise AutoShortsError(f"could not create a job folder in {out_dir}")


def unique_clips(shots: Sequence) -> list[ClipAsset]:
    """Each shot's clip once (by path), in order of first use."""
    seen: set[str] = set()
    out: list[ClipAsset] = []
    for shot in shots:
        key = str(Path(shot.clip.path))
        if key not in seen:
            seen.add(key)
            out.append(shot.clip)
    return out


def topic_matches(topic: str, script: VideoScript) -> bool:
    """Is ``script`` about ``topic``? False only when the script's own topic, title and
    hashtags share no content word with it (the offline content bank picked an unrelated
    script). LLM scripts keep the requested topic, so they always match."""
    from .script.offline import tokens

    wanted = tokens(topic)
    if not wanted:
        return True
    have = tokens(" ".join([script.topic, script.title, " ".join(script.hashtags)]))
    return bool(wanted & have)


def _move_job(cfg: Config, job: VideoJob, label: str) -> None:
    """Move the (still nearly empty) job folder to one named after ``label``."""
    old = job.folder
    new = create_job_folder(cfg, label)
    for item in old.iterdir():
        shutil.move(str(item), str(new / item.name))
    old.rmdir()
    job.folder, job.id = new, new.name


def shorten_script(script: VideoScript, narration: Narration, max_seconds: float) -> VideoScript:
    """Drop segments before the last one (second-to-last first) until it should fit.

    The hook (first) and the ending (last: answer, call to action) are always kept.
    Raises AutoShortsError when nothing more can be dropped.
    """
    segments = list(script.segments)
    if len(segments) <= MIN_SEGMENTS:
        raise AutoShortsError(
            f"the narration is {narration.duration:.1f}s, longer than video.max_seconds "
            f"({max_seconds + DURATION_MARGIN:.0f}s), and the script cannot be shortened further"
        )
    # Each segment "costs" up to the next one's start, which includes the silence gap.
    timed = narration.segments
    lengths = [
        max(0.0, (timed[i + 1].start if i + 1 < len(timed) else ts.end) - ts.start)
        for i, ts in enumerate(timed)
    ]
    excess = narration.duration - max_seconds
    dropped = 0
    removed = 0.0
    idx = len(segments) - 2
    while idx >= 1 and len(segments) - dropped > MIN_SEGMENTS:
        removed += lengths[idx] if idx < len(lengths) else 0.0
        dropped += 1
        idx -= 1
        if removed >= excess:
            break
    keep = segments[: len(segments) - 1 - dropped] + segments[-1:]
    log.warning("narration is %.1fs (limit %.1fs): dropping %d segment(s) and re-recording",
                narration.duration, max_seconds, dropped)
    return replace(script, segments=keep)


def _relative(path: Path, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)


def to_jsonable(value: Any, base: Path) -> Any:
    """Dataclasses -> dicts, Paths -> strings relative to ``base`` where possible."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name), base) for f in fields(value)}
    if isinstance(value, Path):
        return _relative(value, base)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, base) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v, base) for v in value]
    return value


def job_to_dict(job: VideoJob, **extra: Any) -> dict[str, Any]:
    data = to_jsonable(job, job.folder)
    data["folder"] = str(job.folder)
    data.update(extra)
    return data


def save_job(job: VideoJob, **extra: Any) -> Path:
    path = job.folder / "job.json"
    write_json_atomic(path, job_to_dict(job, **extra))
    return path


def save_script(folder: Path, script: VideoScript) -> Path:
    path = folder / "script.json"
    path.write_text(json.dumps(script.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _upload_one(cfg: Config, platform: str, video: Path, meta: dict[str, Any], thumbnail: Path | None) -> UploadResult:
    """Upload to one platform; any failure becomes UploadResult(ok=False)."""
    try:
        if platform == "youtube":
            result = upload_youtube(cfg, video, meta.get("youtube", {}), thumbnail)
        elif platform == "tiktok":
            result = upload_tiktok(cfg, video, meta.get("tiktok", {}))
        else:
            raise AutoShortsError(f"unknown upload platform '{platform}'")
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # an upload problem must never cost the rendered video
        if not isinstance(exc, AutoShortsError):
            log.debug("%s upload crashed", platform, exc_info=True)
        result = UploadResult(platform=platform, ok=False, error=str(exc) or type(exc).__name__)
    if result.ok:
        log.info("%s: uploaded %s", platform, result.url or result.id)
    else:
        log.warning("%s upload failed: %s", platform, result.error)
    return result


def upload_all(cfg: Config, platforms: Sequence[str], video: Path, meta: dict[str, Any],
               thumbnail: Path | None) -> list[UploadResult]:
    return [_upload_one(cfg, p, video, meta, thumbnail) for p in platforms]


def _cleanup_work(cfg: Config, work: Path) -> None:
    if cfg.keep_intermediate:
        log.info("intermediate files kept in %s", work)
    else:
        shutil.rmtree(work, ignore_errors=True)


def _write_error(folder: Path, exc: BaseException) -> None:
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if isinstance(exc, KeyboardInterrupt):
        text = "Interrupted by the user (Ctrl+C).\n\n" + text
    try:
        (folder / "error.txt").write_text(text, encoding="utf-8")
    except OSError:  # pragma: no cover - disk full / folder gone
        log.debug("could not write error.txt", exc_info=True)


# --------------------------------------------------------------------------- public API


def make_video(
    cfg: Config, topic: str | None = None, fmt: str | None = None, upload_to: Iterable[str] = ()
) -> VideoJob:
    """Make one video (and optionally upload it). Returns the finished VideoJob."""
    started = time.monotonic()
    platforms = parse_platforms(upload_to)
    queue = TopicQueue(cfg)
    topic = " ".join((topic or "").split()) or queue.next()
    fmt = resolve_format(fmt or cfg.script.format)

    folder = create_job_folder(cfg, topic)
    work = ensure_dir(folder / "work")
    job = VideoJob(id=folder.name, topic=topic, folder=folder, created_at=datetime.now().astimezone().isoformat(timespec="seconds"))
    log.info("new video: %r (%s) -> %s", topic, fmt, folder)
    stage = _Stages(6 + (1 if platforms else 0))
    on_topic = True
    try:
        with stage(f"Script ({fmt})"):
            script = generate_script(cfg, topic, fmt)
            job.script = script
            on_topic = topic_matches(topic, script)
            if not on_topic:
                log.warning(
                    "no script about %r is available offline, so this video is about %r instead; "
                    "the topic stays unused. For videos about your own topics, install Ollama or set "
                    "%s in .env (free keys: https://console.groq.com/keys).",
                    topic, script.topic or script.title, cfg.script.openai_compatible.api_key_env,
                )
                _move_job(cfg, job, script.title or script.topic)
                job.topic = script.topic or job.topic
                folder, work = job.folder, job.folder / "work"
            save_script(folder, script)
            log.info("title: %s (%s)", script.title, script.format)

        with stage(f"Voice-over ({cfg.tts.provider})"):
            narration, script = _narrate(cfg, script, work, folder)
            job.script, job.narration = script, narration

        with stage("Captions"):
            ass_path = build_captions(cfg, narration, script, folder / "captions.ass")

        with stage("Background visuals"):
            shots = plan_shots(cfg, narration, work, topic=script.topic or topic)  # what the video is about
            job.clips = unique_clips(shots)

        with stage("Render"):
            music = pick_music(cfg)
            render = render_video(cfg, narration, shots, ass_path, folder / "video.mp4", music, work)
            render.thumbnail_path = _thumbnail(render.video_path, folder / "thumbnail.jpg")
            job.render = render

        with stage("Metadata"):
            meta = build_metadata(cfg, script, job.clips)
            write_metadata(folder, meta)
    except BaseException as exc:
        log.error("video failed: %s (details in %s)", str(exc).splitlines()[0] if str(exc) else type(exc).__name__,
                  folder / "error.txt")
        _write_error(folder, exc)
        try:
            save_job(job, status="failed", error=str(exc))
        except Exception:  # pragma: no cover - never mask the real error
            log.debug("could not save job.json", exc_info=True)
        _cleanup_work(cfg, work)
        raise

    # The video exists from here on: nothing below may turn it into a failure.
    try:
        if on_topic:
            queue.mark_used(topic)
    except OSError as exc:
        log.warning("could not record %r as used in %s: %s", topic, queue.state_file, exc)
    if platforms:
        save_job(job, status="rendered")
        with stage(f"Upload ({', '.join(platforms)})"):
            job.uploads = upload_all(cfg, platforms, render.video_path, meta, render.thumbnail_path)
    save_job(job, status="done", elapsed_seconds=round(time.monotonic() - started, 1))
    _cleanup_work(cfg, work)
    log.info("done: %s (%.1fs video, made in %s)", folder, render.duration, _fmt_seconds(time.monotonic() - started))
    return job


def _narrate(cfg: Config, script: VideoScript, work: Path, folder: Path) -> tuple[Narration, VideoScript]:
    """Synthesize the voice-over, shortening the script while it is longer than allowed."""
    limit = float(cfg.video.max_seconds) - DURATION_MARGIN
    for attempt in range(MAX_SHORTEN_ATTEMPTS + 1):
        narration = synthesize_narration(cfg, script, work)
        if narration.duration <= limit:
            if attempt:
                save_script(folder, script)  # script.json matches what is actually said
            log.info("voice-over: %.1fs (%s)", narration.duration, narration.engine or cfg.tts.provider)
            return narration, script
        if attempt == MAX_SHORTEN_ATTEMPTS:
            break
        script = shorten_script(script, narration, limit)
    raise AutoShortsError(
        f"the narration is still {narration.duration:.1f}s after shortening the script "
        f"{MAX_SHORTEN_ATTEMPTS} times; the limit is video.max_seconds = {cfg.video.max_seconds}"
    )


def _thumbnail(video: Path, out_path: Path) -> Path | None:
    try:
        return make_thumbnail(video, out_path)
    except AutoShortsError as exc:
        log.warning("could not make a thumbnail: %s", exc)
        return None


def make_batch(
    cfg: Config, n: int, fmt: str | None = None, upload_to: Iterable[str] = ()
) -> list[VideoJob]:
    """Make ``n`` videos one after another. A failed video is logged and skipped.

    Returns the successful jobs; raises AutoShortsError when every video failed.
    """
    if n < 1:
        raise AutoShortsError("the number of videos must be at least 1")
    platforms = parse_platforms(upload_to)
    queue = TopicQueue(cfg)
    jobs: list[VideoJob] = []
    errors: list[str] = []
    tried: list[str] = []  # topics that failed or were off-topic, so they are not retried in this batch
    last_error, repeats = "", 0
    started = time.monotonic()
    for i in range(1, n + 1):
        log.info("=== video %d/%d ===", i, n)
        try:
            topic = queue.next(exclude=tried)
        except AutoShortsError as exc:
            errors.append(str(exc))
            log.error("stopping the batch: %s", exc)
            break
        try:
            job = make_video(cfg, topic=topic, fmt=fmt, upload_to=platforms)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            tried.append(topic)
            first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            errors.append(f"{topic}: {first_line}")
            repeats = repeats + 1 if first_line == last_error else 1
            last_error = first_line
            if repeats >= SAME_ERROR_LIMIT and i < n:
                log.error("video %d/%d failed with the same error %d times in a row; stopping the batch: %s",
                          i, n, repeats, first_line)
                break
            log.error("video %d/%d (%s) failed: %s; continuing with the next one", i, n, topic, first_line)
            continue
        repeats, last_error = 0, ""
        if job.script is not None and not topic_matches(topic, job.script):
            tried.append(topic)  # made a video about something else: try the next topic next time
        jobs.append(job)
    log.info("batch finished: %d/%d videos made in %s", len(jobs), n, _fmt_seconds(time.monotonic() - started))
    if not jobs:
        detail = "\n  ".join(errors[-5:]) or "no video was attempted"
        raise AutoShortsError(f"every video in the batch failed:\n  {detail}")
    return jobs


def load_job(job_dir: str | Path) -> dict[str, Any]:
    """job.json of a job folder as a dict ({} when missing or unreadable)."""
    path = Path(job_dir) / "job.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("could not read %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def upload_job(cfg: Config, job_dir: str | Path, platforms: Iterable[str]) -> list[UploadResult]:
    """Upload an existing job folder (video.mp4 + metadata.json) and record it in job.json."""
    folder = Path(job_dir).expanduser().resolve()
    targets = parse_platforms(platforms)
    if not targets:
        raise AutoShortsError(f"no upload platform given (use: {', '.join(PLATFORMS)})")
    if not folder.is_dir():
        raise AutoShortsError(f"job folder not found: {folder}")
    video = folder / "video.mp4"
    if not video.is_file():
        raise AutoShortsError(f"no video.mp4 in {folder}; was the video rendered successfully?")
    meta_path = folder / "metadata.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AutoShortsError(f"no metadata.json in {folder}") from None
    except (OSError, ValueError) as exc:
        raise AutoShortsError(f"could not read {meta_path}: {exc}") from exc
    if not isinstance(meta, dict):
        raise AutoShortsError(f"{meta_path} does not contain a JSON object")
    thumbnail = folder / "thumbnail.jpg"

    results = upload_all(cfg, targets, video, meta, thumbnail if thumbnail.is_file() else None)

    data = load_job(folder) or {"id": folder.name, "folder": str(folder)}
    uploads = data.get("uploads") if isinstance(data.get("uploads"), list) else []
    uploads.extend(to_jsonable(r, folder) for r in results)
    data["uploads"] = uploads
    write_json_atomic(folder / "job.json", data)
    return results


__all__ = [
    "PLATFORMS", "make_video", "make_batch", "upload_job", "load_job", "parse_platforms",
    "default_platforms", "shorten_script", "create_job_folder", "unique_clips", "to_jsonable",
    "topic_matches",
]
