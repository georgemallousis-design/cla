"""Tests for autoshorts.cli: argument parsing, init, doctor and commands (pipeline patched)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from autoshorts import cli, pipeline
from autoshorts.config import Config, load_config
from autoshorts.models import RenderResult, Segment, UploadResult, VideoJob, VideoScript
from autoshorts.utils import AutoShortsError

REPO = Path(__file__).resolve().parent.parent
SUBCOMMANDS = [
    ["init"], ["doctor"], ["make"], ["batch"], ["script"], ["upload"], ["auth"],
    ["topics"], ["topics", "list"], ["topics", "add"], ["voices"],
]
SECRET_ENVS = ("PEXELS_API_KEY", "PIXABAY_API_KEY", "LLM_API_KEY", "TIKTOK_ACCESS_TOKEN")


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run every test in an empty folder with no API keys (restored afterwards,
    including anything load_dotenv adds from a .env written by init)."""
    monkeypatch.chdir(tmp_path)
    for name in SECRET_ENVS:
        monkeypatch.setenv(name, "")
    return tmp_path


def fake_job(folder: Path, uploads: list[UploadResult] | None = None) -> VideoJob:
    script = VideoScript(topic="t", format="facts", title="A title", segments=[Segment("Hi.", "sky")], description="")
    return VideoJob(id=folder.name, topic="t", folder=folder, script=script,
                    render=RenderResult(video_path=folder / "video.mp4", duration=61.0),
                    uploads=uploads or [])


# --------------------------------------------------------------------------- help


