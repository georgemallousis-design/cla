"""Housekeeping for unattended runs: keep the cache and output folders from filling the disk.

Run at the start of every video (``pipeline.make_video``); it never fails a run.

* ``cache_dir`` (stock downloads, generated backgrounds, Pixabay search results) is
  trimmed to ``retention.cache_max_mb``, oldest first. Cache hits refresh a file's mtime
  (``utils.touch``), so clips that are still being reused stay.
* Leftovers of interrupted runs (``*.part`` downloads, ``*.tmp`` files, job ``work/``
  folders, ``video.part.mp4``) older than STALE_SECONDS are deleted. A run killed by a
  service timeout (SIGTERM) never reaches its own cleanup.
* With ``retention.output_keep_days`` > 0, job folders older than that lose their
  video.mp4 and thumbnail.jpg; job.json, script.json, metadata and captions stay as a
  record. 0 (the default) keeps every video.
"""
from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from .config import Config
from .utils import log

STALE_SECONDS = 24 * 3600
STALE_SUFFIXES = (".part", ".tmp")
OLD_JOB_FILES = ("video.mp4", "thumbnail.jpg")


def _files(folder: Path) -> list[tuple[Path, os.stat_result]]:
    out = []
    for dirpath, _dirnames, filenames in os.walk(folder):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                out.append((path, path.stat()))
            except OSError:
                continue
    return out


def _unlink(path: Path) -> int:
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except OSError:
        return 0


def _stale(path: Path, st: os.stat_result, now: float) -> bool:
    name = path.name
    leftover = name.endswith(STALE_SUFFIXES) or name.endswith(".tmp.mp4") or name.endswith(".part.mp4")
    return leftover and now - st.st_mtime > STALE_SECONDS


def prune_cache(folder: Path, max_bytes: int, now: float | None = None) -> int:
    """Delete stale temp files, then the oldest files until ``folder`` is under ``max_bytes``.

    Returns the number of bytes freed. ``max_bytes <= 0`` disables the size limit.
    """
    if not folder.is_dir():
        return 0
    now = time.time() if now is None else now
    freed = 0
    keep: list[tuple[Path, os.stat_result]] = []
    for path, st in _files(folder):
        if _stale(path, st, now):
            freed += _unlink(path)
        else:
            keep.append((path, st))
    if max_bytes > 0:
        total = sum(st.st_size for _, st in keep)
        for path, st in sorted(keep, key=lambda item: item[1].st_mtime):
            if total <= max_bytes:
                break
            if now - st.st_mtime < 60:  # being written or just used by this run
                continue
            got = _unlink(path)
            freed += got
            total -= got
    return freed


def job_age(job: Path, now: float) -> float:
    """Seconds since a job was made: from its YYYYMMDD-HHMMSS name prefix (the folder's
    mtime changes whenever something inside is added or removed), else the mtime."""
    try:
        made = datetime.strptime(job.name[:15], "%Y%m%d-%H%M%S").timestamp()
    except ValueError:
        made = job.stat().st_mtime
    return now - made


def prune_outputs(folder: Path, keep_days: float, now: float | None = None, remove_work: bool = True) -> int:
    """Remove leftovers of killed runs in job folders, and old videos when ``keep_days`` > 0."""
    if not folder.is_dir():
        return 0
    now = time.time() if now is None else now
    freed = 0
    for job in folder.iterdir():
        if not job.is_dir():
            continue
        try:
            age = job_age(job, now)
        except OSError:
            continue
        work = job / "work"
        if remove_work and work.is_dir() and age > STALE_SECONDS:
            freed += sum(st.st_size for _, st in _files(work))
            shutil.rmtree(work, ignore_errors=True)
        for path, st in _files(job):
            if _stale(path, st, now):
                freed += _unlink(path)
        if keep_days > 0 and age > keep_days * 86400:
            for name in OLD_JOB_FILES:
                freed += _unlink(job / name)
    return freed


def housekeeping(cfg: Config) -> int:
    """Apply cfg.retention; returns bytes freed. Problems are logged, never raised."""
    freed = 0
    try:
        freed += prune_cache(cfg.path(cfg.visuals.cache_dir), int(cfg.retention.cache_max_mb) * 1024 * 1024)
        freed += prune_outputs(cfg.path(cfg.output_dir), float(cfg.retention.output_keep_days),
                               remove_work=not cfg.keep_intermediate)
    except Exception as exc:  # housekeeping must never cost a video
        log.warning("housekeeping failed: %s", exc)
    if freed:
        log.info("housekeeping: freed %.1f MB (cache limit %s MB, retention.output_keep_days %s)",
                 freed / 1e6, cfg.retention.cache_max_mb, cfg.retention.output_keep_days)
    return freed


def folder_size(folder: Path) -> int:
    return sum(st.st_size for _, st in _files(folder)) if folder.is_dir() else 0
