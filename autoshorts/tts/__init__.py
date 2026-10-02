"""Text-to-speech: VideoScript -> Narration (one audio file + word timings).

Contract
--------
``get_engine(cfg)`` returns a TTSEngine chosen by ``cfg.tts.provider``:

* ``edge``     -> tts.edge.EdgeTTS        (free Microsoft neural voices, needs internet, real word timings)
* ``pyttsx3``  -> tts.pyttsx3_engine.Pyttsx3TTS  (offline; SAPI5 on Windows, NSSpeech on macOS)
* ``espeak``   -> tts.espeak.EspeakTTS    (offline; espeak-ng/espeak binary)
* ``auto``     -> edge, falling back to the offline engines when edge fails at synthesis time

``synthesize_narration(cfg, script, workdir)`` synthesizes each segment separately with
the engine, concatenates them with ``cfg.tts.segment_gap`` seconds of silence into
``workdir/narration.wav`` (48 kHz stereo PCM), offsets word timings to absolute times
and returns a Narration with one TimedSegment per script segment.

Engines that cannot report word timings use ``tts.timing.estimate_word_timings``
(through ``estimate_from_audio``, which also lines words up with the audio's pauses).

Extras: ``check_engines()`` reports which engines can run here (for ``doctor``);
``tts.edge.list_voices(lang)`` lists edge voices (for ``voices``).
"""
from __future__ import annotations

import copy
import re
import sys
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from ..config import Config
from ..models import Narration, SpeechResult, TimedSegment, VideoScript, WordTiming
from ..utils import AutoShortsError, ensure_dir, log, media_duration, run_ffmpeg
from .timing import display_tokens

ENGINE_NAMES = ("edge", "pyttsx3", "espeak")
SAMPLE_RATE = 48_000
CHANNELS = 2
SAMPLE_WIDTH = 2  # bytes, 16-bit PCM

# Emoji and pictographs: not spoken (or read out as "fire emoji"), never captioned.
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]")


class TTSEngine(ABC):
    name: str = "base"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @classmethod
    def problem(cls) -> str | None:
        """Why this engine cannot run on this machine (with an install hint), or None."""
        return None

    @abstractmethod
    def synthesize(self, text: str, out_path: Path) -> SpeechResult:
        """Speak ``text`` into ``out_path`` (the engine may change the suffix; use the
        returned SpeechResult.audio_path)."""


# --------------------------------------------------------------------------- engine selection


def _engine_class(name: str) -> type[TTSEngine]:
    if name == "edge":
        from .edge import EdgeTTS

        return EdgeTTS
    if name == "pyttsx3":
        from .pyttsx3_engine import Pyttsx3TTS

        return Pyttsx3TTS
    if name == "espeak":
        from .espeak import EspeakTTS

        return EspeakTTS
    raise AutoShortsError(f"unknown tts.provider '{name}' (use auto, edge, pyttsx3 or espeak)")


def create_engine(name: str, cfg: Config) -> TTSEngine:
    """Instantiate one engine by name; AutoShortsError with an install hint if it can't run."""
    cls = _engine_class(name)
    problem = cls.problem()
    if problem:
        raise AutoShortsError(f"TTS engine '{name}' is not available: {problem}")
    return cls(cfg)


def check_engines() -> dict[str, str | None]:
    """{engine name: problem or None} for every engine (cheap, no network)."""
    return {name: _engine_class(name).problem() for name in ENGINE_NAMES}


def auto_order(platform: str | None = None) -> list[str]:
    """Engines tried by provider "auto": pyttsx3 only where it has native voices."""
    platform = platform or sys.platform
    order = ["edge"]
    if platform.startswith("win") or platform == "darwin":
        order.append("pyttsx3")
    order.append("espeak")
    return order


def install_hints(platform: str | None = None) -> str:
    platform = platform or sys.platform
    if platform.startswith("win"):
        return "check your internet connection (edge voices), or run: pip install pyttsx3 (offline Windows voices)"
    if platform == "darwin":
        return "check your internet connection (edge voices), or run: pip install pyttsx3  (or: brew install espeak-ng)"
    return "check your internet connection (edge voices), or install offline TTS: sudo apt install espeak-ng"


class AutoTTS(TTSEngine):
    """Tries engines in order on the first call, then sticks to the first that worked
    so one video never mixes voices."""

    def __init__(
        self,
        cfg: Config,
        order: list[str] | None = None,
        factory: Callable[[str, Config], TTSEngine] = create_engine,
    ):
        super().__init__(cfg)
        self.order = list(order or auto_order())
        self._factory = factory
        self._excluded: set[str] = set()
        self.current: TTSEngine | None = None

    @property  # type: ignore[override]
    def name(self) -> str:
        return self.current.name if self.current is not None else "auto"

    def synthesize(self, text: str, out_path: Path) -> SpeechResult:
        if self.current is not None:
            return self.current.synthesize(text, out_path)
        reasons: list[str] = []
        for name in self.order:
            if name in self._excluded:
                continue
            try:
                engine = self._factory(name, self.cfg)
                result = engine.synthesize(text, out_path)
            except Exception as exc:
                log.warning("TTS engine '%s' did not work (%s); trying the next one", name, exc)
                reasons.append(f"{name}: {exc}")
                self._excluded.add(name)
                continue
            if reasons:
                log.info("TTS: using '%s' for this video", engine.name)
            self.current = engine
            return result
        detail = "\n  ".join(reasons) or "every engine was excluded"
        raise AutoShortsError(f"no TTS engine worked:\n  {detail}\nFix: {install_hints()}")

    def drop_current(self, reason: str = "") -> bool:
        """Stop using the current engine after it failed mid-video.
        Returns True when another engine is left to try."""
        if self.current is None:
            return False
        log.warning("TTS engine '%s' failed mid-video (%s)", self.current.name, reason)
        self._excluded.add(self.current.name)
        self.current = None
        return any(n not in self._excluded for n in self.order)


