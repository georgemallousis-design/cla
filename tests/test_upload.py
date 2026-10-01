"""Tests for the YouTube and TikTok uploaders, fully offline (fake Google client, fake HTTP)."""
from __future__ import annotations

import json
import logging
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from autoshorts.config import Config
from autoshorts.upload import tiktok, youtube
from autoshorts.utils import AutoShortsError

MB = 1_000_000
MIB = 1024 * 1024


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.base_dir = tmp_path
    return c


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "video.mp4"
    path.write_bytes(bytes(range(256)) * 12)  # 3072 bytes
    return path


# =========================================================================== YouTube fakes


class FakeHttpError(Exception):
    def __init__(self, status: int, reason: str = "", message: str = ""):
        super().__init__(f"HTTP {status} {reason}")
        self.resp = SimpleNamespace(status=status)
        self.content = json.dumps(
            {"error": {"code": status, "message": message, "errors": [{"reason": reason, "message": message}]}}
        ).encode()


class FakeRefreshError(Exception):
    pass


class FakeCreds:
    def __init__(self, valid: bool = True, expired: bool = False, refresh_token: str | None = "refresh-1",
                 refresh_error: bool = False):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.refresh_error = refresh_error
        self.refreshed = False

    def refresh(self, request: Any) -> None:
        if self.refresh_error:
            raise FakeRefreshError("invalid_grant")
        self.refreshed = True
        self.valid, self.expired = True, False

    def to_json(self) -> str:
        return json.dumps({"token": "access", "refresh_token": self.refresh_token})


class FakeMedia:
    def __init__(self, filename: str, mimetype: str | None = None, chunksize: int | None = None,
                 resumable: bool = False):
        self.filename, self.mimetype, self.chunksize, self.resumable = filename, mimetype, chunksize, resumable


class FakeProgress:
    def __init__(self, value: float):
        self.value = value

    def progress(self) -> float:
        return self.value


class FakeInsertRequest:
    def __init__(self, outcomes: list[Any]):
        self.outcomes = list(outcomes)
        self.calls = 0

    def next_chunk(self) -> Any:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeYouTube:
    def __init__(self, outcomes: list[Any], thumb_error: Exception | None = None):
        self.request = FakeInsertRequest(outcomes)
        self.thumb_error = thumb_error
        self.insert_kwargs: dict[str, Any] = {}
        self.thumb_calls: list[dict[str, Any]] = []

    def videos(self) -> SimpleNamespace:
        def insert(**kw: Any) -> FakeInsertRequest:
            self.insert_kwargs = kw
            return self.request
        return SimpleNamespace(insert=insert)

    def thumbnails(self) -> SimpleNamespace:
        def set_(**kw: Any) -> SimpleNamespace:
            self.thumb_calls.append(kw)

            def execute() -> dict:
                if self.thumb_error:
                    raise self.thumb_error
                return {"items": []}
            return SimpleNamespace(execute=execute)
        return SimpleNamespace(set=set_)


class FakeFlow:
    instances: list["FakeFlow"] = []

    def __init__(self, path: str, scopes: list[str], creds: FakeCreds, error: Exception | None = None):
        self.path, self.scopes, self.creds, self.error = path, scopes, creds, error
        self.run_kwargs: dict[str, Any] = {}

    def run_local_server(self, **kw: Any) -> FakeCreds:
        self.run_kwargs = kw
        if self.error:
            raise self.error
        return self.creds


def fake_google(yt: FakeYouTube | None = None, creds: FakeCreds | None = None,
                flow_error: Exception | None = None) -> SimpleNamespace:
    creds = creds or FakeCreds()
    built: dict[str, Any] = {}
    flows: list[FakeFlow] = []

    def build(service: str, version: str, **kw: Any) -> FakeYouTube:
        built.update(service=service, version=version, **kw)
        assert yt is not None
        return yt

    def from_client_secrets_file(path: str, scopes: list[str]) -> FakeFlow:
        flow = FakeFlow(path, scopes, creds, flow_error)
        flows.append(flow)
        return flow

    return SimpleNamespace(
        build=build,
        built=built,
        flows=flows,
        creds=creds,
        Credentials=SimpleNamespace(from_authorized_user_file=lambda path, scopes: creds),
        HttpError=FakeHttpError,
        InstalledAppFlow=SimpleNamespace(from_client_secrets_file=from_client_secrets_file),
        MediaFileUpload=FakeMedia,
        RefreshError=FakeRefreshError,
        Request=lambda: "request",
        transport_errors=(),
    )


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []
    monkeypatch.setattr(youtube, "_sleep", calls.append)
    monkeypatch.setattr(tiktok, "_sleep", calls.append)
    return calls


