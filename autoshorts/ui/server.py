"""Local web UI: ``autoshorts ui`` opens a dashboard at http://127.0.0.1:8765.

Standard library only (http.server). It shows every channel's videos (with a player and
copy-ready titles/descriptions), edits settings, topics and API keys, and starts videos
in a background process whose log streams to the page.

Security: it listens on 127.0.0.1 only, rejects requests whose Host is not localhost
(DNS rebinding) and requires a custom header on every write (a page on another site
cannot send it without a CORS preflight, which is never granted).
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from ..config import Config, load_config
from ..utils import AutoShortsError, log, update_env

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAIN_ID = "_main"  # the config.yaml in the folder the UI was started from
WRITE_HEADER = "X-Autoshorts"
ENV_KEYS = [
    ("LLM_API_KEY", "AI για σενάρια (Groq, δωρεάν)", "https://console.groq.com/keys"),
    ("PEXELS_API_KEY", "Πλάνα Pexels (δωρεάν)", "https://www.pexels.com/api/"),
    ("PIXABAY_API_KEY", "Πλάνα Pixabay (δωρεάν)", "https://pixabay.com/api/docs/"),
    ("TIKTOK_ACCESS_TOKEN", "TikTok access token", "https://developers.tiktok.com/"),
    ("TIKTOK_REFRESH_TOKEN", "TikTok refresh token", ""),
    ("TIKTOK_CLIENT_KEY", "TikTok client key", ""),
    ("TIKTOK_CLIENT_SECRET", "TikTok client secret", ""),
]
FORMATS = ["random", "facts", "story", "quiz", "motivation", "explainer", "reddit", "whatif", "mystery", "psychology"]

# Settings shown as a form: (dotted path, label, type, options)
FIELDS: list[tuple[str, str, str, list[str] | None]] = [
    ("channel.name", "Όνομα καναλιού (στην κάρτα)", "text", None),
    ("channel.avatar_color", "Χρώμα avatar", "color", None),
    ("script.format", "Είδος βίντεο", "select", FORMATS),
    ("script.provider", "Ποιος γράφει το σενάριο", "select", ["auto", "openai_compatible", "ollama", "offline"]),
    ("video.target_seconds", "Διάρκεια (δευτερόλεπτα)", "number", None),
    ("tts.edge.voice", "Φωνή (άντρας)", "voice", None),
    ("tts.edge.voice_female", "Φωνή (γυναίκα)", "voice", None),
    ("tts.edge.rate", "Ταχύτητα φωνής (π.χ. +10%)", "text", None),
    ("visuals.style", "Φόντο", "select", ["cuts", "continuous", "split"]),
    ("visuals.split_ratio", "Split: ύψος πάνω μέρους (0-1)", "number", None),
    ("captions.font_size", "Μέγεθος υποτίτλων", "number", None),
    ("captions.words_per_caption", "Λέξεις ανά υπότιτλο", "number", None),
    ("captions.position", "Θέση υποτίτλων (0 πάνω - 1 κάτω)", "number", None),
    ("captions.primary_color", "Χρώμα γραμμάτων", "color", None),
    ("captions.highlight_color", "Χρώμα λέξης που ακούγεται", "color", None),
    ("captions.uppercase", "Κεφαλαία", "bool", None),
    ("captions.card", "Κάρτα τύπου Reddit", "bool", None),
    ("captions.show_title", "Τίτλος στην αρχή", "bool", None),
    ("music.enabled", "Μουσική", "bool", None),
    ("music.volume", "Ένταση μουσικής (0-1)", "number", None),
    ("metadata.extra_hashtags", "Έξτρα hashtags (με κόμμα)", "list", None),
    ("topics.when_empty", "Όταν τελειώσουν τα θέματα", "select", ["builtin", "llm"]),
    ("upload.youtube.privacy", "YouTube ορατότητα", "select", ["private", "unlisted", "public"]),
    ("upload.tiktok.mode", "TikTok τρόπος", "select", ["inbox", "direct"]),
]


# --------------------------------------------------------------------------- data helpers


class Workspace:
    """The folder the UI runs in: ./config.yaml plus ./channels/<name>/config.yaml."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def channel_dirs(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        if (self.root / "config.yaml").is_file():
            found[MAIN_ID] = self.root
        chdir = self.root / "channels"
        if chdir.is_dir():
            for p in sorted(chdir.iterdir()):
                if p.is_dir() and (p / "config.yaml").is_file() and re.fullmatch(r"[\w.-]+", p.name):
                    found[p.name] = p
        return found

    def channel(self, cid: str) -> Path:
        dirs = self.channel_dirs()
        if cid not in dirs:
            raise KeyError(cid)
        return dirs[cid]

    def config(self, cid: str) -> Config:
        return load_config(self.channel(cid) / "config.yaml")


