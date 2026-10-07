"""The local web dashboard (autoshorts ui)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from autoshorts import channels
from autoshorts.config import load_config
from autoshorts.ui import server


ui_handlers: list[type] = []  # the handler class bound by the latest ui fixture


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    channels.init_channels(tmp_path / "channels", ["reddit"])
    job = tmp_path / "channels" / "reddit" / "output" / "20260101-120000-my-story"
    job.mkdir(parents=True)
    (job / "video.mp4").write_bytes(bytes(range(256)) * 4)  # 1024 bytes
    (job / "job.json").write_text(json.dumps({
        "status": "done", "created_at": "2026-01-01T12:00:00", "render": {"duration": 61.0},
        "script": {"title": "My story", "format": "reddit"}, "uploads": []}), encoding="utf-8")
    (job / "metadata.json").write_text(json.dumps({"youtube": {"title": "My story"}, "tiktok": {"caption": "c"}}),
                                       encoding="utf-8")
    ws = server.Workspace(tmp_path)
    handler = type("H", (server.Handler,), {"ws": ws, "runner": server.Runner(ws.root), "port": 0})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ui_handlers.append(handler)
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path, job
    httpd.shutdown()
    httpd.server_close()


def call(base: str, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, object]:
    req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


W = {"X-Autoshorts": "1"}


def test_index_and_channels(ui):
    base, _, _ = ui
    status, html = call(base, "/")
    assert status == 200 and b"autoshorts" in html
    status, chans = call(base, "/api/channels")
    assert status == 200 and chans[0]["id"] == "reddit" and chans[0]["videos"] == 1


def test_videos_list_and_media_ranges(ui):
    base, _, job = ui
    _, vids = call(base, "/api/channel/reddit/videos")
    assert vids[0]["title"] == "My story" and vids[0]["has_video"] and vids[0]["duration"] == 61.0
    req = urllib.request.Request(f"{base}/media/reddit/{job.name}/video.mp4", headers={"Range": "bytes=10-19"})
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 206 and resp.read() == bytes(range(10, 20))
        assert resp.headers["Content-Range"] == "bytes 10-19/1024"
    for bad in ("/media/reddit/../../etc/video.mp4", f"/media/reddit/{job.name}/job.json", "/media/nope/x/video.mp4"):
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + bad)


def test_writes_need_the_header_and_a_local_host(ui):
    base, _, _ = ui
    assert call(base, "/api/env", {"LLM_API_KEY": "x"})[0] == 403
    status, _ = call(base, "/api/channels", headers={"Host": "evil.example"})
    assert status == 403


def test_settings_round_trip(ui):
    base, root, _ = ui
    status, _ = call(base, "/api/channel/reddit/settings",
                     {"changes": {"captions.font_size": "110", "metadata.extra_hashtags": "a, #b", "captions.card": False}},
                     W)
    assert status == 200
    cfg = load_config(root / "channels" / "reddit" / "config.yaml")
    assert cfg.captions.font_size == 110 and cfg.metadata.extra_hashtags == ["a", "b"] and cfg.captions.card is False
    assert (root / "channels" / "reddit" / "config.yaml.bak").is_file()
    assert call(base, "/api/channel/reddit/settings", {"changes": {"nope.key": 1}}, W)[0] == 400
    assert call(base, "/api/channel/reddit/settings", {"changes": {"video.target_seconds": "abc"}}, W)[0] == 400


def test_topics_and_env(ui, monkeypatch):
    base, root, _ = ui
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert call(base, "/api/channel/reddit/topics", {"text": "one\ntwo\n"}, W)[0] == 200
    _, topics = call(base, "/api/channel/reddit/topics")
    assert topics["unused"] == ["one", "two"]
    _, saved = call(base, "/api/env", {"PEXELS_API_KEY": "abcdefghijkl", "EVIL": "x"}, W)
    assert saved["saved"] == ["PEXELS_API_KEY"]
    assert "PEXELS_API_KEY=abcdefghijkl" in (root / ".env").read_text(encoding="utf-8")
    _, env = call(base, "/api/env")
    pexels = next(e for e in env if e["key"] == "PEXELS_API_KEY")
    assert pexels["set"] and "abcdefghijkl" not in pexels["masked"]


def test_make_passes_topic_format_and_upload(ui):
    base, _, _ = ui
    seen = []

    def fake_start(args, label):
        if seen:
            raise RuntimeError("busy")
        seen.append(args)

    runner = server.Runner(Path("."))
    runner.start = fake_start
    ui_handlers[-1].runner = runner
    status, _ = call(base, "/api/channel/reddit/make", {"topic": "a cat", "format": "reddit", "upload": ["tiktok", "x"]}, W)
    assert status == 200
    args = seen[0]
    assert args[args.index("--topic") + 1] == "a cat" and args[-2:] == ["--upload", "tiktok"]
    assert call(base, "/api/channel/reddit/make", {}, W)[0] == 409  # one run at a time


def test_delete_video(ui):
    base, _, job = ui
    assert call(base, "/api/channel/reddit/delete", {"job": "../../x"}, W)[0] == 400
    assert call(base, "/api/channel/reddit/delete", {"job": job.name}, W)[0] == 200
    assert not job.exists()


def test_runner_streams_output(tmp_path):
    runner = server.Runner(tmp_path)
    runner.start(["--version"], "version")
    for _ in range(100):
        if not runner.busy() and runner.exit_code is not None:
            break
        threading.Event().wait(0.05)
    state = runner.state()
    assert state["exit_code"] == 0 and any("autoshorts" in line for line in state["lines"])


def test_save_settings_keeps_unknown_sections(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("music:\n  volume: 0.3\nscript:\n  format: quiz\n", encoding="utf-8")
    server.save_settings(cfg, {"script.format": "whatif"})
    loaded = load_config(cfg)
    assert loaded.script.format == "whatif" and loaded.music.volume == 0.3
    assert Path(str(cfg) + "").read_text(encoding="utf-8").startswith("# Saved by the autoshorts UI")
