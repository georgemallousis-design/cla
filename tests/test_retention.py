"""Tests for autoshorts.retention: cache and output pruning for unattended servers."""
from __future__ import annotations

import os
import time
from pathlib import Path

from autoshorts.config import Config
from autoshorts.retention import housekeeping, prune_cache, prune_outputs

MB = 1024 * 1024
DAY = 86400


def make(path: Path, size: int, age: float, now: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (now - age, now - age))
    return path


def test_prune_cache_deletes_oldest_until_under_the_limit(tmp_path):
    now = time.time()
    old = make(tmp_path / "pexels_1.mp4", 3 * MB, 10 * DAY, now)
    mid = make(tmp_path / "pexels_2.mp4", 3 * MB, 5 * DAY, now)
    new = make(tmp_path / "generated_3.mp4", 3 * MB, 1 * DAY, now)
    search = make(tmp_path / "pixabay_search" / "a.json", 1000, 2 * DAY, now)
    freed = prune_cache(tmp_path, 7 * MB, now=now)
    assert freed == 3 * MB
    assert not old.exists() and mid.exists() and new.exists() and search.exists()


def test_prune_cache_removes_stale_partial_files_only(tmp_path):
    now = time.time()
    stale = make(tmp_path / "pexels_9.mp4.part", 100, 2 * DAY, now)
    fresh = make(tmp_path / "pexels_8.mp4.part", 100, 60, now)  # a download in progress
    tmp = make(tmp_path / "generated_x.123.tmp.mp4", 100, 3 * DAY, now)
    prune_cache(tmp_path, 0, now=now)  # 0 = no size limit
    assert not stale.exists() and fresh.exists() and not tmp.exists()


def test_prune_outputs_cleans_killed_runs_and_old_videos(tmp_path):
    now = time.time()
    stamp = lambda age: time.strftime("%Y%m%d-%H%M%S", time.localtime(now - age))  # noqa: E731
    old_job = tmp_path / f"{stamp(40 * DAY)}-old"
    make(old_job / "video.mp4", 1000, 40 * DAY, now)
    make(old_job / "thumbnail.jpg", 10, 40 * DAY, now)
    make(old_job / "job.json", 10, 40 * DAY, now)
    make(old_job / "work" / "shot_000.mp4", 1000, 40 * DAY, now)
    new_job = tmp_path / f"{stamp(3600)}-new"
    make(new_job / "video.mp4", 1000, 3600, now)
    make(new_job / "work" / "shot_000.mp4", 1000, 3600, now)  # a running job

    prune_outputs(tmp_path, keep_days=0, now=now)  # default: videos are kept
    assert (old_job / "video.mp4").exists() and not (old_job / "work").exists()
    assert (new_job / "work").exists()

    prune_outputs(tmp_path, keep_days=30, now=now)
    assert not (old_job / "video.mp4").exists() and not (old_job / "thumbnail.jpg").exists()
    assert (old_job / "job.json").exists() and (new_job / "video.mp4").exists()


def test_housekeeping_uses_config_and_never_raises(tmp_path, monkeypatch):
    cfg = Config(base_dir=tmp_path)
    cfg.retention.cache_max_mb = 1
    now = time.time()
    make(cfg.path(cfg.visuals.cache_dir) / "a.mp4", 2 * MB, DAY, now)
    assert housekeeping(cfg) == 2 * MB

    def boom(*a, **kw):
        raise PermissionError("nope")

    monkeypatch.setattr("autoshorts.retention.prune_cache", boom)
    assert housekeeping(cfg) == 0