def get_path(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def set_in(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    for part in parts[:-1]:
        if not isinstance(data.get(part), dict):
            data[part] = {}
        data = data[part]
    data[parts[-1]] = value


def coerce(kind: str, value: Any, current: Any) -> Any:
    if kind == "bool":
        return bool(value)
    if kind == "number":
        num = float(value)
        return int(num) if isinstance(current, int) and not isinstance(current, bool) and num.is_integer() else num
    if kind == "list":
        items = value if isinstance(value, list) else str(value).split(",")
        return [str(x).strip().lstrip("#") for x in items if str(x).strip()]
    return str(value)


def save_settings(config_path: Path, changes: dict[str, Any]) -> None:
    """Write ``changes`` (dotted path -> value) into config.yaml, keeping a .bak copy."""
    raw = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    data = yaml.safe_load(raw) or {}
    current = load_config(config_path)
    kinds = {path: kind for path, _, kind, _ in FIELDS}
    for path, value in changes.items():
        if path not in kinds:
            raise ValueError(f"unknown setting {path}")
        set_in(data, path, coerce(kinds[path], value, get_path(current, path)))
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    tmp = config_path.with_suffix(".yaml.tmp")
    tmp.write_text("# Saved by the autoshorts UI (previous version: config.yaml.bak)\n" + text, encoding="utf-8")
    load_config(tmp)  # refuse to save something that does not load
    if config_path.is_file():
        shutil.copyfile(config_path, config_path.with_suffix(".yaml.bak"))
    tmp.replace(config_path)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def list_videos(cfg: Config) -> list[dict[str, Any]]:
    out_dir = cfg.path(cfg.output_dir)
    if not out_dir.is_dir():
        return []
    videos = []
    for folder in sorted((p for p in out_dir.iterdir() if p.is_dir()), reverse=True):
        job = read_json(folder / "job.json")
        script = job.get("script") or read_json(folder / "script.json")
        meta = read_json(folder / "metadata.json")
        if not job and not script:
            continue
        videos.append({
            "id": folder.name,
            "title": (script or {}).get("title") or job.get("topic") or folder.name,
            "format": (script or {}).get("format", ""),
            "status": job.get("status", "?"),
            "created": job.get("created_at", ""),
            "duration": ((job.get("render") or {}).get("duration") or 0),
            "has_video": (folder / "video.mp4").is_file(),
            "has_thumb": (folder / "thumbnail.jpg").is_file(),
            "uploads": job.get("uploads") or [],
            "error": (folder / "error.txt").read_text(encoding="utf-8", errors="replace")[-1500:]
            if (folder / "error.txt").is_file() else "",
            "youtube": meta.get("youtube", {}),
            "tiktok": meta.get("tiktok", {}),
            "path": str(folder),
        })
    return videos


def count_files(folder: Path, exts: tuple[str, ...]) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for p in folder.rglob("*") if p.suffix.lower() in exts)


# --------------------------------------------------------------------------- background runs


class Runner:
    """Runs one autoshorts command at a time in a child process and keeps its log."""

    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.lines: list[str] = []
        self.label = ""
        self.started = 0.0
        self.exit_code: int | None = None

    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, args: list[str], label: str) -> None:
        with self.lock:
            if self.busy():
                raise RuntimeError("κάτι τρέχει ήδη· περίμενε να τελειώσει")
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            # The child must import this same autoshorts, even when it is not pip-installed.
            pkg_parent = str(Path(__file__).resolve().parents[2])
            env["PYTHONPATH"] = os.pathsep.join(p for p in (pkg_parent, env.get("PYTHONPATH", "")) if p)
            self.lines, self.label, self.started, self.exit_code = [], label, time.time(), None
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "autoshorts", *args], cwd=self.root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            )
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self.lines.append(line.rstrip("\n"))
            if len(self.lines) > 3000:
                del self.lines[:1000]
        self.exit_code = proc.wait()

    def stop(self) -> None:
        if self.busy() and self.proc is not None:
            self.proc.terminate()

    def state(self, since: int = 0) -> dict[str, Any]:
        return {
            "running": self.busy(), "label": self.label, "exit_code": self.exit_code,
            "elapsed": round(time.time() - self.started) if self.started else 0,
            "lines": self.lines[since:], "next": len(self.lines),
        }


