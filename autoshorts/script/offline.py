"""Offline script generator: picks a hand-written script from data/content_bank.json.

No network, no API keys. The bank holds fact-checked, evergreen scripts for every
format. A topic is matched by word overlap with each entry's topic, title, hashtags
and text; unknown topics get a random entry of the requested format. With format
"random" the best-matching entry's format is used. Recently used
entries are remembered in ``<state>/offline_used.json`` so batches do not repeat.
"""
from __future__ import annotations

import json
import os
import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import Config
from ..models import VideoScript
from ..utils import AutoShortsError, ensure_dir, log
from . import CLASSIC_FORMATS, ScriptGenerator, resolve_format
from .validate import STOPWORDS, script_words, target_words, validate_script

BANK_PATH = Path(__file__).resolve().parent.parent / "data" / "content_bank.json"
STATE_FILE = "offline_used.json"
MAX_REMEMBERED = 100

# Words that say nothing about the subject ("crazy space facts" is about "space").
GENERIC_WORDS = frozenset(
    """fact facts story stories quiz quizzes trivia question questions motivation motivational
    explainer explained explain video videos short shorts tiktok youtube viral cool amazing crazy
    weird fun interesting random top best thing things know learn did true real""".split()
)


@lru_cache(maxsize=4)
def _load_bank_cached(path: str) -> tuple[dict[str, Any], ...]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise AutoShortsError(f"could not read the offline content bank {path}: {exc}") from None
    entries = data.get("entries", data) if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise AutoShortsError(f"offline content bank {path} has no entries")
    return tuple(entries)


def load_bank(path: Path | None = None) -> list[dict[str, Any]]:
    """All content-bank entries (each: topic, format, title, segments, description, hashtags)."""
    return list(_load_bank_cached(str(path or BANK_PATH)))


def _stem(word: str) -> str:
    """Matching key shared by a word and its plural (never shown to anyone):
    octopus/octopuses -> octopus, house/houses -> hous, story/stories -> stori.
    Words that only look plural (octopus, glass, axis) keep their final s."""
    w = word
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "i"
    elif len(w) > 4 and w.endswith("es") and w[:-2].endswith(("s", "x", "z", "ch", "sh")):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    if len(w) > 3 and w.endswith("y"):
        w = w[:-1] + "i"
    return w


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {
        _stem(w) for w in words
        if len(w) > 2 and w not in STOPWORDS and w not in GENERIC_WORDS and _stem(w) not in GENERIC_WORDS
    }


def match_score(topic: str, entry: dict[str, Any]) -> int:
    """Topic-word overlap: the entry's topic counts most, then title, hashtags, then script text."""
    wanted = tokens(topic)
    if not wanted:
        return 0
    body = " ".join(str(s.get("text", "")) for s in entry.get("segments", []) if isinstance(s, dict))
    score = 4 * len(wanted & tokens(entry.get("topic", "")))
    score += 3 * len(wanted & tokens(entry.get("title", "")))
    score += 2 * len(wanted & tokens(" ".join(entry.get("hashtags", []))))
    score += len(wanted & tokens(body + " " + entry.get("description", "")))
    return score


class OfflineGenerator(ScriptGenerator):
    name = "offline"

    def __init__(self, cfg: Config, bank_path: Path | None = None, rng: random.Random | None = None):
        super().__init__(cfg)
        self.bank_path = bank_path
        self.rng = rng or random.Random()
        self.state_path = cfg.path("state") / STATE_FILE

    # ----------------------------------------------------------------- state

    def _used(self) -> list[str]:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                used = json.load(fh).get("used", [])
            return [str(t) for t in used] if isinstance(used, list) else []
        except FileNotFoundError:
            return []
        except (OSError, ValueError, AttributeError) as exc:
            log.debug("ignoring unreadable %s: %s", self.state_path, exc)
            return []

    def _remember(self, title: str) -> None:
        used = [t for t in self._used() if t != title] + [title]
        try:
            ensure_dir(self.state_path.parent)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"used": used[-MAX_REMEMBERED:]}, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError as exc:
            log.warning("could not save offline generator state to %s: %s", self.state_path, exc)

    # ----------------------------------------------------------------- choice

    def choose(self, topic: str, fmt: str) -> dict[str, Any]:
        """Best entry of format ``fmt`` for ``topic``, avoiding recently used ones."""
        candidates = [e for e in load_bank(self.bank_path) if e.get("format") == fmt]
        if not candidates:
            raise AutoShortsError(
                f"the offline content bank has no '{fmt}' scripts; this format needs an AI script writer: "
                "set LLM_API_KEY in .env (free key: https://console.groq.com/keys) or install Ollama"
            )
        used = self._used()
        last = used[-1] if used else None

        def staleness(entry: dict[str, Any]) -> int:
            """0 = never used; otherwise higher = used more recently."""
            title = entry.get("title", "")
            return used.index(title) + 1 if title in used else 0

        scored = [(match_score(topic, e), e) for e in candidates]
        matches = [(s, e) for s, e in scored if s > 0]
        # A matching script that was used recently is not offered again for a related
        # topic: the same video would be made (and uploaded) once per topic. Without a
        # fresh match the topic is treated as unknown (an unrelated script, topic unused).
        fresh = [(s, e) for s, e in matches if staleness(e) == 0]
        if matches and not fresh:
            log.warning("offline: every content-bank script matching %r was used recently", topic)
        matches = fresh
        if matches:
            if len(matches) > 1:
                matches = [(s, e) for s, e in matches if e.get("title") != last]
            best = max(s for s, _ in matches)
            pool = [e for s, e in matches if s == best]
        else:
            if topic.strip():
                log.warning(
                    "offline mode ignores unknown topics: nothing in the content bank matches %r, "
                    "using a random '%s' script (use Ollama or a free LLM API for custom topics)",
                    topic, fmt,
                )
            pool = candidates
        freshest = min(staleness(e) for e in pool)
        pool = [e for e in pool if staleness(e) == freshest]
        return self.rng.choice(pool)

    def pick_format(self, topic: str) -> str:
        """Format for ``fmt="random"``: the format of the best-matching entry (so "octopus
        facts" gets the octopus facts script, not a random quiz), else a random format."""
        scored = [(match_score(topic, e), e.get("format")) for e in load_bank(self.bank_path)
                  if e.get("format") in CLASSIC_FORMATS]
        best = max((score for score, _ in scored), default=0)
        if best > 0:
            return self.rng.choice(sorted({f for score, f in scored if score == best}))
        return resolve_format("random", self.rng)

    def generate(self, topic: str, fmt: str) -> VideoScript:
        topic = topic or ""
        if (fmt or "random").strip().lower() == "random":
            fmt = self.pick_format(topic)
        fmt = resolve_format(fmt, self.rng)
        if not self.cfg.script.language.lower().startswith("en"):
            log.warning("the offline content bank is English only (script.language=%s)", self.cfg.script.language)
        entry = self.choose(topic, fmt)
        target = target_words(self.cfg)
        script = validate_script(
            entry,
            topic=entry.get("topic", topic),
            fmt=fmt,
            target_words=target,
            language="en",
            min_ratio=0.0,  # hand-written scripts are never rejected for length
        )
        words = script_words(script.segments)
        if words < 0.8 * target:
            log.info("offline script has %d words (target %d); the video will be shorter than planned", words, target)
        self._remember(entry.get("title", ""))
        log.info("offline script: %r (%s)", script.title, fmt)
        return script
