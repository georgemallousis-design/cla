"""Pixabay stock videos (free, no attribution required but we credit anyway): https://pixabay.com/api/docs/

Needs a free API key in the env var named by ``cfg.visuals.pixabay_api_key_env``
(default PIXABAY_API_KEY). Pixabay asks clients to cache search results for 24 hours
and allows 100 requests per 60 seconds; search responses are cached under
``cache_dir/pixabay_search/``. Videos have no orientation filter, so portrait results
are preferred when choosing and landscape ones get centre-cropped by the renderer.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests

from ..config import Config
from ..models import ClipAsset
from ..utils import AutoShortsError, download, ensure_dir, http_get, http_session, log, touch
from . import VisualProvider, http_status, log_once, rendition_key, short_error

API_URL = "https://pixabay.com/api/videos/"
PER_PAGE = 20  # API accepts 3-200
MAX_QUERY_CHARS = 100  # limit on the URL-encoded q
CACHE_TTL = 24 * 3600
RENDITIONS = ("large", "medium", "small")  # "tiny" is too small for 1080x1920


class PixabayProvider(VisualProvider):
    name = "pixabay"
    stock = True

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.api_key = cfg.secret(cfg.visuals.pixabay_api_key_env)
        self.cache_dir = cfg.path(cfg.visuals.cache_dir)
        self.timeout = cfg.visuals.timeout
        self._session: requests.Session | None = None
        self._disabled = False  # set after the API refuses the key or rate-limits us
        if not self.api_key:
            log_once("pixabay-no-key", logging.INFO, "Pixabay: no API key in $%s, skipping "
                     "(free key: https://pixabay.com/api/docs/)", cfg.visuals.pixabay_api_key_env)

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
        q = limit_query(query)
        if not self.api_key:
            return []
        if self._disabled or not q or count <= 0:
            return []
        try:
            hits = self._hits(q)
        except (requests.RequestException, ValueError, AutoShortsError) as exc:
            if http_status(exc) == 429:
                self._disabled = True
            log.warning("Pixabay search for %r failed: %s", q, self._safe(exc))
            return []

        clips: list[ClipAsset] = []
        for hit, rendition in choose_hits(hits, min_seconds, count, self.cfg.video.width, self.cfg.video.height):
            try:
                clips.append(self._fetch(hit, rendition, q))
            except (requests.RequestException, OSError) as exc:
                log.warning("Pixabay: download of video %s failed: %s", hit.get("id"), self._safe(exc))
        log.debug("Pixabay: %d clip(s) for %r", len(clips), q)
        return clips

    def _safe(self, exc: BaseException) -> str:
        """short_error() with the API key masked wherever it appears (it travels in the URL)."""
        msg = short_error(exc)
        return msg.replace(self.api_key, "***") if self.api_key else msg

    def _params(self, q: str) -> dict[str, Any]:
        return {"q": q, "safesearch": "true", "per_page": PER_PAGE}

    def search_cache_path(self, q: str) -> Path:
        params = self._params(q)
        digest = hashlib.sha1(json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / "pixabay_search" / f"{digest}.json"

    def _hits(self, q: str) -> list[dict[str, Any]]:
        cache = self.search_cache_path(q)
        cached = _read_json(cache)
        if cached is not None and time.time() - cache.stat().st_mtime < CACHE_TTL:
            log.debug("Pixabay: cached results for %r", q)
            return _hits_of(cached)
        try:
            data = self._request(q)
        except (requests.RequestException, AutoShortsError):
            if cached is not None:  # stale beats nothing when the API is down
                log.info("Pixabay unreachable; using results cached %.0fh ago for %r",
                         (time.time() - cache.stat().st_mtime) / 3600, q)
                return _hits_of(cached)
            raise
        if data is None:
            return []
        _write_json(cache, data)
        return _hits_of(data)

    def _request(self, q: str) -> dict[str, Any] | None:
        params = {"key": self.api_key, **self._params(q)}
        resp = http_get(self.session, API_URL, params=params, timeout=self.timeout, retries=2)
        if resp.status_code in (400, 401, 403) and "key" in (resp.text or "").lower():
            self._disabled = True
            log.warning("Pixabay refused the API key (HTTP %s); check $%s",
                        resp.status_code, self.cfg.visuals.pixabay_api_key_env)
            return None
        if resp.status_code != 200:
            raise AutoShortsError(f"Pixabay API returned HTTP {resp.status_code}: {(resp.text or '')[:120]}")
        data = resp.json()
        if not isinstance(data, dict):
            raise AutoShortsError("Pixabay API returned an unexpected response")
        return data

    def _fetch(self, hit: dict[str, Any], rendition: dict[str, Any], q: str) -> ClipAsset:
        w, h = int(rendition["width"]), int(rendition["height"])
        dest = ensure_dir(self.cache_dir) / f"pixabay_{hit['id']}_{w}x{h}.mp4"
        if not (dest.exists() and dest.stat().st_size > 0):
            log.info("Pixabay: downloading video %s (%dx%d)", hit["id"], w, h)
            try:
                download(self.session, rendition["url"], dest, timeout=self.timeout)
            except BaseException:
                dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
                raise
        else:
            touch(dest)  # recently used: kept longest by the cache pruning (retention.py)
        user = hit.get("user") or "an unknown creator"
        return ClipAsset(
            path=Path(dest), kind="video", duration=float(hit.get("duration") or 0) or None,
            source="pixabay", query=q, attribution=f"Video by {user} on Pixabay",
            width=w, height=h,
        )


# --------------------------------------------------------------------------- selection


def limit_query(query: str) -> str:
    """Collapse whitespace and drop trailing words until the URL-encoded q fits 100 chars."""
    words = (query or "").split()
    while words and len(quote_plus(" ".join(words))) > MAX_QUERY_CHARS:
        words.pop()
    if not words and query.strip():  # a single gigantic word
        text = query.strip()
        while text and len(quote_plus(text)) > MAX_QUERY_CHARS:
            text = text[:-1]
        return text
    return " ".join(words)


def best_rendition(hit: dict[str, Any], target_w: int, target_h: int) -> dict[str, Any] | None:
    """The large/medium/small file closest to the output size (empty entries skipped)."""
    videos = hit.get("videos") or {}
    options = []
    for name in RENDITIONS:
        info = videos.get(name)
        if not isinstance(info, dict) or not info.get("url"):
            continue
        try:
            w, h = int(info.get("width") or 0), int(info.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if w > 0 and h > 0:
            options.append(info)
    if not options:
        return None
    return min(options, key=lambda r: rendition_key(int(r["width"]), int(r["height"]), target_w, target_h))


def choose_hits(
    hits: list[dict[str, Any]], min_seconds: float, count: int, target_w: int, target_h: int
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Up to ``count`` (hit, rendition) pairs: portrait first, long enough, relevance order."""
    need = min(min_seconds, 3.0)
    ranked = []
    for index, hit in enumerate(hits):
        try:
            duration = float(hit.get("duration") or 0)
        except (TypeError, ValueError):
            continue
        if duration < need or "id" not in hit:
            continue
        rendition = best_rendition(hit, target_w, target_h)
        if rendition is None:
            continue
        key = rendition_key(int(rendition["width"]), int(rendition["height"]), target_w, target_h)
        ranked.append(((key[0], key[1], duration < min_seconds, index), hit, rendition))
    ranked.sort(key=lambda item: item[0])
    return [(hit, rendition) for _, hit, rendition in ranked[: max(0, count)]]


def _hits_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    hits = data.get("hits") if isinstance(data, dict) else None
    return [h for h in hits or [] if isinstance(h, dict)]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    try:
        ensure_dir(path.parent)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.debug("Pixabay: could not cache search results: %s", exc)