def get_engine(cfg: Config) -> TTSEngine:
    provider = (cfg.tts.provider or "auto").strip().lower()
    if provider == "auto":
        return AutoTTS(cfg)
    return create_engine(provider, cfg)


# --------------------------------------------------------------------------- narration


def clean_for_speech(text: str) -> str:
    """Drop emoji/pictographs and collapse whitespace."""
    return " ".join(_EMOJI_RE.sub(" ", text).split())


def _speakable(text: str) -> str:
    """Cleaned text, or "" when nothing in it would be spoken (e.g. "!!!")."""
    text = clean_for_speech(text)
    return text if display_tokens(text) else ""


def synthesize_narration(cfg: Config, script: VideoScript, workdir: Path) -> Narration:
    workdir = ensure_dir(workdir)
    texts = [_speakable(s.text) for s in script.segments]
    if not any(texts):
        raise AutoShortsError("the script has no text to speak")
    engine = get_engine(narrator_config(cfg, script.narrator))
    while True:
        try:
            results = [
                engine.synthesize(text, workdir / f"seg_{i:02d}.wav") if text else None
                for i, text in enumerate(texts)
            ]
            break
        except Exception as exc:
            # The chosen engine broke halfway: redo every segment with the next one.
            if isinstance(engine, AutoTTS) and engine.drop_current(str(exc)):
                log.warning("re-synthesizing the whole narration so the voice stays consistent")
                continue
            raise
    narration = _assemble(cfg, script, results, workdir, engine.name)
    log.info("narration: %.1fs, %d words, %d segments (engine: %s)",
             narration.duration, len(narration.words), len(narration.segments), narration.engine)
    return narration


def narrator_config(cfg: Config, narrator: str) -> Config:
    """``cfg`` with the female voices swapped in when the script's narrator is a woman."""
    if (narrator or "").lower() != "female":
        return cfg
    voiced = copy.deepcopy(cfg)
    if cfg.tts.edge.voice_female:
        voiced.tts.edge.voice = cfg.tts.edge.voice_female
    if cfg.tts.pyttsx3.voice_female:
        voiced.tts.pyttsx3.voice = cfg.tts.pyttsx3.voice_female
    log.info("narrator is female: voice %s", voiced.tts.edge.voice)
    return voiced


def _to_pcm(src: Path, dst: Path) -> bytes:
    """Decode any audio file to raw 48 kHz stereo s16le samples."""
    run_ffmpeg(
        ["-i", src, "-vn", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "s16le", "-c:a", "pcm_s16le", dst],
        desc="narration audio conversion",
    )
    data = dst.read_bytes()
    frame = CHANNELS * SAMPLE_WIDTH
    return data[: len(data) // frame * frame]


def _assemble(
    cfg: Config, script: VideoScript, results: list[SpeechResult | None], workdir: Path, engine_name: str
) -> Narration:
    frame = CHANNELS * SAMPLE_WIDTH
    gap = b"\0" * (int(round(max(0.0, cfg.tts.segment_gap) * SAMPLE_RATE)) * frame)
    out = workdir / "narration.wav"
    words: list[WordTiming] = []
    timed: list[TimedSegment] = []
    frames = 0
    with wave.open(str(out), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        for i, (segment, res) in enumerate(zip(script.segments, results)):
            if res is not None and frames and gap:
                w.writeframes(gap)
                frames += len(gap) // frame
            start = frames / SAMPLE_RATE
            if res is not None:
                pcm = _to_pcm(res.audio_path, workdir / f"seg_{i:02d}.pcm")
                w.writeframes(pcm)
                frames += len(pcm) // frame
                words.extend(_offset_words(res.words, start, len(pcm) / frame / SAMPLE_RATE))
            timed.append(TimedSegment(segment=segment, start=round(start, 3), end=round(frames / SAMPLE_RATE, 3)))
    return Narration(audio_path=out, duration=media_duration(out), words=words, segments=timed, engine=engine_name)


def _offset_words(words: list[WordTiming], offset: float, length: float) -> list[WordTiming]:
    out = []
    for wt in words:
        s = min(max(wt.start, 0.0), length)
        e = min(max(wt.end, s), length)
        out.append(WordTiming(wt.word, round(offset + s, 3), round(offset + e, 3)))
    return out
