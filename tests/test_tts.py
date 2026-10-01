"""Tests for autoshorts.tts: timing helpers, engines (edge faked, espeak real), narration."""
from __future__ import annotations

import array
import asyncio
import math
import shutil
import subprocess
import sys
import types
import wave
from pathlib import Path

import pytest

from autoshorts import tts
from autoshorts.config import Config
from autoshorts.models import Segment, SpeechResult, VideoScript, WordTiming
from autoshorts.tts import AutoTTS, auto_order, clean_for_speech, get_engine, synthesize_narration
from autoshorts.tts.timing import (
    align_words,
    analyze_silence,
    display_tokens,
    estimate_from_audio,
    estimate_word_timings,
    pause_after,
)
from autoshorts.utils import AutoShortsError

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
needs_espeak = pytest.mark.skipif(
    not (HAS_FFMPEG and (shutil.which("espeak-ng") or shutil.which("espeak"))), reason="espeak-ng not installed"
)


# --------------------------------------------------------------------------- helpers


def write_wav(path: Path, pattern: list[tuple[float, bool]], rate: int = 24000) -> Path:
    """Mono 16-bit WAV from (seconds, is_tone) pieces."""
    samples = array.array("h")
    for seconds, tone in pattern:
        n = int(seconds * rate)
        if tone:
            samples.extend(int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n))
        else:
            samples.extend([0] * n)
    if sys.byteorder == "big":
        samples.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return path


def make_script(*texts: str) -> VideoScript:
    return VideoScript(
        topic="octopus",
        format="facts",
        title="Octopus facts",
        segments=[Segment(text=t, visual_query="octopus") for t in texts],
        description="Facts.",
    )


def assert_monotonic(words: list[WordTiming]) -> None:
    for prev, cur in zip(words, words[1:]):
        assert cur.start >= prev.start
    for w in words:
        assert w.end >= w.start >= 0


# --------------------------------------------------------------------------- timing


def test_display_tokens_merges_punctuation_only_tokens():
    assert display_tokens("Wait — what?!  Really") == ["Wait —", "what?!", "Really"]
    assert display_tokens("— Hello there") == ["— Hello", "there"]
    assert display_tokens("Rock & roll") == ["Rock", "&", "roll"]
    assert display_tokens("  ") == []


def test_pause_after_kinds():
    assert pause_after("end.") == "sentence"
    assert pause_after('really?"') == "sentence"
    assert pause_after("wait...") == "sentence"
    assert pause_after("hearts,") == "clause"
    assert pause_after("note:") == "clause"
    assert pause_after("Dr.") is None
    assert pause_after("word") is None


def test_estimate_word_timings_monotonic_and_covers_duration():
    text = "Octopuses have three hearts, and blue blood! Each arm can taste what it touches."
    words = estimate_word_timings(text, 5.0, lead=0.05, tail=0.1)
    assert [w.word for w in words] == text.split()
    assert_monotonic(words)
    assert words[0].start == pytest.approx(0.05)
    assert words[-1].end == pytest.approx(4.9)
    for prev, cur in zip(words, words[1:]):
        assert cur.start >= prev.end - 1e-6  # no overlaps


def test_estimate_word_timings_pauses_after_punctuation():
    words = estimate_word_timings("one two, three four. five", 4.0)
    gaps = [b.start - a.end for a, b in zip(words, words[1:])]
    assert gaps[0] == pytest.approx(0.0, abs=1e-3)  # one -> two
    assert gaps[1] > 0.05  # after the comma
    assert gaps[3] > gaps[1]  # a full stop pauses longer than a comma
    longer = estimate_word_timings("a extraordinary", 2.0)
    assert longer[1].end - longer[1].start > longer[0].end - longer[0].start


def test_estimate_word_timings_edge_cases():
    assert estimate_word_timings("", 3.0) == []
    assert estimate_word_timings("hello", 0) == []
    tiny = estimate_word_timings("hi there", 0.1, lead=0.5, tail=0.5)
    assert 0 <= tiny[0].start and tiny[-1].end <= 0.1