@pytest.fixture
def token_file(cfg: Config) -> Path:
    path = cfg.path(cfg.upload.youtube.token_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


YT_META = {
    "title": "Octopus facts",
    "description": "Smart animals.\n\n#ocean #shorts",
    "tags": ["octopus", "ocean", "shorts"],
    "category_id": "27",
    "privacy": "private",
    "made_for_kids": False,
    "contains_synthetic_media": True,
    "default_language": "en",
}


def install(monkeypatch: pytest.MonkeyPatch, g: SimpleNamespace) -> None:
    monkeypatch.setattr(youtube, "_google", lambda: g)


# =========================================================================== YouTube tests


def test_youtube_missing_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "googleapiclient", None)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", None)
    with pytest.raises(AutoShortsError, match=r"pip install 'autoshorts\[youtube\]'"):
        youtube._google()


def test_youtube_upload_retries_then_succeeds(monkeypatch, cfg, video, tmp_path, token_file, sleeps) -> None:
    yt = FakeYouTube([
        FakeHttpError(503, "backendError"),
        ConnectionResetError("connection reset"),
        (FakeProgress(0.5), None),
        (None, {"id": "vid123"}),
    ])
    g = fake_google(yt)
    install(monkeypatch, g)
    thumb = tmp_path / "thumbnail.jpg"
    thumb.write_bytes(b"\xff\xd8jpeg")

    result = youtube.upload(cfg, video, YT_META, thumbnail=thumb)

    assert result.ok and result.platform == "youtube"
    assert result.id == "vid123"
    assert result.url == "https://youtube.com/shorts/vid123"
    assert len(sleeps) == 2
    assert yt.request.calls == 4
    assert g.built["service"] == "youtube" and g.built["version"] == "v3"
    assert g.built["credentials"] is g.creds

    kw = yt.insert_kwargs
    assert kw["part"] == "snippet,status"
    body = kw["body"]
    assert body["snippet"] == {
        "title": "Octopus facts",
        "description": YT_META["description"],
        "tags": ["octopus", "ocean", "shorts"],
        "categoryId": "27",
        "defaultLanguage": "en",
        "defaultAudioLanguage": "en",
    }
    assert body["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}
    media = kw["media_body"]
    assert media.resumable is True and media.mimetype == "video/mp4"
    assert media.chunksize % (256 * 1024) == 0
    assert Path(media.filename) == video

    assert yt.thumb_calls and yt.thumb_calls[0]["videoId"] == "vid123"
    assert yt.thumb_calls[0]["media_body"].mimetype == "image/jpeg"


def test_youtube_accepts_full_metadata_dict(monkeypatch, cfg, video, token_file, sleeps) -> None:
    yt = FakeYouTube([(None, {"id": "abc"})])
    install(monkeypatch, fake_google(yt))
    result = youtube.upload(cfg, video, {"youtube": YT_META, "tiktok": {}, "credits": []})
    assert result.ok and yt.insert_kwargs["body"]["snippet"]["title"] == "Octopus facts"
    assert yt.thumb_calls == []


def test_youtube_quota_exceeded_is_not_retried(monkeypatch, cfg, video, token_file, sleeps) -> None:
    yt = FakeYouTube([FakeHttpError(403, "quotaExceeded", "The request cannot be completed because you have exceeded your quota.")])
    install(monkeypatch, fake_google(yt))
    result = youtube.upload(cfg, video, YT_META)
    assert not result.ok
    assert "quota" in result.error.lower() and "quotaExceeded" in result.error
    assert sleeps == [] and yt.request.calls == 1


def test_youtube_upload_limit_exceeded(monkeypatch, cfg, video, token_file, sleeps) -> None:
    install(monkeypatch, fake_google(FakeYouTube([FakeHttpError(400, "uploadLimitExceeded", "limit")])))
    result = youtube.upload(cfg, video, YT_META)
    assert not result.ok and "upload limit" in result.error


def test_youtube_auth_error_explained(monkeypatch, cfg, video, token_file, sleeps) -> None:
    install(monkeypatch, fake_google(FakeYouTube([FakeHttpError(401, "authError", "Invalid Credentials")])))
    result = youtube.upload(cfg, video, YT_META)
    assert not result.ok and "autoshorts auth youtube" in result.error


def test_youtube_gives_up_after_max_retries(monkeypatch, cfg, video, token_file, sleeps) -> None:
    yt = FakeYouTube([FakeHttpError(503, "backendError")] * (youtube.MAX_RETRIES + 1))
    install(monkeypatch, fake_google(yt))
    result = youtube.upload(cfg, video, YT_META)
    assert not result.ok and f"after {youtube.MAX_RETRIES} retries" in result.error
    assert len(sleeps) == youtube.MAX_RETRIES


def test_youtube_unexpected_response(monkeypatch, cfg, video, token_file, sleeps) -> None:
    install(monkeypatch, fake_google(FakeYouTube([(None, {"kind": "youtube#video"})])))
    result = youtube.upload(cfg, video, YT_META)
    assert not result.ok and "unexpected response" in result.error


def test_youtube_thumbnail_failure_only_warns(monkeypatch, cfg, video, tmp_path, token_file, sleeps, caplog) -> None:
    yt = FakeYouTube([(None, {"id": "v1"})], thumb_error=FakeHttpError(403, "forbidden", "no permission"))
    install(monkeypatch, fake_google(yt))
    thumb = tmp_path / "thumb.png"
    thumb.write_bytes(b"png")
    caplog.set_level(logging.WARNING, logger="autoshorts")
    result = youtube.upload(cfg, video, YT_META, thumbnail=thumb)
    assert result.ok and result.id == "v1"
    assert yt.thumb_calls[0]["media_body"].mimetype == "image/png"
    assert "youtube.com/verify" in caplog.text


def test_youtube_thumbnail_too_big_is_skipped(monkeypatch, cfg, video, tmp_path, token_file, sleeps) -> None:
    yt = FakeYouTube([(None, {"id": "v1"})])
    install(monkeypatch, fake_google(yt))
    thumb = tmp_path / "big.jpg"
    thumb.write_bytes(b"0" * (youtube.THUMBNAIL_MAX_BYTES + 1))
    assert youtube.upload(cfg, video, YT_META, thumbnail=thumb).ok
    assert yt.thumb_calls == []


def test_youtube_missing_token(monkeypatch, cfg, video) -> None:
    install(monkeypatch, fake_google(FakeYouTube([])))
    with pytest.raises(AutoShortsError, match="autoshorts auth youtube"):
        youtube.upload(cfg, video, YT_META)


def test_youtube_missing_video(monkeypatch, cfg, tmp_path) -> None:
    install(monkeypatch, fake_google(FakeYouTube([])))
    with pytest.raises(AutoShortsError, match="video not found"):
        youtube.upload(cfg, tmp_path / "nope.mp4", YT_META)


def test_youtube_expired_token_is_refreshed_and_saved(monkeypatch, cfg, token_file) -> None:
    creds = FakeCreds(valid=False, expired=True)
    g = fake_google(creds=creds)
    assert youtube.load_credentials(cfg, g) is creds
    assert creds.refreshed
    assert json.loads(token_file.read_text())["refresh_token"] == "refresh-1"
    if os.name == "posix":
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_youtube_refresh_failure(monkeypatch, cfg, token_file) -> None:
    g = fake_google(creds=FakeCreds(valid=False, expired=True, refresh_error=True))
    with pytest.raises(AutoShortsError, match="auth youtube"):
        youtube.load_credentials(cfg, g)
    g = fake_google(creds=FakeCreds(valid=False, expired=True, refresh_token=None))
    with pytest.raises(AutoShortsError, match="auth youtube"):
        youtube.load_credentials(cfg, g)


def test_youtube_authorize_requires_client_secrets(monkeypatch, cfg) -> None:
    install(monkeypatch, fake_google())
    with pytest.raises(AutoShortsError, match="console.cloud.google.com"):
        youtube.authorize(cfg)


def _write_client_secrets(cfg: Config) -> Path:
    path = cfg.path(cfg.upload.youtube.client_secrets)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"installed": {}}', encoding="utf-8")
    return path


