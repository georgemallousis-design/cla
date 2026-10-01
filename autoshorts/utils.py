"""Small helpers shared by every stage: ffmpeg/ffprobe, HTTP, filenames, logging."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


STDERR_TAIL_BYTES = 64 * 1024  # how much of ffmpeg's stderr is kept for the error message
FFPROBE_TIMEOUT = 60.0
# Credentials some APIs take in the query string (Pixabay ``key``, TikTok ``upload_token``).
# requests puts the full URL into ConnectionError/Timeout texts, so mask them before logging.
_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:key|api_key|apikey|token|access_token|refresh_token|upload_token|client_secret)=)[^&\s'\")]+"
)


def redact(text: object) -> str:
    """``str(text)`` with credential query-string values replaced by ``***``."""
    return _SECRET_QUERY_RE.sub(r"\1***", str(text))


def run_ffmpeg(
    args: Sequence[str | Path],
    *,
    desc: str = "ffmpeg",
    timeout: float | None = None,
    cwd: str | Path | None = None,
) -> None:
    """Run ffmpeg with ``args`` (without the leading 'ffmpeg'). Raises AutoShortsError on failure.

    ``cwd`` runs ffmpeg inside that folder, so filter arguments can name files there by a
    bare relative name (avoids filter-path escaping trouble with Windows drive letters).
    ``timeout`` (seconds) kills ffmpeg and raises AutoShortsError, so a looped input that
    never decodes cannot hang a run. stderr goes to a temporary file (a broken input can
    print tens of MB of errors) and only its tail ends up in the error message.
    """
    cmd = [require_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    log.debug("%s: %s", desc, " ".join(cmd))
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                                  timeout=timeout, cwd=cwd)
        except subprocess.TimeoutExpired:
            raise AutoShortsError(f"{desc} timed out after {timeout:.0f}s (an input may be unreadable)") from None
        if proc.returncode == 0:
            return
        size = err.seek(0, os.SEEK_END)
        err.seek(max(0, size - STDERR_TAIL_BYTES))
        text = err.read().decode("utf-8", "replace")
    tail = "\n".join(text.strip().splitlines()[-15:])
    raise AutoShortsError(f"{desc} failed (exit {proc.returncode}):\n{tail}")


def ffprobe_json(path: str | Path) -> dict:
    cmd = [
        require_binary("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    # ffprobe writes UTF-8 (file names, tags) whatever the console code page is; the
    # locale default (cp1252/cp1253 on Windows) would crash on e.g. C:\Users\Γιώργος.
    try:
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace",
                              timeout=FFPROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise AutoShortsError(f"ffprobe timed out reading {path}") from None
    if proc.returncode != 0:
        raise AutoShortsError(f"ffprobe could not read {path}: {(proc.stderr or '').strip()[-500:]}")
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise AutoShortsError(f"ffprobe returned unreadable output for {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


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


def _seconds(value: object) -> float | None:
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def probe_video(path: str | Path) -> dict:
    """First video stream of a file: {"duration", "width", "height", "color_transfer"}.

    ``duration`` is the video stream's own length (the format duration is the longest
    stream's, so audio that outlasts the picture would overstate it), falling back to the
    format duration when the stream has none (e.g. webm/mkv); None for still images.
    Raises AutoShortsError when the file has no video stream (audio-only, broken).
    """
    info = ffprobe_json(path)
    stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if stream is None:
        raise AutoShortsError(f"{Path(path).name} has no video stream")
    duration = _seconds(stream.get("duration")) or _seconds(info.get("format", {}).get("duration"))
    return {
        "duration": duration,
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "color_transfer": str(stream.get("color_transfer") or ""),
    }


def video_frame_count(path: str | Path) -> int | None:
    """Number of packets in the first video stream (counted by ffprobe), None if there is none."""
    cmd = [
        require_binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets", "-of", "json", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=FFPROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise AutoShortsError(f"ffprobe timed out reading {path}") from None
    if proc.returncode != 0:
        raise AutoShortsError(f"ffprobe could not read {path}: {(proc.stderr or '').strip()[-300:]}")
    try:
        streams = json.loads(proc.stdout or "{}").get("streams") or []
        return int(streams[0]["nb_read_packets"]) if streams else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def decodes(path: str | Path, timeout: float = 30) -> bool:
    """True when ffmpeg can decode at least one video frame of ``path``.

    ffprobe only reads the container, so a clip with an intact header but zeroed or
    garbage video data probes fine and then never yields a frame (looped forever with
    ``-stream_loop -1``). ffmpeg exits non-zero when no frame could be decoded.
    """
    try:
        run_ffmpeg(["-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-"],
                   desc=f"decode check ({Path(path).name})", timeout=timeout)
    except AutoShortsError as exc:
        log.debug("%s", exc)
        return False
    return True


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


def touch(path: str | Path) -> None:
    """Mark a cached file as just used (its mtime decides what cache pruning keeps)."""
    try:
        os.utime(path)
    except OSError:
        pass


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


def update_env(path: str | Path, updates: dict[str, str]) -> None:
    """Set KEY=value lines in a .env file, keeping every other line.

    Written atomically with owner-only permissions (0600). When run as root on a file
    owned by someone else (``sudo``), the original owner is kept, so the service user
    can still read it.
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    pending = dict(updates)
    out: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        key = stripped.partition("=")[0].strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not stripped.startswith("#") and "=" in stripped and key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(raw)
    out.extend(f"{key}={value}" for key, value in pending.items())
    text = "\n".join(out) + "\n"

    owner = None
    if path.is_file() and hasattr(os, "geteuid") and os.geteuid() == 0:
        st = path.stat()
        owner = (st.st_uid, st.st_gid)
    folder = path.resolve().parent
    try:
        fd, tmp = tempfile.mkstemp(prefix=".env.", dir=folder)
    except PermissionError:
        if not path.is_file():
            raise
        # The folder belongs to someone else (deploy/install.sh keeps the code root-owned
        # and gives the service user only .env itself): rewrite the file in place.
        with open(path, "r+", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.truncate()
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - e.g. some network filesystems
            pass
        if owner is not None and sys.platform != "win32":
            os.chown(tmp, *owner)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