# --------------------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "autoshorts-ui"
    ws: Workspace
    runner: Runner
    port: int

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet console
        log.debug("ui: " + fmt, *args)

    # -- plumbing

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _send(self, code: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, code: int, msg: str) -> None:
        self._json({"error": msg}, code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._error(403, "forbidden host")
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            if not parts:
                return self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            if parts[0] == "media" and len(parts) == 4:
                return self._media(parts[1], parts[2], parts[3])
            if parts[0] == "api":
                return self._json(self._api_get(parts[1:], parse_qs(url.query)))
            return self._error(404, "not found")
        except KeyError as exc:
            return self._error(404, f"not found: {exc}")
        except (AutoShortsError, ValueError, OSError) as exc:
            return self._error(400, str(exc))

    def do_POST(self) -> None:
        if not self._host_ok():
            return self._error(403, "forbidden host")
        if self.headers.get(WRITE_HEADER) != "1":
            return self._error(403, "missing header")
        parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
        try:
            if not parts or parts[0] != "api":
                return self._error(404, "not found")
            return self._json(self._api_post(parts[1:], self._body()))
        except KeyError as exc:
            return self._error(404, f"not found: {exc}")
        except RuntimeError as exc:
            return self._error(409, str(exc))
        except (AutoShortsError, ValueError, OSError) as exc:
            return self._error(400, str(exc))

    # -- API

    def _api_get(self, parts: list[str], query: dict[str, list[str]]) -> Any:
        ws = self.ws
        if parts == ["status"]:
            return self._status()
        if parts == ["channels"]:
            return [self._channel_summary(cid) for cid in ws.channel_dirs()]
        if parts == ["env"]:
            return self._env()
        if parts == ["run"]:
            return self.runner.state(int((query.get("since") or ["0"])[0]))
        if len(parts) >= 3 and parts[0] == "channel":
            cid, what = parts[1], parts[2]
            cfg = ws.config(cid)
            if what == "videos":
                return list_videos(cfg)
            if what == "settings":
                return {"fields": [
                    {"path": p, "label": label, "type": kind, "options": opts, "value": get_path(cfg, p)}
                    for p, label, kind, opts in FIELDS
                ]}
            if what == "topics":
                from ..topics import TopicQueue

                queue = TopicQueue(cfg)
                text = queue.file.read_text(encoding="utf-8") if queue.file.is_file() else ""
                return {"text": text, "unused": queue.unused(), "file": str(queue.file)}
        raise KeyError("/".join(parts))

    def _api_post(self, parts: list[str], body: dict) -> Any:
        ws = self.ws
        if parts == ["env"]:
            updates = {k: str(v).strip() for k, v in body.items() if k in {e[0] for e in ENV_KEYS} and str(v).strip()}
            if updates:
                update_env(ws.root / ".env", updates)
                os.environ.update(updates)
            return {"saved": sorted(updates)}
        if parts == ["run-all"]:
            args = ["run-all", "-n", str(int(body.get("count") or 1))] + self._upload_args(body)
            self.runner.start(args, "Όλα τα κανάλια")
            return {"started": True}
        if parts == ["stop"]:
            self.runner.stop()
            return {"stopped": True}
        if parts == ["channels-init"]:
            from .. import channels

            created = channels.init_channels(ws.root / channels.CHANNELS_DIR, body.get("names") or [])
            return {"created": [str(p) for p in created]}
        if parts == ["schedule"]:
            return self._schedule(body)
        if len(parts) == 3 and parts[0] == "channel":
            cid, what = parts[1], parts[2]
            folder = ws.channel(cid)
            cfg_path = folder / "config.yaml"
            if what == "settings":
                save_settings(cfg_path, body.get("changes") or {})
                return {"saved": True}
            if what == "topics":
                from ..topics import TopicQueue

                queue = TopicQueue(ws.config(cid))
                queue.file.parent.mkdir(parents=True, exist_ok=True)
                queue.file.write_text(str(body.get("text", "")), encoding="utf-8")
                return {"saved": True}
            if what == "make":
                args = ["--config", str(cfg_path), "make"]
                if str(body.get("topic") or "").strip():
                    args += ["--topic", str(body["topic"]).strip()[:300]]
                if body.get("format") and body["format"] in FORMATS:
                    args += ["--format", body["format"]]
                self.runner.start(args + self._upload_args(body), f"Νέο βίντεο: {self._label(cid)}")
                return {"started": True}
            if what == "upload":
                job = self._job_dir(cid, str(body.get("job", "")))
                platforms = ",".join(p for p in body.get("platforms", []) if p in ("youtube", "tiktok"))
                if not platforms:
                    raise ValueError("διάλεξε πλατφόρμα")
                self.runner.start(["--config", str(cfg_path), "upload", str(job), "--to", platforms],
                                  f"Ανέβασμα σε {platforms}")
                return {"started": True}
            if what == "delete":
                job = self._job_dir(cid, str(body.get("job", "")))
                shutil.rmtree(job)
                return {"deleted": job.name}
        raise KeyError("/".join(parts))

    @staticmethod
    def _upload_args(body: dict) -> list[str]:
        platforms = ",".join(p for p in body.get("upload", []) if p in ("youtube", "tiktok"))
        return ["--upload", platforms] if platforms else ["--no-upload"]

    def _label(self, cid: str) -> str:
        try:
            return self.ws.config(cid).channel.name or cid
        except Exception:
            return cid

    def _job_dir(self, cid: str, job: str) -> Path:
        cfg = self.ws.config(cid)
        out_dir = cfg.path(cfg.output_dir).resolve()
        if not re.fullmatch(r"[\w.-]+", job):
            raise ValueError("bad video id")
        path = (out_dir / job).resolve()
        if path.parent != out_dir or not path.is_dir():
            raise KeyError(job)
        return path

    def _media(self, cid: str, job: str, name: str) -> None:
        if name not in ("video.mp4", "thumbnail.jpg"):
            return self._error(404, "not found")
        path = self._job_dir(cid, job) / name
        if not path.is_file():
            return self._error(404, "not found")
        size = path.stat().st_size
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        start, end = 0, size - 1
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
            else:  # suffix range: last N bytes
                start = max(0, size - int(rng.group(2)))
            if start > end or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        length = end - start + 1
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            try:
                while remaining > 0:
                    chunk = fh.read(min(1 << 16, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser seeked or closed the player

    def _channel_summary(self, cid: str) -> dict[str, Any]:
        try:
            cfg = self.ws.config(cid)
            from ..topics import TopicQueue

            videos = list_videos(cfg)
            return {
                "id": cid, "name": cfg.channel.name or ("Κύριο" if cid == MAIN_ID else cid),
                "format": cfg.script.format, "style": cfg.visuals.style,
                "topics_left": len(TopicQueue(cfg).unused()), "videos": len(videos),
                "failed": sum(1 for v in videos if v["status"] == "failed"),
            }
        except Exception as exc:  # show a broken channel instead of hiding it
            return {"id": cid, "name": cid, "error": str(exc)}

    def _env(self) -> list[dict[str, Any]]:
        load_config(self.ws.root / "config.yaml") if (self.ws.root / "config.yaml").is_file() else None
        out = []
        for key, label, link in ENV_KEYS:
            value = Config.secret(key) or ""
            out.append({"key": key, "label": label, "link": link, "set": bool(value),
                        "masked": (value[:3] + "…" + value[-2:]) if len(value) > 8 else ("•••" if value else "")})
        return out

    def _status(self) -> dict[str, Any]:
        root = self.ws.root
        checks = []

        def add(name: str, ok: bool, detail: str, level: str = "warn") -> None:
            checks.append({"name": name, "ok": ok, "detail": detail, "level": "ok" if ok else level})

        add("FFmpeg", bool(shutil.which("ffmpeg")), shutil.which("ffmpeg") or "δεν βρέθηκε", "fail")
        env = {e["key"]: e["set"] for e in self._env()}
        add("AI σεναρίων (LLM_API_KEY)", env["LLM_API_KEY"], "ok" if env["LLM_API_KEY"] else
            "λείπει: μόνο λίγα έτοιμα σενάρια")
        add("Πλάνα (Pexels/Pixabay)", env["PEXELS_API_KEY"] or env["PIXABAY_API_KEY"],
            "ok" if env["PEXELS_API_KEY"] or env["PIXABAY_API_KEY"] else "λείπει: αφηρημένα φόντα")
        gameplay = count_files(root / "assets" / "gameplay", (".mp4", ".mov", ".mkv", ".webm", ".m4v"))
        add("Gameplay", gameplay > 0, f"{gameplay} βίντεο στο assets/gameplay")
        music = count_files(root / "assets" / "music", (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"))
        add("Μουσική", music > 0, f"{music} κομμάτια στο assets/music")
        return {"checks": checks, "root": str(root), "windows": os.name == "nt",
                "has_channels": bool([c for c in self.ws.channel_dirs() if c != MAIN_ID])}

    def _schedule(self, body: dict) -> dict[str, Any]:
        if os.name != "nt":
            raise ValueError("ο αυτόματος προγραμματισμός από το UI γίνεται μόνο στα Windows")
        script = self.ws.root / "deploy" / "windows" / "schedule.ps1"
        if not script.is_file():
            raise ValueError(f"δεν βρέθηκε το {script}")
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        if body.get("remove"):
            args.append("-Remove")
        else:
            at = str(body.get("at") or "18:00")
            if not re.fullmatch(r"\d{1,2}:\d{2}", at):
                raise ValueError("ώρα σε μορφή 18:00")
            args += ["-AllChannels", "-At", at]
            platforms = ",".join(p for p in body.get("upload", []) if p in ("youtube", "tiktok"))
            if platforms:
                args += ["-Upload", platforms]
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=self.ws.root)
        return {"ok": proc.returncode == 0, "output": (proc.stdout + proc.stderr)[-3000:]}


def serve(root: Path, port: int = 8765, open_browser: bool = True) -> None:
    ws = Workspace(root)
    handler = type("BoundHandler", (Handler,), {"ws": ws, "runner": Runner(ws.root), "port": port})
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        raise AutoShortsError(f"could not start the UI on port {port} ({exc}); try --port 8766") from exc
    url = f"http://127.0.0.1:{port}/"
    print(f"autoshorts UI: {url}   (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        handler.runner.stop()
        httpd.server_close()


__all__ = ["serve", "Workspace", "save_settings", "list_videos", "FIELDS"]