def test_youtube_authorize_saves_token(monkeypatch, cfg) -> None:
    secrets = _write_client_secrets(cfg)
    g = fake_google()
    install(monkeypatch, g)
    monkeypatch.setattr(youtube, "_looks_headless", lambda: False)
    youtube.authorize(cfg)
    flow = g.flows[0]
    assert Path(flow.path) == secrets
    assert flow.scopes == ["https://www.googleapis.com/auth/youtube.upload"]
    assert flow.run_kwargs["port"] == 0 and flow.run_kwargs["open_browser"] is True
    token = cfg.path(cfg.upload.youtube.token_file)
    assert json.loads(token.read_text())["refresh_token"] == "refresh-1"
    if os.name == "posix":
        assert stat.S_IMODE(token.stat().st_mode) == 0o600


def test_youtube_authorize_headless(monkeypatch, cfg, caplog) -> None:
    _write_client_secrets(cfg)
    g = fake_google()
    install(monkeypatch, g)
    monkeypatch.setattr(youtube, "_looks_headless", lambda: True)
    caplog.set_level(logging.WARNING, logger="autoshorts")
    youtube.authorize(cfg)
    kw = g.flows[0].run_kwargs
    assert kw["port"] == youtube.HEADLESS_PORT and kw["open_browser"] is False
    assert "autoshorts auth youtube" in caplog.text and "ssh -L" in caplog.text


