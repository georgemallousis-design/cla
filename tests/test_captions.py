"""Tests for autoshorts.captions: grouping, ASS output, font resolution, libass rendering."""
from __future__ import annotations

import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from autoshorts import captions
from autoshorts.captions import (
    Caption,
    ass_time,
    build_ass,
    escape_ass,
    font_family,
    group_captions,
    inline_color,
    read_font_names,
    resolve_font,
    wrap_words,
)
from autoshorts.config import Config
from autoshorts.models import Narration, Segment, TimedSegment, VideoScript, WordTiming

HIGHLIGHT = r"{\c&H35E1FF&}"


# --------------------------------------------------------------------------- helpers


def words_from(spec: str, start: float = 0.0, step: float = 0.3, gap: float = 0.0) -> list[WordTiming]:
    """Back-to-back WordTimings for the words in ``spec``."""
    out, t = [], start
    for w in spec.split():
        out.append(WordTiming(w, round(t, 3), round(t + step, 3)))
        t += step + gap
    return out


def make_narration(*segment_texts: str, step: float = 0.3, seg_gap: float = 0.15) -> tuple[Narration, VideoScript]:
    words, timed, t = [], [], 0.0
    for text in segment_texts:
        seg_words = words_from(text, start=t, step=step)
        words.extend(seg_words)
        end = seg_words[-1].end
        timed.append(TimedSegment(Segment(text, "x"), round(t, 3), end))
        t = end + seg_gap
    duration = timed[-1].end + 0.2
    nar = Narration(audio_path=Path("narration.wav"), duration=duration, words=words, segments=timed, engine="test")
    script = VideoScript(topic="t", format="facts", title="Three {weird} facts \\N about octopuses",
                         segments=[s.segment for s in timed], description="d")
    return nar, script


def group(words: list[WordTiming], seg_starts=(0.0,), max_words=3, max_chars=20) -> list[list[str]]:
    return [[w.word for w in c.words] for c in group_captions(words, list(seg_starts), max_words, max_chars)]


def events(ass: str, style: str) -> list[tuple[str, str, str]]:
    pat = re.compile(rf"^Dialogue: \d+,([^,]+),([^,]+),{style},,0,0,0,,(.*)$", re.M)
    return pat.findall(ass)


def to_seconds(ts: str) -> float:
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def make_font(names: dict[int, str], ttc: bool = False) -> bytes:
    """A minimal sfnt with only a 'name' table (Windows/Unicode English records)."""
    base = 16 if ttc else 0
    records, strings = [], b""
    for nid, text in names.items():
        raw = text.encode("utf-16-be")
        records.append(struct.pack(">6H", 3, 1, 0x409, nid, len(raw), len(strings)))
        strings += raw
    # a Mac Roman record that must lose against the Windows English one
    mac = b"Wrong Name"
    records.append(struct.pack(">6H", 1, 0, 5, 1, len(mac), len(strings)))
    strings += mac
    name_table = struct.pack(">HHH", 0, len(records), 6 + 12 * len(records)) + b"".join(records) + strings
    table_offset = base + 12 + 16
    font = struct.pack(">IHHHH", 0x00010000, 1, 16, 0, 0)
    font += struct.pack(">4sIII", b"name", 0, table_offset, len(name_table)) + name_table
    if ttc:
        font = b"ttcf" + struct.pack(">HHII", 1, 0, 1, 16) + font
    return font


# --------------------------------------------------------------------------- formatting


def test_ass_time_format():
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(1.234) == "0:00:01.23"
    assert ass_time(59.999) == "0:01:00.00"
    assert ass_time(3661.5) == "1:01:01.50"
    assert ass_time(-1) == "0:00:00.00"


def test_escape_ass_and_inline_color():
    assert escape_ass("{bold}") == r"\{bold\}"
    escaped = escape_ass("a\\Nb")
    assert "\\N" not in escaped and escaped.startswith("a\\")
    assert escape_ass("two\nlines") == "two lines"
    assert inline_color("#FFE135") == "&H35E1FF&"
    assert inline_color("#000000") == "&H000000&"


