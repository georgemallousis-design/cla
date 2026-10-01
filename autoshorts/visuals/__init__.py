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
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..models import ClipAsset, Narration


@dataclass
class Shot:
    clip: ClipAsset
    start: float  # position in the final video, seconds
    end: float


class VisualProvider(ABC):
    name: str = "base"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @abstractmethod
    def search(self, query: str, min_seconds: float, count: int = 1) -> list[ClipAsset]: ...


def get_providers(cfg: Config) -> list[VisualProvider]:
    raise NotImplementedError


def plan_shots(cfg: Config, narration: Narration, workdir: Path) -> list[Shot]:
    raise NotImplementedError