def test_youtube_authorize_port_error(monkeypatch, cfg) -> None:
    _write_client_secrets(cfg)
    install(monkeypatch, fake_google(flow_error=OSError("address in use")))
    monkeypatch.setattr(youtube, "_looks_headless", lambda: False)
    with pytest.raises(AutoShortsError, match="OAuth callback"):
        youtube.authorize(cfg)


def test_looks_headless(monkeypatch) -> None:
    monkeypatch.setattr(youtube.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert youtube._looks_headless() is True
    monkeypatch.setenv("DISPLAY", ":0")
    assert youtube._looks_headless() is False
    monkeypatch.setattr(youtube.sys, "platform", "win32")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert youtube._looks_headless() is False


# =========================================================================== TikTok chunk math


def assert_valid_plan(size: int, chunk: int, count: int) -> None:
    assert 1 <= count <= tiktok.MAX_CHUNKS
    if count == 1:
        assert chunk == size <= tiktok.MAX_CHUNK
    else:
        assert tiktok.MIN_CHUNK <= chunk <= tiktok.MAX_CHUNK
        assert count == size // chunk
    ranges = list(tiktok.chunk_ranges(size, chunk, count))
    assert ranges[0][0] == 0 and ranges[-1][1] == size - 1
    for (_, prev_end), (start, _) in zip(ranges, ranges[1:]):
        assert start == prev_end + 1
    lengths = [end - start + 1 for start, end in ranges]
    assert sum(lengths) == size
    assert all(n == chunk for n in lengths[:-1])
    assert chunk <= lengths[-1] <= tiktok.MAX_FINAL_CHUNK if count > 1 else True


@pytest.mark.parametrize("size, expected", [
    (1 * MB, (1 * MB, 1)),            # < 5 MB: whole file, chunk_size == video_size
    (1 * MIB, (1 * MIB, 1)),
    (5 * MB, (5 * MB, 1)),
    (5 * MIB, (5 * MIB, 1)),
    (12 * MB, (12 * MB, 1)),          # fits in one allowed chunk
    (25 * MB, (10 * MB, 2)),          # last chunk carries 15 MB
    (70 * MB, (10 * MB, 7)),          # > 64 MB must be split
    (70 * MIB, (10 * MB, 7)),         # last chunk 13.4 MB
])
def test_chunk_plan_sizes(size: int, expected: tuple[int, int]) -> None:
    plan = tiktok.chunk_plan(size)
    assert plan == expected
    assert_valid_plan(size, *plan)


@pytest.mark.parametrize("size", [1, 4_999_999, 5 * MIB - 1, 5 * MIB, 19_999_999, 20 * MB, 64 * MB, 64 * MB + 1,
                                  128 * MB, 999 * MB, 4 * 1024 * MIB])
@pytest.mark.parametrize("preferred", [1, 5 * MIB, tiktok.DEFAULT_CHUNK, 64 * MB, 500 * MB])
def test_chunk_plan_invariants(size: int, preferred: int) -> None:
    assert_valid_plan(size, *tiktok.chunk_plan(size, preferred))


def test_chunk_plan_big_preferred_splits_over_64mb() -> None:
    assert tiktok.chunk_plan(100 * MB, 64 * MB) == (50 * MB, 2)
    assert tiktok.chunk_plan(60 * MB, 64 * MB) == (60 * MB, 1)


def test_chunk_plan_rejects_empty() -> None:
    with pytest.raises(ValueError):
        tiktok.chunk_plan(0)


# =========================================================================== TikTok fakes


class FakeResp:
    def __init__(self, status: int = 200, payload: Any = None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


def ok(data: dict[str, Any]) -> FakeResp:
    return FakeResp(200, {"data": data, "error": {"code": "ok", "message": "", "log_id": "log-ok"}})


def api_error(status: int, code: str, message: str = "") -> FakeResp:
    return FakeResp(status, {"data": {}, "error": {"code": code, "message": message, "log_id": "log-err"}})


UPLOAD_URL = "https://open-upload.tiktokapis.com/video/?upload_id=1&upload_token=abc"
INIT_OK = ok({"publish_id": "v_pub_1", "upload_url": UPLOAD_URL})


class FakeSession:
    """Scripted responses per URL; the last response for a URL repeats."""

    def __init__(self, routes: dict[str, list[Any]]):
        self.routes = {url: list(resps) for url, resps in routes.items()}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _next(self, url: str) -> FakeResp:
        queue = self.routes.get(url)
        if not queue:
            raise AssertionError(f"unexpected request to {url}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def post(self, url: str, json: Any = None, data: Any = None, headers: Any = None, timeout: Any = None) -> FakeResp:
        self.calls.append(("POST", url, {"json": json, "data": data, "headers": dict(headers or {})}))
        return self._next(url)

    def put(self, url: str, data: Any = None, headers: Any = None, timeout: Any = None) -> FakeResp:
        self.calls.append(("PUT", url, {"data": data, "headers": dict(headers or {})}))
        return self._next(url)

    def requests_to(self, url: str) -> list[dict[str, Any]]:
        return [kw for _, u, kw in self.calls if u == url]


@pytest.fixture
def tt_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "tok-1")
    for name in (tiktok.REFRESH_TOKEN_ENV, tiktok.CLIENT_KEY_ENV, tiktok.CLIENT_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tiktok, "media_duration", lambda path: 62.0)


def use_session(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> FakeSession:
    monkeypatch.setattr(tiktok, "http_session", lambda: session)
    return session


TT_META = {"caption": "Octopus facts #ocean #facts", "privacy_level": "SELF_ONLY", "is_aigc": True}

CREATOR = {
    "creator_username": "me",
    "creator_nickname": "Me",
    "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
    "comment_disabled": True,
    "duet_disabled": False,
    "stitch_disabled": False,
    "max_video_post_duration_sec": 600,
}


# =========================================================================== TikTok tests


def test_tiktok_inbox_flow(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    session = use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [INIT_OK],
        UPLOAD_URL: [FakeResp(201)],
        tiktok.STATUS_URL: [ok({"status": "PROCESSING_UPLOAD"}), ok({"status": "SEND_TO_USER_INBOX"})],
    }))
    result = tiktok.upload(cfg, video, TT_META)

    assert result.ok and result.platform == "tiktok" and result.id == "v_pub_1"
    init = session.requests_to(tiktok.INBOX_INIT_URL)[0]
    assert init["json"] == {"source_info": {"source": "FILE_UPLOAD", "video_size": 3072,
                                            "chunk_size": 3072, "total_chunk_count": 1}}
    assert init["headers"]["Authorization"] == "Bearer tok-1"
    assert init["headers"]["Content-Type"].startswith("application/json")

    puts = session.requests_to(UPLOAD_URL)
    assert len(puts) == 1
    assert puts[0]["headers"] == {"Content-Type": "video/mp4", "Content-Length": "3072",
                                  "Content-Range": "bytes 0-3071/3072"}
    assert puts[0]["data"] == video.read_bytes()
    assert session.requests_to(tiktok.STATUS_URL)[0]["json"] == {"publish_id": "v_pub_1"}
    assert sleeps == [tiktok.POLL_INTERVAL]
    assert tiktok.CREATOR_INFO_URL not in [u for _, u, _ in session.calls]


def test_tiktok_accepts_full_metadata_dict(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [INIT_OK], UPLOAD_URL: [FakeResp(201)],
        tiktok.STATUS_URL: [ok({"status": "SEND_TO_USER_INBOX"})],
    }))
    assert tiktok.upload(cfg, video, {"youtube": {}, "tiktok": TT_META, "credits": []}).ok