def test_wrap_words_balances_lines():
    assert wrap_words(["SHORT", "TEXT"], 88, 918) == [[0, 1]]
    lines = wrap_words(["THEY", "ARE", "INCREDIBLY", "SMART"], 88, 918)
    assert len(lines) == 2 and lines[0] + lines[1] == [0, 1, 2, 3]
    assert len(wrap_words(["W" * 30], 88, 918)) == 1  # a single word is never split


# --------------------------------------------------------------------------- grouping


def test_grouping_respects_word_and_character_limits():
    ws = words_from("one two three four five six seven")
    assert group(ws, max_words=3) == [["one", "two", "three"], ["four", "five"], ["six", "seven"]]
    ws = words_from("supercalifragilistic is long")
    assert group(ws, max_chars=20) == [["supercalifragilistic"], ["is", "long"]]
    for g in group(words_from("aaaaaa bbbbbb cccccc dddddd eeeeee"), max_words=5, max_chars=13):
        assert len(" ".join(g)) <= 13


def test_grouping_balances_instead_of_leaving_orphans():
    assert group(words_from("octopuses have three hearts")) == [["octopuses", "have"], ["three", "hearts"]]


def test_grouping_never_spans_sentence_end_or_segment():
    ws = words_from("It works. Really well! Does it? Yes")
    assert group(ws) == [["It", "works."], ["Really", "well!"], ["Does", "it?"], ["Yes"]]
    ws = words_from("one two three four")
    assert group(ws, seg_starts=(0.0, ws[2].start)) == [["one", "two"], ["three", "four"]]
    assert group(words_from("first, second")) == [["first,"], ["second"]]


def test_grouping_splits_on_long_pause():
    ws = words_from("one two", step=0.3) + words_from("three", start=2.0)
    assert group(ws) == [["one", "two"], ["three"]]


def test_caption_end_times():
    ws = words_from("a b", step=0.3) + words_from("c d", start=0.8) + words_from("e", start=3.0)
    caps = group_captions(ws, [0.0, 0.7, 2.9], 3, 20, end_limit=3.5)
    assert [(c.start, c.end) for c in caps] == [(0.0, 0.8), (0.8, pytest.approx(1.7)), (3.0, 3.5)]
    # gap <= 0.6 s: stays up until the next caption; gap > 0.6 s: last word end + 0.3 s
    assert group_captions(ws, [0.0, 0.7, 2.9], 3, 20, end_limit=3.0)[-1].end == pytest.approx(3.6)
    # without a limit the last caption lingers HOLD seconds
    last = group_captions(words_from("x"), [0.0], 3, 20)[0]
    assert last.end == pytest.approx(0.6)


# --------------------------------------------------------------------------- ASS file