def test_align_words_keeps_punctuation_from_text():
    raw = [WordTiming("Hello", 0.1, 0.4), WordTiming("world", 0.5, 0.9), WordTiming("Its", 1.2, 1.4),
           WordTiming("5", 1.4, 1.7), WordTiming("oclock", 1.7, 2.2)]
    words = align_words("Hello, world! It's 5 o'clock.", raw, duration=2.5)
    assert [w.word for w in words] == ["Hello,", "world!", "It's", "5", "o'clock."]
    assert [(w.start, w.end) for w in words] == [(0.1, 0.4), (0.5, 0.9), (1.2, 1.4), (1.4, 1.7), (1.7, 2.2)]


def test_align_words_handles_split_merged_and_missing_tokens():
    raw = [WordTiming("a", 0.0, 0.2), WordTiming("well", 0.2, 0.5), WordTiming("known", 0.5, 0.9),
           WordTiming("fact", 1.5, 2.0)]
    words = align_words("A well-known 🙂 surprising fact.", raw, duration=2.2)
    assert [w.word for w in words] == ["A", "well-known 🙂", "surprising", "fact."]
    assert words[1].start == pytest.approx(0.2) and words[1].end == pytest.approx(0.9)
    # "surprising" was never reported: it is placed between its neighbours
    assert 0.9 <= words[2].start < words[2].end <= 1.5
    assert_monotonic(words)


def test_align_words_squeezes_missing_word_without_room():
    raw = [WordTiming("one", 0.0, 0.5), WordTiming("three", 0.5, 1.0)]
    words = align_words("one two three", raw, duration=1.0)
    assert [w.word for w in words] == ["one", "two", "three"]
    assert_monotonic(words)
    assert words[1].end - words[1].start >= 0.05


def test_align_words_without_events_estimates():
    words = align_words("no events here", [], duration=2.0)
    assert [w.word for w in words] == ["no", "events", "here"]
    assert words[-1].end <= 2.0


def test_analyze_silence_and_pause_aligned_estimate(tmp_path):
    wav = write_wav(tmp_path / "a.wav", [(0.3, False), (0.5, True), (0.2, False), (0.4, True), (0.3, False)])
    info = analyze_silence(wav)
    assert info is not None
    assert info.duration == pytest.approx(1.7, abs=0.01)
    assert info.speech_start == pytest.approx(0.3, abs=0.02)
    assert info.speech_end == pytest.approx(1.4, abs=0.02)
    assert len(info.gaps) == 1 and info.gaps[0][0] == pytest.approx(0.8, abs=0.02)

    words = estimate_from_audio("Hello there, friend.", wav)
    assert [w.word for w in words] == ["Hello", "there,", "friend."]
    assert words[0].start == pytest.approx(0.3, abs=0.02)
    assert words[1].end == pytest.approx(0.8, abs=0.02)
    assert words[2].start == pytest.approx(1.0, abs=0.02)
    assert words[2].end == pytest.approx(1.4, abs=0.02)


def test_estimate_from_audio_falls_back_for_unreadable_file(tmp_path):
    bad = tmp_path / "x.wav"
    bad.write_bytes(b"not a wav")
    words = estimate_from_audio("one two", bad, duration=1.0)
    assert len(words) == 2 and words[-1].end <= 1.0


def test_clean_for_speech_strips_emoji():
    assert clean_for_speech("Wow 🔥🔥 this is   wild ✨!") == "Wow this is wild !"
    assert clean_for_speech("It was 30°C") == "It was 30°C"


# --------------------------------------------------------------------------- edge (faked)


@pytest.fixture(scope="module")
def tiny_mp3(tmp_path_factory) -> bytes:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("mp3") / "tone.mp3"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-ac", "1", "-ar", "24000", "-b:a", "48k", str(out)],
        check=True,
    )
    return out.read_bytes()