def test_tiktok_multi_chunk_put(monkeypatch, cfg, tmp_path, tt_env, sleeps) -> None:
    path = tmp_path / "v.mp4"
    payload = os.urandom(3500)
    path.write_bytes(payload)
    monkeypatch.setattr(tiktok, "chunk_plan", lambda size: (1000, 3))
    session = use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [INIT_OK],
        UPLOAD_URL: [FakeResp(206), FakeResp(206), FakeResp(201)],
        tiktok.STATUS_URL: [ok({"status": "SEND_TO_USER_INBOX"})],
    }))
    assert tiktok.upload(cfg, path, TT_META).ok
    source = session.requests_to(tiktok.INBOX_INIT_URL)[0]["json"]["source_info"]
    assert source == {"source": "FILE_UPLOAD", "video_size": 3500, "chunk_size": 1000, "total_chunk_count": 3}
    puts = session.requests_to(UPLOAD_URL)
    assert [p["headers"]["Content-Range"] for p in puts] == [
        "bytes 0-999/3500", "bytes 1000-1999/3500", "bytes 2000-3499/3500"]
    assert [p["headers"]["Content-Length"] for p in puts] == ["1000", "1000", "1500"]
    assert b"".join(p["data"] for p in puts) == payload
    assert all("Authorization" not in p["headers"] for p in puts)  # token never sent to the upload host


