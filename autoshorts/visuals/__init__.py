"""Background visuals: Narration segments -> list[ClipAsset] (one or more shots per segment).

Contract
--------
``VisualProvider.search(query, min_seconds, count)`` returns up to ``count`` ClipAssets
(already downloaded into ``cfg.visuals.cache_dir``) or [] when nothing matches /
the provider is not configured. It must never raise for "no results" or a missing API
key; network/API errors are logged and also return [].

``get_providers(cfg)`` builds providers in ``cfg.visuals.providers`` order
("pexels", "pixabay", "local", "generated"); unknown names raise AutoShortsError.

``plan_shots(cfg, narration, workdir) -> list[Shot]`` decides how many shots each
TimedSegment gets (each <= cfg.visuals.max_shot_seconds), fetches clips for each
segment's visual_query trying providers in order, avoids reusing the same clip in
one video where possible, and always returns shots that exactly cover
[0, narration.duration] with no gaps (the "generated" provider guarantees a result).
``plan_shots`` also accepts an optional keyword ``topic`` (the video topic), used as a
simpler fallback query when a segment's visual_query finds nothing on stock sites.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..models import ClipAsset, Narration, Segment
from ..utils import AutoShortsError, ensure_dir, log, redact

PROVIDER_NAMES = ("pexels", "pixabay", "local", "generated")

# Segments shorter than this (e.g. a lone "Wow.") are merged into a neighbour so no
# shot is only a few frames long.
MIN_WINDOW_SECONDS = 0.25
# Continuous backgrounds start early enough in the clip to also cover the outro.
CONTINUOUS_MARGIN = 5.0

_STOPWORDS = frozenset(
    """a an the and or but of in on at to for from by with without into onto over under
    about above below between through during before after is are was were be been being
    am do does did have has had this that these those it its it's i you he she we they me
    my your his her our their them what which who whom whose why how when where there here
    than then so as if not no nor too very just can could should would will shall may might
    must also only own same such some any all each every both few more most other ever
    really things thing fact facts""".split()
)
# Words that describe footage rather than name a subject; skipped when picking the
# "first noun" of a query ("dark ocean waves" -> "ocean").
_MODIFIERS = frozenset(
    """aerial closeup close up slow motion timelapse time lapse macro dark bright light
    beautiful old ancient modern new small big large giant tiny huge little young happy sad
    red orange yellow green blue purple pink black white golden grey gray night day morning
    evening sunny cloudy cinematic abstract background footage video clip shot view vertical
    portrait landscape amazing epic""".split()
)
_NUMBER_WORDS = frozenset(
    "one two three four five six seven eight nine ten hundred thousand million billion".split()
)


@dataclass
class Shot:
    clip: ClipAsset
    start: float  # position in the final video, seconds
    end: float


class VisualProvider(ABC):
    name: str = "base"
    # Stock-footage sites: plan_shots retries them with a simplified query.
    stock: bool = False
    # Extra candidates plan_shots asks for, so clips already used elsewhere in the
    # video can be skipped. Providers where every result costs a render ask for none.
    spares: int = 2

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def available(self) -> bool:
        """False when searching is pointless (no API key, key refused, rate-limited)."""
        return True

    @abstractmethod
    def search(self, query: str, min_seconds: float, count: int = 1) -> list[ClipAsset]: ...


# --------------------------------------------------------------------------- shared helpers

_LOGGED_ONCE: set[str] = set()


def log_once(key: str, level: int, msg: str, *args: object) -> None:
    """Log ``msg`` only the first time ``key`` is seen in this process."""
    if key in _LOGGED_ONCE:
        return
    _LOGGED_ONCE.add(key)
    log.log(level, msg, *args)


def http_status(exc: BaseException) -> int | None:
    """HTTP status code carried by a requests exception, if any."""
    return getattr(getattr(exc, "response", None), "status_code", None)


def short_error(exc: BaseException) -> str:
    """First line of an error, for logs, with credential query parameters masked.

    requests errors never include our headers (Pexels' key), but connection errors do
    include the full URL with its query string, where Pixabay's ``key=`` lives.
    """
    text = redact(str(exc) or type(exc).__name__)
    return text.splitlines()[0][:300]


def rendition_key(width: int, height: int, target_w: int, target_h: int) -> tuple:
    """Sort key (lower is better) for choosing a stock video file.

    Portrait first (landscape gets centre-cropped, losing most of its pixels), then files
    at least ~1280 px tall, then files not much bigger than the output (a 4K download is
    several times the size of 1440p/1080p for no visible gain once scaled down), then the
    one closest to the output size; ties go to the larger file. For landscape files only
    the height survives the 9:16 crop, so only the height is compared.
    """
    min_h = min(1280, target_h)
    landscape = width > height
    too_big = max(width, height) > 1.5 * max(target_w, target_h)
    distance = abs(height - target_h) if landscape else abs(width - target_w) + abs(height - target_h)
    return (landscape, height < min_h, too_big, distance, -height)


def query_keywords(text: str) -> list[str]:
    """Lower-case content words of ``text`` (stopwords removed, order kept)."""
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", (text or "").lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def simplify_query(query: str, extra: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Simpler fallback queries for ``query``: its first noun-ish word, then the first
    keywords of each ``extra`` text (topic / hook query). Never includes ``query`` itself."""
    original = " ".join(query_keywords(query))
    alternatives: list[str] = []
    words = query_keywords(query)
    if len(words) > 1:
        nouns = [w for w in words if w not in _MODIFIERS and w not in _NUMBER_WORDS and not w.isdigit()]
        alternatives.append((nouns or words)[0])
    for text in extra:
        kws = [w for w in query_keywords(text) if w not in _NUMBER_WORDS and not w.isdigit()]
        if kws:
            alternatives.append(" ".join(kws[:2]))
    out: list[str] = []
    for alt in alternatives:
        if alt and alt != original and alt not in out:
            out.append(alt)
    return out


# --------------------------------------------------------------------------- providers


def _provider_class(name: str) -> type[VisualProvider]:
    if name == "pexels":
        from .pexels import PexelsProvider

        return PexelsProvider
    if name == "pixabay":
        from .pixabay import PixabayProvider

        return PixabayProvider
    if name == "local":
        from .local import LocalProvider

        return LocalProvider
    if name == "generated":
        from .generated import GeneratedProvider

        return GeneratedProvider
    raise AutoShortsError(
        f"unknown visuals provider '{name}' (choose from: {', '.join(PROVIDER_NAMES)})"
    )


def get_providers(cfg: Config) -> list[VisualProvider]:
    names = cfg.visuals.providers
    if isinstance(names, str):
        names = names.split(",")
    providers: list[VisualProvider] = []
    seen: set[str] = set()
    for raw in names or []:
        name = str(raw).strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        providers.append(_provider_class(name)(cfg))
    return providers


# --------------------------------------------------------------------------- shot planning


def segment_windows(narration: Narration) -> list[tuple[float, float, Segment | None]]:
    """Split [0, duration] into one window per TimedSegment.

    Each window runs from its segment's start to the next segment's start (so pauses
    between segments keep the previous visual); the first starts at 0 and the last ends
    at ``narration.duration``. Windows shorter than MIN_WINDOW_SECONDS are merged into
    the previous one (or the next one, at the very start).
    """
    duration = float(narration.duration)
    segs = sorted(narration.segments, key=lambda ts: ts.start)
    if not segs:
        return [(0.0, duration, None)]
    bounds = [0.0]
    for ts in segs[1:]:
        bounds.append(min(max(float(ts.start), bounds[-1]), duration))
    bounds.append(duration)

    windows: list[tuple[float, float, Segment | None]] = []
    pending_start: float | None = None
    for i, ts in enumerate(segs):
        a = bounds[i] if pending_start is None else pending_start
        b = bounds[i + 1]
        if b - a < MIN_WINDOW_SECONDS:
            if windows:
                windows[-1] = (windows[-1][0], b, windows[-1][2])
            else:
                pending_start = a
            continue
        pending_start = None
        windows.append((a, b, ts.segment))
    if not windows:  # the whole narration is shorter than MIN_WINDOW_SECONDS
        windows.append((0.0, duration, segs[0].segment))
    return windows


def split_window(start: float, end: float, max_shot: float) -> list[tuple[float, float]]:
    """Split [start, end] into equal parts no longer than ``max_shot`` (shared bounds)."""
    length = end - start
    n = max(1, math.ceil(length / max_shot - 1e-9)) if max_shot > 0 else 1
    cuts = [start] + [round(start + length * k / n, 3) for k in range(1, n)] + [end]
    return list(zip(cuts[:-1], cuts[1:]))


def _clip_key(clip: ClipAsset) -> str:
    return os.path.normcase(str(clip.path))


def _long_enough(clip: ClipAsset, seconds: float) -> bool:
    return clip.kind == "image" or clip.duration is None or clip.duration >= seconds - 0.05


class _ShotPlanner:
    """Fetches candidates per window and hands out clips, tracking reuse across the video."""

    def __init__(self, cfg: Config, providers: list[VisualProvider], fallback_queries: list[str]):
        self.cfg = cfg
        self.providers = providers
        self.fallback_queries = fallback_queries
        self.uses: dict[str, int] = {}
        self.pool: dict[str, ClipAsset] = {}  # every candidate seen, for last-resort reuse
        self.errors: list[str] = []
        self._memo: dict[tuple[int, str], tuple[float, int, list[ClipAsset]]] = {}
        self._fallback: VisualProvider | None = None

    # ----------------------------------------------------------------- fetching

    def _search(self, idx: int, provider: VisualProvider, query: str, min_seconds: float,
                count: int) -> list[ClipAsset]:
        memo = self._memo.get((idx, query))
        if memo and memo[0] >= min_seconds - 1e-6 and memo[1] >= count:
            return memo[2][:count]
        try:
            clips = list(provider.search(query, min_seconds=min_seconds, count=count) or [])
        except Exception as exc:  # providers should not raise, but one bad provider must not kill the video
            log.warning("visuals: provider '%s' failed for %r: %s", provider.name, query, exc)
            self.errors.append(f"{provider.name}: {exc}")
            clips = []
        self._memo[(idx, query)] = (min_seconds, count, clips)
        return clips

    def gather(self, query: str, min_seconds: float, needed: int) -> list[ClipAsset]:
        candidates: list[ClipAsset] = []
        keys: set[str] = set()

        def fresh() -> int:
            return sum(1 for c in candidates if _clip_key(c) not in self.uses)

        def add(clips: list[ClipAsset]) -> None:
            for clip in clips:
                key = _clip_key(clip)
                if key not in keys:
                    keys.add(key)
                    candidates.append(clip)
                    self.pool.setdefault(key, clip)

        for idx, provider in enumerate(self.providers):
            if fresh() >= needed:
                break
            if not provider.available:
                continue
            queries = [query]
            if provider.stock:
                queries += simplify_query(query, self.fallback_queries)
            for i, q in enumerate(queries):
                missing = needed - fresh()
                if missing <= 0:
                    break
                if provider.stock and not q.strip():
                    continue
                if i:
                    log.debug("visuals: %s had too little for %r, trying %r", provider.name, query, q)
                count = missing + max(0, provider.spares)
                clips = self._search(idx, provider, q, min_seconds, count)
                add(clips)
                # The top results were already used earlier in the video: dig one page deeper.
                reused = sum(1 for c in clips if _clip_key(c) in self.uses)
                if reused and len(clips) >= count and fresh() < needed:
                    add(self._search(idx, provider, q, min_seconds, count + reused))

        # Nothing found and nothing earlier in the video to reuse: generated backgrounds
        # are the safety net even when they are not in visuals.providers.
        if not candidates and not self.pool and not any(p.name == "generated" for p in self.providers):
            add(self._search(-1, self._generated(), query, min_seconds, needed))
        return candidates

    def _generated(self) -> VisualProvider:
        if self._fallback is None:
            log.warning("visuals: no provider had footage; using generated backgrounds "
                        "(add \"generated\" to visuals.providers to make this explicit)")
            self._fallback = _provider_class("generated")(self.cfg)
        return self._fallback

    # ----------------------------------------------------------------- picking

    def pick(self, candidates: list[ClipAsset], seconds: float, previous: ClipAsset | None) -> ClipAsset:
        unused = [c for c in candidates if _clip_key(c) not in self.uses]
        if unused:
            clip = next((c for c in unused if _long_enough(c, seconds)), unused[0])
        else:
            prev_key = _clip_key(previous) if previous is not None else None
            order = sorted(
                candidates,
                key=lambda c: (_clip_key(c) == prev_key, not _long_enough(c, seconds), self.uses[_clip_key(c)]),
            )
            clip = order[0]
        key = _clip_key(clip)
        self.uses[key] = self.uses.get(key, 0) + 1
        return clip


def plan_shots(cfg: Config, narration: Narration, workdir: Path, *, topic: str = "") -> list[Shot]:
    duration = float(narration.duration)
    if duration <= 0:
        raise AutoShortsError("cannot plan visuals for a narration with no duration")
    style = (cfg.visuals.style or "cuts").strip().lower()
    if style == "continuous":
        shots = continuous_shots(cfg, duration)
        if shots:
            _write_plan(workdir, shots)
            return shots
    elif style != "cuts":
        raise AutoShortsError(f"unknown visuals.style '{cfg.visuals.style}'; use 'cuts' or 'continuous'")
    windows = segment_windows(narration)
    max_shot = float(cfg.visuals.max_shot_seconds or 0)

    hook_query = next((ts.segment.visual_query for ts in narration.segments if ts.segment.visual_query), "")
    fallback_queries = [q for q in (topic, hook_query) if q and q.strip()]
    planner = _ShotPlanner(cfg, get_providers(cfg), fallback_queries)

    planned: list[tuple[float, float, ClipAsset | None]] = []
    previous: ClipAsset | None = None
    for start, end, segment in windows:
        parts = split_window(start, end, max_shot)
        shot_len = (end - start) / len(parts)
        query = _segment_query(segment, topic)
        candidates = planner.gather(query.strip(), shot_len, len(parts))
        if not candidates:
            log.warning("visuals: nothing found for %r; reusing footage from other segments", query)
        for a, b in parts:
            clip = planner.pick(candidates, b - a, previous) if candidates else None
            planned.append((a, b, clip))
            previous = clip if clip is not None else previous

    shots = _fill_missing(planned, planner)
    _write_plan(workdir, shots)
    sources = sorted({s.clip.source or "?" for s in shots})
    log.info("visuals: %d shots over %.1fs from %s", len(shots), duration, ", ".join(sources))
    return shots


def continuous_shots(cfg: Config, duration: float, rng: random.Random | None = None) -> list[Shot]:
    """One long gameplay clip behind the whole video, starting at a random point.

    Picks a video from ``visuals.gameplay_dir`` (preferring clips long enough to need no
    loop); returns [] when the folder has no usable video, so plan_shots falls back to cuts.
    """
    from .local import LocalProvider

    rng = rng or random.Random()
    root = cfg.path(cfg.visuals.gameplay_dir)
    clips = [c for c in LocalProvider(cfg, rng=rng, root=root).search("", duration, count=50) if c.kind == "video"]
    if not clips:
        log_once(f"no-gameplay:{root}", logging.WARNING,
                 "visuals: style 'continuous' but no videos in %s; using normal cuts instead. "
                 "Record some gameplay (e.g. Minecraft parkour with Win+G) and put it there.", root)
        return []
    needed = duration + CONTINUOUS_MARGIN
    long_enough = [c for c in clips if (c.duration or 0) >= needed]
    clip = rng.choice(long_enough or clips)
    room = (clip.duration or 0) - needed
    clip.start_offset = round(rng.uniform(0.0, room), 3) if room > 0 else 0.0
    log.info("visuals: continuous background %s from %.1fs", Path(clip.path).name, clip.start_offset)
    return [Shot(clip=clip, start=0.0, end=duration)]


def _segment_query(segment: Segment | None, topic: str) -> str:
    """The segment's visual_query; else the topic; else keywords from what is said."""
    if segment is not None and segment.visual_query.strip():
        return " ".join(segment.visual_query.split())
    if topic.strip():
        return " ".join(topic.split())
    if segment is not None:
        return " ".join(query_keywords(segment.text)[:3])
    return ""


def _fill_missing(planned: list[tuple[float, float, ClipAsset | None]], planner: _ShotPlanner) -> list[Shot]:
    """Give shots whose segment found nothing a clip from elsewhere in the video."""
    if all(clip is not None for _, _, clip in planned):
        return [Shot(clip=c, start=a, end=b) for a, b, c in planned]  # type: ignore[arg-type]
    pool = list(planner.pool.values())
    if not pool:
        detail = "; ".join(planner.errors[-3:]) or "every provider returned nothing"
        raise AutoShortsError(
            f"no background visuals found ({detail}). Check `autoshorts doctor`: FFmpeg must work "
            "for generated backgrounds, or add clips to visuals.local_dir / set a Pexels or Pixabay key."
        )
    shots: list[Shot] = []
    for a, b, clip in planned:
        if clip is None:
            prev = shots[-1].clip if shots else None
            clip = planner.pick(pool, b - a, prev)
        shots.append(Shot(clip=clip, start=a, end=b))
    return shots


def _write_plan(workdir: Path | None, shots: list[Shot]) -> None:
    """Save the shot list as workdir/shots.json (handy when a background looks wrong)."""
    if workdir is None:
        return
    try:
        ensure_dir(workdir)
        data = [
            {
                "start": s.start, "end": s.end, "path": str(s.clip.path), "kind": s.clip.kind,
                "source": s.clip.source, "query": s.clip.query, "attribution": s.clip.attribution,
            }
            for s in shots
        ]
        (Path(workdir) / "shots.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        log.debug("could not write shots.json: %s", exc)


__all__ = [
    "PROVIDER_NAMES", "Shot", "VisualProvider", "get_providers", "plan_shots",
    "rendition_key", "segment_windows", "simplify_query", "split_window", "log_once",
    "query_keywords", "http_status", "short_error",
]
