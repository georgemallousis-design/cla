"""Normalize and validate raw script data (LLM JSON or content-bank entries) into a VideoScript.

Everything a generator produces goes through :func:`validate_script`, so downstream
stages can rely on: clean spoken text, 3+ segments, a visual_query on every segment,
a title <= 90 chars, a hashtag-free description and clean lowercase hashtags, and a
spoken length close to ``target_words(cfg)``.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Iterable

from ..config import Config
from ..models import Segment, VideoScript
from ..utils import AutoShortsError, log

WORDS_PER_SECOND = 2.6  # typical TTS narration pace for Shorts
MAX_TITLE_CHARS = 90
MAX_HASHTAGS = 10
MAX_QUERY_WORDS = 4

STOPWORDS = frozenset(
    """a an the and or but nor of in on at to for with from by about as into onto over under
    is are was were be been being am it its it's this that these those there here
    i me my we our you your yours he him his she her they them their what which who whom whose
    how why when where do does did done doing not no yes can could will would should shall may might
    must just than then so very too also only even more most much many some any all each every
    one ones thing things get got have has had having make makes made really actually ever never
    if up out off again once like""".split()
)

TEXT_KEYS = ("text", "narration", "voiceover", "voice_over", "line", "content", "script", "sentence")
QUERY_KEYS = ("visual_query", "visual", "visuals", "query", "footage", "keywords", "broll", "b_roll", "image")
SEGMENT_KEYS = ("segments", "scenes", "beats", "parts", "script", "lines")

_LABEL_RE = re.compile(
    r"^\s*(?:hook|intro|outro|cta|call to action|narrator|voice\s*over|vo|"
    r"(?:segment|scene|part|beat)\s*\d+)\s*[:\-\u2013\u2014]\s*",
    re.IGNORECASE,
)
_SQUARE_RE = re.compile(r"\[[^\]]*\]")
_STAGE_RE = re.compile(
    r"\(\s*(?:pause|beat|music|sfx|sound|silence|dramatic|cut|transition|whisper|laugh)[^)]*\)",
    re.IGNORECASE,
)
_HASHTAG_RE = re.compile(r"#([^\W\d_][\w]*)", re.UNICODE)
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"[^\W\d_]+(?:['\u2019-][^\W\d_]+)*", re.UNICODE)


class ScriptValidationError(AutoShortsError):
    """Script data is unusable. The message is written so it can be fed back to an LLM."""


# --------------------------------------------------------------------------- counting


def target_words(cfg: Config) -> int:
    """Spoken words needed for ``cfg.video.target_seconds`` of narration."""
    return max(1, round(cfg.video.target_seconds * WORDS_PER_SECOND))


def count_words(text: str) -> int:
    """Spoken-word count. CJK characters (no spaces between words) count ~1.6 chars per word."""
    cjk = len(_CJK_RE.findall(text))
    rest = _CJK_RE.sub(" ", text)
    words = sum(1 for tok in rest.split() if any(ch.isalnum() for ch in tok))
    return words + math.ceil(cjk / 1.6)


def script_words(segments: Iterable[Segment]) -> int:
    return sum(count_words(s.text) for s in segments)


def keywords(text: str, limit: int = 3) -> list[str]:
    """First ``limit`` distinct content words of ``text`` (lowercase, stopwords removed)."""
    out: list[str] = []
    for word in _WORD_RE.findall(text.lower()):
        word = re.sub(r"'s$", "", word.replace("\u2019", "'"))
        if len(word) < 3 or word in STOPWORDS or word in out:
            continue
        out.append(word)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- cleaning


def collapse(text: str) -> str:
    return " ".join(str(text).split())


def strip_symbols(text: str) -> str:
    """Remove emoji/pictographs and invisible joiners that TTS would read out or fonts can't draw."""
    return "".join(
        ch for ch in text
        if unicodedata.category(ch) not in ("So", "Cs", "Co") and ch not in "\u200d\ufe0f\ufe0e"
    )