def fake_edge_module(audio: bytes, words: list[tuple[str, float, float]], fail_times: int = 0,
                     error: type[Exception] = ConnectionError) -> types.ModuleType:
    mod = types.ModuleType("edge_tts")
    mod.calls = []  # type: ignore[attr-defined]

    class Communicate:
        def __init__(self, text, voice, *, rate="+0%", volume="+0%", pitch="+0Hz", boundary="SentenceBoundary"):
            if not voice.endswith("Neural"):
                raise ValueError(f"Invalid voice '{voice}'")
            mod.calls.append({"text": text, "voice": voice, "rate": rate, "volume": volume, "pitch": pitch,
                              "boundary": boundary})

        async def stream(self):
            if len(mod.calls) <= fail_times:
                raise error("network down")
            half = len(audio) // 2
            yield {"type": "audio", "data": audio[:half]}
            for text, start, end in words:
                if boundary_ok(mod.calls[-1]):
                    yield {"type": "WordBoundary", "offset": int(start * 1e7), "duration": int((end - start) * 1e7),
                           "text": text}
            yield {"type": "audio", "data": audio[half:]}

    def boundary_ok(call):
        return call["boundary"] == "WordBoundary"

    async def list_voices():
        return [
            {"ShortName": "en-US-AndrewNeural", "Locale": "en-US", "Gender": "Male",
             "VoiceTag": {"VoicePersonalities": ["Warm"]}},
            {"ShortName": "de-DE-KatjaNeural", "Locale": "de-DE", "Gender": "Female", "VoiceTag": {}},
            {"ShortName": "en-GB-SoniaNeural", "Locale": "en-GB", "Gender": "Female"},
        ]

    mod.Communicate = Communicate  # type: ignore[attr-defined]
    mod.list_voices = list_voices  # type: ignore[attr-defined]
    return mod


EDGE_WORDS = [("Octopuses", 0.05, 0.4), ("have", 0.4, 0.55), ("three", 0.55, 0.75), ("hearts", 0.75, 0.95)]


def edge_engine(monkeypatch, mod):
    from autoshorts.tts.edge import EdgeTTS

    monkeypatch.setitem(sys.modules, "edge_tts", mod)
    cfg = Config()
    cfg.tts.provider = "edge"
    engine = get_engine(cfg)
    assert isinstance(engine, EdgeTTS)
    engine.retry_delay = 0
    return engine


def test_edge_engine_streams_audio_and_word_boundaries(monkeypatch, tmp_path, tiny_mp3):
    mod = fake_edge_module(tiny_mp3, EDGE_WORDS)
    engine = edge_engine(monkeypatch, mod)
    res = engine.synthesize("Octopuses have three hearts!", tmp_path / "seg_00.wav")
    assert res.audio_path == tmp_path / "seg_00.mp3" and res.audio_path.read_bytes() == tiny_mp3
    assert res.duration == pytest.approx(1.0, abs=0.1)
    assert [w.word for w in res.words] == ["Octopuses", "have", "three", "hearts!"]
    assert res.words[0].start == pytest.approx(0.05) and res.words[-1].end == pytest.approx(0.95)
    call = mod.calls[0]
    assert call["boundary"] == "WordBoundary"
    assert (call["voice"], call["rate"], call["pitch"], call["volume"]) == ("en-US-AndrewNeural", "+5%", "+0Hz", "+0%")


def test_edge_retries_network_errors(monkeypatch, tmp_path, tiny_mp3):
    mod = fake_edge_module(tiny_mp3, EDGE_WORDS, fail_times=2)
    engine = edge_engine(monkeypatch, mod)
    res = engine.synthesize("Octopuses have three hearts", tmp_path / "a.wav")
    assert len(mod.calls) == 3
    assert len(res.words) == 4


