"""Word timing helpers shared by the TTS engines and the caption builder.

* ``display_tokens`` splits narration text into the words shown on screen
  (punctuation kept; punctuation-only tokens such as "—" are glued to a neighbour).
* ``estimate_word_timings`` spreads words over a clip proportionally to a
  syllable-ish weight, with extra time after , ; : and . ! ? (engines without
  word events: espeak, pyttsx3).
* ``estimate_from_audio`` does the same but first looks at the audio itself
  (leading/trailing silence and pauses at punctuation) for a much tighter fit.
* ``align_words`` maps an engine's word events (edge-tts drops punctuation)
  back onto the original text tokens so captions keep the punctuation.
"""
from __future__ import annotations

import array
import difflib
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path

from ..models import WordTiming

SENTENCE_END = ".!?…"
CLAUSE_END = ",;:—–"
_CLOSERS = "\"'”’»)]}"
# Symbols that are read aloud even when they stand alone ("rock & roll", "50 %").
SPEAKABLE_SYMBOLS = frozenset("&%$€£+=")
# Tokens ending in "." that are not sentence ends.
ABBREVIATIONS = frozenset(
    {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "jr.", "sr.", "prof.", "e.g.", "i.e.", "approx.", "no."}
)

# Pause after a token, in the same units as word_weight (~1 unit per letter).
PAUSE_WEIGHT = {"sentence": 7.0, "clause": 4.0}
MIN_WORD_SECONDS = 0.08


# --------------------------------------------------------------------------- tokens


def is_speakable(token: str) -> bool:
    return any(ch.isalnum() or ch in SPEAKABLE_SYMBOLS for ch in token)


def display_tokens(text: str) -> list[str]:
    """Whitespace tokens; punctuation-only tokens are merged into the previous word
    (or the next one when they come first), e.g. "Wait — what?" -> ["Wait —", "what?"]."""
    tokens: list[str] = []
    prefix = ""
    for raw in text.split():
        if is_speakable(raw):
            tokens.append(f"{prefix} {raw}" if prefix else raw)
            prefix = ""
        elif tokens:
            tokens[-1] = f"{tokens[-1]} {raw}"
        else:
            prefix = f"{prefix} {raw}".strip()
    return tokens


def pause_after(token: str) -> str | None:
    """"sentence", "clause" or None depending on the token's trailing punctuation."""
    t = token.rstrip(_CLOSERS)
    if not t or t.lower() in ABBREVIATIONS:
        return None
    if t.endswith("...") or t[-1] in SENTENCE_END:
        return "sentence"
    if t[-1] in CLAUSE_END:
        return "clause"
    return None


def ends_sentence(token: str) -> bool:
    return pause_after(token) == "sentence"


def ends_clause(token: str) -> bool:
    return pause_after(token) is not None


def word_weight(token: str) -> float:
    """Rough spoken length: letters, digits count extra ("1990" is four syllables)."""
    letters = sum(ch.isalpha() for ch in token)
    digits = sum(ch.isdigit() for ch in token)
    symbols = sum(ch in SPEAKABLE_SYMBOLS for ch in token)
    return 1.0 + letters + 2.5 * digits + 5.0 * symbols


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


# --------------------------------------------------------------------------- estimation


def _distribute(tokens: list[str], t0: float, t1: float) -> list[WordTiming]:
    """Lay ``tokens`` out back to back over [t0, t1], with pauses after punctuation."""
    if not tokens:
        return []
    t1 = max(t1, t0)
    weights = [word_weight(t) for t in tokens]
    pauses = [PAUSE_WEIGHT.get(pause_after(t) or "", 0.0) for t in tokens[:-1]] + [0.0]
    unit = (t1 - t0) / (sum(weights) + sum(pauses))
    out: list[WordTiming] = []
    t = t0
    for tok, w, p in zip(tokens, weights, pauses):
        end = t + w * unit
        out.append(WordTiming(tok, round(t, 3), round(end, 3)))
        t = end + p * unit
    out[-1].end = round(t1, 3)
    return out