def clean_spoken(text: Any) -> str:
    """Spoken text: no labels, stage directions, markdown, hashtags or emoji."""
    t = strip_symbols(collapse(text if isinstance(text, str) else ("" if text is None else str(text))))
    t = _LABEL_RE.sub("", t)
    t = _SQUARE_RE.sub(" ", t)
    t = _STAGE_RE.sub(" ", t)
    t = _HASHTAG_RE.sub(" ", t)
    t = re.sub(r"[*_`#~]+", "", t)
    t = collapse(t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    return t.strip(" -\u2013\u2014")


def clean_query(query: Any) -> str:
    """1-4 lowercase search words for stock footage."""
    if isinstance(query, (list, tuple)):
        query = " ".join(str(q) for q in query)
    q = strip_symbols(str(query or "")).lower().replace("_", " ")
    q = re.sub(r"[^\w\s-]", " ", q, flags=re.UNICODE)
    words = q.split()
    if len(words) > MAX_QUERY_WORDS:
        words = [w for w in words if w not in STOPWORDS] or words
    return " ".join(words[:MAX_QUERY_WORDS])


def trim_at_word(text: str, limit: int) -> str:
    """Cut ``text`` to at most ``limit`` chars, at a word boundary when possible."""
    if len(text) <= limit:
        return text
    cut = text[: limit + 1]
    space = cut.rfind(" ")
    cut = cut[:space] if space > 0 else text[:limit]
    return cut.rstrip(" ,;:-\u2013\u2014|/")


def clean_title(title: Any, fallback: str) -> str:
    t = strip_symbols(collapse(title if isinstance(title, str) else ""))
    t = _HASHTAG_RE.sub(" ", t)
    t = collapse(t.replace("#", "")).strip(" \"'\u201c\u201d\u2018\u2019")
    if not t:
        t = fallback
    return trim_at_word(t, MAX_TITLE_CHARS)


def clean_hashtags(raw: Any, extra: Iterable[str] = ()) -> list[str]:
    """Lowercase alphanumeric tags without '#', de-duplicated, at most MAX_HASHTAGS."""
    if isinstance(raw, str):
        items: list[Any] = re.split(r"[\s,;]+", raw)
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = []
    out: list[str] = []
    for item in [*items, *extra]:
        tag = "".join(ch for ch in str(item).lower() if ch.isalnum())
        if tag and tag not in out:
            out.append(tag)
    return out[:MAX_HASHTAGS]


def clean_description(desc: Any) -> tuple[str, list[str]]:
    """Return (description without hashtags, hashtags that were found in it)."""
    text = strip_symbols(collapse(desc if isinstance(desc, str) else ""))
    found = _HASHTAG_RE.findall(text)
    text = collapse(_HASHTAG_RE.sub(" ", text).replace("#", ""))
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return text, found


# --------------------------------------------------------------------------- structure


def _first(d: dict, keys: Iterable[str]) -> Any:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return None


def _unwrap(data: Any) -> dict:
    """Accept {"script": {...}} style wrappers some models produce."""
    if not isinstance(data, dict):
        raise ScriptValidationError(
            "expected one JSON object with keys title, segments, description, hashtags"
        )
    if not any(isinstance(data.get(k), list) for k in SEGMENT_KEYS):
        nested = [v for v in data.values() if isinstance(v, dict)]
        if len(nested) == 1 and any(isinstance(nested[0].get(k), list) for k in SEGMENT_KEYS):
            return nested[0]
    return data


def _raw_segments(data: dict) -> list[tuple[str, str]]:
    raw = next((data[k] for k in SEGMENT_KEYS if isinstance(data.get(k), list)), None)
    if raw is None:
        raise ScriptValidationError('missing "segments": a list of {"text": ..., "visual_query": ...} objects')
    out: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            text, query = _first(item, TEXT_KEYS), _first(item, QUERY_KEYS)
        else:
            text, query = item, ""
        out.append((clean_spoken(text), clean_query(query)))
    return out


def fallback_query(topic: str, text: str) -> str:
    words = keywords(topic, 3) or keywords(text, 2)
    return clean_query(" ".join(words)) or "abstract background"


def _drop_range(segments: list[Segment], fmt: str) -> tuple[int, int]:
    """Which middle segments to drop next when over budget (never the hook or the last one)."""
    last = len(segments) - 1
    if fmt == "quiz":
        # Drop the last question together with its answer so no question is left unanswered.
        for i in range(last - 2, 0, -1):
            if "?" in segments[i].text:
                if last - i <= 3 and len(segments) - (last - i) >= 3:
                    return i, last
                break
    mid = min(max(len(segments) // 2, 1), last - 1)
    return mid, mid + 1


def trim_to_budget(segments: list[Segment], max_words: int, fmt: str = "") -> list[Segment]:
    """Drop middle segments until the script fits ``max_words`` (keeps >= 3 segments)."""
    segs = list(segments)
    while script_words(segs) > max_words and len(segs) > 3:
        start, end = _drop_range(segs, fmt)
        log.debug("script over budget: dropping segment(s) %d-%d", start, end - 1)
        del segs[start:end]
    return segs


# --------------------------------------------------------------------------- entry point


def validate_script(
    data: Any,
    *,
    topic: str,
    fmt: str,
    target_words: int,
    language: str = "en",
    min_ratio: float = 0.4,
    max_ratio: float = 1.25,
) -> VideoScript:
    """Normalize ``data`` into a VideoScript or raise ScriptValidationError.

    ``target_words`` is the spoken-word goal; scripts longer than ``max_ratio`` x target
    lose middle segments, scripts shorter than ``min_ratio`` x target are rejected.
    """
    data = _unwrap(data)
    topic = collapse(topic or data.get("topic") or "")
    pairs = [(t, q) for t, q in _raw_segments(data) if t]
    segments = [Segment(text=t, visual_query=q or fallback_query(topic, t)) for t, q in pairs]
    if len(segments) < 3:
        raise ScriptValidationError(
            f"only {len(segments)} non-empty segment(s); write 6-10 segments, each with a spoken "
            '"text" and a "visual_query"'
        )

    max_words = int(target_words * max_ratio)
    before = script_words(segments)
    if before > max_words:
        segments = trim_to_budget(segments, max_words, fmt)
        log.info("script trimmed from %d to %d words (budget %d)", before, script_words(segments), max_words)

    total = script_words(segments)
    min_words = math.ceil(target_words * min_ratio)
    if total < min_words:
        raise ScriptValidationError(
            f"script too short: {total} spoken words, but it needs about {target_words} "
            f"(at least {min_words}). Write longer segments or more of them."
        )

    description, desc_tags = clean_description(data.get("description"))
    hook = segments[0].text
    title = clean_title(data.get("title"), fallback=hook or topic.title())
    if not description:
        description = hook
    hashtags = clean_hashtags(data.get("hashtags") or data.get("tags"), extra=desc_tags)

    return VideoScript(
        topic=topic or title,
        format=fmt,
        title=title,
        segments=segments,
        description=description,
        hashtags=hashtags,
        language=language,
    )
