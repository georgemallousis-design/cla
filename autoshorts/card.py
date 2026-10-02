"""Story card for the reddit format: a white, post-style card drawn with ASS vector shapes.

Shows the channel avatar and name, an "original story" note and the post title while the
title is read aloud. Everything is drawn by libass (no image library needed), so the card
is burned in together with the captions. It deliberately uses no Reddit logo or branding.
"""
from __future__ import annotations

from .captions import _dialogue, escape_ass, text_width
from .config import Config
from .utils import hex_to_ass

CARD_WIDTH = 0.86  # fraction of the frame width
CARD_TOP = 0.27  # fraction of the frame height
MAX_TITLE_LINES = 5
CARD_FADE = r"\fad(120,200)"
NOTE = "Original story"


def rounded_rect(w: float, h: float, r: float) -> str:
    """ASS drawing commands for a w x h rectangle with corner radius r (origin top-left)."""
    r = max(0.0, min(r, w / 2, h / 2))
    n = lambda v: f"{v:.0f}"  # noqa: E731 - tiny local formatter
    return (
        f"m {n(r)} 0 l {n(w - r)} 0 b {n(w)} 0 {n(w)} 0 {n(w)} {n(r)} "
        f"l {n(w)} {n(h - r)} b {n(w)} {n(h)} {n(w)} {n(h)} {n(w - r)} {n(h)} "
        f"l {n(r)} {n(h)} b 0 {n(h)} 0 {n(h)} 0 {n(h - r)} "
        f"l 0 {n(r)} b 0 0 0 0 {n(r)} 0"
    )


def circle(d: float) -> str:
    """ASS drawing of a circle with diameter d (origin top-left of its bounding box)."""
    k = 0.5523 * d / 2  # cubic bezier circle constant
    r = d / 2
    n = lambda v: f"{v:.0f}"  # noqa: E731
    return (
        f"m {n(r)} 0 b {n(r + k)} 0 {n(d)} {n(r - k)} {n(d)} {n(r)} "
        f"b {n(d)} {n(r + k)} {n(r + k)} {n(d)} {n(r)} {n(d)} "
        f"b {n(r - k)} {n(d)} 0 {n(r + k)} 0 {n(r)} "
        f"b 0 {n(r - k)} {n(r - k)} 0 {n(r)} 0"
    )


def wrap_title(title: str, font_size: float, max_width: float, max_lines: int = MAX_TITLE_LINES) -> tuple[list[str], int]:
    """Greedy-wrap ``title``; shrink the font until it fits in ``max_lines`` lines."""
    words = title.split()
    size = font_size
    while True:
        lines: list[str] = []
        for word in words:
            if lines and text_width(f"{lines[-1]} {word}", size) <= max_width:
                lines[-1] = f"{lines[-1]} {word}"
            else:
                lines.append(word)
        too_wide = any(text_width(line, size) > max_width for line in lines)
        if (len(lines) <= max_lines and not too_wide) or size <= font_size * 0.5:
            return lines, round(size)
        size *= 0.92


def card_events(cfg: Config, font: str, width: int, height: int, title: str, start: float, end: float) -> list[str]:
    """Dialogue lines drawing the card from ``start`` to ``end`` seconds."""
    title = " ".join(title.split())
    if not title or end <= start:
        return []
    s = min(width / 1080, height / 1920)
    card_w = width * CARD_WIDTH
    x0 = (width - card_w) / 2
    y0 = height * CARD_TOP
    pad = 44 * s
    avatar = 92 * s
    name_fs = round(40 * s)
    note_fs = round(30 * s)
    lines, title_fs = wrap_title(title, 54 * s, card_w - 2 * pad)
    line_h = title_fs * 1.28
    title_y = y0 + pad + avatar + 30 * s
    card_h = (title_y - y0) + len(lines) * line_h + pad
    a, b = round(start * 100), round(end * 100)

    channel = " ".join((cfg.channel.name or "Story Time").split())
    accent = hex_to_ass(cfg.channel.avatar_color or "#FF4500")[2:]  # "&HAABBGGRR" -> "AABBGGRR"
    accent = f"&H{accent[2:]}&"
    dark, grey, white = "&H1B1A1A&", "&H7E7C78&", "&HFFFFFF&"

    def shape(x: float, y: float, drawing: str, colour: str, extra: str = "") -> str:
        return f"{{\\an7\\pos({x:.0f},{y:.0f})\\bord0\\shad0\\1c{colour}{extra}{CARD_FADE}\\p1}}{drawing}{{\\p0}}"

    def text(x: float, y: float, value: str, size: int, colour: str, an: int = 7, bold: bool = True) -> str:
        return (f"{{\\an{an}\\pos({x:.0f},{y:.0f})\\fs{size}\\b{int(bold)}\\bord0\\shad0"
                f"\\1c{colour}{CARD_FADE}}}{escape_ass(value)}")

    events = [
        # soft drop shadow, then the card itself
        _dialogue(3, a, b, "Card", shape(x0 + 6 * s, y0 + 10 * s, rounded_rect(card_w, card_h, 34 * s),
                                         "&H000000&", "\\1a&H90&\\blur12")),
        _dialogue(4, a, b, "Card", shape(x0, y0, rounded_rect(card_w, card_h, 34 * s), white)),
        _dialogue(5, a, b, "Card", shape(x0 + pad, y0 + pad, circle(avatar), accent)),
        _dialogue(6, a, b, "Card", text(x0 + pad + avatar / 2, y0 + pad + avatar / 2, channel[:1].upper(),
                                        round(48 * s), white, an=5)),
        _dialogue(6, a, b, "Card", text(x0 + pad + avatar + 24 * s, y0 + pad + 4 * s, channel, name_fs, dark)),
        _dialogue(6, a, b, "Card", text(x0 + pad + avatar + 24 * s, y0 + pad + 4 * s + name_fs * 1.25, NOTE,
                                        note_fs, grey, bold=False)),
    ]
    for i, line in enumerate(lines):
        events.append(_dialogue(6, a, b, "Card", text(x0 + pad, title_y + i * line_h, line, title_fs, dark)))
    return events
