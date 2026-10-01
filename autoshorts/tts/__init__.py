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

Engines that cannot report word timings use ``tts.timing.estimate_word_timings``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..config import Config
from ..models import Narration, SpeechResult, VideoScript


class TTSEngine(ABC):
    name: str = "base"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @abstractmethod
    def synthesize(self, text: str, out_path: Path) -> SpeechResult:
        """Speak ``text`` into ``out_path`` (the engine may change the suffix; use the
        returned SpeechResult.audio_path)."""


def get_engine(cfg: Config) -> TTSEngine:
    raise NotImplementedError


def synthesize_narration(cfg: Config, script: VideoScript, workdir: Path) -> Narration:
    raise NotImplementedError
