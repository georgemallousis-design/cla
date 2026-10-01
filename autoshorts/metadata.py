"""Titles, descriptions, tags and hashtags for each platform, plus copy-paste text files.

``build_metadata(cfg, script, clips)`` returns::

    {
      "hashtags": [...],                     # cleaned, shared by both platforms
      "youtube": {title, description, tags, category_id, privacy, made_for_kids,
                  contains_synthetic_media, default_language},
      "tiktok":  {caption, privacy_level, is_aigc},
      "credits": ["Video by Jane Doe on Pexels", ...],
    }

Platform limits enforced here (so uploads are not rejected):

* YouTube title <= 100 chars, description <= 5000 chars, neither may contain '<' or '>'.
* YouTube tags total <= 500 chars, counted the way YouTube does: commas between tags
  count, and a tag containing a space counts two extra characters for its quotes.
* TikTok caption <= 2200 chars (TikTok counts UTF-16 units; ``len`` is close enough for
  the text we generate, and we stay well under the limit in practice).

Pexels and Pixabay do not require attribution, but crediting creators is good practice,
so their attributions are listed in the YouTube description and in credits.txt.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from .config import Config
from .models import ClipAsset, VideoScript
from .utils import ensure_dir, log

YT_TITLE_MAX = 100
YT_DESCRIPTION_MAX = 5000
YT_TAGS_MAX = 500
YT_TAG_MAX = 100  # single tag; keeps one long topic from eating the whole budget
TIKTOK_CAPTION_MAX = 2200

YT_PRIVACY = ("private", "unlisted", "public")
TIKTOK_PRIVACY = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")

SHORTS_TAG = "shorts"
# YouTube-only hashtags that look odd on TikTok.
YOUTUBE_ONLY_TAGS = {"shorts", "youtubeshorts", "ytshorts", "short"}

AI_DISCLOSURE = "Narration voiced with text-to-speech; script assisted by AI."


# --------------------------------------------------------------------------- text helpers


def _collapse(text: str) -> str:
    return " ".join(str(text or "").split())


def strip_angle_brackets(text: str) -> str:
    """YouTube rejects '<' and '>' in titles and descriptions."""
    return str(text or "").replace("<", "").replace(">", "")


def trim_words(text: str, limit: int) -> str:
    """Cut ``text`` to at most ``limit`` chars, at a word boundary when possible."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-")


