"""Command line interface: ``autoshorts <command>`` (or ``python -m autoshorts``).

Run ``autoshorts --help`` for the commands and ``autoshorts <command> --help`` for their
options. Stage modules are imported inside the command handlers, so ``--help`` and
``doctor`` keep working even when a stage cannot be imported.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import logging
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml

from . import __version__
from .config import Config, load_config
from .utils import AutoShortsError, ensure_dir, log, setup_logging

EXIT_OK = 0
EXIT_FAIL = 1  # doctor found a FAIL
EXIT_ERROR = 2  # AutoShortsError / bad arguments
EXIT_PARTIAL = 3  # video made, but an upload (or some batch videos) failed
EXIT_INTERRUPTED = 130

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__file__).resolve().parent / "data"
FORMAT_HELP = "facts, story, quiz, motivation, explainer or random (default: script.format in config)"
PLATFORM_HELP = "comma-separated: youtube,tiktok"

EXIT_CODES = """\
exit codes:
  0    success
  1    doctor found a problem marked FAIL (or an unexpected crash)
  2    error: bad config or arguments, missing tool, the video failed
  3    the video was made, but an upload (or some videos of a batch) failed
  130  interrupted with Ctrl+C
"""
EPILOG = """\
typical first run:
  autoshorts init      create config.yaml, .env and topics.txt here
  autoshorts doctor    check FFmpeg, fonts, voices, API keys
  autoshorts make      make one video into ./output/

