"""Your own background footage from ``cfg.visuals.local_dir`` (searched recursively).

Files whose name or folder shares a keyword with the query are preferred
("assets/backgrounds/ocean/waves_01.mp4" matches "ocean waves"). Otherwise random files
are returned: local folders are usually generic loops (gameplay, satisfying videos,
nature) that suit any narration. Still images become kind "image" (Ken Burns motion).
"""
from __future__ import annotations

import os
import random
import re
from pathlib import Path

from ..config import Config
from ..models import ClipAsset
from ..utils import AutoShortsError, decodes, log, probe_video
from . import VisualProvider, query_keywords

VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v"})
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
MIN_VIDEO_SECONDS = 0.5  # anything shorter is treated as broken
# Clips shorter than this may be looped (-stream_loop -1) for a shot; they are test-decoded
# once, because a clip whose header is fine but whose video data is not would loop forever.
LOOP_CHECK_SECONDS = 10.0

# (path, mtime_ns, size) -> video duration (0.0 for stills) or None when unusable;
# shared by every instance, so a bad file is probed (and reported) once per process.
_DURATIONS: dict[tuple[str, int, int], float | None] = {}
_DECODES: dict[tuple[str, int, int], bool] = {}


class LocalProvider(VisualProvider):
    name = "local"

    def __init__(self, cfg: Config, rng: random.Random | None = None):
        super().__init__(cfg)
        self.root = cfg.path(cfg.visuals.local_dir)
        self.rng = rng or random.Random()
        self._files: list[Path] | None = None

    def files(self) -> list[Path]:
        """Every usable media file under the folder (hidden files/folders skipped)."""
        if self._files is None:
            self._files = scan_media(self.root)
            if self._files:
                log.debug("local visuals: %d file(s) in %s", len(self._files), self.root)
        return self._files

    def search(self, query: str, min_seconds: float, count: int = 1) -> list[ClipAsset]:
        files = self.files()
        if not files or count <= 0:
            return []
        wanted = {stem(w) for w in query_keywords(query)}
        scored = [(match_score(wanted, self._keywords(f)), self.rng.random(), f) for f in files]
        if any(score > 0 for score, _, _ in scored):
            log.debug("local visuals: keyword match for %r", query)
        # Best keyword match first; non-matching files (random order) pad the list.
        scored.sort(key=lambda item: (-item[0], item[1]))

        # Probe lazily (ffprobe per file) until ``count`` clips are long enough; within the
        # same match score, clips that cover the whole shot come first.
        picked: list[tuple[float, bool, int, ClipAsset]] = []
        n_long = 0
        for order, (score, _, path) in enumerate(scored):
            clip = self._asset(path, query)
            if clip is None:
                continue
            is_short = clip.kind != "image" and (clip.duration or 0) < min_seconds - 0.05
            picked.append((-score, is_short, order, clip))
            n_long += not is_short
            if n_long >= count:
                break
        picked.sort(key=lambda item: item[:3])
        return [item[3] for item in picked[:count]]

    def _keywords(self, path: Path) -> set[str]:
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            rel = Path(path.name)
        parts = [*rel.parent.parts, rel.stem]
        return {stem(w) for part in parts for w in split_words(part)}

    def _asset(self, path: Path, query: str) -> ClipAsset | None:
        if path.suffix.lower() in IMAGE_EXTS:
            if probe_duration(path, image=True) is None or not decodes_cached(path):
                return None
            return ClipAsset(path=path, kind="image", duration=None, source="local", query=query)
        duration = probe_duration(path)
        if duration is None or duration < MIN_VIDEO_SECONDS:
            return None
        if duration < LOOP_CHECK_SECONDS and not decodes_cached(path):
            return None
        return ClipAsset(path=path, kind="video", duration=duration, source="local", query=query)


# --------------------------------------------------------------------------- helpers


def scan_media(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            suffix = os.path.splitext(name)[1].lower()
            if suffix in VIDEO_EXTS or suffix in IMAGE_EXTS:
                out.append(Path(dirpath) / name)
    return out


def _cache_key(path: Path) -> tuple[str, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path), st.st_mtime_ns, st.st_size)


def _skip(path: Path, reason: object) -> None:
    text = str(reason)
    log.warning("local visuals: skipping %s (%s)", path.name, text.splitlines()[0] if text else type(reason).__name__)


def probe_duration(path: Path, image: bool = False) -> float | None:
    """Length of the file's video stream via ffprobe, cached per process.

    None when the file can't be read or has no video stream (an audio-only .mp4 would
    otherwise fail every shot it is picked for). ``image=True`` only checks that a still
    image is readable and returns 0.0.
    """
    key = _cache_key(path)
    if key is None:
        return None
    if key not in _DURATIONS:
        try:
            duration = probe_video(path)["duration"]
            _DURATIONS[key] = 0.0 if image else duration
        except (AutoShortsError, OSError, ValueError) as exc:
            _skip(path, exc)
            _DURATIONS[key] = None
    return _DURATIONS[key]


def decodes_cached(path: Path) -> bool:
    """utils.decodes(), cached per process; logs a skip when the video cannot be decoded."""
    key = _cache_key(path)
    if key is None:
        return False
    if key not in _DECODES:
        _DECODES[key] = decodes(path)
        if not _DECODES[key]:
            _skip(path, "the video data cannot be decoded")
    return _DECODES[key]


def split_words(text: str) -> list[str]:
    """'MinecraftParkour_02-night' -> ['minecraft', 'parkour', 'night']"""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 1]


def stem(word: str) -> str:
    """Crude singular form so "waves" matches "wave" and "boxes" matches "box"."""
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("ches", "shes", "sses", "xes", "zes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def match_score(wanted: set[str], have: set[str]) -> float:
    """1 per query keyword found in the file's words, 0.5 for a partial (substring) hit."""
    score = 0.0
    for w in wanted:
        if w in have:
            score += 1.0
        elif len(w) >= 4 and any(w in h for h in have):
            score += 0.5
    return score
