"""Background music: pick a track from ``cfg.music.dir`` (searched recursively).

Drop royalty-free tracks there, e.g. from the YouTube Audio Library or Pixabay Music;
TikTok's in-app sounds cannot be baked into an uploaded file. ``pick_track`` returns
None when music is disabled or the folder is missing/empty (or holds no readable track),
and the video is rendered with narration only. Tracks are probed before use: an empty,
truncated or zero-sample file would fail (or, looped, hang) the final render.
"""
from __future__ import annotations

import os
import random
from pathlib import Path

from .config import Config
from .utils import AutoShortsError, log, media_duration

AUDIO_EXTS = frozenset({".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"})
MIN_TRACK_SECONDS = 1.0

# (path, mtime_ns, size) -> usable? (probed once per process)
_USABLE: dict[tuple[str, int, int], bool] = {}


def usable(track: Path) -> bool:
    """True when ffprobe reads the track and it lasts at least MIN_TRACK_SECONDS."""
    try:
        st = track.stat()
    except OSError:
        return False
    key = (str(track), st.st_mtime_ns, st.st_size)
    if key not in _USABLE:
        try:
            ok = st.st_size > 0 and media_duration(track) >= MIN_TRACK_SECONDS
            reason = "too short or empty"
        except (AutoShortsError, OSError, ValueError) as exc:
            ok, reason = False, str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        if not ok:
            log.warning("music: skipping %s (%s)", track.name, reason)
        _USABLE[key] = ok
    return _USABLE[key]


def list_tracks(cfg: Config) -> list[Path]:
    """Every audio file under the music folder, in a stable (sorted) order."""
    root = cfg.path(cfg.music.dir)
    if not root.is_dir():
        return []
    tracks: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if not name.startswith(".") and os.path.splitext(name)[1].lower() in AUDIO_EXTS:
                tracks.append(Path(dirpath) / name)
    # Sort on the path relative to the folder so the order (and a seeded pick) is the
    # same on every OS and wherever the folder lives.
    return sorted(tracks, key=lambda p: p.relative_to(root).as_posix().lower())


def pick_track(cfg: Config, seed: int | str | None = None) -> Path | None:
    """A random track, or None. The same ``seed`` always picks the same track."""
    if not cfg.music.enabled:
        return None
    tracks = list_tracks(cfg)
    if not tracks:
        log.info("music: no audio files in %s, rendering without music", cfg.path(cfg.music.dir))
        return None
    rng = random.Random(seed) if seed is not None else random.Random()
    for track in rng.sample(tracks, len(tracks)):
        if usable(track):
            log.info("music: %s", track.name)
            return track
    log.warning("music: none of the %d file(s) in %s could be read, rendering without music",
                len(tracks), cfg.path(cfg.music.dir))
    return None
