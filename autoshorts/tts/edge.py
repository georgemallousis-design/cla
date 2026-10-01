"""Microsoft Edge neural voices via the ``edge-tts`` package (free, needs internet).

Gives real per-word timings: edge-tts >= 7 only emits word events when the
Communicate object is created with ``boundary="WordBoundary"`` (the default is
SentenceBoundary). Offsets/durations arrive in 100-ns ticks.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from ..models import SpeechResult, WordTiming
from ..utils import AutoShortsError, log, media_duration
from . import TTSEngine
from .timing import align_words

T = TypeVar("T")
TICKS_PER_SECOND = 10_000_000
INSTALL_HINT = "install it with: pip install edge-tts"


def _import_edge_tts() -> Any:
    try:
        import edge_tts
    except ImportError as exc:
        raise AutoShortsError(f"edge-tts is not installed; {INSTALL_HINT}") from exc
    return edge_tts


def run_coroutine(factory: Callable[[], Awaitable[T]]) -> T:
    """Run ``factory()`` to completion, even when called from inside a running event loop
    (e.g. Jupyter): then it runs in a worker thread with its own loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())  # type: ignore[arg-type]
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(factory())).result()  # type: ignore[arg-type]


class EdgeTTS(TTSEngine):
    name = "edge"
    attempts = 3
    retry_delay = 2.0  # seconds, doubled after each failed attempt

    @classmethod
    def problem(cls) -> str | None:
        try:
            _import_edge_tts()
        except AutoShortsError as exc:
            return str(exc)
        return None

    def synthesize(self, text: str, out_path: Path) -> SpeechResult:
        out = Path(out_path).with_suffix(".mp3")
        out.parent.mkdir(parents=True, exist_ok=True)
        raw = self._synthesize_with_retries(text, out)
        duration = media_duration(out)
        words = align_words(text, raw, duration)
        log.debug("edge-tts: %.2fs, %d word events -> %d words", duration, len(raw), len(words))
        return SpeechResult(audio_path=out, duration=duration, words=words)

    def _synthesize_with_retries(self, text: str, out: Path) -> list[WordTiming]:
        edge_tts = _import_edge_tts()
        last: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            comm = self._communicate(edge_tts, text)  # a Communicate can only stream once
            try:
                raw = run_coroutine(lambda: self._stream(comm, out))
                if not out.exists() or out.stat().st_size == 0:
                    raise AutoShortsError("edge-tts returned no audio")
                return raw
            except Exception as exc:  # network errors from aiohttp / edge_tts.exceptions
                last = exc
            if attempt < self.attempts:
                delay = self.retry_delay * 2 ** (attempt - 1)
                log.warning("edge-tts attempt %d/%d failed (%s); retrying in %.0fs", attempt, self.attempts,
                            _describe(last), delay)
                time.sleep(delay)
        raise AutoShortsError(f"edge-tts failed after {self.attempts} attempts: {_describe(last)}")

    @staticmethod
    async def _stream(comm: Any, out: Path) -> list[WordTiming]:
        words: list[WordTiming] = []
        with open(out, "wb") as fh:
            async for chunk in comm.stream():
                kind = chunk.get("type")
                if kind == "audio":
                    fh.write(chunk["data"])
                elif kind == "WordBoundary":
                    start = chunk["offset"] / TICKS_PER_SECOND
                    end = (chunk["offset"] + chunk["duration"]) / TICKS_PER_SECOND
                    words.append(WordTiming(chunk.get("text", ""), start, end))
        return words

    def _communicate(self, edge_tts: Any, text: str) -> Any:
        ec = self.cfg.tts.edge
        kwargs = {"rate": ec.rate, "pitch": ec.pitch, "volume": ec.volume}
        try:
            try:
                return edge_tts.Communicate(text, ec.voice, boundary="WordBoundary", **kwargs)
            except TypeError as exc:
                if "boundary" not in str(exc):
                    raise
                # edge-tts < 7 has no 'boundary' argument and always sends word events
                return edge_tts.Communicate(text, ec.voice, **kwargs)
        except (ValueError, TypeError) as exc:  # bad voice/rate/pitch format: retrying won't help
            raise AutoShortsError(f"edge-tts rejected the settings in tts.edge: {exc}") from exc


def _describe(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown error"
    msg = str(exc).strip()
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def list_voices(lang_prefix: str = "") -> list[dict]:
    """Edge voices whose locale starts with ``lang_prefix`` (e.g. "en", "en-GB"), sorted.

    Each dict keeps edge-tts' keys (ShortName, Gender, Locale, FriendlyName, VoiceTag...)
    plus lower-case shortcuts: name, gender, locale, personalities.
    """
    edge_tts = _import_edge_tts()
    try:
        voices = run_coroutine(lambda: edge_tts.list_voices())
    except Exception as exc:
        raise AutoShortsError(f"could not fetch the edge-tts voice list (needs internet): {_describe(exc)}") from exc
    prefix = lang_prefix.lower()
    out = []
    for v in voices:
        locale = str(v.get("Locale", ""))
        if prefix and not locale.lower().startswith(prefix):
            continue
        tags = v.get("VoiceTag") or {}
        out.append({
            **v,
            "name": v.get("ShortName", ""),
            "gender": v.get("Gender", ""),
            "locale": locale,
            "personalities": list(tags.get("VoicePersonalities", [])),
        })
    return sorted(out, key=lambda d: (d["locale"], d["name"]))
