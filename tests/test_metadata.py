"""Tests for platform metadata: limits, sanitising, de-duplication, credits and output files."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoshorts import metadata
from autoshorts.config import Config
from autoshorts.metadata import (
    AI_DISCLOSURE,
    TIKTOK_CAPTION_MAX,
    YT_DESCRIPTION_MAX,
    YT_TAGS_MAX,
    YT_TITLE_MAX,
    build_hashtags,
    build_metadata,
    clean_hashtag,
    write_metadata,
    youtube_tag_length,
)
from autoshorts.models import ClipAsset, Segment, VideoScript


def make_script(**kw) -> VideoScript:
    base = dict(
        topic="deep sea creatures",
        format="facts",
        title="5 Deep Sea Creatures That Look Fake",
        segments=[Segment("The ocean hides monsters.", "deep sea"), Segment("Meet the anglerfish.", "anglerfish")],
        description="Strange animals from the deep ocean.",
        hashtags=["ocean", "DeepSea", "#facts"],
        language="en",
    )
    base.update(kw)
    return VideoScript(**base)


def clip(attribution: str, source: str = "pexels") -> ClipAsset:
    return ClipAsset(path=Path("x.mp4"), source=source, attribution=attribution)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.base_dir = tmp_path
    return c


# --------------------------------------------------------------------------- hashtags


@pytest.mark.parametrize("raw, expected", [
    ("#Fun Facts!", "funfacts"),
    ("sea-life", "sealife"),
    ("École", "ecole"),
    ("snake_case", "snake_case"),
    ("日本", ""),
    ("  ", ""),
])
def test_clean_hashtag(raw: str, expected: str) -> None:
    assert clean_hashtag(raw) == expected


def test_hashtags_dedupe_case_insensitive_and_extras_last(cfg: Config) -> None:
    script = make_script(hashtags=["Ocean", "#ocean", "OCEAN", "Deep Sea", "日本", "Shorts"])
    cfg.metadata.extra_hashtags = ["shorts", "#Facts"]
    assert build_hashtags(cfg, script) == ["ocean", "deepsea", "shorts", "facts"]


def test_hashtag_cap_keeps_configured_extras(cfg: Config) -> None:
    script = make_script(hashtags=[f"tag{i}" for i in range(20)])
    cfg.metadata.extra_hashtags = ["shorts"]
    cfg.metadata.max_hashtags = 4
    tags = build_hashtags(cfg, script)
    assert tags == ["tag0", "tag1", "tag2", "shorts"]


def test_hashtag_cap_zero(cfg: Config) -> None:
    cfg.metadata.max_hashtags = 0
    assert build_hashtags(cfg, make_script()) == []


# --------------------------------------------------------------------------- YouTube


def test_youtube_fields_from_config(cfg: Config) -> None:
    cfg.upload.youtube.category_id = "24"
    cfg.upload.youtube.privacy = "Unlisted"
    cfg.upload.youtube.made_for_kids = False
    cfg.upload.youtube.contains_synthetic_media = True
    yt = build_metadata(cfg, make_script(language="de"), [])["youtube"]
    assert yt["category_id"] == "24"
    assert yt["privacy"] == "unlisted"
    assert yt["made_for_kids"] is False
    assert yt["contains_synthetic_media"] is True
    assert yt["default_language"] == "de"


def test_invalid_privacy_falls_back_to_private(cfg: Config) -> None:
    cfg.upload.youtube.privacy = "everyone"
    cfg.upload.tiktok.privacy_level = "friends"
    meta = build_metadata(cfg, make_script(), [])
    assert meta["youtube"]["privacy"] == "private"
    assert meta["tiktok"]["privacy_level"] == "SELF_ONLY"


def test_youtube_title_strips_angle_brackets_and_trims(cfg: Config) -> None:
    long_title = "Why <b>octopuses</b> are > smart " + "really " * 30
    title = build_metadata(cfg, make_script(title=long_title), [])["youtube"]["title"]
    assert len(title) <= YT_TITLE_MAX
    assert "<" not in title and ">" not in title
    assert title.startswith("Why boctopuses/b are smart")
    assert not title.endswith(" ")


def test_youtube_title_falls_back_to_topic(cfg: Config) -> None:
    assert build_metadata(cfg, make_script(title="  "), [])["youtube"]["title"] == "deep sea creatures"


def test_youtube_description_layout(cfg: Config) -> None:
    clips = [clip("Video by Jane Doe on Pexels"), clip("Video by Bob on Pixabay", "pixabay")]
    desc = build_metadata(cfg, make_script(description="Strange <animals> from the deep."), clips)["youtube"]["description"]
    assert desc.startswith("Strange animals from the deep.\n\n#ocean #deepsea #facts #shorts")
    assert AI_DISCLOSURE in desc
    assert desc.endswith("Credits:\nVideo by Jane Doe on Pexels\nVideo by Bob on Pixabay")
    assert "<" not in desc and ">" not in desc


def test_youtube_description_always_has_shorts_and_optional_disclosure(cfg: Config) -> None:
    cfg.metadata.extra_hashtags = []
    cfg.metadata.ai_disclosure = False
    meta = build_metadata(cfg, make_script(), [])
    assert "#shorts" in meta["youtube"]["description"]
    assert "shorts" not in meta["hashtags"]
    assert AI_DISCLOSURE not in meta["youtube"]["description"]
    assert "Credits" not in meta["youtube"]["description"]


def test_youtube_description_limit_drops_credits_first(cfg: Config) -> None:
    clips = [clip(f"Video by Creator Number {i} With A Long Name on Pexels") for i in range(300)]
    desc = build_metadata(cfg, make_script(), clips)["youtube"]["description"]
    assert len(desc) <= YT_DESCRIPTION_MAX
    assert "Video by Creator Number 0 " in desc
    assert "more" in desc.splitlines()[-1]
    assert AI_DISCLOSURE in desc


def test_youtube_description_limit_with_huge_text(cfg: Config) -> None:
    desc = build_metadata(cfg, make_script(description="word " * 3000), [clip("Video by A on Pexels")])["youtube"]["description"]
    assert len(desc) <= YT_DESCRIPTION_MAX
    assert "#shorts" in desc


def test_youtube_tags_budget(cfg: Config) -> None:
    cfg.metadata.max_hashtags = 200
    script = make_script(hashtags=[f"averyveryverylonghashtagnumber{i}" for i in range(100)])
    tags = build_metadata(cfg, script, [])["youtube"]["tags"]
    assert youtube_tag_length(tags) <= YT_TAGS_MAX
    assert tags[0] == "deep sea creatures"
    assert all("#" not in t and "," not in t for t in tags)
    assert len({t.lower() for t in tags}) == len(tags)


def test_youtube_tag_length_counts_quotes_and_commas() -> None:
    assert youtube_tag_length([]) == 0
    assert youtube_tag_length(["abc"]) == 3
    assert youtube_tag_length(["abc", "de f"]) == 3 + 1 + 4 + 2


def test_youtube_tags_include_shorts_and_clean_topic(cfg: Config) -> None:
    tags = build_metadata(cfg, make_script(topic='Space, "black holes" <2024>'), [])["youtube"]["tags"]
    assert tags[0] == "space black holes 2024"
    assert "shorts" in tags


# --------------------------------------------------------------------------- TikTok


def test_tiktok_caption(cfg: Config) -> None:
    cfg.upload.tiktok.is_aigc = True
    tt = build_metadata(cfg, make_script(), [])["tiktok"]
    assert tt["caption"] == "5 Deep Sea Creatures That Look Fake #ocean #deepsea #facts"
    assert "#shorts" not in tt["caption"]
    assert tt["is_aigc"] is True
    assert tt["privacy_level"] == "SELF_ONLY"


def test_tiktok_caption_limit(cfg: Config) -> None:
    cfg.metadata.max_hashtags = 500
    script = make_script(title="T" * 2100, hashtags=[f"tag{i}" for i in range(400)])
    caption = build_metadata(cfg, script, [])["tiktok"]["caption"]
    assert len(caption) <= TIKTOK_CAPTION_MAX
    assert caption.startswith("T" * 2100)
    huge = build_metadata(cfg, make_script(title="word " * 1000), [])["tiktok"]["caption"]
    assert len(huge) <= TIKTOK_CAPTION_MAX


# --------------------------------------------------------------------------- credits + files


def test_credits_unique_and_non_empty(cfg: Config) -> None:
    clips = [
        clip("Video by Jane Doe on Pexels"),
        clip(""),
        clip("Video by Jane  Doe on Pexels"),
        clip("", "generated"),
        clip("Video by Bob on Pixabay", "pixabay"),
    ]
    assert build_metadata(cfg, make_script(), clips)["credits"] == [
        "Video by Jane Doe on Pexels",
        "Video by Bob on Pixabay",
    ]
    assert build_metadata(cfg, make_script(), None)["credits"] == []


def test_write_metadata_files(cfg: Config, tmp_path: Path) -> None:
    script = make_script(title="Café secrets — ünïcode ✨", description="Déjà vu.")
    meta = build_metadata(cfg, script, [clip("Video by Zoë on Pexels")])
    out = tmp_path / "job"
    path = write_metadata(out, meta)
    assert path == out / "metadata.json"
    raw = path.read_text(encoding="utf-8")
    assert "Café secrets" in raw and "\\u" not in raw  # ensure_ascii=False
    assert json.loads(raw) == meta
    assert raw.startswith('{\n  "')  # indent=2

    yt = (out / "youtube.txt").read_text(encoding="utf-8")
    for section in ("TITLE", "DESCRIPTION", "TAGS", "SETTINGS"):
        assert section in yt
    assert meta["youtube"]["title"] in yt
    assert meta["youtube"]["description"] in yt
    assert ", ".join(meta["youtube"]["tags"]) in yt
    assert "Altered or synthetic content: yes" in yt

    assert (out / "tiktok.txt").read_text(encoding="utf-8").strip() == meta["tiktok"]["caption"]
    assert (out / "credits.txt").read_text(encoding="utf-8") == "Video by Zoë on Pexels\n"


def test_write_metadata_without_credits(cfg: Config, tmp_path: Path) -> None:
    write_metadata(tmp_path, build_metadata(cfg, make_script(), []))
    assert (tmp_path / "credits.txt").read_text(encoding="utf-8") == ""


def test_trim_words_word_boundary() -> None:
    assert metadata.trim_words("hello wonderful world", 17) == "hello wonderful"
    assert metadata.trim_words("abcdefghijkl", 5) == "abcde"
    assert metadata.trim_words("short", 50) == "short"