def test_edge_gives_up_after_three_attempts(monkeypatch, tmp_path, tiny_mp3):
    mod = fake_edge_module(tiny_mp3, EDGE_WORDS, fail_times=99, error=OSError)
    engine = edge_engine(monkeypatch, mod)
    with pytest.raises(AutoShortsError, match="after 3 attempts"):
        engine.synthesize("hello", tmp_path / "a.wav")
    assert len(mod.calls) == 3


def test_edge_bad_settings_are_not_retried(monkeypatch, tmp_path, tiny_mp3):
    mod = fake_edge_module(tiny_mp3, EDGE_WORDS)
    engine = edge_engine(monkeypatch, mod)
    engine.cfg.tts.edge.voice = "nonsense"
    with pytest.raises(AutoShortsError, match="tts.edge"):
        engine.synthesize("hello", tmp_path / "a.wav")
    assert mod.calls == []


def test_edge_works_inside_a_running_event_loop(monkeypatch, tmp_path, tiny_mp3):
    engine = edge_engine(monkeypatch, fake_edge_module(tiny_mp3, EDGE_WORDS))

    async def main():
        return engine.synthesize("Octopuses have three hearts", tmp_path / "loop.wav")

    res = asyncio.run(main())
    assert len(res.words) == 4


def test_edge_list_voices_filters_by_language(monkeypatch, tiny_mp3):
    from autoshorts.tts.edge import list_voices

    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge_module(tiny_mp3, []))
    voices = list_voices("en")
    assert [v["name"] for v in voices] == ["en-GB-SoniaNeural", "en-US-AndrewNeural"]
    assert voices[1]["ShortName"] == "en-US-AndrewNeural" and voices[1]["personalities"] == ["Warm"]
    assert len(list_voices("")) == 3
    assert list_voices("EN-gb")[0]["locale"] == "en-GB"


def test_edge_missing_package_gives_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "edge_tts", None)  # makes "import edge_tts" raise ImportError
    cfg = Config()
    cfg.tts.provider = "edge"
    with pytest.raises(AutoShortsError, match="pip install edge-tts"):
        get_engine(cfg)


# --------------------------------------------------------------------------- pyttsx3 (faked)


@needs_ffmpeg
def test_pyttsx3_engine_with_fake_driver(monkeypatch, tmp_path):
    from autoshorts.tts.pyttsx3_engine import Pyttsx3TTS

    props: dict = {}

    class Voice:
        def __init__(self, vid, name):
            self.id, self.name = vid, name

    class Engine:
        def setProperty(self, key, value):
            props[key] = value

        def getProperty(self, key):
            return [Voice("v1", "Microsoft David"), Voice("v2", "Microsoft Zira Desktop")]

        def save_to_file(self, text, path):
            props["saved"] = path

        def runAndWait(self):
            write_wav(Path(props["saved"]), [(0.1, False), (0.6, True), (0.1, False)], rate=22050)

    mod = types.ModuleType("pyttsx3")
    mod.init = lambda: Engine()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyttsx3", mod)
    cfg = Config()
    cfg.tts.pyttsx3.voice = "zira"
    res = Pyttsx3TTS(cfg).synthesize("Hello there", tmp_path / "seg.wav")
    assert props["voice"] == "v2" and props["rate"] == 185
    assert res.audio_path == tmp_path / "seg.wav" and res.audio_path.exists()
    assert not Path(props["saved"]).exists()  # raw driver output removed
    assert res.duration == pytest.approx(0.8, abs=0.02)
    assert res.words[0].start == pytest.approx(0.1, abs=0.02)
    assert res.words[-1].end == pytest.approx(0.7, abs=0.02)


# --------------------------------------------------------------------------- espeak (real)