def test_tiktok_put_retries_5xx(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    session = use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [INIT_OK],
        UPLOAD_URL: [FakeResp(503, text="busy"), requests.ConnectionError("reset"), FakeResp(201)],
        tiktok.STATUS_URL: [ok({"status": "SEND_TO_USER_INBOX"})],
    }))
    assert tiktok.upload(cfg, video, TT_META).ok
    assert len(session.requests_to(UPLOAD_URL)) == 3
    assert len(sleeps) == 2


def test_tiktok_put_client_error_fails(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [INIT_OK],
        UPLOAD_URL: [FakeResp(416, text="range not satisfiable")],
    }))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok and "upload_failed" in result.error and "416" in result.error


def test_tiktok_direct_flow_with_privacy_fallback(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    cfg.upload.tiktok.mode = "direct"
    creator = dict(CREATOR, privacy_level_options=["FOLLOWER_OF_CREATOR", "SELF_ONLY"])
    session = use_session(monkeypatch, FakeSession({
        tiktok.CREATOR_INFO_URL: [ok(creator)],
        tiktok.DIRECT_INIT_URL: [INIT_OK],
        UPLOAD_URL: [FakeResp(201)],
        tiktok.STATUS_URL: [ok({"status": "PROCESSING_UPLOAD"}),
                            ok({"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7123]})],
    }))
    meta = dict(TT_META, privacy_level="PUBLIC_TO_EVERYONE")
    result = tiktok.upload(cfg, video, meta)

    assert result.ok and result.id == "7123"
    assert result.url == "https://www.tiktok.com/@me/video/7123"
    body = session.requests_to(tiktok.DIRECT_INIT_URL)[0]["json"]
    info = body["post_info"]
    assert info["title"] == TT_META["caption"]
    assert info["privacy_level"] == "SELF_ONLY"
    assert info["is_aigc"] is True
    assert info["disable_comment"] is True and info["disable_duet"] is False and info["disable_stitch"] is False
    assert info["video_cover_timestamp_ms"] == tiktok.COVER_TIMESTAMP_MS
    assert info["brand_content_toggle"] is False
    assert body["source_info"] == {"source": "FILE_UPLOAD", "video_size": 3072, "chunk_size": 3072,
                                   "total_chunk_count": 1}
    assert session.requests_to(tiktok.CREATOR_INFO_URL)[0]["json"] == {}


def test_tiktok_direct_requested_privacy_kept_when_allowed(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    cfg.upload.tiktok.mode = "direct"
    session = use_session(monkeypatch, FakeSession({
        tiktok.CREATOR_INFO_URL: [ok(CREATOR)], tiktok.DIRECT_INIT_URL: [INIT_OK],
        UPLOAD_URL: [FakeResp(201)], tiktok.STATUS_URL: [ok({"status": "PUBLISH_COMPLETE"})],
    }))
    result = tiktok.upload(cfg, video, dict(TT_META, privacy_level="MUTUAL_FOLLOW_FRIENDS"))
    assert result.ok and result.id == "v_pub_1" and result.url == ""
    assert session.requests_to(tiktok.DIRECT_INIT_URL)[0]["json"]["post_info"]["privacy_level"] == "MUTUAL_FOLLOW_FRIENDS"


def test_tiktok_choose_privacy() -> None:
    assert tiktok.choose_privacy("PUBLIC_TO_EVERYONE", ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]) == "PUBLIC_TO_EVERYONE"
    assert tiktok.choose_privacy("PUBLIC_TO_EVERYONE", ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS"]) == "MUTUAL_FOLLOW_FRIENDS"
    assert tiktok.choose_privacy("PUBLIC_TO_EVERYONE", []) == "SELF_ONLY"


def test_tiktok_unaudited_app_retries_as_self_only(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    cfg.upload.tiktok.mode = "direct"
    session = use_session(monkeypatch, FakeSession({
        tiktok.CREATOR_INFO_URL: [ok(CREATOR)],
        tiktok.DIRECT_INIT_URL: [api_error(403, "unaudited_client_can_only_post_to_private_accounts"), INIT_OK],
        UPLOAD_URL: [FakeResp(201)],
        tiktok.STATUS_URL: [ok({"status": "PUBLISH_COMPLETE"})],
    }))
    result = tiktok.upload(cfg, video, dict(TT_META, privacy_level="PUBLIC_TO_EVERYONE"))
    assert result.ok
    inits = session.requests_to(tiktok.DIRECT_INIT_URL)
    assert [i["json"]["post_info"]["privacy_level"] for i in inits] == ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]


def test_tiktok_unaudited_error_explained(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    cfg.upload.tiktok.mode = "direct"
    use_session(monkeypatch, FakeSession({
        tiktok.CREATOR_INFO_URL: [ok(CREATOR)],
        tiktok.DIRECT_INIT_URL: [api_error(403, "unaudited_client_can_only_post_to_private_accounts", "nope")],
    }))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok
    assert "SELF_ONLY" in result.error and "inbox" in result.error and "log-err" in result.error


def test_tiktok_direct_rejects_too_long_video(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    cfg.upload.tiktok.mode = "direct"
    monkeypatch.setattr(tiktok, "media_duration", lambda path: 300.0)
    session = use_session(monkeypatch, FakeSession({
        tiktok.CREATOR_INFO_URL: [ok(dict(CREATOR, max_video_post_duration_sec=180))],
    }))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok and "at most 180s" in result.error
    assert session.requests_to(tiktok.DIRECT_INIT_URL) == []


def test_tiktok_failed_status(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [INIT_OK], UPLOAD_URL: [FakeResp(201)],
        tiktok.STATUS_URL: [ok({"status": "FAILED", "fail_reason": "frame_rate_check_failed"})],
    }))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok and result.id == "v_pub_1"
    assert "frame_rate_check_failed" in result.error and "frame rate" in result.error


def test_tiktok_poll_timeout_still_ok(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    session = use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [INIT_OK], UPLOAD_URL: [FakeResp(201)],
        tiktok.STATUS_URL: [ok({"status": "PROCESSING_UPLOAD"})],
    }))
    result = tiktok.upload(cfg, video, TT_META)
    assert result.ok and result.id == "v_pub_1"
    polls = int(tiktok.POLL_TIMEOUT // tiktok.POLL_INTERVAL) + 1
    assert len(session.requests_to(tiktok.STATUS_URL)) == polls
    assert sleeps == [tiktok.POLL_INTERVAL] * (polls - 1)


def test_tiktok_poll_survives_rate_limit(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [INIT_OK], UPLOAD_URL: [FakeResp(201)],
        tiktok.STATUS_URL: [api_error(429, "rate_limit_exceeded"), requests.ConnectionError("blip"),
                            ok({"status": "SEND_TO_USER_INBOX"})],
    }))
    assert tiktok.upload(cfg, video, TT_META).ok
    assert len(sleeps) == 2


def test_tiktok_inbox_errors_are_readable(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [api_error(403, "spam_risk_too_many_pending_share", "too many")],
    }))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok and "max 5 per 24 h" in result.error


