"""Tests for autoshorts.music: background track selection."""
from __future__ import annotations

from pathlib import Path

import pytest

from autoshorts.config import Config
from autoshorts.music import list_tracks, pick_track


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.base_dir = tmp_path
    c.music.dir = "music"
    return c


def add_files(root: Path, *names: str) -> None:
    for name in names:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def test_disabled_missing_or_empty_returns_none(cfg):
    assert pick_track(cfg) is None  # folder missing
    cfg.path("music").mkdir()
    assert pick_track(cfg) is None  # folder empty
    add_files(cfg.path("music"), "cover.jpg", "readme.txt")
    assert pick_track(cfg) is None  # nothing playable
    add_files(cfg.path("music"), "calm.mp3")
    cfg.music.enabled = False
    assert pick_track(cfg) is None


def test_lists_audio_recursively_and_skips_the_rest(cfg):
    root = cfg.path("music")
    add_files(root, "b.MP3", "a.wav", "lofi/c.m4a", "lofi/d.flac", "e.ogg", "f.aac",
              "cover.png", ".hidden.mp3", ".trash/old.mp3", "notes.md")
    names = [p.relative_to(root).as_posix() for p in list_tracks(cfg)]
    assert names == ["a.wav", "b.MP3", "e.ogg", "f.aac", "lofi/c.m4a", "lofi/d.flac"]


def test_seeded_pick_is_deterministic(cfg):
    add_files(cfg.path("music"), *(f"t{i}.mp3" for i in range(8)))
    first = pick_track(cfg, seed=42)
    assert first is not None and first.suffix == ".mp3"
    assert all(pick_track(cfg, seed=42) == first for _ in range(5))
    assert pick_track(cfg, seed="my video") == pick_track(cfg, seed="my video")
    picks = {pick_track(cfg, seed=s) for s in range(40)}
    assert len(picks) > 1  # different seeds spread over the tracks


def test_unseeded_pick_returns_an_existing_track(cfg):
    add_files(cfg.path("music"), "one.mp3", "two.wav")
    for _ in range(10):
        track = pick_track(cfg)
        assert track is not None and track.exists()


def test_absolute_music_dir(cfg, tmp_path):
    other = tmp_path / "elsewhere"
    add_files(other, "song.ogg")
    cfg.music.dir = str(other)
    assert pick_track(cfg) == other / "song.ogg"
