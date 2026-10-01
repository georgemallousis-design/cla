"""Tests for autoshorts.music: background track selection."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from autoshorts import music
from autoshorts.config import Config
from autoshorts.music import list_tracks, pick_track
from autoshorts.utils import AutoShortsError


@pytest.fixture(autouse=True)
def fake_probe(monkeypatch):
    """Placeholder files are 'tracks' of 30 s unless a test says otherwise."""
    lengths: dict[str, float | Exception] = {}

    def fake(path):
        value = lengths.get(Path(path).name, 30.0)
        if isinstance(value, Exception):
            raise value
        return value

    music._USABLE.clear()
    monkeypatch.setattr(music, "media_duration", fake)
    yield lengths
    music._USABLE.clear()


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


def test_unreadable_tracks_are_skipped(cfg, fake_probe, caplog):
    add_files(cfg.path("music"), "broken.mp3", "silent.wav", "good.mp3")
    fake_probe["broken.mp3"] = AutoShortsError("ffprobe could not read broken.mp3: Invalid data")
    fake_probe["silent.wav"] = 0.0
    for seed in range(12):
        assert pick_track(cfg, seed=seed).name == "good.mp3"
    assert "skipping broken.mp3" in caplog.text and "skipping silent.wav" in caplog.text


def test_no_readable_track_means_no_music(cfg, fake_probe):
    add_files(cfg.path("music"), "a.mp3")
    fake_probe["a.mp3"] = AutoShortsError("bad")
    assert pick_track(cfg) is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_probe_rejects_empty_and_header_only_files(cfg, monkeypatch):
    from autoshorts.utils import media_duration

    monkeypatch.setattr(music, "media_duration", media_duration)
    root = cfg.path("music")
    root.mkdir()
    (root / "zero.mp3").write_bytes(b"")
    # a 44-byte WAV header with no samples: looped with -stream_loop -1 it never ends
    header = (b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
              + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (44100).to_bytes(4, "little")
              + (88200).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
              + b"data" + (0).to_bytes(4, "little"))
    (root / "empty.wav").write_bytes(header)
    assert pick_track(cfg, seed=1) is None
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=2", str(root / "ok.wav")], check=True)
    assert pick_track(cfg, seed=1) == root / "ok.wav"