def test_tiktok_network_error(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    use_session(monkeypatch, FakeSession({tiktok.INBOX_INIT_URL: [requests.ConnectionError("offline")]}))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok and "network error" in result.error


def test_tiktok_invalid_init_response(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    use_session(monkeypatch, FakeSession({tiktok.INBOX_INIT_URL: [ok({"publish_id": "x"})]}))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok and "upload_url" in result.error


def test_tiktok_non_json_error(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    use_session(monkeypatch, FakeSession({tiktok.INBOX_INIT_URL: [FakeResp(502, text="<html>bad gateway</html>")]}))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok and "http_502" in result.error


# --------------------------------------------------------------------------- token refresh


def set_refresh_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tiktok.REFRESH_TOKEN_ENV, "ref-1")
    monkeypatch.setenv(tiktok.CLIENT_KEY_ENV, "key-1")
    monkeypatch.setenv(tiktok.CLIENT_SECRET_ENV, "secret-1")


TOKEN_OK = FakeResp(200, {"access_token": "tok-2", "expires_in": 86400, "open_id": "o", "refresh_token": "ref-2",
                          "refresh_expires_in": 31536000, "scope": "video.upload", "token_type": "Bearer"})


def test_tiktok_refreshes_token_on_401(monkeypatch, cfg, video, tt_env, sleeps, caplog) -> None:
    set_refresh_env(monkeypatch)
    caplog.set_level(logging.DEBUG, logger="autoshorts")
    session = use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [api_error(401, "access_token_invalid", "expired"), INIT_OK],
        tiktok.TOKEN_URL: [TOKEN_OK],
        UPLOAD_URL: [FakeResp(201)],
        tiktok.STATUS_URL: [ok({"status": "SEND_TO_USER_INBOX"})],
    }))
    result = tiktok.upload(cfg, video, TT_META)
    assert result.ok

    token_req = session.requests_to(tiktok.TOKEN_URL)
    assert len(token_req) == 1
    assert token_req[0]["data"] == {"client_key": "key-1", "client_secret": "secret-1",
                                    "grant_type": "refresh_token", "refresh_token": "ref-1"}
    assert token_req[0]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    inits = session.requests_to(tiktok.INBOX_INIT_URL)
    assert [i["headers"]["Authorization"] for i in inits] == ["Bearer tok-1", "Bearer tok-2"]
    assert session.requests_to(tiktok.STATUS_URL)[0]["headers"]["Authorization"] == "Bearer tok-2"
    assert os.environ["TIKTOK_ACCESS_TOKEN"] == "tok-2"
    assert os.environ[tiktok.REFRESH_TOKEN_ENV] == "ref-2"
    assert "new refresh token" in caplog.text
    for secret in ("tok-1", "tok-2", "ref-1", "ref-2", "secret-1"):
        assert secret not in caplog.text


