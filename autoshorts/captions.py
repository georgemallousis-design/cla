"""Burned-in captions: Narration word timings -> styled ASS subtitle file (drawn by libass).

The look is the usual Shorts/TikTok one: a few big bold words at a time in the lower
middle of the frame, the word being spoken highlighted, a small pop-in for every new
caption, plus an optional title banner at the top for the first seconds.

Sizes in ``cfg.captions`` are for a 1080x1920 frame and are scaled to ``cfg.video``.
"""
from __future__ import annotations

import bisect
import math
import os
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .models import Narration, VideoScript, WordTiming
from .tts import clean_for_speech
from .tts.timing import ends_clause
from .utils import ensure_dir, hex_to_ass, log

BASE_WIDTH, BASE_HEIGHT = 1080, 1920
MAX_TEXT_WIDTH = 0.85  # captions never get wider than this fraction of the frame
TITLE_MAX_WIDTH = 0.80
TITLE_Y = 0.16
TITLE_SIZE = 0.6  # title font size relative to the caption font size
MAX_GAP = 0.6  # a caption stays up until the next one unless the pause is longer than this
HOLD = 0.3  # ... then it disappears this long after its last word
LONG_PAUSE = 0.6  # a silence this long inside a phrase starts a new caption
POP = r"\fscx80\fscy80\t(0,90,\fscx100\fscy100)"
FADE = r"\fad(150,250)"

FONT_CANDIDATES = {
    "win": ["Arial Black", "Arial"],
    "darwin": ["Arial Black", "Helvetica"],
    "linux": ["Arial Black", "DejaVu Sans", "Liberation Sans", "FreeSans"],
}
FONT_FALLBACK = {"win": "Arial", "darwin": "Helvetica", "linux": "DejaVu Sans"}
WINDOWS_FONT_FILES = {"Arial Black": ["ariblk.ttf"], "Arial": ["arialbd.ttf", "arial.ttf"]}
MAC_FONT_FILES = {"Arial Black": ["Arial Black.ttf"], "Helvetica": ["Helvetica.ttc"]}
MAC_FONT_DIRS = ["/System/Library/Fonts/Supplemental", "/System/Library/Fonts", "/Library/Fonts", "~/Library/Fonts"]
FONT_SUFFIXES = (".ttf", ".otf", ".ttc")


# --------------------------------------------------------------------------- fonts


def _platform() -> str:
    if sys.platform.startswith("win"):
        return "win"
    return "darwin" if sys.platform == "darwin" else "linux"


def read_font_names(path: str | Path) -> dict[int, str]:
    """Name-table strings of a .ttf/.otf/.ttc (first face): {name_id: text}.
    1 = family, 2 = subfamily, 4 = full name, 16 = typographic family. {} on error."""
    try:
        data = Path(path).read_bytes()
        base = struct.unpack_from(">I", data, 12)[0] if data[:4] == b"ttcf" else 0
        version, num_tables = struct.unpack_from(">IH", data, base)
        if version not in (0x00010000, 0x4F54544F, 0x74727565):  # TrueType, 'OTTO', 'true'
            return {}
        for i in range(num_tables):
            tag, _, table, _ = struct.unpack_from(">4sIII", data, base + 12 + 16 * i)
            if tag == b"name":
                break
        else:
            return {}
        _, count, strings = struct.unpack_from(">HHH", data, table)
        best: dict[int, tuple[int, str]] = {}
        for i in range(count):
            pid, eid, lang, nid, length, off = struct.unpack_from(">6H", data, table + 6 + 12 * i)
            if nid not in (1, 2, 4, 16):
                continue
            raw = data[table + strings + off : table + strings + off + length]
            if pid == 3 or pid == 0:
                text, rank = raw.decode("utf-16-be", "replace"), 0 if (pid == 3 and lang == 0x409) else 2
            elif pid == 1 and eid == 0:
                text, rank = raw.decode("mac_roman", "replace"), 1 if lang == 0 else 3
            else:
                continue
            text = text.strip("\x00 ").strip()
            if text and (nid not in best or rank < best[nid][0]):
                best[nid] = (rank, text)
        return {nid: text for nid, (_, text) in best.items()}
    except (OSError, struct.error, IndexError):
        return {}


def font_family(path: str | Path) -> str:
    """Family name libass matches a font file by (name ID 1), else the file stem."""
    names = read_font_names(path)
    return names.get(1) or names.get(16) or Path(path).stem


def _boldness(names: dict[int, str], path: Path) -> int:
    text = " ".join([names.get(2, ""), names.get(4, ""), names.get(1, ""), path.stem]).lower().replace("-", " ")
    score = 0
    if any(k in text for k in ("black", "heavy", "extrabold", "extra bold", "ultrabold", "ultra bold")):
        score = 3
    elif "semibold" in text or "demibold" in text or "semi bold" in text:
        score = 1
    elif "bold" in text:
        score = 2
    if "italic" in text or "oblique" in text:
        score -= 2
    return score


