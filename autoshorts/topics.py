"""Topic queue: decides what the next video is about.

Topics come from ``cfg.topics.file`` (one per line; blank lines and lines starting with
'#' are ignored; duplicates are dropped case-insensitively, first spelling wins). Topics
that already became a video are remembered in ``cfg.topics.state_file``::

    {"used": {"<topic lower-cased>": "<ISO timestamp>"}}

``next()`` returns the first unused topic from the file. When the file is missing or
every topic in it was used, a random unused idea from the built-in list
(``data/topic_ideas.txt``) is returned. When those are used up too, the queue does not
silently start over: it raises AutoShortsError, unless ``topics.allow_repeats`` is set,
in which case the least-recently used topic comes back.

``next()`` never marks a topic as used; the pipeline calls ``mark_used()`` after a
successful render, so a failed video does not burn its topic.
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import Config
from .utils import AutoShortsError, ensure_dir, log

IDEAS_PATH = Path(__file__).resolve().parent / "data" / "topic_ideas.txt"


def topic_key(topic: str) -> str:
    """Case- and whitespace-insensitive identity of a topic (the key in the state file)."""
    return " ".join(str(topic).split()).lower()


def clean_topic(line: str) -> str:
    """One topic line with surrounding/internal runs of whitespace collapsed."""
    return " ".join(str(line).split())


def parse_topics(text: str) -> list[str]:
    """Topics in ``text``: one per line, '#' comment lines and blanks skipped, deduped."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        topic = clean_topic(raw)
        if not topic or topic.startswith("#"):
            continue
        key = topic_key(topic)
        if key not in seen:
            seen.add(key)
            out.append(topic)
    return out


def read_topics_file(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        # utf-8-sig: files saved by Windows Notepad may start with a byte-order mark.
        return parse_topics(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as exc:
        raise AutoShortsError(f"could not read topics file {path}: {exc}") from exc


def builtin_ideas() -> list[str]:
    """The evergreen topic ideas shipped with autoshorts."""
    try:
        return read_topics_file(IDEAS_PATH)
    except AutoShortsError as exc:  # pragma: no cover - only with a broken install
        log.warning("%s", exc)
        return []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def write_json_atomic(path: Path, data: object) -> None:
    """Write JSON to a temp file next to ``path`` and rename it over ``path``."""
    ensure_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class TopicQueue:
    """User topics first (in file order), then built-in ideas (random order)."""

    def __init__(self, cfg: Config, rng: random.Random | None = None):
        self.cfg = cfg
        self.file = cfg.path(cfg.topics.file)
        self.state_file = cfg.path(cfg.topics.state_file)
        self.rng = rng or random.Random()

    # ----------------------------------------------------------------- reading

    def topics(self) -> list[str]:
        """Every topic in the topics file (used or not)."""
        return read_topics_file(self.file)

    def used(self) -> dict[str, str]:
        """{topic key: ISO timestamp of when it was used}."""
        if not self.state_file.is_file():
            return {}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            used = data.get("used", {}) if isinstance(data, dict) else {}
            if not isinstance(used, dict):
                raise ValueError("'used' is not a mapping")
        except (OSError, ValueError) as exc:
            log.warning("topic history %s is unreadable (%s); treating every topic as unused",
                        self.state_file, exc)
            return {}
        return {topic_key(k): str(v) for k, v in used.items()}

    def is_used(self, topic: str) -> bool:
        return topic_key(topic) in self.used()

    def unused(self, include_builtin: bool = False) -> list[str]:
        """Unused topics from the topics file, in order (plus unused built-in ideas)."""
        used = self.used()
        pool = self.topics()
        if include_builtin:
            pool = _merge(pool, builtin_ideas())
        return [t for t in pool if topic_key(t) not in used]

    # ----------------------------------------------------------------- picking

    def next(self, exclude: Iterable[str] = ()) -> str:
        """The topic for the next video (not marked as used; see module docstring).

        ``exclude`` skips topics, e.g. ones that already failed in this batch.
        """
        used = self.used()
        skip = {topic_key(t) for t in exclude}
        mine = self.topics()
        for topic in mine:
            key = topic_key(topic)
            if key not in used and key not in skip:
                return topic

        user_keys = {topic_key(t) for t in mine}
        ideas = [
            t for t in builtin_ideas()
            if topic_key(t) not in used and topic_key(t) not in skip and topic_key(t) not in user_keys
        ]
        if ideas:
            topic = self.rng.choice(ideas)
            if mine:
                log.info("topics: every topic in %s is used; picked a built-in idea: %s", self.file.name, topic)
            else:
                log.info("topics: no topics in %s; picked a built-in idea: %s", self.file, topic)
            return topic

        if not self.cfg.topics.allow_repeats:
            raise AutoShortsError(
                f"every topic has been used. Add new topics (one per line) to {self.file} or run "
                "'autoshorts topics add \"your topic\"', or set topics.allow_repeats: true in config.yaml "
                f"to reuse the oldest ones (history: {self.state_file})."
            )
        return self._least_recently_used(_merge(mine, builtin_ideas()), used, skip)

    def _least_recently_used(self, pool: list[str], used: dict[str, str], skip: set[str]) -> str:
        candidates = [t for t in pool if topic_key(t) not in skip] or pool
        if not candidates:
            raise AutoShortsError(f"no topics available: add some to {self.file}")
        # min() keeps the first of equal timestamps, so file order breaks ties.
        topic = min(candidates, key=lambda t: used.get(topic_key(t), ""))
        log.info("topics: all used; repeating the least recently used one: %s", topic)
        return topic

    # ----------------------------------------------------------------- writing

    def mark_used(self, topic: str) -> None:
        """Record that ``topic`` became a video (written atomically)."""
        key = topic_key(topic)
        if not key:
            return
        used = self.used()
        used.pop(key, None)  # re-insert so the file also lists topics in usage order
        used[key] = _now()
        write_json_atomic(self.state_file, {"used": used})
        log.debug("topics: marked used: %s", topic)

    def add(self, topics: Iterable[str]) -> int:
        """Append topics that are not in the topics file yet. Returns how many were added."""
        existing = {topic_key(t) for t in self.topics()}
        used = self.used()
        new: list[str] = []
        for raw in topics:
            topic = clean_topic(raw)
            key = topic_key(topic)
            if not topic or topic.startswith("#") or key in existing:
                continue
            existing.add(key)
            new.append(topic)
            if key in used:
                log.warning("topic %r was already used for a video; it will be skipped unless "
                            "topics.allow_repeats is true", topic)
        if not new:
            return 0
        ensure_dir(self.file.parent)
        prefix = ""
        if self.file.is_file() and self.file.stat().st_size > 0:
            with open(self.file, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) not in (b"\n", b"\r"):
                    prefix = "\n"
        with open(self.file, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(prefix + "".join(f"{t}\n" for t in new))
        return len(new)


def _merge(first: list[str], second: list[str]) -> list[str]:
    """``first`` followed by the items of ``second`` it does not already contain."""
    keys = {topic_key(t) for t in first}
    return first + [t for t in second if topic_key(t) not in keys]
