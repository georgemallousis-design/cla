"""Pexels stock videos (free, attribution appreciated): https://www.pexels.com/api/

Needs a free API key in the env var named by ``cfg.visuals.pexels_api_key_env``
(default PEXELS_API_KEY). Requests send the key as the bare ``Authorization`` header.
Free-tier limit: 200 requests/hour, 20,000/month.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests

from ..config import Config
from ..models import ClipAsset
from ..utils import AutoShortsError, download, ensure_dir, http_get, http_session, log, touch
from . import VisualProvider, http_status, log_once, rendition_key, short_error

API_URL = "https://api.pexels.com/videos/search"
PER_PAGE = 15  # API default; max 80
MAX_PER_PAGE = 80


class PexelsProvider(VisualProvider):
    name = "pexels"
    stock = True

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.api_key = cfg.secret(cfg.visuals.pexels_api_key_env)
        self.cache_dir = cfg.path(cfg.visuals.cache_dir)
        self.timeout = cfg.visuals.timeout
        self._session: requests.Session | None = None
        self._disabled = False  # set after the API refuses the key or rate-limits us
        if not self.api_key:
            log_once("pexels-no-key", logging.INFO, "Pexels: no API key in $%s, skipping "
                     "(free key: https://www.pexels.com/api/)", cfg.visuals.pexels_api_key_env)

    @property
    def available(self) -> bool:
        return bool(self.api_key) and not self._disabled

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = http_session()
        return self._session

    # ----------------------------------------------------------------- search

    def search(self, query: str, min_seconds: float, count: int = 1) -> list[ClipAsset]:
        query = " ".join((query or "").split())
        if not self.api_key:
            return []
        if self._disabled or not query or count <= 0:
            return []
        try:
            videos = self._query(query, portrait=True, count=count)
            picks = choose_videos(videos, min_seconds, count, self.cfg.video.width, self.cfg.video.height)
            if not picks and not self._disabled:
                log.debug("Pexels: no portrait clips for %r, trying any orientation", query)
                videos = self._query(query, portrait=False, count=count)
                picks = choose_videos(videos, min_seconds, count, self.cfg.video.width, self.cfg.video.height)
        except (requests.RequestException, ValueError, AutoShortsError) as exc:
            if http_status(exc) == 429:
                self._disabled = True  # don't burn the hourly quota on retries
            log.warning("Pexels search for %r failed: %s", query, short_error(exc))
            return []

        clips: list[ClipAsset] = []
        for video, vfile in picks:
            try:
                clips.append(self._fetch(video, vfile, query))
            except (requests.RequestException, OSError) as exc:
                log.warning("Pexels: download of video %s failed: %s", video.get("id"), short_error(exc))
        log.debug("Pexels: %d clip(s) for %r", len(clips), query)
        return clips

    def _query(self, query: str, *, portrait: bool, count: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "query": query,
            "size": "medium",  # at least Full HD
            "per_page": min(MAX_PER_PAGE, max(PER_PAGE, count)),
        }
        if portrait:
            params["orientation"] = "portrait"
        resp = http_get(
            self.session, API_URL, params=params, headers={"Authorization": self.api_key},
            timeout=self.timeout, retries=2,
        )
        if resp.status_code in (401, 403):
            self._disabled = True
            log.warning("Pexels refused the API key (HTTP %s); check $%s",
                        resp.status_code, self.cfg.visuals.pexels_api_key_env)
            return []
        if resp.status_code != 200:
            raise AutoShortsError(f"Pexels API returned HTTP {resp.status_code}")
        data = resp.json()
        videos = data.get("videos") if isinstance(data, dict) else None
        return [v for v in videos or [] if isinstance(v, dict)]

    def _fetch(self, video: dict[str, Any], vfile: dict[str, Any], query: str) -> ClipAsset:
        w, h = int(vfile["width"]), int(vfile["height"])
        dest = ensure_dir(self.cache_dir) / f"pexels_{video['id']}_{w}x{h}.mp4"
        if not (dest.exists() and dest.stat().st_size > 0):
            log.info("Pexels: downloading video %s (%dx%d)", video["id"], w, h)
            try:
                download(self.session, vfile["link"], dest, timeout=self.timeout)
            except BaseException:
                dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
                raise
        else:
            touch(dest)  # recently used: kept longest by the cache pruning (retention.py)
        user = (video.get("user") or {}).get("name") or "an unknown creator"
        return ClipAsset(
            path=Path(dest), kind="video", duration=float(video.get("duration") or 0) or None,
            source="pexels", query=query, attribution=f"Video by {user} on Pexels",
            width=w, height=h,
        )


# --------------------------------------------------------------------------- selection


def usable_files(video: dict[str, Any]) -> list[dict[str, Any]]:
    """Progressive H.264 MP4 renditions with known dimensions (no HLS playlists)."""
    out = []
    for f in video.get("video_files") or []:
        if not isinstance(f, dict):
            continue
        link = str(f.get("link") or "")
        if (f.get("file_type") or "").lower() != "video/mp4" or not link or ".m3u8" in link:
            continue
        if str(f.get("quality") or "").lower() == "hls":
            continue
        try:
            if int(f.get("width") or 0) <= 0 or int(f.get("height") or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        out.append(f)
    return out


def best_file(video: dict[str, Any], target_w: int, target_h: int) -> dict[str, Any] | None:
    files = usable_files(video)
    if not files:
        return None
    return min(files, key=lambda f: rendition_key(int(f["width"]), int(f["height"]), target_w, target_h))


def choose_videos(
    videos: list[dict[str, Any]], min_seconds: float, count: int, target_w: int, target_h: int
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pick up to ``count`` (video, file) pairs: long enough, best rendition first,
    otherwise in the API's relevance order."""
    need = min(min_seconds, 3.0)
    ranked = []
    for index, video in enumerate(videos):
        try:
            duration = float(video.get("duration") or 0)
        except (TypeError, ValueError):
            continue
        if duration < need or "id" not in video:
            continue
        vfile = best_file(video, target_w, target_h)
        if vfile is None:
            continue
        key = rendition_key(int(vfile["width"]), int(vfile["height"]), target_w, target_h)
        ranked.append(((key[0], key[1], duration < min_seconds, index), video, vfile))
    ranked.sort(key=lambda item: item[0])
    return [(video, vfile) for _, video, vfile in ranked[: max(0, count)]]