def clean_hashtag(raw: Any) -> str:
    """'#Fun Facts!' -> 'funfacts'; accents folded to ASCII, anything outside [a-z0-9_] dropped."""
    text = unicodedata.normalize("NFKD", str(raw or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9_]", "", text.lower())


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def build_hashtags(cfg: Config, script: VideoScript) -> list[str]:
    """script.hashtags + cfg.metadata.extra_hashtags, cleaned, de-duplicated and capped.

    When the cap bites, the configured extra hashtags (channel-wide, e.g. "shorts") are
    kept and the script's own hashtags fill the remaining slots, in their original order.
    """
    cap = max(0, int(cfg.metadata.max_hashtags))
    extra = _dedupe(clean_hashtag(t) for t in cfg.metadata.extra_hashtags)[:cap]
    own = [t for t in _dedupe(clean_hashtag(t) for t in script.hashtags) if t not in extra]
    return [*own[: cap - len(extra)], *extra]


def unique_credits(clips: Iterable[ClipAsset] | None) -> list[str]:
    """Distinct, non-empty clip attributions in first-seen order."""
    return _dedupe(_collapse(c.attribution) for c in (clips or []) if _collapse(c.attribution))


# --------------------------------------------------------------------------- YouTube


def youtube_title(script: VideoScript) -> str:
    title = _collapse(strip_angle_brackets(script.title)) or _collapse(strip_angle_brackets(script.topic))
    return trim_words(title or "Untitled short", YT_TITLE_MAX)


def youtube_description(cfg: Config, script: VideoScript, hashtags: list[str], credits: list[str]) -> str:
    tags = hashtags if SHORTS_TAG in hashtags else [*hashtags, SHORTS_TAG]
    tail: list[str] = [" ".join(f"#{t}" for t in tags)]
    if cfg.metadata.ai_disclosure:
        tail.append(AI_DISCLOSURE)
    body = strip_angle_brackets(script.description).strip()

    def assemble(credit_lines: list[str]) -> str:
        parts = [body, *tail] if body else list(tail)
        if credit_lines:
            parts.append("Credits:\n" + "\n".join(credit_lines))
        return "\n\n".join(parts)

    credit_lines = [strip_angle_brackets(c) for c in credits]
    shown = len(credit_lines)
    text = assemble(credit_lines)
    while len(text) > YT_DESCRIPTION_MAX and shown > 0:
        # Too long: drop credits from the end and say how many were left out.
        shown -= 1
        more = [f"...and {len(credit_lines) - shown} more"] if shown else []
        text = assemble([*credit_lines[:shown], *more])
    if len(text) > YT_DESCRIPTION_MAX:  # the script's own description is huge
        body = trim_words(body, max(0, len(body) - (len(text) - YT_DESCRIPTION_MAX)))
        text = assemble([])[:YT_DESCRIPTION_MAX]
    return text


def youtube_tag_length(tags: list[str]) -> int:
    """Length as YouTube counts it: commas between tags, quotes around tags with spaces."""
    if not tags:
        return 0
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + len(tags) - 1


def youtube_tags(script: VideoScript, hashtags: list[str]) -> list[str]:
    """Plain-word tags (topic phrase + hashtags), within YouTube's 500-char budget."""
    topic = _collapse(re.sub(r"[<>,\"]", " ", script.topic))
    candidates = [topic.lower(), *hashtags, SHORTS_TAG]
    tags: list[str] = []
    for tag in _dedupe(t for t in candidates if t):
        tag = trim_words(tag, YT_TAG_MAX)
        if tag and youtube_tag_length([*tags, tag]) <= YT_TAGS_MAX:
            tags.append(tag)
    return tags


def youtube_privacy(value: str) -> str:
    privacy = str(value or "").strip().lower()
    if privacy not in YT_PRIVACY:
        log.warning("unknown YouTube privacy %r, using 'private'", value)
        return "private"
    return privacy


# --------------------------------------------------------------------------- TikTok


def tiktok_caption(script: VideoScript, hashtags: list[str]) -> str:
    """Title followed by hashtags, within TikTok's 2200-char caption limit."""
    title = _collapse(script.title) or _collapse(script.topic)
    tags = [f"#{t}" for t in hashtags if t not in YOUTUBE_ONLY_TAGS]
    while tags and len(" ".join([title, *tags])) > TIKTOK_CAPTION_MAX:
        tags.pop()
    caption = " ".join([title, *tags]).strip()
    return trim_words(caption, TIKTOK_CAPTION_MAX)


def tiktok_privacy(value: str) -> str:
    privacy = str(value or "").strip().upper()
    if privacy not in TIKTOK_PRIVACY:
        log.warning("unknown TikTok privacy_level %r, using SELF_ONLY", value)
        return "SELF_ONLY"
    return privacy


# --------------------------------------------------------------------------- public API


def build_metadata(cfg: Config, script: VideoScript, clips: list[ClipAsset] | None) -> dict[str, Any]:
    """Platform-ready metadata for one video (see module docstring for the shape)."""
    hashtags = build_hashtags(cfg, script)
    credits = unique_credits(clips)
    yt = cfg.upload.youtube
    tt = cfg.upload.tiktok
    return {
        "hashtags": hashtags,
        "youtube": {
            "title": youtube_title(script),
            "description": youtube_description(cfg, script, hashtags, credits),
            "tags": youtube_tags(script, hashtags),
            "category_id": str(yt.category_id),
            "privacy": youtube_privacy(yt.privacy),
            "made_for_kids": bool(yt.made_for_kids),
            "contains_synthetic_media": bool(yt.contains_synthetic_media),
            "default_language": script.language or "en",
        },
        "tiktok": {
            "caption": tiktok_caption(script, hashtags),
            "privacy_level": tiktok_privacy(tt.privacy_level),
            "is_aigc": bool(tt.is_aigc),
        },
        "credits": credits,
    }


def _youtube_text(yt: dict[str, Any]) -> str:
    yes_no = {True: "yes", False: "no"}
    return "\n".join([
        "TITLE",
        "-----",
        yt.get("title", ""),
        "",
        "DESCRIPTION",
        "-----------",
        yt.get("description", ""),
        "",
        "TAGS (comma separated)",
        "----------------------",
        ", ".join(yt.get("tags", [])),
        "",
        "SETTINGS",
        "--------",
        f"Category ID: {yt.get('category_id', '')}",
        f"Visibility: {yt.get('privacy', '')}",
        f"Made for kids: {yes_no[bool(yt.get('made_for_kids'))]}",
        f"Altered or synthetic content: {yes_no[bool(yt.get('contains_synthetic_media'))]}",
        "",
    ])


def write_metadata(folder: str | Path, meta: dict[str, Any]) -> Path:
    """Write metadata.json, youtube.txt, tiktok.txt and credits.txt into ``folder``.

    Returns the path of metadata.json.
    """
    folder = ensure_dir(folder)
    json_path = folder / "metadata.json"
    json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (folder / "youtube.txt").write_text(_youtube_text(meta.get("youtube", {})), encoding="utf-8")
    (folder / "tiktok.txt").write_text(meta.get("tiktok", {}).get("caption", "") + "\n", encoding="utf-8")
    credits = meta.get("credits", [])
    (folder / "credits.txt").write_text("".join(f"{c}\n" for c in credits), encoding="utf-8")
    return json_path