@needs_espeak
def test_espeak_real_synthesis(tmp_path):
    cfg = Config()
    cfg.tts.provider = "espeak"
    engine = get_engine(cfg)
    res = engine.synthesize("-Hello world, this is a test.", tmp_path / "e.wav")
    assert res.audio_path.suffix == ".wav" and res.audio_path.stat().st_size > 1000
    assert 0.8 < res.duration < 6
    assert [w.word for w in res.words] == ["-Hello", "world,", "this", "is", "a", "test."]
    assert_monotonic(res.words)
    assert res.words[-1].end <= res.duration + 1e-6
    assert not (tmp_path / "e.txt").exists()


@pytest.mark.skipif(shutil.which("false") is None, reason="needs a 'false' binary")
def test_espeak_failure_is_reported(monkeypatch, tmp_path):
    from autoshorts.tts import espeak as espeak_mod

    monkeypatch.setattr(espeak_mod, "find_espeak", lambda: shutil.which("false"))
    with pytest.raises(AutoShortsError, match="espeak failed"):
        tts.create_engine("espeak", Config()).synthesize("hello", tmp_path / "e.wav")
    assert not (tmp_path / "e.txt").exists()


@needs_espeak
def test_synthesize_narration_with_espeak(tmp_path):
    cfg = Config()
    cfg.tts.provider = "espeak"
    cfg.tts.segment_gap = 0.25
    script = make_script("Octopuses have three hearts.", "Each arm can taste, really!", "Follow for more.")
    nar = synthesize_narration(cfg, script, tmp_path / "work")
    assert nar.engine == "espeak"
    assert nar.audio_path == tmp_path / "work" / "narration.wav"
    with wave.open(str(nar.audio_path), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (48000, 2, 2)
    assert len(nar.segments) == 3
    assert nar.segments[0].start == 0
    for prev, cur in zip(nar.segments, nar.segments[1:]):
        assert cur.start == pytest.approx(prev.end + 0.25, abs=0.002)
    assert nar.duration == pytest.approx(nar.segments[-1].end, abs=0.01)
    assert [w.word for w in nar.words] == " ".join(s.text for s in script.segments).split()
    assert_monotonic(nar.words)
    # every word lies inside its own segment
    counts = [len(s.text.split()) for s in script.segments]
    idx = 0
    for seg, n in zip(nar.segments, counts):
        for w in nar.words[idx : idx + n]:
            assert seg.start - 1e-3 <= w.start <= w.end <= seg.end + 1e-3
        idx += n


# --------------------------------------------------------------------------- engine selection


def test_auto_order_per_platform():
    assert auto_order("linux") == ["edge", "espeak"]
    assert auto_order("win32") == ["edge", "pyttsx3", "espeak"]
    assert auto_order("darwin") == ["edge", "pyttsx3", "espeak"]


def test_get_engine_auto_and_unknown():
    cfg = Config()
    assert isinstance(get_engine(cfg), AutoTTS)
    cfg.tts.provider = "nope"
    with pytest.raises(AutoShortsError, match="unknown tts.provider"):
        get_engine(cfg)


def test_explicit_unavailable_engines_give_install_hints(monkeypatch):
    import importlib.util

    from autoshorts.tts import espeak as espeak_mod

    monkeypatch.setattr(espeak_mod, "find_espeak", lambda: None)
    cfg = Config()
    cfg.tts.provider = "espeak"
    with pytest.raises(AutoShortsError, match="apt install espeak-ng"):
        get_engine(cfg)

    real_find_spec = importlib.util.find_spec

    def find_spec(name, *args):
        return None if name == "pyttsx3" else real_find_spec(name, *args)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    cfg.tts.provider = "pyttsx3"
    with pytest.raises(AutoShortsError, match="pip install pyttsx3"):
        get_engine(cfg)
    assert set(tts.check_engines()) == {"edge", "pyttsx3", "espeak"}


class FakeEngine(tts.TTSEngine):
    """Writes a tone of 0.25 s per word; can be told to fail on certain calls."""

    log: list[tuple[str, str]] = []
    fail_on: dict[str, set[int]] = {}

    def synthesize(self, text: str, out_path: Path) -> SpeechResult:
        calls = [c for c in FakeEngine.log if c[0] == self.name]
        FakeEngine.log.append((self.name, text))
        if len(calls) in FakeEngine.fail_on.get(self.name, set()):
            raise AutoShortsError(f"{self.name} broke")
        out = Path(out_path).with_suffix(".wav")
        seconds = 0.25 * len(text.split())
        write_wav(out, [(seconds, True)], rate=16000)
        words = estimate_word_timings(text, seconds, lead=0, tail=0)
        return SpeechResult(audio_path=out, duration=seconds, words=words)


def fake_classes(unavailable: tuple[str, ...] = ()):
    classes = {}
    for name in ("edge", "pyttsx3", "espeak"):
        problem = f"{name} missing" if name in unavailable else None
        attrs = {"name": name, "problem": classmethod(lambda cls, p=problem: p)}
        classes[name] = type(f"Fake{name}", (FakeEngine,), attrs)
    return classes


@pytest.fixture
def fake_engines(monkeypatch):
    FakeEngine.log = []
    FakeEngine.fail_on = {}

    def install(order, unavailable=()):
        classes = fake_classes(unavailable)
        monkeypatch.setattr(tts, "_engine_class", lambda name: classes[name])
        monkeypatch.setattr(tts, "auto_order", lambda platform=None: list(order))

    return install


def test_auto_tts_falls_back_in_order_and_sticks(fake_engines, tmp_path):
    fake_engines(["edge", "pyttsx3", "espeak"], unavailable=("pyttsx3",))
    FakeEngine.fail_on = {"edge": {0}}
    engine = get_engine(Config())
    assert engine.name == "auto"
    engine.synthesize("first segment", tmp_path / "a.wav")
    assert engine.name == "espeak"
    engine.synthesize("second segment", tmp_path / "b.wav")
    assert FakeEngine.log == [("edge", "first segment"), ("espeak", "first segment"), ("espeak", "second segment")]


def test_auto_tts_reports_all_failures(fake_engines, tmp_path):
    fake_engines(["edge", "espeak"], unavailable=("espeak",))
    FakeEngine.fail_on = {"edge": {0}}
    with pytest.raises(AutoShortsError) as err:
        get_engine(Config()).synthesize("hi", tmp_path / "a.wav")
    msg = str(err.value)
    assert "edge broke" in msg and "espeak missing" in msg and "Fix:" in msg


@needs_ffmpeg
def test_narration_restarts_with_next_engine_when_chosen_one_breaks(fake_engines, tmp_path):
    fake_engines(["edge", "espeak"])
    FakeEngine.fail_on = {"edge": {2}}  # edge works for segments 1-2, then breaks
    script = make_script("one two three", "four five", "six seven eight nine")
    nar = synthesize_narration(Config(), script, tmp_path)
    assert nar.engine == "espeak"
    assert [n for n, _ in FakeEngine.log] == ["edge", "edge", "edge", "espeak", "espeak", "espeak"]
    assert nar.duration == pytest.approx(0.25 * 9 + 2 * 0.15, abs=0.01)


@needs_ffmpeg
def test_narration_handles_empty_segments_and_emoji(fake_engines, tmp_path):
    fake_engines(["edge"])
    cfg = Config()
    cfg.tts.segment_gap = 0.5
    script = make_script("one two", "🔥🔥 !!!", "three four")
    nar = synthesize_narration(cfg, script, tmp_path)
    assert len(nar.segments) == 3
    assert nar.segments[1].start == nar.segments[1].end == pytest.approx(0.5)
    assert nar.segments[2].start == pytest.approx(1.0)  # only one gap between spoken segments
    assert [t for _, t in FakeEngine.log] == ["one two", "three four"]
    with pytest.raises(AutoShortsError, match="no text"):
        synthesize_narration(cfg, make_script("✨"), tmp_path)