def test_main_help_lists_commands_and_exit_codes(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("init", "doctor", "make", "batch", "script", "upload", "auth", "topics", "voices"):
        assert cmd in out
    assert "exit codes" in out and "130" in out


@pytest.mark.parametrize("args", SUBCOMMANDS, ids=" ".join)
def test_every_subcommand_has_help(args, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([*args, "--help"])
    assert exc.value.code == 0
    assert "usage: autoshorts" in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "usage: autoshorts" in capsys.readouterr().out


def test_python_dash_m_entry_point():
    proc = subprocess.run([sys.executable, "-m", "autoshorts", "--version"], cwd=REPO,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert "autoshorts" in proc.stdout


def test_bad_arguments_exit_2(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["batch", "-n", "0"])
    assert exc.value.code == 2


def test_unexpected_error_is_one_short_line(isolated, monkeypatch, capsys):
    def boom(args):
        raise ValueError("something odd")

    monkeypatch.setattr(cli, "cmd_topics", boom)
    assert cli.main(["topics"]) == cli.EXIT_FAIL
    err = capsys.readouterr().err
    assert "error: unexpected ValueError: something odd" in err and "Traceback" not in err
    assert cli.main(["topics", "-v"]) == cli.EXIT_FAIL
    assert "Traceback" in capsys.readouterr().err


# --------------------------------------------------------------------------- init


def test_init_creates_files_without_overwriting(isolated, monkeypatch, capsys):
    empty = isolated / "no-templates"
    empty.mkdir()
    monkeypatch.setattr(cli, "REPO_ROOT", empty)
    monkeypatch.setattr(cli, "DATA_DIR", empty)

    assert cli.main(["init"]) == 0
    for name in ("config.yaml", ".env", "topics.txt"):
        assert (isolated / name).is_file(), name
    assert (isolated / "assets" / "music").is_dir()

    # The generated config is the defaults and loads back cleanly.
    data = yaml.safe_load((isolated / "config.yaml").read_text(encoding="utf-8"))
    assert "base_dir" not in data
    assert data["video"]["width"] == 1080
    cfg = load_config(isolated / "config.yaml")
    assert cfg.video == Config().video and cfg.upload == Config().upload
    env = (isolated / ".env").read_text(encoding="utf-8")
    for name in SECRET_ENVS:
        assert f"\n{name}=\n" in env

    (isolated / "config.yaml").write_text("log_level: DEBUG\n", encoding="utf-8")
    (isolated / "topics.txt").write_text("mine\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["init"]) == 0
    assert (isolated / "config.yaml").read_text(encoding="utf-8") == "log_level: DEBUG\n"
    assert (isolated / "topics.txt").read_text(encoding="utf-8") == "mine\n"
    assert capsys.readouterr().out.count("skipped") == 3


def test_init_prefers_repo_templates(isolated, monkeypatch):
    templates = isolated / "repo"
    templates.mkdir()
    (templates / "config.example.yaml").write_text("output_dir: videos\n", encoding="utf-8")
    (templates / ".env.example").write_text("PEXELS_API_KEY=\n", encoding="utf-8")
    (templates / "topics.example.txt").write_text("Example topic\n", encoding="utf-8")
    monkeypatch.setattr(cli, "REPO_ROOT", templates)

    target = isolated / "channel"
    assert cli.main(["init", "--config", str(target / "config.yaml")]) == 0
    assert (target / "config.yaml").read_text(encoding="utf-8") == "output_dir: videos\n"
    assert (target / ".env").read_text(encoding="utf-8") == "PEXELS_API_KEY=\n"
    assert (target / "topics.txt").read_text(encoding="utf-8") == "Example topic\n"


# --------------------------------------------------------------------------- doctor


def test_doctor_runs_in_this_environment(capsys):
    code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert code == 0, out
    for name in ("python", "ffmpeg", "ffprobe", "libass", "libx264", "caption font", "script writer",
                 "pexels key", "music", "youtube upload", "tiktok upload", "topics"):
        assert name in out, name
    assert "FAIL" not in out


def test_doctor_survives_crashing_checks_and_reports_fail(monkeypatch, capsys):
    def boom(cfg):
        raise RuntimeError("font lookup exploded")

    monkeypatch.setattr(cli, "check_font", boom)
    monkeypatch.setattr(cli, "check_ffmpeg", lambda: [cli.Check("FAIL", "ffmpeg", "not found on PATH", "install it")])
    code = cli.main(["doctor", "-v"])  # global option after the command
    out = capsys.readouterr().out
    assert code == 1
    assert "font lookup exploded" in out
    assert "[FAIL] install it" in out


def test_doctor_masks_keys(monkeypatch, capsys):
    monkeypatch.setenv("PEXELS_API_KEY", "abcdefghijklmnop1234")
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "abcdefghijklmnop" not in out
    assert "...1234" in out


def test_missing_config_file_is_an_error(capsys):
    assert cli.main(["--config", "nope.yaml", "doctor"]) == 2
    assert "error:" in capsys.readouterr().err


# --------------------------------------------------------------------------- make / batch / script / upload


def test_make_calls_pipeline_and_prints_folder(isolated, monkeypatch, capsys):
    seen = {}

    def fake_make_video(cfg, topic=None, fmt=None, upload_to=()):
        seen.update(topic=topic, fmt=fmt, upload_to=upload_to)
        return fake_job(isolated / "output" / "job1", [UploadResult("youtube", True, "abc", "https://youtu.be/abc")])

    monkeypatch.setattr(pipeline, "make_video", fake_make_video)
    code = cli.main(["make", "--topic", "Octopus facts", "--format", "quiz", "--upload", "youtube"])
    out = capsys.readouterr().out
    assert code == 0
    assert seen == {"topic": "Octopus facts", "fmt": "quiz", "upload_to": ("youtube",)}
    assert "job1" in out and "https://youtu.be/abc" in out and "A title" in out


def test_make_uses_enabled_platforms_unless_no_upload(isolated, monkeypatch):
    (isolated / "config.yaml").write_text("upload:\n  tiktok:\n    enabled: true\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(pipeline, "make_video",
                        lambda cfg, topic=None, fmt=None, upload_to=(): calls.append(upload_to) or fake_job(isolated))
    assert cli.main(["make"]) == 0
    assert cli.main(["make", "--no-upload"]) == 0
    assert calls == [("tiktok",), ()]


def test_make_upload_failure_exit_3_and_errors_exit_2(isolated, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "make_video", lambda cfg, **kw: fake_job(
        isolated, [UploadResult("tiktok", False, error="token expired")]))
    assert cli.main(["make", "--upload", "tiktok"]) == 3
    assert "FAILED: token expired" in capsys.readouterr().out

    def broken(cfg, **kw):
        raise AutoShortsError("ffmpeg is missing")

    monkeypatch.setattr(pipeline, "make_video", broken)
    assert cli.main(["make"]) == 2
    assert "error: ffmpeg is missing" in capsys.readouterr().err

    assert cli.main(["make", "--upload", "myspace"]) == 2


def test_keyboard_interrupt_exits_130(monkeypatch):
    def interrupted(cfg, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(pipeline, "make_video", interrupted)
    assert cli.main(["make"]) == 130


def test_batch(isolated, monkeypatch, capsys):
    seen = {}

    def fake_batch(cfg, n, fmt=None, upload_to=()):
        seen.update(n=n, fmt=fmt, upload_to=upload_to)
        return [fake_job(isolated / "a"), fake_job(isolated / "b")]

    monkeypatch.setattr(pipeline, "make_batch", fake_batch)
    assert cli.main(["batch", "-n", "2", "--format", "story"]) == 0
    assert seen == {"n": 2, "fmt": "story", "upload_to": ()}
    assert "2 of 2 video(s) made" in capsys.readouterr().out
    assert cli.main(["batch", "-n", "3"]) == 3  # one of three failed


def test_script_prints_json(monkeypatch, capsys):
    def fake_generate(cfg, topic, fmt):
        return VideoScript(topic=topic, format=fmt, title="T", segments=[Segment("Hello.", "sun")], description="d")

    monkeypatch.setattr(pipeline, "generate_script", fake_generate)
    assert cli.main(["script", "--topic", "The Sun", "--format", "facts"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["topic"] == "The Sun" and data["format"] == "facts"
    assert data["segments"] == [{"text": "Hello.", "visual_query": "sun"}]


def test_upload_command(isolated, monkeypatch, capsys):
    seen = {}

    def fake_upload_job(cfg, job_dir, platforms):
        seen.update(job_dir=job_dir, platforms=platforms)
        return [UploadResult("youtube", True, "x1", "https://youtu.be/x1")]

    monkeypatch.setattr(pipeline, "upload_job", fake_upload_job)
    assert cli.main(["upload", "output/job", "--to", "youtube"]) == 0
    assert seen == {"job_dir": "output/job", "platforms": ("youtube",)}
    assert "https://youtu.be/x1" in capsys.readouterr().out
    # nothing enabled and no --to
    assert cli.main(["upload", "output/job"]) == 2


def test_upload_latest_picks_newest_finished_job(isolated, monkeypatch):
    for name, video in (("20260101-000000-a", True), ("20260102-000000-b", True), ("20260103-000000-c", False)):
        folder = isolated / "output" / name
        folder.mkdir(parents=True)
        if video:
            (folder / "video.mp4").write_bytes(b"v")
    seen = []
    monkeypatch.setattr(pipeline, "upload_job", lambda cfg, job_dir, platforms: seen.append(Path(job_dir).name) or [])
    assert cli.main(["upload", "latest", "--to", "tiktok"]) == 0
    assert seen == ["20260102-000000-b"]


# --------------------------------------------------------------------------- topics / voices


def test_topics_add_and_list(isolated, capsys):
    assert cli.main(["topics", "add", "Volcanoes", "Black holes", "volcanoes"]) == 0
    assert "added 2 topic(s)" in capsys.readouterr().out
    assert (isolated / "topics.txt").read_text(encoding="utf-8") == "Volcanoes\nBlack holes\n"

    assert cli.main(["topics", "list"]) == 0
    out = capsys.readouterr().out
    assert "[ ] Volcanoes" in out and "[ ] Black holes" in out
    assert cli.main(["topics"]) == 0  # list is the default
    assert "[ ] Volcanoes" in capsys.readouterr().out


def test_voices(monkeypatch, capsys):
    from autoshorts.tts import edge

    seen = []

    def fake_list(prefix=""):
        seen.append(prefix)
        return [{"name": "en-US-AndrewNeural", "gender": "Male", "locale": "en-US", "personalities": ["Warm"]}]

    monkeypatch.setattr(edge, "list_voices", fake_list)
    assert cli.main(["voices", "--lang", "en-US"]) == 0
    assert "en-US-AndrewNeural" in capsys.readouterr().out
    assert cli.main(["voices"]) == 0
    assert cli.main(["voices", "--lang", "all"]) == 0
    assert seen == ["en-US", "en", ""]


def test_mask():
    assert cli.mask(None) == ""
    assert cli.mask("short") == "set"
    assert cli.mask("0123456789abcdefWXYZ") == "set (...WXYZ)"