def _fonts_dir_family(folder: Path) -> str | None:
    """Boldest upright font in ``folder`` (ties: first by file name)."""
    if not folder.is_dir():
        return None
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in FONT_SUFFIXES and p.is_file())
    if not files:
        return None
    scored = [(_boldness(read_font_names(p), p), -i, p) for i, p in enumerate(files)]
    best = max(scored)[2]
    return font_family(best)


def _fc_families() -> set[str] | None:
    """Casefolded family names known to fontconfig, or None when fc-list is unavailable."""
    fc = shutil.which("fc-list")
    if not fc:
        return None
    try:
        proc = subprocess.run([fc, ":", "family"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    families: set[str] = set()
    for line in proc.stdout.splitlines():
        for name in line.split(","):
            name = name.replace("\\-", "-").strip()
            if name:
                families.add(name.casefold())
    return families


def _font_file_exists(family: str, plat: str) -> bool:
    if plat == "win":
        dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
        names = WINDOWS_FONT_FILES.get(family, [])
    elif plat == "darwin":
        dirs = [Path(d).expanduser() for d in MAC_FONT_DIRS]
        names = MAC_FONT_FILES.get(family, [])
    else:
        return False
    return any((d / n).is_file() for d in dirs for n in names)


def resolve_font(cfg: Config) -> str:
    """Font family for the captions. "auto": the boldest font in cfg.captions.fonts_dir,
    else a bold-looking system font (Arial Black / Arial on Windows, Arial Black /
    Helvetica on macOS, DejaVu Sans / Liberation Sans / FreeSans on Linux)."""
    name = (cfg.captions.font or "auto").strip()
    if name.lower() != "auto":
        return name
    custom = _fonts_dir_family(cfg.path(cfg.captions.fonts_dir))
    if custom:
        return custom
    plat = _platform()
    families = _fc_families() if plat != "win" else None
    for family in FONT_CANDIDATES[plat]:
        if families is not None and family.casefold() in families:
            return family
        if _font_file_exists(family, plat):
            return family
    log.debug("no preferred caption font found; using %s", FONT_FALLBACK[plat])
    return FONT_FALLBACK[plat]


# --------------------------------------------------------------------------- text helpers


def ass_time(seconds: float) -> str:
    """Seconds -> ASS timestamp H:MM:SS.cc."""
    cs = max(0, int(round(seconds * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def escape_ass(text: str) -> str:
    """Make arbitrary text safe inside a Dialogue line (libass)."""
    text = " ".join(text.split())  # no raw newlines/tabs
    text = text.replace("\\", "\\\u2060")  # a word joiner stops "\N", "\h"... from being commands
    return text.replace("{", "\\{").replace("}", "\\}")


def inline_color(hex_color: str) -> str:
    """'#RRGGBB' -> inline override colour '&HBBGGRR&'."""
    return f"&H{hex_to_ass(hex_color)[4:]}&"


def _char_width(ch: str) -> float:
    """Approximate advance of one character in em (bold sans)."""
    if ch == " ":
        return 0.3
    if ch in "iIl1.,;:!'|jft":
        return 0.38
    if ch in "MWmw@%":
        return 0.9
    if ch.isupper() or ch.isdigit():
        return 0.72
    return 0.6


def text_width(text: str, font_size: float) -> float:
    return sum(_char_width(c) for c in text) * font_size


def wrap_words(words: list[str], font_size: float, max_width: float, max_lines: int = 2) -> list[list[int]]:
    """Indices of ``words`` per line: one line if it fits, else the most balanced two
    lines, else (when max_lines > 2) a greedy wrap. Lines may still be too wide (one
    long word, or more text than max_lines allow): callers shrink the font then."""
    n = len(words)

    def width(idx) -> float:
        return text_width(" ".join(words[i] for i in idx), font_size)

    if n <= 1 or max_lines <= 1 or width(range(n)) <= max_width:
        return [list(range(n))]
    k = min(range(1, n), key=lambda k: max(width(range(k)), width(range(k, n))))
    two = [list(range(k)), list(range(k, n))]
    if max(width(line) for line in two) <= max_width or max_lines == 2:
        return two
    lines: list[list[int]] = [[]]
    for i in range(n):
        if lines[-1] and width(lines[-1] + [i]) > max_width:
            lines.append([])
        lines[-1].append(i)
    return lines if len(lines) <= max_lines else two


def fit_font_size(lines: list[str], font_size: int, max_width: float) -> int:
    """``font_size``, reduced so the widest line fits ``max_width``."""
    widest = max((text_width(line, font_size) for line in lines), default=0.0)
    if widest <= max_width:
        return font_size
    return max(8, math.floor(font_size * max_width / widest))


# --------------------------------------------------------------------------- grouping


@dataclass
class Caption:
    words: list[WordTiming]
    start: float
    end: float


def _segment_index(starts: list[float], t: float) -> int:
    return max(0, bisect.bisect_right(starts, t + 1e-3) - 1)


def _phrases(words: list[WordTiming], seg_starts: list[float]) -> list[list[WordTiming]]:
    """Runs of words that may share a caption: split at punctuation (sentence and
    clause ends), segment boundaries and long pauses."""
    phrases: list[list[WordTiming]] = []
    for w in words:
        if phrases:
            prev = phrases[-1][-1]
            if (
                not ends_clause(prev.word)
                and _segment_index(seg_starts, w.start) == _segment_index(seg_starts, prev.start)
                and w.start - prev.end <= LONG_PAUSE
            ):
                phrases[-1].append(w)
                continue
        phrases.append([w])
    return phrases


def _split_balanced(phrase: list[WordTiming], max_words: int, max_chars: int) -> list[list[WordTiming]]:
    """Fewest chunks with <= max_words words and <= max_chars characters each (a single
    over-long word gets a chunk of its own); among those, the most evenly sized."""
    texts = [w.word for w in phrase]
    n = len(texts)

    def chars(i: int, j: int) -> int:
        return len(" ".join(texts[i:j]))

    # best[j]: (chunks, sum of squared word counts, sum of squared lengths, start of last chunk)
    best: list[tuple[int, int, int, int] | None] = [None] * (n + 1)
    best[0] = (0, 0, 0, -1)
    for j in range(1, n + 1):
        for i in range(max(0, j - max_words), j):
            prev = best[i]
            if prev is None or (j - i > 1 and chars(i, j) > max_chars):
                continue
            cand = (prev[0] + 1, prev[1] + (j - i) ** 2, prev[2] + chars(i, j) ** 2, i)
            if best[j] is None or cand[:3] < best[j][:3]:  # type: ignore[index]
                best[j] = cand
    chunks: list[list[WordTiming]] = []
    j = n
    while j > 0:
        i = best[j][3]  # type: ignore[index]
        chunks.append(phrase[i:j])
        j = i
    return chunks[::-1]


def group_captions(
    words: list[WordTiming], seg_starts: list[float], max_words: int, max_chars: int, end_limit: float | None = None
) -> list[Caption]:
    """Group words into timed captions (see module docstring for the rules)."""
    max_words = max(1, int(max_words))
    max_chars = max(1, int(max_chars))
    groups = [g for p in _phrases(words, seg_starts) for g in _split_balanced(p, max_words, max_chars)]
    captions: list[Caption] = []
    for i, g in enumerate(groups):
        last_end = max(g[-1].end, g[-1].start)
        nxt = groups[i + 1][0].start if i + 1 < len(groups) else None
        if nxt is not None and nxt - last_end <= MAX_GAP:
            end = nxt
        else:
            end = last_end + HOLD
            if nxt is not None:
                end = min(end, nxt)
            elif end_limit is not None and end_limit > last_end:
                end = min(end, end_limit)
        captions.append(Caption(words=g, start=g[0].start, end=max(end, g[0].start)))
    return captions


# --------------------------------------------------------------------------- ASS output


@dataclass
class _Layout:
    width: int
    height: int
    scale: float
    font: str
    font_size: int
    title_size: int


def _layout(cfg: Config) -> _Layout:
    w, h = int(cfg.video.width), int(cfg.video.height)
    scale = min(w / BASE_WIDTH, h / BASE_HEIGHT)
    fs = max(8, round(cfg.captions.font_size * scale))
    return _Layout(w, h, scale, resolve_font(cfg), fs, max(6, round(fs * TITLE_SIZE)))


def _num(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _header(cfg: Config, lay: _Layout, title: str) -> list[str]:
    cc = cfg.captions
    margin = round(lay.width * (1 - MAX_TEXT_WIDTH) / 2)
    fields = (
        "Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding"
    )
    caption_style = ",".join([
        "Caption", lay.font, str(lay.font_size), hex_to_ass(cc.primary_color), hex_to_ass(cc.highlight_color),
        hex_to_ass(cc.outline_color), hex_to_ass("#000000", 0x80), "-1", "0", "0", "0", "100", "100", "0", "0",
        "1", _num(cc.outline * lay.scale), _num(cc.shadow * lay.scale), "5", str(margin), str(margin), "0", "1",
    ])
    title_style = ",".join([
        "Title", lay.font, str(lay.title_size), hex_to_ass("#FFFFFF"), hex_to_ass("#FFFFFF"),
        hex_to_ass("#000000", 0x60), hex_to_ass("#000000", 0xFF), "-1", "0", "0", "0", "100", "100", "0", "0",
        "3", _num(max(1.0, 16 * lay.scale)), "0", "5", str(margin), str(margin), "0", "1",
    ])
    return [
        "[Script Info]",
        "; Generated by autoshorts",
        f"Title: {' '.join(title.split()) or 'autoshorts'}",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {lay.width}",
        f"PlayResY: {lay.height}",
        "",
        "[V4+ Styles]",
        f"Format: {fields}",
        f"Style: {caption_style}",
        f"Style: {title_style}",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]


def _dialogue(layer: int, start_cs: int, end_cs: int, style: str, text: str) -> str:
    return f"Dialogue: {layer},{ass_time(start_cs / 100)},{ass_time(end_cs / 100)},{style},,0,0,0,,{text}"


def _display_word(word: str, upper: bool) -> str:
    text = clean_for_speech(word)
    return text.upper() if upper else text


def _caption_events(cfg: Config, lay: _Layout, cap: Caption) -> list[str]:
    cc = cfg.captions
    texts = [_display_word(w.word, cc.uppercase) for w in cap.words]
    keep = [i for i, t in enumerate(texts) if t]
    if not keep:
        return []
    texts = [texts[i] for i in keep]
    words = [cap.words[i] for i in keep]
    max_w = lay.width * MAX_TEXT_WIDTH
    lines = wrap_words(texts, lay.font_size, max_w)
    size = fit_font_size([" ".join(texts[i] for i in line) for line in lines], lay.font_size, max_w)
    font_tag = f"\\fs{size}" if size != lay.font_size else ""  # too long even on two lines: shrink
    pos = f"\\pos({round(lay.width / 2)},{round(cc.position * lay.height)})"
    hl, base = inline_color(cc.highlight_color), inline_color(cc.primary_color)

    # one event per word: [word start, next word start), the first from the caption start
    bounds = [round(cap.start * 100)] + [round(w.start * 100) for w in words[1:]] + [round(cap.end * 100)]
    for k in range(1, len(bounds)):
        bounds[k] = max(bounds[k], bounds[k - 1])
    events: list[str] = []
    for k in range(len(words)):
        if bounds[k + 1] <= bounds[k]:
            continue
        anim = POP if cc.pop and not events else ""
        text = f"{{{pos}{font_tag}{anim}}}" + _caption_text(texts, lines, k, hl, base)
        events.append(_dialogue(1, bounds[k], bounds[k + 1], "Caption", text))
    return events


def _caption_text(texts: list[str], lines: list[list[int]], active: int, hl: str, base: str) -> str:
    """Caption lines joined by \\N, the ``active`` word coloured ``hl``."""
    rendered = []
    for line in lines:
        parts = [escape_ass(texts[i]) for i in line]
        if active in line:
            j = line.index(active)
            parts[j] = f"{{\\c{hl}}}{parts[j]}{{\\c{base}}}"
        rendered.append(" ".join(parts))
    return "\\N".join(rendered)


def _title_event(cfg: Config, lay: _Layout, title: str, duration: float) -> str | None:
    title = clean_for_speech(title)
    seconds = min(cfg.captions.title_seconds, duration) if duration > 0 else cfg.captions.title_seconds
    if not title or seconds <= 0:
        return None
    words = title.split()
    max_w = lay.width * TITLE_MAX_WIDTH
    lines = [" ".join(words[i] for i in line) for line in wrap_words(words, lay.title_size, max_w, max_lines=3)]
    size = fit_font_size(lines, lay.title_size, max_w)
    tags = f"\\pos({round(lay.width / 2)},{round(TITLE_Y * lay.height)})"
    if size != lay.title_size:
        tags += f"\\fs{size}"
    text = "\\N".join(escape_ass(line) for line in lines)
    return _dialogue(2, 0, round(seconds * 100), "Title", f"{{{tags}{FADE}}}{text}")


def build_ass(cfg: Config, narration: Narration, script: VideoScript, out_path: Path) -> Path:
    """Write the captions (+ title banner) for ``narration`` to ``out_path`` (.ass)."""
    out_path = Path(out_path)
    ensure_dir(out_path.parent)
    lay = _layout(cfg)
    cc = cfg.captions
    seg_starts = [s.start for s in narration.segments]
    captions = group_captions(
        narration.words, seg_starts, cc.words_per_caption, cc.max_chars, end_limit=narration.duration or None
    )
    lines = _header(cfg, lay, script.title)
    if cc.show_title:
        title = _title_event(cfg, lay, script.title, narration.duration)
        if title:
            lines.append(title)
    for cap in captions:
        lines.extend(_caption_events(cfg, lay, cap))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.debug("captions: %d captions, font '%s' -> %s", len(captions), lay.font, out_path)
    return out_path
