"""Offline TTS with the espeak-ng (or classic espeak) command-line synthesizer.

Robotic but dependable and fully offline. espeak reports no word events, so
timings are estimated from the text and the silences in the produced WAV.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..models import SpeechResult
from ..utils import AutoShortsError, log, media_duration
from . import TTSEngine
from .timing import estimate_from_audio

INSTALL_HINT = (
    "Linux: sudo apt install espeak-ng | macOS: brew install espeak-ng | "
    "Windows: install espeak-ng from https://github.com/espeak-ng/espeak-ng/releases"
)


def find_espeak() -> str | None:
    """Path of espeak-ng/espeak, also checking the default Windows install folders."""
    for name in ("espeak-ng", "espeak"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform.startswith("win"):
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                exe = Path(base) / "eSpeak NG" / "espeak-ng.exe"
                if exe.is_file():
                    return str(exe)
    return None


class EspeakTTS(TTSEngine):
    name = "espeak"
    timeout = 120  # seconds per segment

    @classmethod
    def problem(cls) -> str | None:
        return None if find_espeak() else f"espeak-ng was not found ({INSTALL_HINT})"

    def synthesize(self, text: str, out_path: Path) -> SpeechResult:
        binary = find_espeak()
        if not binary:
            raise AutoShortsError(f"espeak-ng was not found ({INSTALL_HINT})")
        out = Path(out_path).with_suffix(".wav")
        out.parent.mkdir(parents=True, exist_ok=True)
        text_file = out.with_suffix(".txt")  # a file avoids argv quoting and leading '-' issues
        text_file.write_text(text, encoding="utf-8")
        ec = self.cfg.tts.espeak
        cmd = [binary, "-v", str(ec.voice), "-s", str(int(ec.speed)), "-b", "1", "-w", str(out), "-f", str(text_file)]
        log.debug("espeak: %s", " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise AutoShortsError(f"espeak timed out after {self.timeout}s") from exc
        finally:
            text_file.unlink(missing_ok=True)
        if proc.returncode != 0 or not out.exists() or out.stat().st_size <= 44:
            detail = (proc.stderr or proc.stdout or "").strip()[-500:]
            raise AutoShortsError(f"espeak failed (exit {proc.returncode}) for voice '{ec.voice}': {detail}")
        duration = media_duration(out)
        return SpeechResult(audio_path=out, duration=duration, words=estimate_from_audio(text, out, duration))
