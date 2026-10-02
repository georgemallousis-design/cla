"""Channels, the reddit story card, continuous gameplay backgrounds and narrator voices."""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

import pytest
import yaml

from autoshorts import captions, channels, cli
from autoshorts.card import card_events, circle, rounded_rect, wrap_title
from autoshorts.config import Config, load_config
from autoshorts.models import ClipAsset, Narration, Segment, TimedSegment, VideoScript, WordTiming
from autoshorts.render import shot_input_args
from autoshorts.topics import TopicQueue
from autoshorts.tts import narrator_config
from autoshorts.utils import AutoShortsError
from autoshorts.visuals import continuous_shots, plan_shots


def _narration(tmp_path: Path) -> tuple[Narration, VideoScript]:
    segs = [Segment("Am I wrong for keeping my dress?", "dress"), Segment("I am twenty six.", "city")]
    words = [WordTiming(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate("Am I wrong for keeping my dress?".split())]
    words += [WordTiming(w, 3.0 + i * 0.4, 3.35 + i * 0.4) for i, w in enumerate("I am twenty six.".split())]
    timed = [TimedSegment(segs[0], 0.0, 2.8), TimedSegment(segs[1], 3.0, 4.6)]
    narration = Narration(tmp_path / "n.wav", 4.6, words, timed)
    script = VideoScript("t", "reddit", "Am I wrong for keeping my dress?", segs, "d", narrator="female")
    return narration, script


# --------------------------------------------------------------------------- card


def test_shapes_are_closed_drawings():
    for drawing in (rounded_rect(400, 200, 30), circle(90)):
        assert drawing.startswith("m ") and " b " in drawing
    assert rounded_rect(10, 10, 99).startswith("m 5 0")  # radius clamped to half the size


def test_wrap_title_shrinks_long_titles():
    lines, size = wrap_title("word " * 60, 54, 800)
    assert len(lines) <= 5 or size <= 27
    lines, size = wrap_title("Short title", 54, 800)
    assert lines == ["Short title"] and size == 54


def test_card_events_escape_text_and_use_channel(tmp_path):
    cfg = Config(base_dir=tmp_path)
    cfg.channel.name = "Night {Tales}"
    events = card_events(cfg, "Arial", 1080, 1920, "Was I wrong?", 0.0, 2.5)
    joined = "\n".join(events)
    assert "Night \\{Tales\\}" in joined and "Was I wrong?" in joined
    assert all(e.startswith("Dialogue:") and ",Card," in e for e in events)
    assert card_events(cfg, "Arial", 1080, 1920, "", 0, 2) == []


def test_build_ass_card_replaces_title_and_hides_title_captions(tmp_path):
    cfg = Config(base_dir=tmp_path)
    cfg.captions.card = True
    cfg.channel.name = "Midnight"
    narration, script = _narration(tmp_path)
    text = captions.build_ass(cfg, narration, script, tmp_path / "c.ass").read_text(encoding="utf-8")
    assert "Midnight" in text and "Style: Card" in text
    caption_lines = [line for line in text.splitlines() if ",Caption," in line]
    assert caption_lines and all("WRONG" not in line for line in caption_lines)  # title words only on the card
    assert ",Title," not in text


# --------------------------------------------------------------------------- continuous gameplay


@pytest.fixture
def gameplay(tmp_path):
    folder = tmp_path / "assets" / "gameplay"
    folder.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=108x192:r=10:d=30", "-pix_fmt", "yuv420p", str(folder / "parkour.mp4")], check=True)
    return folder


def test_continuous_uses_one_clip_with_random_offset(tmp_path, gameplay):
    cfg = Config(base_dir=tmp_path)
    shots = continuous_shots(cfg, 10.0, rng=random.Random(1))
    assert len(shots) == 1 and shots[0].start == 0 and shots[0].end == 10.0
    clip = shots[0].clip
    assert clip.path.name == "parkour.mp4" and 0 <= clip.start_offset <= 30 - 15


def test_continuous_falls_back_to_cuts_without_gameplay(tmp_path):
    cfg = Config(base_dir=tmp_path)
    cfg.visuals.style = "continuous"
    cfg.visuals.providers = ["generated"]
    cfg.video.width, cfg.video.height = 108, 192
    narration, _ = _narration(tmp_path)
    assert continuous_shots(cfg, 4.6) == []
    shots = plan_shots(cfg, narration, tmp_path / "work")
    assert shots and shots[-1].end == pytest.approx(4.6)


def test_unknown_visual_style(tmp_path):
    cfg = Config(base_dir=tmp_path)
    cfg.visuals.style = "zoomy"
    with pytest.raises(AutoShortsError, match="visuals.style"):
        plan_shots(cfg, _narration(tmp_path)[0], tmp_path)


def test_renderer_uses_planned_offset():
    clip = ClipAsset(path=Path("g.mp4"), duration=100.0, start_offset=42.5)
    assert shot_input_args(clip, 0, 10.0, 100.0)[:2] == ["-ss", "42.500"]
    clip.start_offset = 99.0  # clamped so the shot still fits
    assert float(shot_input_args(clip, 0, 10.0, 100.0)[1]) <= 90.0


# --------------------------------------------------------------------------- voices


def test_female_narrator_swaps_voices(tmp_path):
    cfg = Config(base_dir=tmp_path)
    voiced = narrator_config(cfg, "female")
    assert voiced.tts.edge.voice == cfg.tts.edge.voice_female and voiced.tts.pyttsx3.voice == "Zira"
    assert cfg.tts.edge.voice == "en-US-AndrewNeural"  # original untouched
    assert narrator_config(cfg, "male") is cfg and narrator_config(cfg, "") is cfg


# --------------------------------------------------------------------------- topics


def test_when_empty_llm_returns_blank_topic(tmp_path):
    (tmp_path / "topics.txt").write_text("only one\n", encoding="utf-8")
    cfg = Config(base_dir=tmp_path)
    cfg.topics.when_empty = "llm"
    queue = TopicQueue(cfg)
    assert queue.next() == "only one"
    queue.mark_used("only one")
    assert queue.next() == ""


# --------------------------------------------------------------------------- channels


def test_presets_load_cleanly(capsys):
    names = channels.presets()
    assert set(names) == {"reddit", "whatif", "mystery", "psychology", "quiz"}
    for name in names:
        path = channels.PRESETS_DIR / name / "config.yaml"
        raw = path.read_text(encoding="utf-8")
        yaml.safe_load(raw)
        top = [line.split(":")[0] for line in raw.splitlines() if line and not line[0].isspace() and ":" in line
               and not line.startswith("#")]
        assert len(top) == len(set(top)), f"{name}: duplicate top-level sections"
        cfg = load_config(path)
        assert cfg.script.format == name and cfg.channel.name
        assert (channels.PRESETS_DIR / name / "topics.txt").read_text(encoding="utf-8").count("\n") >= 30
    assert "warning" not in capsys.readouterr().err


def test_init_never_overwrites(tmp_path):
    created = channels.init_channels(tmp_path, ["reddit"])
    assert {p.name for p in created} == {"config.yaml", "topics.txt"}
    (tmp_path / "reddit" / "topics.txt").write_text("mine\n", encoding="utf-8")
    assert channels.init_channels(tmp_path, ["reddit"]) == []
    assert (tmp_path / "reddit" / "topics.txt").read_text(encoding="utf-8") == "mine\n"
    with pytest.raises(AutoShortsError, match="unknown channel"):
        channels.init_channels(tmp_path, ["cooking"])


def test_run_all_runs_each_channel_in_its_own_process(tmp_path, monkeypatch):
    channels.init_channels(tmp_path, ["reddit", "quiz"])
    calls = []

    def fake_run(cmd, check=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if "reddit" in cmd[4] else 2)

    monkeypatch.setattr(channels.subprocess, "run", fake_run)
    results = channels.run_all(tmp_path, 2, upload="tiktok")
    assert results == {"quiz": 2, "reddit": 0}
    assert all(c[-2:] == ["--upload", "tiktok"] for c in calls)
    assert all(c[c.index("-n") + 1] == "2" for c in calls)
    assert channels.run_all(tmp_path, only=["quiz"], no_upload=True) == {"quiz": 2}
    with pytest.raises(AutoShortsError, match="no such channel"):
        channels.run_all(tmp_path, only=["nope"])


def test_cli_channels_and_run_all(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["channels", "list"]) == 0
    assert "channels init" in capsys.readouterr().out
    assert cli.main(["channels", "init", "whatif"]) == 0
    assert cli.main(["channels", "list"]) == 0
    assert "What If Lab" in capsys.readouterr().out
    monkeypatch.setattr(channels, "run_all", lambda root, count, **kw: {"whatif": 0})
    assert cli.main(["run-all"]) == 0
    monkeypatch.setattr(channels, "run_all", lambda root, count, **kw: {"whatif": 3, "quiz": 0})
    assert cli.main(["run-all"]) == 3


def test_channel_env_wins_over_shared_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AS_TEST_KEY", raising=False)
    monkeypatch.delenv("AS_TEST_SHARED", raising=False)
    (tmp_path / ".env").write_text("AS_TEST_KEY=shared\nAS_TEST_SHARED=yes\n", encoding="utf-8")
    ch = tmp_path / "channels" / "a"
    ch.mkdir(parents=True)
    (ch / "config.yaml").write_text("output_dir: out\n", encoding="utf-8")
    (ch / ".env").write_text("AS_TEST_KEY=channel\n", encoding="utf-8")
    load_config(ch / "config.yaml")
    assert Config.secret("AS_TEST_KEY") == "channel" and Config.secret("AS_TEST_SHARED") == "yes"