""" + EXIT_CODES


# --------------------------------------------------------------------------- helpers


def _setup_logging(level: str) -> None:
    setup_logging(level)
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.getLogger().setLevel(numeric)
    for noisy in ("urllib3", "asyncio", "googleapiclient.discovery_cache", "websockets"):
        logging.getLogger(noisy).setLevel(max(numeric, logging.INFO))


def _load(args: argparse.Namespace) -> Config:
    """Load the config named by --config (or ./config.yaml) and set up logging."""
    try:
        cfg = load_config(args.config)
    except FileNotFoundError as exc:
        raise AutoShortsError(f"{exc} (create one with: autoshorts init)") from None
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        raise AutoShortsError(f"could not load the config: {exc}") from exc
    _setup_logging("DEBUG" if args.verbose else cfg.log_level)
    return cfg


def _platforms(value: str | None, cfg: Config, no_upload: bool = False) -> tuple[str, ...]:
    from .pipeline import default_platforms, parse_platforms

    if no_upload:
        return ()
    return default_platforms(cfg) if value is None else parse_platforms(value)


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def mask(secret: str | None) -> str:
    """Show that a secret is set without revealing it."""
    if not secret:
        return ""
    return f"set (...{secret[-4:]})" if len(secret) >= 16 else "set"


def _print_uploads(results: Sequence[Any]) -> None:
    for r in results:
        if r.ok:
            print(f"  {r.platform + ':':9} uploaded {r.url or r.id}")
        else:
            print(f"  {r.platform + ':':9} FAILED: {r.error}")


def _print_job(job: Any) -> None:
    render = job.render
    print(f"Video ready: {job.folder}")
    if render is not None:
        print(f"  {'video:':9} {render.video_path} ({render.duration:.1f}s)")
    if job.script is not None:
        print(f"  {'title:':9} {job.script.title}")
    _print_uploads(job.uploads)


# --------------------------------------------------------------------------- init


def _template(name: str) -> str | None:
    """Text of a template shipped next to the package (repo root) or in package data."""
    for folder in (REPO_ROOT, DATA_DIR):
        path = folder / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


def default_config_yaml() -> str:
    """config.yaml content generated from the built-in defaults."""
    data = dataclasses.asdict(Config())
    data.pop("base_dir", None)
    header = (
        "# autoshorts configuration (generated from the built-in defaults).\n"
        "# Relative paths are relative to this file. Secrets (API keys, tokens) go in .env,\n"
        "# never here: the *_env settings only name the environment variable to read.\n"
        "# Delete any setting to fall back to its default.\n\n"
    )
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def default_env() -> str:
    cfg = Config()
    names = [
        (cfg.visuals.pexels_api_key_env, "free stock videos: https://www.pexels.com/api/"),
        (cfg.visuals.pixabay_api_key_env, "free stock videos: https://pixabay.com/api/docs/"),
        (cfg.script.openai_compatible.api_key_env,
         "free LLM key, e.g. Groq https://console.groq.com/keys (matches script.openai_compatible)"),
        (cfg.upload.tiktok.access_token_env, "TikTok Content Posting API user access token"),
    ]
    lines = ["# autoshorts secrets. Keep this file private; never commit it.", ""]
    for env, about in names:
        lines += [f"# {about}", f"{env}=", ""]
    return "\n".join(lines)


def default_topics() -> str:
    return (
        "# One video topic per line. Lines starting with '#' are ignored.\n"
        "# Topics are used from top to bottom; used ones are remembered in state/used_topics.json.\n"
        "# When this file runs out, autoshorts picks from its built-in list of ideas.\n"
        "# Examples:\n"
        "# Why octopuses have three hearts\n"
        "# How the Great Pyramid of Giza was built\n"
    )


def cmd_init(args: argparse.Namespace) -> int:
    _setup_logging("DEBUG" if args.verbose else "INFO")
    cfg_path = Path(args.config) if args.config else Path("config.yaml")
    folder = ensure_dir(cfg_path.resolve().parent)
    plan: list[tuple[Path, str, Callable[[], str]]] = [
        (folder / cfg_path.name, "config.example.yaml", default_config_yaml),
        (folder / ".env", ".env.example", default_env),
        (folder / "topics.txt", "topics.example.txt", default_topics),
    ]
    for target, template, fallback in plan:
        if target.exists():
            print(f"skipped {target} (already exists)")
            continue
        text = _template(template)
        source = template if text is not None else "built-in defaults"
        target.write_text(text if text is not None else fallback(), encoding="utf-8")
        print(f"created {target} (from {source})")

    cfg = load_config(folder / cfg_path.name)
    for sub in (cfg.visuals.local_dir, cfg.music.dir, cfg.captions.fonts_dir):
        ensure_dir(cfg.path(sub))
    print(
        "\nNext steps:\n"
        "  1. Optional free keys in .env: PEXELS_API_KEY / PIXABAY_API_KEY (stock footage),\n"
        "     LLM_API_KEY (Groq or Gemini free tier) - or install Ollama for local scripts.\n"
        f"  2. Your own topics in {cfg.path(cfg.topics.file).name} (one per line), music in "
        f"{cfg.music.dir}/, background clips in {cfg.visuals.local_dir}/.\n"
        "  3. autoshorts doctor\n"
        "  4. autoshorts make"
    )
    return EXIT_OK


# --------------------------------------------------------------------------- doctor


@dataclass
class Check:
    status: str  # OK | WARN | FAIL
    name: str
    detail: str
    hint: str = ""


def _ffmpeg_install_hint() -> str:
    if sys.platform.startswith("win"):
        return "install FFmpeg: winget install Gyan.FFmpeg (then open a new terminal)"
    if sys.platform == "darwin":
        return "install FFmpeg: brew install ffmpeg"
    return "install FFmpeg: sudo apt install ffmpeg (or your distro's package manager)"


def _run_text(cmd: list[str], timeout: float = 20) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    return (proc.stdout or "") + (proc.stderr or "")


def check_python() -> list[Check]:
    ok = sys.version_info >= (3, 10)
    return [Check("OK" if ok else "FAIL", "python", platform.python_version(),
                  "" if ok else "install Python 3.10 or newer")]


def check_ffmpeg() -> list[Check]:
    rows: list[Check] = []
    found: dict[str, str | None] = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    for name, path in found.items():
        if not path:
            rows.append(Check("FAIL", name, "not found on PATH", _ffmpeg_install_hint()))
            continue
        first = _run_text([path, "-version"]).strip().splitlines()
        version = first[0] if first else "unknown version"
        rows.append(Check("OK", name, re.sub(r"\s+Copyright.*$", "", version)))
    ffmpeg = found["ffmpeg"]
    if not ffmpeg:
        return rows
    filters = _run_text([ffmpeg, "-hide_banner", "-filters"])
    if re.search(r"^\s*\S*\s+subtitles\s", filters, re.MULTILINE):
        rows.append(Check("OK", "libass", "subtitles filter available"))
    else:
        rows.append(Check("FAIL", "libass", "FFmpeg has no 'subtitles' filter (captions need libass)",
                          "install an FFmpeg build with libass (the official full/static builds have it)"))
    encoders = _run_text([ffmpeg, "-hide_banner", "-encoders"])
    if re.search(r"\blibx264\b", encoders):
        rows.append(Check("OK", "libx264", "H.264 encoder available"))
    else:
        rows.append(Check("FAIL", "libx264", "FFmpeg has no libx264 encoder",
                          "install an FFmpeg build with libx264 (the official full/static builds have it)"))
    return rows


def check_font(cfg: Config) -> list[Check]:
    from .captions import resolve_font

    font = resolve_font(cfg)
    extra = cfg.path(cfg.captions.fonts_dir)
    detail = font + (" (configured)" if cfg.captions.font != "auto" else " (auto)")
    if extra.is_dir() and any(p.suffix.lower() in (".ttf", ".otf", ".ttc") for p in extra.iterdir()):
        detail += f"; extra fonts in {cfg.captions.fonts_dir}"
    return [Check("OK", "caption font", detail)]


def _tts_problems() -> dict[str, str | None]:
    try:
        from .tts import check_engines

        return check_engines()
    except Exception as exc:  # fall back to cheap local checks
        log.debug("tts.check_engines failed: %s", exc)
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        return {
            "edge": None if importlib.util.find_spec("edge_tts") else "pip install edge-tts",
            "pyttsx3": None if importlib.util.find_spec("pyttsx3") else "pip install pyttsx3",
            "espeak": None if espeak else "install espeak-ng",
        }


def check_tts(cfg: Config) -> list[Check]:
    problems = _tts_problems()
    provider = (cfg.tts.provider or "auto").strip().lower()
    try:
        from .tts import auto_order

        used_by_auto = auto_order()
    except Exception:
        used_by_auto = list(problems)
    rows: list[Check] = []
    for name, problem in problems.items():
        relevant = name == provider or (provider == "auto" and name in used_by_auto)
        if problem is None:
            detail = {"edge": f"ready; voice {cfg.tts.edge.voice} (needs internet while making videos)"}.get(name, "ready")
            if provider == "auto" and name not in used_by_auto:
                detail += " (not used by tts.provider auto on this OS)"
            rows.append(Check("OK", f"tts: {name}", detail))
        else:
            status = "FAIL" if name == provider else ("WARN" if relevant else "OK")
            detail = f"not available: {problem}" if status != "OK" else f"not installed (optional): {problem}"
            rows.append(Check(status, f"tts: {name}", detail, problem if status != "OK" else ""))
    if provider == "auto" and all(problems.get(n) for n in used_by_auto):
        rows.append(Check("FAIL", "tts", "no text-to-speech engine can run here",
                          "pip install edge-tts (online voices) or install espeak-ng (offline)"))
    return rows


def check_llm(cfg: Config) -> list[Check]:
    from .script import get_generator

    gen = get_generator(cfg)
    chain = [g.name for g in getattr(gen, "generators", [gen])]
    models = {
        "ollama": cfg.script.ollama.model,
        "openai_compatible": f"{cfg.script.openai_compatible.model} @ {cfg.script.openai_compatible.base_url}",
    }
    detail = chain[0] + (f" ({models[chain[0]]})" if chain[0] in models else "")
    if len(chain) > 1:
        detail += f"; fallback: {', '.join(chain[1:])}"
    if chain[0] == "offline":
        return [Check("WARN", "script writer", "offline content bank only (a limited set of scripts)",
                      f"for unlimited scripts: set {cfg.script.openai_compatible.api_key_env} in .env "
                      "(free: https://console.groq.com/keys) or install Ollama (https://ollama.com) "
                      f"and run 'ollama pull {cfg.script.ollama.model}'")]
    return [Check("OK", "script writer", detail)]


def _count_media(folder: Path) -> int:
    try:
        from .visuals.local import scan_media

        return len(scan_media(folder))
    except ImportError:
        exts = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".jpg", ".jpeg", ".png", ".webp"}
        return sum(1 for p in folder.rglob("*") if p.suffix.lower() in exts) if folder.is_dir() else 0


def check_visuals(cfg: Config) -> list[Check]:
    rows: list[Check] = []
    keys = {
        "pexels": (cfg.visuals.pexels_api_key_env, "https://www.pexels.com/api/"),
        "pixabay": (cfg.visuals.pixabay_api_key_env, "https://pixabay.com/api/docs/"),
    }
    have_stock = False
    for name, (env, url) in keys.items():
        value = Config.secret(env)
        in_use = name in [str(p).strip().lower() for p in cfg.visuals.providers]
        if value:
            have_stock = have_stock or in_use
            rows.append(Check("OK", f"{name} key", f"{env} {mask(value)}" + ("" if in_use else " (provider not enabled)")))
        else:
            rows.append(Check("WARN" if in_use else "OK", f"{name} key", f"{env} not set",
                              f"free key: {url} -> put {env}=... in .env" if in_use else ""))
    local = cfg.path(cfg.visuals.local_dir)
    count = _count_media(local)
    rows.append(Check("OK", "local backgrounds", f"{count} file(s) in {local}",
                      "" if count else f"drop your own vertical clips/images into {cfg.visuals.local_dir}/"))
    if not have_stock and not count:
        enabled = [str(p).strip().lower() for p in cfg.visuals.providers]
        # with pexels enabled, its key row above already says how to get a key
        hint = "" if "pexels" in enabled else (
            f"for real stock footage: free key at https://www.pexels.com/api/ -> "
            f'{cfg.visuals.pexels_api_key_env}=... in .env, and add "pexels" to visuals.providers'
        )
        rows.append(Check("WARN", "backgrounds",
                          "no stock keys and no local clips: only generated (abstract) backgrounds", hint))
    return rows


def check_music(cfg: Config) -> list[Check]:
    if not cfg.music.enabled:
        return [Check("OK", "music", "disabled (music.enabled: false)")]
    from .music import list_tracks

    count = len(list_tracks(cfg))
    if count:
        return [Check("OK", "music", f"{count} track(s) in {cfg.path(cfg.music.dir)}")]
    return [Check("WARN", "music", f"no audio files in {cfg.path(cfg.music.dir)} (videos will have voice only)",
                  f"add royalty-free tracks (e.g. YouTube Audio Library) to {cfg.music.dir}/")]


def check_uploads(cfg: Config) -> list[Check]:
    rows: list[Check] = []
    yt = cfg.upload.youtube
    libs = all(importlib.util.find_spec(m) for m in ("googleapiclient", "google_auth_oauthlib"))
    secrets, token = cfg.path(yt.client_secrets), cfg.path(yt.token_file)
    missing = []
    if not libs:
        missing.append("pip install 'autoshorts[youtube]'")
    if not secrets.is_file():
        missing.append(f"OAuth client JSON at {yt.client_secrets}")
    if not token.is_file():
        missing.append("run 'autoshorts auth youtube'")
    if not missing:
        rows.append(Check("OK", "youtube upload", f"ready (token {yt.token_file}, privacy {yt.privacy})"
                          + ("" if yt.enabled else "; upload.youtube.enabled is false")))
    else:
        status = "WARN" if yt.enabled else "OK"
        detail = "not set up" + ("" if yt.enabled else " (optional)")
        rows.append(Check(status, "youtube upload", detail, "for YouTube upload: " + "; ".join(missing)))

    tt = cfg.upload.tiktok
    token_value = Config.secret(tt.access_token_env)
    if token_value:
        rows.append(Check("OK", "tiktok upload", f"{tt.access_token_env} {mask(token_value)}, mode {tt.mode}"
                          + ("" if tt.enabled else "; upload.tiktok.enabled is false")))
    else:
        rows.append(Check("WARN" if tt.enabled else "OK", "tiktok upload",
                          "not set up" + ("" if tt.enabled else " (optional)"),
                          f"for TikTok upload: put a Content Posting API access token in .env as {tt.access_token_env}"))
    return rows


def check_files(cfg: Config, config_arg: str | None) -> list[Check]:
    rows: list[Check] = []
    cfg_file = Path(config_arg) if config_arg else Path("config.yaml")
    if cfg_file.is_file():
        rows.append(Check("OK", "config", str(cfg_file.resolve())))
    else:
        rows.append(Check("WARN", "config", "no config.yaml here; using the built-in defaults",
                          "autoshorts init (creates config.yaml, .env and topics.txt)"))
    out = cfg.path(cfg.output_dir)
    try:
        ensure_dir(out)
        with tempfile.TemporaryFile(dir=out):
            pass
        rows.append(Check("OK", "output folder", str(out)))
    except OSError as exc:
        rows.append(Check("FAIL", "output folder", f"{out} is not writable: {exc}", "fix output_dir in config.yaml"))

    from .topics import TopicQueue, builtin_ideas

    queue = TopicQueue(cfg)
    mine = queue.topics()
    left = len(queue.unused())
    ideas_left = len(queue.unused(include_builtin=True)) - left
    detail = f"{left} of {len(mine)} unused in {queue.file.name}; {ideas_left} of {len(builtin_ideas())} built-in ideas left"
    if left or ideas_left or cfg.topics.allow_repeats:
        rows.append(Check("OK", "topics", detail))
    else:
        rows.append(Check("WARN", "topics", detail + " (all used)",
                          "add topics: autoshorts topics add \"your topic\" (or set topics.allow_repeats: true)"))
    return rows


def run_checks(cfg: Config, config_arg: str | None) -> list[Check]:
    checks: list[tuple[str, Callable[[], list[Check]]]] = [
        ("python", check_python),
        ("ffmpeg", check_ffmpeg),
        ("caption font", lambda: check_font(cfg)),
        ("tts", lambda: check_tts(cfg)),
        ("script writer", lambda: check_llm(cfg)),
        ("visuals", lambda: check_visuals(cfg)),
        ("music", lambda: check_music(cfg)),
        ("uploads", lambda: check_uploads(cfg)),
        ("files", lambda: check_files(cfg, config_arg)),
    ]
    rows: list[Check] = []
    for name, fn in checks:
        try:
            rows.extend(fn())
        except Exception as exc:  # doctor must never crash
            log.debug("doctor check %s crashed", name, exc_info=True)
            rows.append(Check("WARN", name, f"check failed: {type(exc).__name__}: {exc}"))
    return rows


def print_checks(rows: Sequence[Check]) -> None:
    width = max(len(r.name) for r in rows) if rows else 10
    print(f"autoshorts {__version__} doctor\n")
    print(f"  {'STATUS':6}  {'CHECK':{width}}  DETAIL")
    for r in rows:
        print(f"  {r.status:6}  {r.name:{width}}  {r.detail}")
    order = {"FAIL": 0, "WARN": 1, "OK": 2}
    hints = [r for r in sorted(rows, key=lambda r: order.get(r.status, 3)) if r.hint]
    if hints:
        print("\nNext steps:")
        for r in hints:
            tag = "optional" if r.status == "OK" else r.status
            print(f"  - [{tag}] {r.hint}")
    fails = sum(r.status == "FAIL" for r in rows)
    warns = sum(r.status == "WARN" for r in rows)
    if fails:
        print(f"\n{fails} problem(s) must be fixed before videos can be made.")
    else:
        print(f"\nReady to make videos ({warns} warning(s)). Try: autoshorts make")


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _load(args)
    level = log.level
    if not args.verbose:  # the table says it all; keep INFO chatter from the checks out of it
        log.setLevel(max(log.getEffectiveLevel(), logging.WARNING))
    try:
        rows = run_checks(cfg, args.config)
    finally:
        log.setLevel(level)
    print_checks(rows)
    return EXIT_FAIL if any(r.status == "FAIL" for r in rows) else EXIT_OK


# --------------------------------------------------------------------------- making videos


def cmd_make(args: argparse.Namespace) -> int:
    cfg = _load(args)
    from . import pipeline

    job = pipeline.make_video(cfg, topic=args.topic, fmt=args.format,
                              upload_to=_platforms(args.upload, cfg, args.no_upload))
    _print_job(job)
    return EXIT_PARTIAL if any(not u.ok for u in job.uploads) else EXIT_OK


def cmd_batch(args: argparse.Namespace) -> int:
    cfg = _load(args)
    from . import pipeline

    jobs = pipeline.make_batch(cfg, args.count, fmt=args.format,
                               upload_to=_platforms(args.upload, cfg, args.no_upload))
    for job in jobs:
        _print_job(job)
    print(f"\n{len(jobs)} of {args.count} video(s) made.")
    failed_upload = any(not u.ok for job in jobs for u in job.uploads)
    return EXIT_PARTIAL if failed_upload or len(jobs) < args.count else EXIT_OK


def cmd_script(args: argparse.Namespace) -> int:
    cfg = _load(args)
    from . import pipeline
    from .topics import TopicQueue

    topic = args.topic or TopicQueue(cfg).next()
    fmt = pipeline.resolve_format(args.format or cfg.script.format)
    script = pipeline.generate_script(cfg, topic, fmt)
    print(json.dumps(script.to_dict(), indent=2, ensure_ascii=False))
    return EXIT_OK


def latest_job(cfg: Config) -> Path:
    """The newest job folder in output_dir that has a video."""
    out = cfg.path(cfg.output_dir)
    jobs = sorted(p for p in out.iterdir() if (p / "video.mp4").is_file()) if out.is_dir() else []
    if not jobs:
        raise AutoShortsError(f"no finished videos in {out}")
    return jobs[-1]  # folder names start with a sortable timestamp


def cmd_upload(args: argparse.Namespace) -> int:
    cfg = _load(args)
    from .pipeline import upload_job

    platforms = _platforms(args.to, cfg)
    if not platforms:
        raise AutoShortsError("say where to upload: --to youtube,tiktok (or enable upload.<platform>.enabled)")
    job_dir = latest_job(cfg) if args.job_dir == "latest" else args.job_dir
    results = upload_job(cfg, job_dir, platforms)
    print(f"Uploads for {Path(job_dir).resolve()}:")
    _print_uploads(results)
    return EXIT_OK if all(r.ok for r in results) else EXIT_PARTIAL


def cmd_auth(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if args.platform == "youtube":
        from .upload import youtube

        youtube.authorize(cfg)
        print(f"YouTube authorised; token saved to {cfg.path(cfg.upload.youtube.token_file)}")
    return EXIT_OK


# --------------------------------------------------------------------------- topics / voices


def cmd_topics(args: argparse.Namespace) -> int:
    cfg = _load(args)
    from .topics import TopicQueue, builtin_ideas, topic_key

    queue = TopicQueue(cfg)
    if getattr(args, "action", None) == "add":
        added = queue.add(args.topics)
        skipped = len(args.topics) - added
        print(f"added {added} topic(s) to {queue.file}" + (f" ({skipped} already there or empty)" if skipped else ""))
        return EXIT_OK

    used = queue.used()
    mine = queue.topics()
    print(f"Topics in {queue.file}:" if mine else f"No topics in {queue.file} yet (add some with: autoshorts topics add \"...\").")
    for topic in mine:
        when = used.get(topic_key(topic))
        print(f"  [x] {topic}  (used {when[:10]})" if when else f"  [ ] {topic}")
    mine_keys = {topic_key(m) for m in mine}
    unused_ideas = [t for t in queue.unused(include_builtin=True) if topic_key(t) not in mine_keys]
    print(f"\n{len(queue.unused())} of {len(mine)} unused; {len(unused_ideas)} of {len(builtin_ideas())} "
          "built-in ideas left" + ("" if getattr(args, "ideas", False) else " (show them with --ideas)"))
    if getattr(args, "ideas", False):
        for topic in unused_ideas:
            print(f"  - {topic}")
    return EXIT_OK


def cmd_voices(args: argparse.Namespace) -> int:
    cfg = _load(args)
    from .tts.edge import list_voices

    lang = cfg.script.language if args.lang is None else args.lang
    prefix = "" if lang.lower() in ("", "all", "*") else lang
    voices = list_voices(prefix)
    if not voices:
        print(f"no edge-tts voices for '{lang}'")
        return EXIT_OK
    width = max(len(v["name"]) for v in voices)
    for v in voices:
        extra = ", ".join(v.get("personalities") or [])
        print(f"{v['name']:{width}}  {v['gender']:6}  {v['locale']:8}  {extra}")
    print(f"\n{len(voices)} voice(s). Set one in config.yaml as tts.edge.voice (current: {cfg.tts.edge.voice}).")
    return EXIT_OK


# --------------------------------------------------------------------------- parser


def _add_global_options(parser: argparse.ArgumentParser, suppress: bool) -> None:
    """--config/-v on the main parser and (suppressed defaults) on every subcommand,
    so they work before or after the command name."""
    parser.add_argument("--config", "-c", metavar="PATH", default=argparse.SUPPRESS if suppress else None,
                        help="config file (default: ./config.yaml, or built-in defaults when missing)")
    parser.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS if suppress else False,
                        help="debug logging")


def _add_upload_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--upload", metavar="PLATFORMS", default=None,
                        help=f"upload when done, {PLATFORM_HELP} (default: platforms with upload.<platform>.enabled)")
    parser.add_argument("--no-upload", action="store_true", help="do not upload, even if enabled in the config")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoshorts",
        description="Free, automatic vertical short-video generator for TikTok and YouTube Shorts.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"autoshorts {__version__}")
    _add_global_options(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add(name: str, func: Callable[[argparse.Namespace], int], help_text: str, examples: str = "",
            **kw: Any) -> argparse.ArgumentParser:
        epilog = (f"examples:\n{examples}\n" if examples else "") + EXIT_CODES
        p = sub.add_parser(name, help=help_text, description=help_text, epilog=epilog,
                           formatter_class=argparse.RawDescriptionHelpFormatter, **kw)
        _add_global_options(p, suppress=True)
        p.set_defaults(func=func)
        return p

    add("init", cmd_init, "create config.yaml, .env and topics.txt (never overwrites)")
    add("doctor", cmd_doctor, "check FFmpeg, fonts, voices, script writer, API keys and upload setup")

    p = add("make", cmd_make, "make one video", examples=(
        "  autoshorts make\n"
        "  autoshorts make --topic \"Why octopuses have three hearts\" --format facts\n"
        "  autoshorts make --format quiz --upload youtube,tiktok\n"))
    p.add_argument("--topic", "-t", help="what the video is about (default: next topic from topics.txt)")
    p.add_argument("--format", "-f", help=FORMAT_HELP)
    _add_upload_options(p)

    p = add("batch", cmd_batch, "make several videos in a row (topics from topics.txt)", examples=(
        "  autoshorts batch -n 5\n"
        "  autoshorts batch -n 3 --format story --upload youtube\n"))
    p.add_argument("-n", "--count", type=_positive_int, default=3, metavar="N", help="number of videos (default: 3)")
    p.add_argument("--format", "-f", help=FORMAT_HELP)
    _add_upload_options(p)

    p = add("script", cmd_script, "print a generated script as JSON (no video)")
    p.add_argument("--topic", "-t", help="topic (default: next topic from topics.txt, not marked as used)")
    p.add_argument("--format", "-f", help=FORMAT_HELP)

    p = add("upload", cmd_upload, "upload an existing job folder", examples=(
        "  autoshorts upload latest --to youtube,tiktok\n"
        "  autoshorts upload output/20260101-120000-why-the-sky-is-blue --to tiktok\n"))
    p.add_argument("job_dir", metavar="JOB_DIR",
                   help="a folder in output/ containing video.mp4 and metadata.json, or 'latest'")
    p.add_argument("--to", metavar="PLATFORMS", default=None,
                   help=f"{PLATFORM_HELP} (default: platforms with upload.<platform>.enabled)")

    p = add("auth", cmd_auth, "authorise uploads (one-time OAuth login in your browser)", examples=(
        "  autoshorts auth youtube    (needs upload.youtube.client_secrets, see the README)\n"))
    p.add_argument("platform", choices=["youtube"], help="platform to authorise")

    p = add("topics", cmd_topics, "list or add video topics", examples=(
        "  autoshorts topics list --ideas\n"
        "  autoshorts topics add \"How bees talk\" \"Why cats purr\"\n"))
    topics_sub = p.add_subparsers(dest="action", metavar="ACTION")
    tl = topics_sub.add_parser("list", help="show topics and which are used",
                               description="show the topics in topics.txt and which are already used")
    tl.add_argument("--ideas", action="store_true", help="also list the unused built-in ideas")
    _add_global_options(tl, suppress=True)
    ta = topics_sub.add_parser("add", help="append topics to topics.txt",
                               description="append topics to topics.txt (duplicates are skipped)")
    ta.add_argument("topics", nargs="+", metavar="TOPIC", help="topics to add (quote each one)")
    _add_global_options(ta, suppress=True)

    p = add("voices", cmd_voices, "list edge-tts voices (needs internet)")
    p.add_argument("--lang", "-l", default=None, help="language/locale prefix, e.g. en, en-GB, es; 'all' for every voice "
                                                     "(default: script.language)")
    return parser


def _safe_stdio() -> None:
    """Never crash on characters the console encoding cannot show (Windows cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _safe_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_OK
    try:
        return int(func(args) or EXIT_OK)
    except AutoShortsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as exc:  # a bug or an unexpected system error: short message, details with -v
        if getattr(args, "verbose", False):
            traceback.print_exc()
        print(f"error: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        print("(run again with -v for the full traceback; a failed video also has error.txt in its folder)",
              file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
