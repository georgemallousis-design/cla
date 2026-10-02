"""Several channels from one install: one folder per channel under ./channels/.

Each channel folder has its own config.yaml (niche, format, voice, captions...), topics.txt,
state/, output/ and secrets/ (its YouTube login), plus an optional .env for that channel's
TikTok account. Shared things (API keys in ./.env, assets/, cache/) stay in the main folder.

``autoshorts channels init`` copies the built-in presets (autoshorts/data/channels/) and
``autoshorts run-all`` makes videos for every channel, each in its own process so one
channel's settings and secrets never leak into another's.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from .utils import AutoShortsError, log

PRESETS_DIR = Path(__file__).resolve().parent / "data" / "channels"
CHANNELS_DIR = "channels"


def presets() -> list[str]:
    return sorted(p.name for p in PRESETS_DIR.iterdir() if (p / "config.yaml").is_file())


def channel_dirs(root: Path) -> list[Path]:
    """Channel folders (those with a config.yaml) under ``root``, sorted by name."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "config.yaml").is_file())


def init_channels(root: Path, names: Sequence[str] = ()) -> list[Path]:
    """Copy preset channels into ``root``; existing files are never overwritten."""
    available = presets()
    wanted = list(names) or available
    unknown = [n for n in wanted if n not in available]
    if unknown:
        raise AutoShortsError(f"unknown channel preset(s): {', '.join(unknown)}; available: {', '.join(available)}")
    created: list[Path] = []
    for name in wanted:
        dest = root / name
        dest.mkdir(parents=True, exist_ok=True)
        for src in (PRESETS_DIR / name).iterdir():
            target = dest / src.name
            if src.is_file() and not target.exists():
                shutil.copyfile(src, target)
                created.append(target)
    return created


def select(root: Path, only: Sequence[str] = ()) -> list[Path]:
    dirs = channel_dirs(root)
    if not dirs:
        raise AutoShortsError(f"no channels in {root}; create them with 'autoshorts channels init'")
    if only:
        names = {d.name: d for d in dirs}
        missing = [n for n in only if n not in names]
        if missing:
            raise AutoShortsError(f"no such channel(s): {', '.join(missing)}; have: {', '.join(names)}")
        dirs = [names[n] for n in only]
    return dirs


def batch_command(channel: Path, count: int, upload: str | None, no_upload: bool, verbose: bool) -> list[str]:
    cmd = [sys.executable, "-m", "autoshorts", "--config", str(channel / "config.yaml")]
    if verbose:
        cmd.append("-v")
    cmd += ["batch", "-n", str(count)]
    if no_upload:
        cmd.append("--no-upload")
    elif upload:
        cmd += ["--upload", upload]
    return cmd


def run_all(root: Path, count: int = 1, *, only: Sequence[str] = (), upload: str | None = None,
            no_upload: bool = False, verbose: bool = False) -> dict[str, int]:
    """Run ``batch -n count`` for each channel, one after another. Returns exit codes by name.

    A failing channel never stops the others.
    """
    results: dict[str, int] = {}
    for channel in select(root, only):
        log.info("===== channel %s =====", channel.name)
        cmd = batch_command(channel, count, upload, no_upload, verbose)
        try:
            # cwd stays the main folder so the shared .env, assets/ and cache/ are found.
            results[channel.name] = subprocess.run(cmd, check=False).returncode
        except OSError as exc:
            log.error("channel %s could not start: %s", channel.name, exc)
            results[channel.name] = 2
        if results[channel.name] not in (0,):
            log.warning("channel %s finished with exit code %d", channel.name, results[channel.name])
    return results
