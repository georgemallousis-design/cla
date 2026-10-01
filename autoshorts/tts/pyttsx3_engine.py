"""Offline TTS through pyttsx3 (SAPI5 on Windows, NSSpeechSynthesizer on macOS,
espeak on Linux). Uses the voices installed in the operating system.

pyttsx3's ``runAndWait`` is known to hang or crash when driven from worker
threads, so this engine must be used from the main thread (the pipeline does).
macOS writes AIFF whatever the extension, so the raw output is always
re-encoded to 16-bit PCM WAV with ffmpeg. Word timings are estimated.
"""
from __future__ import annotations

import importlib.util
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..models import SpeechResult
from ..utils import AutoShortsError, log, media_duration, run_ffmpeg
from . import TTSEngine
from .timing import estimate_from_audio

INSTALL_HINT = "install it with: pip install pyttsx3"


def match_voice(voices: list[Any], query: str) -> Any | None:
    """First voice whose name or id contains ``query`` (case-insensitive)."""
    q = query.casefold()
    for v in voices:
        for attr in ("name", "id"):
            if q in str(getattr(v, attr, "") or "").casefold():
                return v
    return None


class Pyttsx3TTS(TTSEngine):
    name = "pyttsx3"
    file_wait = 5.0  # seconds to wait for the driver to finish writing the file

    def __init__(self, cfg):
        super().__init__(cfg)
        self._engine: Any = None

    @classmethod
    def problem(cls) -> str | None:
        if importlib.util.find_spec("pyttsx3") is None:
            return f"pyttsx3 is not installed; {INSTALL_HINT}"
        return None

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            import pyttsx3
        except ImportError as exc:
            raise AutoShortsError(f"pyttsx3 is not installed; {INSTALL_HINT}") from exc
        try:
            engine = pyttsx3.init()
        except Exception as exc:  # missing SAPI/NSSpeech/espeak driver
            hint = " (on Linux it also needs: sudo apt install espeak-ng)" if sys.platform.startswith("linux") else ""
            raise AutoShortsError(f"pyttsx3 could not start a speech driver: {exc}{hint}") from exc
        pc = self.cfg.tts.pyttsx3
        engine.setProperty("rate", int(pc.rate))
        if pc.voice:
            voices = list(engine.getProperty("voices") or [])
            voice = match_voice(voices, pc.voice)
            if voice is not None:
                engine.setProperty("voice", voice.id)
            else:
                names = ", ".join(str(getattr(v, "name", v)) for v in voices[:10])
                log.warning("pyttsx3: no voice matches '%s', using the default (available: %s)", pc.voice, names)
        self._engine = engine
        return engine

    def synthesize(self, text: str, out_path: Path) -> SpeechResult:
        if threading.current_thread() is not threading.main_thread():
            log.warning("pyttsx3 is running outside the main thread; it may hang on some systems")
        out = Path(out_path).with_suffix(".wav")
        out.parent.mkdir(parents=True, exist_ok=True)
        raw = out.with_name(out.stem + ("_raw.aiff" if sys.platform == "darwin" else "_raw.wav"))
        raw.unlink(missing_ok=True)
        engine = self._get_engine()
        try:
            engine.save_to_file(text, str(raw))
            engine.runAndWait()
        except Exception as exc:
            raise AutoShortsError(f"pyttsx3 failed to synthesize speech: {exc}") from exc
        self._wait_for(raw)
        try:
            run_ffmpeg(["-i", raw, "-vn", "-ac", "1", "-c:a", "pcm_s16le", out], desc="pyttsx3 audio conversion")
        finally:
            raw.unlink(missing_ok=True)
        duration = media_duration(out)
        return SpeechResult(audio_path=out, duration=duration, words=estimate_from_audio(text, out, duration))

    def _wait_for(self, path: Path) -> None:
        """Some drivers finish writing the file shortly after runAndWait returns."""
        deadline = time.monotonic() + self.file_wait
        last_size = -1
        while time.monotonic() < deadline:
            size = path.stat().st_size if path.exists() else 0
            if size > 0 and size == last_size:
                return
            last_size = size
            time.sleep(0.1)
        if not path.exists() or path.stat().st_size == 0:
            raise AutoShortsError("pyttsx3 produced no audio file (is a system voice installed?)")