def estimate_word_timings(text: str, duration: float, lead: float = 0.05, tail: float = 0.1) -> list[WordTiming]:
    """Spread the words of ``text`` over [lead, duration - tail] by syllable-ish weight."""
    tokens = display_tokens(text)
    if not tokens or duration <= 0:
        return []
    lead, tail = max(0.0, lead), max(0.0, tail)
    if lead + tail > duration * 0.5:  # very short clip: shrink the margins
        scale = duration * 0.5 / (lead + tail)
        lead, tail = lead * scale, tail * scale
    return _distribute(tokens, lead, duration - tail)


@dataclass
class SilenceInfo:
    duration: float
    speech_start: float
    speech_end: float
    gaps: list[tuple[float, float]] = field(default_factory=list)  # silent stretches inside the speech


def analyze_silence(
    wav_path: str | Path, window: float = 0.01, rel_db: float = -35.0, min_gap: float = 0.12
) -> SilenceInfo | None:
    """Find where speech starts/ends and the pauses in between (16-bit PCM WAV only).

    Returns None for unreadable/unsupported files or pure silence.
    """
    try:
        with wave.open(str(wav_path), "rb") as w:
            width, channels, rate, frames = w.getsampwidth(), w.getnchannels(), w.getframerate(), w.getnframes()
            if width != 2 or frames == 0 or rate <= 0:
                return None
            data = w.readframes(frames)
    except (wave.Error, OSError, EOFError):
        return None
    samples = array.array("h")
    samples.frombytes(data[: len(data) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    step = max(1, int(rate * window)) * channels
    peaks = []
    for i in range(0, len(samples), step):
        chunk = samples[i : i + step]
        peaks.append(max(max(chunk), -min(chunk)))
    if not peaks:
        return None
    threshold = max(max(peaks) * 10 ** (rel_db / 20), 64)
    voiced = [i for i, p in enumerate(peaks) if p > threshold]
    if not voiced:
        return None
    win = step / channels / rate
    gaps: list[tuple[float, float]] = []
    for prev, cur in zip(voiced, voiced[1:]):
        if (cur - prev - 1) * win >= min_gap:
            gaps.append(((prev + 1) * win, cur * win))
    return SilenceInfo(frames / rate, voiced[0] * win, (voiced[-1] + 1) * win, gaps)


def _pause_aligned(tokens: list[str], info: SilenceInfo) -> list[WordTiming] | None:
    """Split tokens at punctuation and pin each chunk between the matching audio pauses."""
    cuts = [i for i, t in enumerate(tokens[:-1]) if pause_after(t)]
    if not cuts or len(info.gaps) < len(cuts):
        return None
    gaps = sorted(sorted(info.gaps, key=lambda g: g[1] - g[0], reverse=True)[: len(cuts)])
    bounds = [i + 1 for i in cuts]
    chunks = [tokens[a:b] for a, b in zip([0, *bounds], [*bounds, len(tokens)])]
    spans = list(zip([info.speech_start, *(g[1] for g in gaps)], [*(g[0] for g in gaps), info.speech_end]))
    total_w = sum(word_weight(t) for t in tokens)
    total_t = sum(b - a for a, b in spans)
    if total_t <= 0:
        return None
    for chunk, (a, b) in zip(chunks, spans):
        expected = sum(word_weight(t) for t in chunk) / total_w
        actual = (b - a) / total_t
        if not 0.5 <= actual / expected <= 2.0:  # pauses don't line up with the text
            return None
    out: list[WordTiming] = []
    for chunk, (a, b) in zip(chunks, spans):
        out.extend(_distribute(chunk, a, b))
    return out


def estimate_from_audio(text: str, wav_path: str | Path, duration: float | None = None) -> list[WordTiming]:
    """Estimate word timings using the audio's silences (16-bit WAV); falls back to
    ``estimate_word_timings`` when the file can't be analysed."""
    info = analyze_silence(wav_path)
    if duration is None:
        duration = info.duration if info else 0.0
    tokens = display_tokens(text)
    if info is None or not tokens or info.speech_end - info.speech_start < 0.05:
        return estimate_word_timings(text, duration)
    aligned = _pause_aligned(tokens, info)
    if aligned is not None:
        return aligned
    return _distribute(tokens, info.speech_start, min(info.speech_end, duration))


# --------------------------------------------------------------------------- alignment


def align_words(text: str, raw_words: list[WordTiming], duration: float | None = None) -> list[WordTiming]:
    """Map engine word events onto the display tokens of ``text``.

    Matching is done on lower-cased alphanumeric characters, so missing punctuation,
    different apostrophes or one token spoken as several events ("well-known" ->
    "well", "known") all line up. Tokens the engine skipped get time interpolated
    from their neighbours. Without any events this falls back to estimation.
    """
    tokens = display_tokens(text)
    if not tokens:
        return []
    raw_words = [w for w in raw_words if _normalize(w.word)]
    if not raw_words:
        return estimate_word_timings(text, duration or 0.0)

    orig_chars, orig_owner = _char_stream([_normalize(t) for t in tokens])
    raw_chars, raw_owner = _char_stream([_normalize(w.word) for w in raw_words])
    matcher = difflib.SequenceMatcher(None, orig_chars, raw_chars, autojunk=False)
    hits: list[dict[int, int]] = [{} for _ in raw_words]  # raw word -> {token index: matched chars}
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            tok, rw = orig_owner[a + k], raw_owner[b + k]
            hits[rw][tok] = hits[rw].get(tok, 0) + 1

    spans: list[tuple[float, float] | None] = [None] * len(tokens)
    for rw, counts in zip(raw_words, hits):
        if not counts:
            continue
        total = sum(counts.values())
        t = rw.start
        for tok in sorted(counts):  # split the event's time across the tokens it covers
            end = t + (rw.end - rw.start) * counts[tok] / total
            old = spans[tok]
            spans[tok] = (t, end) if old is None else (min(old[0], t), max(old[1], end))
            t = end

    if all(s is None for s in spans):
        return estimate_word_timings(text, duration or raw_words[-1].end)
    _fill_missing(tokens, spans, duration)
    return _monotonic(tokens, spans, duration)  # type: ignore[arg-type]


def _char_stream(parts: list[str]) -> tuple[str, list[int]]:
    owner: list[int] = []
    for i, p in enumerate(parts):
        owner.extend([i] * len(p))
    return "".join(parts), owner


def _fill_missing(tokens: list[str], spans: list[tuple[float, float] | None], duration: float | None) -> None:
    """Give tokens without an engine event a slice of time between their neighbours."""
    n = len(tokens)
    i = 0
    while i < n:
        if spans[i] is not None:
            i += 1
            continue
        j = i
        while j + 1 < n and spans[j + 1] is None:
            j += 1
        left = i - 1 if i > 0 else None
        right = j + 1 if j + 1 < n else None
        lo = spans[left][1] if left is not None else 0.0  # type: ignore[index]
        if right is not None:
            hi = spans[right][0]  # type: ignore[index]
        else:
            hi = duration if duration and duration > lo else lo + 0.3 * (j - i + 1)
        idx = list(range(i, j + 1))
        if hi - lo < MIN_WORD_SECONDS * len(idx):  # no room: share the neighbours' time too
            if left is not None:
                lo = spans[left][0]  # type: ignore[index]
                idx.insert(0, left)
            if right is not None:
                hi = spans[right][1]  # type: ignore[index]
                idx.append(right)
        for k, wt in zip(idx, _distribute([tokens[k] for k in idx], lo, hi)):
            spans[k] = (wt.start, wt.end)
        i = j + 1


def _monotonic(tokens: list[str], spans: list[tuple[float, float]], duration: float | None) -> list[WordTiming]:
    out: list[WordTiming] = []
    prev_start = 0.0
    for tok, (start, end) in zip(tokens, spans):
        start = max(start, prev_start, 0.0)
        end = max(end, start)
        if duration:
            start, end = min(start, duration), min(end, duration)
        out.append(WordTiming(tok, round(start, 3), round(end, 3)))
        prev_start = start
    return out