def test_tiktok_refresh_only_once(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    set_refresh_env(monkeypatch)
    session = use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [api_error(401, "access_token_invalid")],
        tiktok.TOKEN_URL: [TOKEN_OK],
    }))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok and "access_token_invalid" in result.error
    assert len(session.requests_to(tiktok.TOKEN_URL)) == 1
    assert len(session.requests_to(tiktok.INBOX_INIT_URL)) == 2


def test_tiktok_refresh_failure(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    set_refresh_env(monkeypatch)
    use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [api_error(401, "access_token_invalid")],
        tiktok.TOKEN_URL: [FakeResp(400, {"error": "invalid_grant", "error_description": "Refresh token is invalid"})],
    }))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok and "invalid_grant" in result.error and "Refresh token is invalid" in result.error


def test_tiktok_401_without_refresh_setup(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    session = use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [api_error(401, "access_token_invalid")],
    }))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok and tiktok.REFRESH_TOKEN_ENV in result.error
    assert session.requests_to(tiktok.TOKEN_URL) == []


def test_tiktok_scope_error_does_not_refresh(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    set_refresh_env(monkeypatch)
    session = use_session(monkeypatch, FakeSession({
        tiktok.INBOX_INIT_URL: [api_error(401, "scope_not_authorized")],
    }))
    result = tiktok.upload(cfg, video, TT_META)
    assert not result.ok and "video.upload" in result.error
    assert session.requests_to(tiktok.TOKEN_URL) == []


def test_tiktok_missing_access_token_uses_refresh(monkeypatch, cfg, video, tt_env, sleeps) -> None:
    monkeypatch.delenv("TIKTOK_ACCESS_TOKEN")
    set_refresh_env(monkeypatch)
    session = use_session(monkeypatch, FakeSession({
        tiktok.TOKEN_URL: [TOKEN_OK],
        tiktok.INBOX_INIT_URL: [INIT_OK], UPLOAD_URL: [FakeResp(201)],
        tiktok.STATUS_URL: [ok({"status": "SEND_TO_USER_INBOX"})],
    }))
    assert tiktok.upload(cfg, video, TT_META).ok
    assert session.calls[0][1] == tiktok.TOKEN_URL
    assert session.requests_to(tiktok.INBOX_INIT_URL)[0]["headers"]["Authorization"] == "Bearer tok-2"


def test_tiktok_missing_credentials(monkeypatch, cfg, video, tt_env) -> None:
    monkeypatch.delenv("TIKTOK_ACCESS_TOKEN")
    with pytest.raises(AutoShortsError, match="developers.tiktok.com"):
        tiktok.upload(cfg, video, TT_META)


def test_tiktok_bad_mode_and_missing_video(cfg, video, tmp_path, tt_env) -> None:
    cfg.upload.tiktok.mode = "carrier-pigeon"
    with pytest.raises(AutoShortsError, match="inbox"):
        tiktok.upload(cfg, video, TT_META)
    cfg.upload.tiktok.mode = "inbox"
    with pytest.raises(AutoShortsError, match="video not found"):
        tiktok.upload(cfg, tmp_path / "missing.mp4", TT_META)
