"""Small helpers shared by every stage: ffmpeg/ffprobe, HTTP, filenames, logging."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Sequence

import requests

log = logging.getLogger("autoshorts")

USER_AGENT = "autoshorts/0.1 (+https://github.com/georgemallousis-design/cla)"


class AutoShortsError(RuntimeError):
    """Expected, user-facing failure (bad config, missing tool, API refused...)."""


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].rstrip("-")) or "video"


def require_binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise AutoShortsError(
            f"'{name}' was not found on PATH. Install FFmpeg (https://ffmpeg.org/download.html) "
            f"and make sure '{name}' runs from a terminal."
        )
    return found


def run_ffmpeg(args: Sequence[str | Path], *, desc: str = "ffmpeg", timeout: float | None = None) -> None:
    """Run ffmpeg with ``args`` (without the leading 'ffmpeg'). Raises AutoShortsError on failure."""
    cmd = [require_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    log.debug("%s: %s", desc, " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        raise AutoShortsError(f"{desc} failed (exit {proc.returncode}):\n{tail}")


def ffprobe_json(path: str | Path) -> dict:
    cmd = [
        require_binary("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AutoShortsError(f"ffprobe could not read {path}: {proc.stderr.strip()}")
    return json.loads(proc.stdout or "{}")


def media_duration(path: str | Path) -> float:
    """Duration in seconds of an audio/video file."""
    info = ffprobe_json(path)
    dur = info.get("format", {}).get("duration")
    if dur in (None, "N/A"):
        for stream in info.get("streams", []):
            if stream.get("duration") not in (None, "N/A"):
                dur = stream["duration"]
                break
    if dur in (None, "N/A"):
        raise AutoShortsError(f"could not determine duration of {path}")
    return float(dur)


def video_size(path: str | Path) -> tuple[int, int] | None:
    for stream in ffprobe_json(path).get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    return None


def http_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def http_get(session: requests.Session, url: str, *, retries: int = 3, timeout: float = 30, **kw) -> requests.Response:
    """GET with simple exponential backoff on network errors, 429 and 5xx."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=timeout, **kw)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            return resp
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    assert last is not None
    raise last


def download(session: requests.Session, url: str, dest: Path, *, timeout: float = 60) -> Path:
    """Stream ``url`` to ``dest`` atomically (writes to .part then renames)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with session.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)
    tmp.replace(dest)
    return dest


def hex_to_ass(color: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> ASS '&HAABBGGRR' (alpha 0 = opaque)."""
    c = color.lstrip("#")
    if len(c) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", c):
        raise ValueError(f"expected a #RRGGBB colour, got {color!r}")
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()