def test_build_ass_structure(tmp_path):
    cfg = Config()
    cfg.captions.font = "Test Sans"
    nar, script = make_narration("Octopuses have three hearts.", "Each arm can taste!", step=0.5)
    out = build_ass(cfg, nar, script, tmp_path / "sub" / "captions.ass")
    ass = out.read_text(encoding="utf-8")
    assert ass.startswith("[Script Info]")
    for line in ("PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "[V4+ Styles]", "[Events]"):
        assert line in ass
    style = re.search(r"^Style: Caption,(.*)$", ass, re.M).group(1).split(",")
    assert style[0] == "Test Sans" and style[1] == "88"
    assert style[2] == "&H00FFFFFF" and style[3] == "&H0035E1FF" and style[4] == "&H00000000"
    assert style[14] == "1" and style[15] == "6" and style[16] == "3" and style[17] == "5"
    title_style = re.search(r"^Style: Title,(.*)$", ass, re.M).group(1).split(",")
    assert title_style[14] == "3"  # opaque box
    assert title_style[4] == "&H60000000"  # box colour: semi-transparent black

    caps = events(ass, "Caption")
    assert len(caps) == len(nar.words)  # one event per spoken word
    for _, _, text in caps:
        assert text.count(HIGHLIGHT) == 1
        assert text.startswith(r"{\pos(540,1306)")
        plain = re.sub(r"{[^}]*}", "", text)
        assert plain == plain.upper()
    assert "OCTOPUSES" in caps[0][2] and "Octopuses" not in ass.split("[Events]")[1]
    # pop animation only on each caption's first slice: captions here are 2+2 and 2+2 words
    pops = [r"\t(0,90,\fscx100\fscy100)" in t for _, _, t in caps]
    assert pops == [True, False, True, False, True, False, True, False]
    # slices are contiguous: each event ends where the next starts within a sentence
    for (s1, e1, _), (s2, _, _) in zip(caps, caps[1:]):
        assert to_seconds(e1) <= to_seconds(s2) + 1e-9
        assert to_seconds(s1) < to_seconds(e1)
    assert caps[0][1] == caps[1][0]

    titles = events(ass, "Title")
    assert len(titles) == 1
    start, end, text = titles[0]
    assert start == "0:00:00.00" and end == "0:00:03.00"
    assert r"\fad(150,250)" in text and r"\pos(540,307)" in text
    assert r"\{weird\}" in text and "\\N about" not in text  # escaped braces and backslash


def test_build_ass_options_and_scaling(tmp_path):
    cfg = Config()
    cfg.captions.font = "X"
    cfg.captions.uppercase = False
    cfg.captions.pop = False
    cfg.captions.show_title = False
    cfg.video.width, cfg.video.height = 540, 960
    nar, script = make_narration("Hello {there} friend.")
    ass = build_ass(cfg, nar, script, tmp_path / "c.ass").read_text(encoding="utf-8")
    assert "PlayResX: 540" in ass and "Style: Caption,X,44," in ass
    assert not events(ass, "Title")
    caps = events(ass, "Caption")
    assert all(r"\t(" not in t for _, _, t in caps)
    assert r"Hello" in caps[0][2] and r"\{there\}" in caps[0][2]
    assert r"\pos(270,653)" in caps[0][2]


def test_build_ass_wraps_and_shrinks_long_text(tmp_path):
    cfg = Config()
    cfg.captions.font = "X"
    cfg.captions.words_per_caption = 6
    cfg.captions.max_chars = 60
    nar, script = make_narration(
        "They are really very smart.",
        "Pneumonoultramicroscopicsilicovolcanoconiosis",
        "Absolutely incredible marine creatures live here",
    )
    caps = events(build_ass(cfg, nar, script, tmp_path / "c.ass").read_text(encoding="utf-8"), "Caption")
    first = caps[0][2]
    assert first.count("\\N") == 1 and not re.search(r"\\fs\d+", first)  # two lines, full size
    long_word = caps[5][2]
    assert "\\N" not in long_word and int(re.search(r"\\fs(\d+)", long_word).group(1)) < 88
    crowded = caps[-1][2]
    assert crowded.count("\\N") == 1 and re.search(r"\\fs\d+", crowded)  # never more than two lines


def test_group_captions_fits_predicate_limits_chunks():
    ws = words_from("Venus takes about 243 Earth days")
    narrow = group_captions(ws, [0.0], 3, 20, fits=lambda text: len(text) <= 11)
    assert [[w.word for w in c.words] for c in narrow] == [["Venus", "takes"], ["about", "243"], ["Earth", "days"]]
    # a single word is always allowed, even when it does not "fit"
    assert [len(c.words) for c in group_captions(ws, [0.0], 3, 20, fits=lambda text: False)] == [1] * 6


def test_build_ass_short_captions_stay_on_one_line(tmp_path):
    cfg = Config()
    cfg.captions.font = "X"  # defaults: 3 words, 20 chars, 88 px on 1080 wide
    nar, script = make_narration("Venus takes about two hundred forty three Earth days to spin once")
    caps = events(build_ass(cfg, nar, script, tmp_path / "c.ass").read_text(encoding="utf-8"), "Caption")
    assert caps and all("\\N" not in text and not re.search(r"\\fs\d+", text) for _, _, text in caps)
    shown = {re.sub(r"\{[^}]*\}", "", text) for _, _, text in caps}
    assert "VENUS TAKES ABOUT" not in shown  # estimated wider than 85% of the frame at 88 px


def test_title_returns_as_end_card_when_render_adds_an_outro(tmp_path):
    from autoshorts.render import video_duration

    cfg = Config()
    cfg.captions.font = "X"
    nar, script = make_narration("Octopuses have three hearts.")
    nar.duration = 58.0  # target 65 s: the renderer pads this to just over a minute
    titles = events(build_ass(cfg, nar, script, tmp_path / "c.ass").read_text(encoding="utf-8"), "Title")
    assert [(start, end) for start, end, _ in titles] == [
        ("0:00:00.00", "0:00:03.00"), ("0:00:58.30", ass_time(video_duration(cfg, 58.0))),
    ]
    assert titles[0][2] == titles[1][2]

    cfg.video.target_seconds = 45  # no outro: the title shows only at the start
    titles = events(build_ass(cfg, nar, script, tmp_path / "c.ass").read_text(encoding="utf-8"), "Title")
    assert len(titles) == 1


def test_build_ass_without_words_has_only_title(tmp_path):
    cfg = Config()
    cfg.captions.font = "X"
    nar = Narration(audio_path=Path("n.wav"), duration=2.0, words=[], segments=[])
    script = VideoScript(topic="t", format="facts", title="Only a title 🔥", segments=[], description="")
    ass = build_ass(cfg, nar, script, tmp_path / "c.ass").read_text(encoding="utf-8")
    assert not events(ass, "Caption")
    (_, end, text), = events(ass, "Title")
    assert end == "0:00:02.00" and text.endswith("Only a title")


def test_caption_events_skip_zero_length_slices():
    cfg = Config()
    lay = captions._Layout(1080, 1920, 1.0, "X", 88, 53)
    ws = [WordTiming("a", 1.0, 1.0), WordTiming("b", 1.0, 1.4), WordTiming("c", 1.4, 1.8)]
    evs = captions._caption_events(cfg, lay, Caption(words=ws, start=1.0, end=2.0))
    assert len(evs) == 2  # "a" has no time of its own
    assert r"\fscx80" in evs[0]  # the pop still lands on the first visible slice


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ass_renders_with_libass(tmp_path):
    cfg = Config()
    cfg.video.width, cfg.video.height = 216, 384
    cfg.captions.show_title = False
    nar, script = make_narration("Big bold words here")
    build_ass(cfg, nar, script, tmp_path / "captions.ass")
    w, h = 216, 384
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:d=1",
         "-vf", "subtitles=captions.ass", "-ss", "0.2", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        cwd=tmp_path, capture_output=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    frame = proc.stdout
    assert len(frame) == w * h
    rows = [frame[y * w : (y + 1) * w] for y in range(h)]
    lit = [y for y, row in enumerate(rows) if max(row) > 200]
    assert lit, "no caption pixels rendered"
    centre = (min(lit) + max(lit)) / 2
    assert abs(centre - cfg.captions.position * h) < h * 0.08


# --------------------------------------------------------------------------- fonts


def test_resolve_font_explicit_name_is_kept():
    cfg = Config()
    cfg.captions.font = "Comic Neue"
    assert resolve_font(cfg) == "Comic Neue"


def test_read_font_names_from_ttf_and_ttc(tmp_path):
    names = {1: "My Family", 2: "Bold", 4: "My Family Bold", 16: "My Typo Family"}
    ttf = tmp_path / "a.ttf"
    ttf.write_bytes(make_font(names))
    assert read_font_names(ttf) == names
    assert font_family(ttf) == "My Family"
    ttc = tmp_path / "b.ttc"
    ttc.write_bytes(make_font({1: "Collection Face"}, ttc=True))
    assert font_family(ttc) == "Collection Face"
    junk = tmp_path / "Broken-Font.otf"
    junk.write_bytes(b"\x00\x01garbage")
    assert read_font_names(junk) == {}
    assert font_family(junk) == "Broken-Font"


def test_fonts_dir_prefers_boldest_upright_font(tmp_path):
    fonts = tmp_path / "assets" / "fonts"
    fonts.mkdir(parents=True)
    (fonts / "a.ttf").write_bytes(make_font({1: "Alpha", 2: "Regular", 4: "Alpha Regular"}))
    (fonts / "b.ttf").write_bytes(make_font({1: "Bravo Black", 2: "Regular", 4: "Bravo Black"}))
    (fonts / "c.ttf").write_bytes(make_font({1: "Charlie", 2: "Black Italic", 4: "Charlie Black Italic"}))
    (fonts / "notes.txt").write_text("not a font")
    cfg = Config()
    cfg.base_dir = tmp_path
    assert resolve_font(cfg) == "Bravo Black"


def test_resolve_font_uses_fontconfig_candidates(monkeypatch, tmp_path):
    cfg = Config()
    cfg.base_dir = tmp_path  # no fonts dir
    monkeypatch.setattr(captions, "_platform", lambda: "linux")
    monkeypatch.setattr(captions, "_fc_families", lambda: {"freesans", "liberation sans"})
    assert resolve_font(cfg) == "Liberation Sans"
    monkeypatch.setattr(captions, "_fc_families", lambda: None)  # no fc-list at all
    assert resolve_font(cfg) == "DejaVu Sans"


def test_resolve_font_windows_files(monkeypatch, tmp_path):
    cfg = Config()
    cfg.base_dir = tmp_path
    windir = tmp_path / "Windows"
    (windir / "Fonts").mkdir(parents=True)
    monkeypatch.setattr(captions, "_platform", lambda: "win")
    monkeypatch.setenv("WINDIR", str(windir))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert resolve_font(cfg) == "Arial"  # fallback, nothing installed
    (windir / "Fonts" / "arialbd.ttf").write_bytes(b"x")
    assert resolve_font(cfg) == "Arial"
    (windir / "Fonts" / "ariblk.ttf").write_bytes(b"x")
    assert resolve_font(cfg) == "Arial Black"


def test_resolve_font_macos_without_fontconfig(monkeypatch, tmp_path):
    cfg = Config()
    cfg.base_dir = tmp_path
    supplemental = tmp_path / "Supplemental"
    supplemental.mkdir()
    monkeypatch.setattr(captions, "_platform", lambda: "darwin")
    monkeypatch.setattr(captions, "_fc_families", lambda: None)
    monkeypatch.setattr(captions, "MAC_FONT_DIRS", [str(supplemental)])
    assert resolve_font(cfg) == "Helvetica"
    (supplemental / "Arial Black.ttf").write_bytes(b"x")
    assert resolve_font(cfg) == "Arial Black"


@pytest.mark.skipif(shutil.which("fc-list") is None, reason="fontconfig not installed")
def test_resolve_font_on_this_system(tmp_path):
    cfg = Config()
    cfg.base_dir = tmp_path
    name = resolve_font(cfg)
    assert name and name != "auto"


def test_real_font_file_family_if_present():
    path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if not path.exists():
        pytest.skip("DejaVu fonts not installed")
    assert font_family(path) == "DejaVu Sans"


# --------------------------------------------------------------------------- scripts and languages


def test_styles_use_unicode_bidi_encoding(tmp_path):
    """Encoding 1 (VSFilter bidi) reorders each run between colour tags on its own, which
    scrambles Arabic/Hebrew karaoke captions; -1 makes libass apply Unicode bidi."""
    cfg = Config(base_dir=tmp_path)
    lines = captions._header(cfg, captions._layout(cfg), "שלום עולם")
    styles = [line for line in lines if line.startswith("Style:")]
    assert len(styles) == 3 and all(line.endswith(",-1") for line in styles)


def test_cjk_characters_are_measured_full_width():
    assert captions._char_width("章") == 1.0 and captions._char_width("タ") == 1.0
    assert captions._char_width("Ａ") == 1.0  # fullwidth Latin
    sentence = "章鱼有三颗心脏"
    assert captions.text_width(sentence, 100) == pytest.approx(700)


def test_cjk_sentence_becomes_several_caption_words(tmp_path):
    from autoshorts.tts.timing import display_tokens

    text = "章鱼有三颗心脏，它们的血液是蓝色的。科学家们至今仍在研究这种神奇的生物。"
    tokens = display_tokens(text)
    assert len(tokens) >= 5 and "".join(tokens) == text
    cfg = Config(base_dir=tmp_path)
    lay = captions._layout(cfg)
    # every caption word fits the frame at the configured size
    assert all(captions.text_width(t, lay.font_size) <= lay.width * captions.MAX_TEXT_WIDTH for t in tokens)


def test_uppercase_follows_the_script_language():
    assert captions.upper_for("istanbul bilgi ılık", "tr") == "İSTANBUL BİLGİ ILIK"
    assert captions.upper_for("ήλιος και αϋπνία", "el") == "ΗΛΙΟΣ ΚΑΙ ΑΥΠΝΙΑ"
    assert captions.upper_for("istanbul", "en") == "ISTANBUL"
    assert captions._display_word("bilgi", True, "tr") == "BİLGİ"
    assert captions._display_word("bilgi", False, "tr") == "bilgi"
