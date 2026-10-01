"""Tests for autoshorts.topics: topic file parsing, used-topic state, fallbacks."""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path

import pytest

from autoshorts import topics as topics_mod
from autoshorts.config import Config
from autoshorts.topics import TopicQueue, builtin_ideas, parse_topics, topic_key
from autoshorts.utils import AutoShortsError


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.base_dir = tmp_path
    return c


def write_topics(cfg: Config, text: str) -> Path:
    path = cfg.path(cfg.topics.file)
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_skips_comments_blanks_and_dedupes_case_insensitively():
    text = "# my topics\n\n  Why the sky is blue  \nOctopus  facts\nwhy THE sky is blue\n   # indented comment\nBees\n"
    assert parse_topics(text) == ["Why the sky is blue", "Octopus facts", "Bees"]


def test_reads_utf8_with_bom(cfg):
    cfg.path("topics.txt").write_bytes("﻿Café history\nTea\n".encode("utf-8"))
    assert TopicQueue(cfg).topics() == ["Café history", "Tea"]


def test_next_returns_first_unused_and_mark_used_writes_state(cfg):
    write_topics(cfg, "First topic\nSecond topic\n")
    q = TopicQueue(cfg)
    assert q.next() == "First topic"
    assert q.next() == "First topic"  # next() alone never consumes a topic
    q.mark_used("First topic")
    assert q.next() == "Second topic"
    assert q.unused() == ["Second topic"]

    state = json.loads(cfg.path(cfg.topics.state_file).read_text(encoding="utf-8"))
    assert list(state) == ["used"]
    assert list(state["used"]) == ["first topic"]
    assert "T" in state["used"]["first topic"]  # ISO timestamp
    # atomic write leaves no temp files behind
    assert [p.name for p in cfg.path(cfg.topics.state_file).parent.iterdir()] == ["used_topics.json"]


def test_used_state_is_case_insensitive(cfg):
    write_topics(cfg, "Black Holes\nVolcanoes\n")
    q = TopicQueue(cfg)
    q.mark_used("  black   HOLES ")
    assert q.is_used("Black Holes")
    assert q.next() == "Volcanoes"


def test_next_can_exclude_topics(cfg):
    write_topics(cfg, "a\nb\nc\n")
    assert TopicQueue(cfg).next(exclude=["A", "b"]) == "c"


def test_builtin_ideas_are_plentiful_and_unique():
    ideas = builtin_ideas()
    assert len(ideas) >= 150
    assert len({topic_key(t) for t in ideas}) == len(ideas)
    assert not any(t.startswith("#") for t in ideas)


def test_falls_back_to_builtin_ideas_when_file_missing_or_used(cfg):
    q = TopicQueue(cfg, rng=random.Random(1))
    first = q.next()
    assert first in builtin_ideas()

    write_topics(cfg, "Only topic\n")
    assert q.next() == "Only topic"
    q.mark_used("Only topic")
    fallback = q.next()
    assert fallback in builtin_ideas()
    q.mark_used(fallback)
    assert q.next() != fallback


def test_builtin_fallback_skips_ideas_already_in_topics_file(cfg, monkeypatch):
    monkeypatch.setattr(topics_mod, "builtin_ideas", lambda: ["Shared idea", "Other idea"])
    write_topics(cfg, "shared IDEA\n")
    q = TopicQueue(cfg)
    q.mark_used("shared IDEA")
    assert q.next() == "Other idea"


def test_everything_used_raises_without_allow_repeats(cfg, monkeypatch):
    monkeypatch.setattr(topics_mod, "builtin_ideas", lambda: ["Idea one", "Idea two"])
    write_topics(cfg, "Mine\n")
    q = TopicQueue(cfg)
    for topic in ("Mine", "Idea one", "Idea two"):
        q.mark_used(topic)
    with pytest.raises(AutoShortsError, match="every topic has been used"):
        q.next()
    # still nothing reset automatically
    assert len(q.used()) == 3


def test_allow_repeats_picks_least_recently_used(cfg, monkeypatch):
    monkeypatch.setattr(topics_mod, "builtin_ideas", lambda: [])
    cfg.topics.allow_repeats = True
    write_topics(cfg, "a\nb\nc\n")
    q = TopicQueue(cfg)
    for t in ("b", "a", "c"):
        q.mark_used(t)
    assert q.next() == "b"
    q.mark_used("b")
    assert q.next() == "a"
    assert q.next(exclude=["a"]) == "c"


def test_corrupt_state_file_is_treated_as_empty(cfg):
    write_topics(cfg, "a\n")
    state = cfg.path(cfg.topics.state_file)
    state.parent.mkdir(parents=True)
    state.write_text("{not json", encoding="utf-8")
    q = TopicQueue(cfg)
    assert q.next() == "a"
    q.mark_used("a")  # rewrites a valid file
    assert list(json.loads(state.read_text(encoding="utf-8"))["used"]) == ["a"]


def test_corrupt_state_file_is_moved_aside_not_overwritten(cfg, caplog):
    """A truncated history must not be replaced by a one-entry file (that would make every
    old topic come round again on the timer)."""
    write_topics(cfg, "alpha\nbeta\ngamma\n")
    q = TopicQueue(cfg)
    q.mark_used("alpha")
    q.mark_used("beta")
    state = cfg.path(cfg.topics.state_file)
    good = state.read_text(encoding="utf-8")
    state.write_text(good[:-5], encoding="utf-8")  # truncated by a crash
    with caplog.at_level(logging.WARNING, logger="autoshorts"):
        assert q.next() == "alpha"
    q.mark_used("alpha")
    aside = [p for p in state.parent.iterdir() if p.name.startswith("used_topics.json.corrupt-")]
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == good[:-5]  # old history kept
    assert "moved it to" in caplog.text


def test_mark_used_refuses_to_overwrite_an_unmovable_unreadable_history(cfg, monkeypatch):
    write_topics(cfg, "a\n")
    state = cfg.path(cfg.topics.state_file)
    state.parent.mkdir(parents=True)
    state.write_text("{not json", encoding="utf-8")

    def no_move(src, dst):
        raise PermissionError("read-only")

    monkeypatch.setattr("autoshorts.topics.os.replace", no_move)
    with pytest.raises(AutoShortsError, match="refusing to overwrite"):
        TopicQueue(cfg).mark_used("a")
    assert state.read_text(encoding="utf-8") == "{not json"


def test_add_appends_only_new_unique_topics(cfg):
    path = write_topics(cfg, "# header\nExisting topic")  # no trailing newline
    q = TopicQueue(cfg)
    added = q.add(["existing TOPIC", "New one", "  new   one ", "", "# not a topic", "Another"])
    assert added == 2
    assert path.read_text(encoding="utf-8") == "# header\nExisting topic\nNew one\nAnother\n"
    assert q.add(["Another"]) == 0
    assert q.unused() == ["Existing topic", "New one", "Another"]


def test_add_creates_missing_file(cfg):
    cfg.topics.file = "lists/my_topics.txt"
    q = TopicQueue(cfg)
    assert q.add(["Space", "Sea"]) == 2
    assert cfg.path("lists/my_topics.txt").read_text(encoding="utf-8") == "Space\nSea\n"
    assert q.next() == "Space"


def test_unused_can_include_builtin_ideas(cfg, monkeypatch):
    monkeypatch.setattr(topics_mod, "builtin_ideas", lambda: ["x", "Mine", "y"])
    write_topics(cfg, "Mine\n")
    q = TopicQueue(cfg)
    q.mark_used("x")
    assert q.unused() == ["Mine"]
    assert q.unused(include_builtin=True) == ["Mine", "y"]
