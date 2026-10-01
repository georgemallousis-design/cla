"""Tests for autoshorts.visuals: stock providers (HTTP mocked), local files, generated
backgrounds (real low-res ffmpeg renders) and shot planning (fake providers)."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote_plus

import pytest
import requests

from autoshorts import utils, visuals
from autoshorts.config import Config
from autoshorts.models import ClipAsset, Narration, Segment, TimedSegment
from autoshorts.utils import AutoShortsError
from autoshorts.visuals import (
    Shot,
    VisualProvider,
    get_providers,
    plan_shots,
    rendition_key,
    segment_windows,
    simplify_query,
    split_window,
)
from autoshorts.visuals import generated, local, pexels, pixabay
from autoshorts.visuals.generated import GeneratedProvider
from autoshorts.visuals.local import LocalProvider
from autoshorts.visuals.pexels import PexelsProvider
from autoshorts.visuals.pixabay import PixabayProvider

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


# --------------------------------------------------------------------------- helpers


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.base_dir = tmp_path
    c.visuals.cache_dir = "cache"
    c.visuals.local_dir = "backgrounds"
    c.visuals.timeout = 5
    return c


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """http_get backs off with time.sleep between retries; tests don't wait."""
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)


class FakeResponse:
    def __init__(self, status: int = 200, payload=None, body: bytes = b"", text: str | None = None):
        self.status_code = status
        self._payload = payload
        self._body = body
        self.text = text if text is not None else (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size=1):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Routes GETs: API URL -> ``api(params)``; anything else is a file download."""

    def __init__(self, api, api_url: str):
        self.api = api
        self.api_url = api_url
        self.api_calls: list[dict] = []
        self.downloads: list[str] = []
        self.headers: dict = {}

    def get(self, url, params=None, headers=None, stream=False, timeout=None, **kw):
        if url == self.api_url:
            self.api_calls.append({"params": dict(params or {}), "headers": dict(headers or {})})
            result = self.api(dict(params or {}))
            if isinstance(result, Exception):
                raise result
            return result
        self.downloads.append(url)
        return FakeResponse(200, body=b"fake mp4 bytes")


class ExplodingSession:
    def get(self, *a, **kw):
        raise AssertionError("no HTTP request expected")


def pexels_video(vid: int, files: list[tuple[int, int]], duration: int = 12, user: str = "Jane Doe") -> dict:
    return {
        "id": vid, "width": files[0][0], "height": files[0][1], "duration": duration,
        "user": {"id": 1, "name": user, "url": "https://www.pexels.com/@jane"},
        "video_files": [
            {"id": vid * 10 + i, "quality": "hd", "file_type": "video/mp4", "width": w, "height": h,
             "fps": 25, "link": f"https://videos.pexels.com/{vid}/{w}x{h}.mp4"}
            for i, (w, h) in enumerate(files)
        ],
    }


def pixabay_hit(hid: int, renditions: dict[str, tuple[int, int]], duration: int = 15, user: str = "bob") -> dict:
    videos = {}
    for name in ("large", "medium", "small", "tiny"):
        if name in renditions:
            w, h = renditions[name]
            videos[name] = {"url": f"https://cdn.pixabay.com/video/{hid}_{name}.mp4", "width": w,
                            "height": h, "size": 1000, "thumbnail": ""}
        else:  # Pixabay returns an empty url and zero size when a rendition is missing
            videos[name] = {"url": "", "width": 0, "height": 0, "size": 0, "thumbnail": ""}
    return {"id": hid, "duration": duration, "tags": "x", "videos": videos, "user": user, "user_id": 7}


def clip(name: str, duration: float | None = 10.0, source: str = "fake", kind: str = "video") -> ClipAsset:
    return ClipAsset(path=Path("/clips") / name, kind=kind, duration=duration, source=source, query=name)


def make_narration(spans: list[tuple[float, float, str]], duration: float) -> Narration:
    timed = [TimedSegment(Segment(text=f"segment {i}", visual_query=q), a, b) for i, (a, b, q) in enumerate(spans)]
    return Narration(audio_path=Path("narration.wav"), duration=duration, words=[], segments=timed, engine="test")


class FakeProvider(VisualProvider):
    """Returns clips from ``table[query]`` (or ``default``), recording every call."""

    def __init__(self, cfg, name="fake", table=None, default=None, stock=False, spares=2, error=None):
        super().__init__(cfg)
        self.name = name
        self.stock = stock
        self.spares = spares
        self.table = table or {}
        self.default = default
        self.error = error
        self.calls: list[tuple[str, float, int]] = []

    def search(self, query, min_seconds, count=1):
        self.calls.append((query, min_seconds, count))
        if self.error:
            raise self.error
        clips = self.table.get(query, self.default if self.default is not None else [])
        return list(clips)[:count]


def assert_covers(shots: list[Shot], duration: float, max_shot: float) -> None:
    assert shots, "no shots"
    assert shots[0].start == 0.0
    assert shots[-1].end == duration
    for a, b in zip(shots, shots[1:]):
        assert a.end == b.start
    for s in shots:
        assert 0 < s.end - s.start <= max_shot + 1e-3


# --------------------------------------------------------------------------- shared helpers


def test_rendition_key_prefers_portrait_and_closest_size():
    files = [(3840, 2160), (720, 1280), (1080, 1920), (1440, 2560), (540, 960)]
    best = min(files, key=lambda f: rendition_key(*f, 1080, 1920))
    assert best == (1080, 1920)
    # portrait but small loses to tall-enough portrait; landscape always loses to portrait
    assert min([(540, 960), (720, 1280), (1920, 1080)], key=lambda f: rendition_key(*f, 1080, 1920)) == (720, 1280)
    # landscape only: the taller file wins (only the height survives the 9:16 crop)
    assert min([(1280, 720), (1920, 1080)], key=lambda f: rendition_key(*f, 1080, 1920)) == (1920, 1080)


def test_simplify_query():
    assert simplify_query("dark ocean waves") == ["ocean"]
    assert simplify_query("octopus underwater") == ["octopus"]
    assert simplify_query("octopus") == []
    assert simplify_query("dark ocean waves", ["why octopuses have three hearts"]) == ["ocean", "octopuses hearts"]
    # alternatives never repeat the original query
    assert simplify_query("ocean", ["ocean"]) == []


# --------------------------------------------------------------------------- pexels


def test_rendition_key_avoids_4k_when_a_smaller_good_file_exists():
    def best(files):
        return min(files, key=lambda f: rendition_key(*f, 1080, 1920))

    assert best([(3840, 2160), (2560, 1440), (1920, 1080)]) == (2560, 1440)
    assert best([(2160, 3840), (1440, 2560), (1080, 1920)]) == (1080, 1920)
    assert best([(3840, 2160), (1280, 720)]) == (3840, 2160)  # still better than a tiny file


def test_pexels_best_file_skips_hls_and_prefers_portrait():
    video = pexels_video(1, [(3840, 2160), (1080, 1920), (1440, 2560), (720, 1280)])
    video["video_files"].append({"id": 99, "quality": "hls", "file_type": "video/mp4", "width": None,
                                 "height": None, "link": "https://x/playlist.m3u8"})
    video["video_files"].append({"id": 98, "quality": "hd", "file_type": "video/quicktime", "width": 1080,
                                 "height": 1920, "link": "https://x/a.mov"})
    best = pexels.best_file(video, 1080, 1920)
    assert (best["width"], best["height"]) == (1080, 1920)
    assert best["link"].endswith(".mp4")


def test_pexels_choose_videos_order_and_duration():
    landscape = pexels_video(1, [(1920, 1080), (3840, 2160)], duration=20)
    too_short = pexels_video(2, [(1080, 1920)], duration=2)
    good = pexels_video(3, [(1080, 1920)], duration=10)
    shortish = pexels_video(4, [(1080, 1920)], duration=3)  # >= min(4, 3) but < 4
    picks = pexels.choose_videos([landscape, too_short, shortish, good], 4.0, 5, 1080, 1920)
    assert [v["id"] for v, _ in picks] == [3, 4, 1]
    assert pexels.choose_videos([landscape, good], 4.0, 1, 1080, 1920)[0][0]["id"] == 3


def test_pexels_search_downloads_and_reuses_cache(cfg, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "secret-key")
    payload = {"videos": [pexels_video(11, [(720, 1280), (1080, 1920)]),
                          pexels_video(12, [(1080, 1920)], user="Max")]}
    prov = PexelsProvider(cfg)
    prov._session = FakeSession(lambda p: FakeResponse(200, payload), pexels.API_URL)

    clips = prov.search("octopus  underwater", min_seconds=4, count=2)

    assert [c.path.name for c in clips] == ["pexels_11_1080x1920.mp4", "pexels_12_1080x1920.mp4"]
    assert all(c.path.parent == cfg.path("cache") and c.path.exists() for c in clips)
    assert clips[0].attribution == "Video by Jane Doe on Pexels"
    assert clips[1].attribution == "Video by Max on Pexels"
    assert (clips[0].source, clips[0].kind, clips[0].duration, clips[0].width) == ("pexels", "video", 12.0, 1080)
    call = prov._session.api_calls[0]
    assert call["headers"] == {"Authorization": "secret-key"}
    assert call["params"]["query"] == "octopus underwater"
    assert call["params"]["orientation"] == "portrait"
    assert call["params"]["size"] == "medium"
    assert len(prov._session.downloads) == 2

    prov.search("octopus underwater", min_seconds=4, count=2)
    assert len(prov._session.downloads) == 2  # cached files reused


def test_pexels_retries_without_orientation(cfg, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "k")

    def api(params):
        if params.get("orientation") == "portrait":
            return FakeResponse(200, {"videos": []})
        return FakeResponse(200, {"videos": [pexels_video(5, [(1920, 1080), (2560, 1440)])]})

    prov = PexelsProvider(cfg)
    prov._session = FakeSession(api, pexels.API_URL)
    clips = prov.search("volcano", min_seconds=3, count=1)
    assert len(prov._session.api_calls) == 2
    assert "orientation" not in prov._session.api_calls[1]["params"]
    assert [c.path.name for c in clips] == ["pexels_5_2560x1440.mp4"]


def test_pexels_without_key_returns_nothing(cfg, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    prov = PexelsProvider(cfg)
    prov._session = ExplodingSession()
    assert prov.search("cats", 4, 3) == []


def test_pexels_network_error_returns_nothing(cfg, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    prov = PexelsProvider(cfg)
    prov._session = FakeSession(lambda p: requests.ConnectionError("offline"), pexels.API_URL)
    assert prov.search("cats", 4, 3) == []


def test_pexels_rejected_key_stops_asking(cfg, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "bad")
    prov = PexelsProvider(cfg)
    prov._session = FakeSession(lambda p: FakeResponse(401, {"error": "nope"}), pexels.API_URL)
    assert prov.search("cats", 4, 3) == []
    assert prov.search("dogs", 4, 3) == []
    assert len(prov._session.api_calls) == 1


def test_pexels_failed_download_is_skipped(cfg, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    payload = {"videos": [pexels_video(1, [(1080, 1920)]), pexels_video(2, [(1080, 1920)])]}
    session = FakeSession(lambda p: FakeResponse(200, payload), pexels.API_URL)
    real_get = session.get

    def get(url, **kw):
        if "/1/" in url:
            raise requests.ConnectionError("reset")
        return real_get(url, **kw)

    session.get = get
    prov = PexelsProvider(cfg)
    prov._session = session
    clips = prov.search("x", 4, 2)
    assert [c.path.name for c in clips] == ["pexels_2_1080x1920.mp4"]
    assert not list(cfg.path("cache").glob("*.part"))


# --------------------------------------------------------------------------- pixabay


def test_pixabay_best_rendition():
    landscape = pixabay_hit(1, {"medium": (1920, 1080), "small": (1280, 720), "tiny": (640, 360)})
    r = pixabay.best_rendition(landscape, 1080, 1920)
    assert (r["width"], r["height"]) == (1920, 1080)  # empty "large" skipped
    portrait = pixabay_hit(2, {"large": (2160, 3840), "medium": (1080, 1920), "small": (720, 1280)})
    r = pixabay.best_rendition(portrait, 1080, 1920)
    assert (r["width"], r["height"]) == (1080, 1920)
    assert pixabay.best_rendition(pixabay_hit(3, {"tiny": (640, 360)}), 1080, 1920) is None


def test_pixabay_choose_hits_prefers_portrait():
    hits = [
        pixabay_hit(1, {"large": (1920, 1080)}),
        pixabay_hit(2, {"medium": (1080, 1920)}),
        pixabay_hit(3, {"medium": (1080, 1920)}, duration=1),  # too short
    ]
    assert [h["id"] for h, _ in pixabay.choose_hits(hits, 5, 5, 1080, 1920)] == [2, 1]


def test_pixabay_limit_query():
    long = " ".join(["wonderful"] * 30) + " ünïcode & stuff"
    q = pixabay.limit_query(long)
    assert len(quote_plus(q)) <= 100 and q.startswith("wonderful") and not q.endswith(" ")
    assert pixabay.limit_query("  ocean   waves ") == "ocean waves"
    assert len(quote_plus(pixabay.limit_query("x" * 300))) <= 100


def test_pixabay_search_and_24h_cache(cfg, monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "pk")
    payload = {"total": 2, "totalHits": 2, "hits": [
        pixabay_hit(21, {"large": (1920, 1080), "medium": (1280, 720)}, user="alice"),
        pixabay_hit(22, {"large": (2160, 3840), "medium": (1080, 1920)}, user="bob"),
    ]}
    prov = PixabayProvider(cfg)
    prov._session = FakeSession(lambda p: FakeResponse(200, payload), pixabay.API_URL)

    clips = prov.search("ocean waves", 4, 2)
    assert [c.path.name for c in clips] == ["pixabay_22_1080x1920.mp4", "pixabay_21_1920x1080.mp4"]
    assert clips[0].attribution == "Video by bob on Pixabay"
    assert clips[0].source == "pixabay"
    params = prov._session.api_calls[0]["params"]
    assert params == {"key": "pk", "q": "ocean waves", "safesearch": "true", "per_page": 20}

    cache_file = prov.search_cache_path("ocean waves")
    assert cache_file.parent == cfg.path("cache") / "pixabay_search" and cache_file.exists()
    assert "pk" not in cache_file.read_text()  # the key is never cached

    prov.search("ocean waves", 4, 2)
    assert len(prov._session.api_calls) == 1  # served from the 24 h cache

    old = time.time() - 25 * 3600
    os.utime(cache_file, (old, old))
    prov.search("ocean waves", 4, 2)
    assert len(prov._session.api_calls) == 2  # expired -> asked again


def test_pixabay_uses_stale_cache_when_offline(cfg, monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "pk")
    payload = {"hits": [pixabay_hit(31, {"medium": (1080, 1920)})]}
    prov = PixabayProvider(cfg)
    prov._session = FakeSession(lambda p: FakeResponse(200, payload), pixabay.API_URL)
    prov.search("forest", 4, 1)
    cache_file = prov.search_cache_path("forest")
    old = time.time() - 48 * 3600
    os.utime(cache_file, (old, old))

    prov._session = FakeSession(lambda p: requests.ConnectionError("offline"), pixabay.API_URL)
    clips = prov.search("forest", 4, 1)
    assert [c.path.name for c in clips] == ["pixabay_31_1080x1920.mp4"]


def test_pixabay_without_key_or_on_error(cfg, monkeypatch):
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    prov = PixabayProvider(cfg)
    prov._session = ExplodingSession()
    assert prov.search("cats", 4, 2) == []

    monkeypatch.setenv("PIXABAY_API_KEY", "pk")
    prov = PixabayProvider(cfg)
    prov._session = FakeSession(lambda p: FakeResponse(500, text="boom"), pixabay.API_URL)
    assert prov.search("cats", 4, 2) == []

    prov = PixabayProvider(cfg)
    prov._session = FakeSession(lambda p: FakeResponse(400, text="[ERROR 400] Invalid or missing API key"),
                                pixabay.API_URL)
    assert prov.search("cats", 4, 2) == []
    assert prov.search("dogs", 4, 2) == []
    assert len(prov._session.api_calls) == 1


def test_pixabay_connection_error_does_not_log_the_key(cfg, monkeypatch, caplog):
    """Pixabay's key is a query parameter, and requests puts the URL into ConnectionError."""
    monkeypatch.setenv("PIXABAY_API_KEY", "SECRET_PIXABAY_KEY_123")
    prov = PixabayProvider(cfg)

    def refuse(params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return requests.ConnectionError(
            "HTTPSConnectionPool(host='pixabay.com', port=443): Max retries exceeded with url: "
            f"/api/videos/?{query} (Caused by NewConnectionError('refused'))")

    prov._session = FakeSession(refuse, pixabay.API_URL)
    with caplog.at_level(logging.DEBUG, logger="autoshorts"):
        assert prov.search("ocean waves", 4, 1) == []
    assert "Pixabay search for 'ocean waves' failed" in caplog.text
    assert "SECRET_PIXABAY_KEY_123" not in caplog.text
    assert "key=***" in caplog.text


def test_short_error_masks_credential_query_values():
    from autoshorts.visuals import short_error

    exc = requests.ConnectionError("Max retries exceeded with url: /v/?upload_id=1&upload_token=TOK&key=K2 (x)")
    text = short_error(exc)
    assert "TOK" not in text and "K2" not in text and "upload_id=1" in text


# --------------------------------------------------------------------------- local


@pytest.fixture
def local_dir(cfg) -> Path:
    root = cfg.path("backgrounds")
    files = [
        "ocean/waves_01.mp4", "ocean/Deep Sea.mov", "MinecraftParkour_02.mp4", "satisfying/soap.webm",
        "city_night.jpg", "notes.txt", ".hidden.mp4", ".cache/skip.mp4",
    ]
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return root


@pytest.fixture
def durations(monkeypatch):
    """Fake ffprobe: every video lasts 10 s unless listed; counts probes."""
    table: dict[str, float | Exception] = {}
    calls: list[str] = []
    local._DURATIONS.clear()
    local._DECODES.clear()

    def fake(path):
        calls.append(Path(path).name)
        value = table.get(Path(path).name, 10.0)
        if isinstance(value, Exception):
            raise value
        return {"duration": value, "width": 1080, "height": 1920, "color_transfer": ""}

    monkeypatch.setattr(local, "probe_video", fake)
    monkeypatch.setattr(local, "decodes", lambda path: True)
    yield table, calls
    local._DURATIONS.clear()
    local._DECODES.clear()


def test_local_scan_skips_hidden_and_non_media(cfg, local_dir):
    names = sorted(p.name for p in LocalProvider(cfg).files())
    assert names == ["Deep Sea.mov", "MinecraftParkour_02.mp4", "city_night.jpg", "soap.webm", "waves_01.mp4"]


def test_local_keyword_match_first(cfg, local_dir, durations):
    prov = LocalProvider(cfg)
    clips = prov.search("ocean waves", min_seconds=4, count=2)
    assert clips[0].path.name == "waves_01.mp4"  # folder + file name match
    assert clips[1].path.name == "Deep Sea.mov"  # folder match
    assert all(c.source == "local" and c.kind == "video" and c.duration == 10.0 for c in clips)
    assert prov.search("minecraft gameplay", 4, 1)[0].path.name == "MinecraftParkour_02.mp4"


def test_local_images(cfg, local_dir, durations):
    clips = LocalProvider(cfg).search("city lights", 4, 1)
    assert clips[0].path.name == "city_night.jpg"
    assert clips[0].kind == "image" and clips[0].duration is None


def test_local_random_fallback(cfg, local_dir, durations):
    clips = LocalProvider(cfg).search("quantum physics", 4, 3)
    names = [c.path.name for c in clips]
    assert len(names) == 3 and len(set(names)) == 3
    assert set(names) <= {"Deep Sea.mov", "MinecraftParkour_02.mp4", "city_night.jpg", "soap.webm", "waves_01.mp4"}


def test_local_probe_cache_and_unreadable_files(cfg, local_dir, durations):
    table, calls = durations
    table["waves_01.mp4"] = AutoShortsError("broken file")
    table["Deep Sea.mov"] = 2.0
    prov = LocalProvider(cfg)
    clips = prov.search("ocean", min_seconds=4, count=5)
    names = [c.path.name for c in clips]
    assert "waves_01.mp4" not in names
    assert names[0] == "Deep Sea.mov"  # keyword match beats length...
    first_calls = len(calls)
    LocalProvider(cfg).search("ocean", min_seconds=4, count=5)
    assert len(calls) == first_calls  # ...and durations are probed once per process


def test_local_missing_folder(cfg):
    assert LocalProvider(cfg).search("anything", 4, 3) == []


def test_local_word_helpers():
    assert local.split_words("MinecraftParkour_02-night") == ["minecraft", "parkour", "night"]
    stems = [local.stem(w) for w in ("waves", "boxes", "cities", "glass", "bus")]
    assert stems == ["wave", "box", "city", "glass", "bus"]


# --------------------------------------------------------------------------- generated


@pytest.fixture
def small_video(cfg) -> Config:
    cfg.video.width, cfg.video.height, cfg.video.fps = 216, 384, 15
    return cfg


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


@needs_ffmpeg
def test_generated_renders_and_caches(small_video, monkeypatch):
    prov = GeneratedProvider(small_video)
    clips = prov.search("octopus underwater", min_seconds=2.0, count=1)
    assert len(clips) == 1
    c = clips[0]
    assert c.path.exists() and c.path.name.startswith("generated_") and c.path.suffix == ".mp4"
    assert c.path.parent == small_video.path("cache")
    assert (c.source, c.kind, c.duration, c.width, c.height) == ("generated", "video", 4.0, 216, 384)
    info = probe(c.path)
    streams = info["streams"]
    assert [s["codec_type"] for s in streams] == ["video"]  # no audio
    assert (streams[0]["width"], streams[0]["height"], streams[0]["pix_fmt"]) == (216, 384, "yuv420p")
    assert streams[0]["codec_name"] == "h264"
    assert abs(float(info["format"]["duration"]) - 4.0) < 0.15
    assert not list(c.path.parent.glob("*.tmp.mp4"))

    def boom(*a, **kw):
        raise AssertionError("should come from the cache")

    monkeypatch.setattr(generated, "render_look", boom)
    again = prov.search("octopus underwater", min_seconds=2.0, count=1)
    assert again[0].path == c.path


@needs_ffmpeg
@pytest.mark.parametrize("style", generated.STYLES)
def test_generated_every_style_renders(small_video, style, tmp_path):
    look = generated.Look(style, generated.PALETTES[3], 4242, 1.0, 1)
    out = generated.render_look(look, tmp_path / f"{style}.mp4", 216, 384, 15, 4.0)
    stream = probe(out)["streams"][0]
    assert (stream["width"], stream["height"]) == (216, 384)
    assert int(stream.get("nb_frames", 60)) == 60


def test_generated_looks_are_deterministic_and_varied():
    a1 = generated.choose_look(generated.seed_for("Octopus  underwater"))
    a2 = generated.choose_look(generated.seed_for("octopus underwater"))
    assert a1 == a2
    queries = ["octopus", "space", "ancient rome", "volcano", "money", "sleep", "sharks", "pyramids", "coffee"]
    looks = [generated.choose_look(generated.seed_for(q)) for q in queries]
    assert len({lk.style for lk in looks}) >= 2
    assert len({(lk.style, lk.palette, lk.seed) for lk in looks}) == len(queries)
    args = [generated.ffmpeg_args(lk, 216, 384, 15, 4.0, Path("o.mp4")) for lk in looks]
    assert len({tuple(a) for a in args}) == len(queries)
    assert generated.seed_for("x", 0) != generated.seed_for("x", 1)
    assert generated.clip_seconds(1.2) == 4.0 and generated.clip_seconds(4.2) == 4.5


def test_generated_args_are_safe_for_any_platform():
    for style in generated.STYLES:
        look = generated.Look(style, generated.PALETTES[0], 7, 1.0, 2)
        args = generated.ffmpeg_args(look, 1080, 1920, 30, 4.0, Path("out dir") / "o.mp4")
        assert all(" " not in a for a in args[:-1])  # no shell, no spaces to quote
        assert "libx264" in args and "yuv420p" in args and "-an" in args
        assert args[args.index("-frames:v") + 1] == "120"


@needs_ffmpeg
def test_generated_falls_back_to_portable_style(small_video, monkeypatch, tmp_path):
    def broken(look, width, height, fps, duration, frames):
        return ["-f", "lavfi", "-i", "nosuchsource=s=10x10"], "[0:v]null"

    monkeypatch.setattr(generated, "_gradient", broken)
    look = generated.Look("gradient", generated.PALETTES[1], 9, 1.0, 0)
    out = generated.render_look(look, tmp_path / "g.mp4", 216, 384, 15, 4.0)
    assert out.exists() and probe(out)["streams"][0]["width"] == 216


def test_generated_timeout_falls_back_and_leaves_no_temp_file(monkeypatch, tmp_path):
    """A timeout (now an AutoShortsError from run_ffmpeg) must try the portable style, and
    a half-written .tmp.mp4 must never stay in the cache."""
    styles = []

    def fake_run(args, desc="", timeout=None, cwd=None):
        styles.append(desc)
        Path(args[-1]).write_bytes(b"partial")
        raise AutoShortsError(f"{desc} timed out after {timeout:.0f}s")

    monkeypatch.setattr(generated, "run_ffmpeg", fake_run)
    look = generated.Look("nebula", generated.PALETTES[0], 3, 1.0, 0)
    with pytest.raises(AutoShortsError, match="timed out"):
        generated.render_look(look, tmp_path / "g.mp4", 216, 384, 15, 4.0)
    assert len(styles) == 2 and generated.FALLBACK_STYLE in styles[1]
    assert list(tmp_path.iterdir()) == []


def test_run_ffmpeg_converts_timeout(monkeypatch):
    def slow(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=kw.get("timeout"))

    monkeypatch.setattr(utils, "require_binary", lambda name: name)
    monkeypatch.setattr(utils.subprocess, "run", slow)
    with pytest.raises(AutoShortsError, match="shot 3 timed out after 5s"):
        utils.run_ffmpeg(["-i", "x"], desc="shot 3", timeout=5)


def test_ffprobe_output_is_decoded_as_utf8(monkeypatch):
    """On Windows the locale code page (cp1252/cp1253) cannot decode the UTF-8 file names
    ffprobe prints; text=True without an encoding crashed every video in C:\\Users\\Γιώργος."""
    raw = json.dumps({"format": {"filename": "C:\\Users\\Γιώργος\\seg_00.mp3", "duration": "2.5"},
                      "streams": [{"codec_type": "audio", "tags": {"title": "Μαρία ♪"}}]},
                     ensure_ascii=False).encode("utf-8")

    def fake_run(cmd, **kw):
        encoding = kw.get("encoding") or ("cp1253" if kw.get("text") else None)
        out = raw.decode(encoding, kw.get("errors") or "strict") if encoding else raw
        return subprocess.CompletedProcess(cmd, 0, out, "" if encoding else b"")

    monkeypatch.setattr(utils, "require_binary", lambda name: name)
    monkeypatch.setattr(utils.subprocess, "run", fake_run)
    assert utils.media_duration("seg_00.mp3") == 2.5
    assert "Γιώργος" in utils.ffprobe_json("x")["format"]["filename"]


def test_ffprobe_garbage_output_is_an_autoshorts_error(monkeypatch):
    monkeypatch.setattr(utils, "require_binary", lambda name: name)
    monkeypatch.setattr(utils.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "{oops", ""))
    with pytest.raises(AutoShortsError, match="unreadable output"):
        utils.media_duration("x.mp4")


@needs_ffmpeg
def test_generated_variants_for_count(small_video):
    clips = GeneratedProvider(small_video).search("rain", 1.0, count=2)
    assert len(clips) == 2 and clips[0].path != clips[1].path


# --------------------------------------------------------------------------- providers / planning


def test_get_providers_order_and_unknown(cfg):
    cfg.visuals.providers = ["Local", "generated", "local"]
    provs = get_providers(cfg)
    assert [type(p) for p in provs] == [LocalProvider, GeneratedProvider]
    cfg.visuals.providers = ["pexels", "pixabay"]
    assert [p.name for p in get_providers(cfg)] == ["pexels", "pixabay"]
    assert all(p.stock for p in get_providers(cfg))
    cfg.visuals.providers = ["pexels", "giphy"]
    with pytest.raises(AutoShortsError, match="giphy"):
        get_providers(cfg)


def test_segment_windows_absorb_gaps_and_merge_tiny_ones():
    nar = make_narration([(0.05, 3.0, "a"), (3.15, 3.2, "b"), (3.35, 9.0, "c")], duration=9.4)
    wins = segment_windows(nar)
    assert [(a, b, s.visual_query) for a, b, s in wins] == [(0.0, 3.35, "a"), (3.35, 9.4, "c")]
    assert segment_windows(make_narration([], 5.0)) == [(0.0, 5.0, None)]
    assert split_window(0.0, 9.0, 4.0) == [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0)]
    assert split_window(2.0, 3.0, 0) == [(2.0, 3.0)]


def test_plan_shots_covers_duration_without_reuse(cfg, monkeypatch, tmp_path):
    many = [clip(f"c{i}.mp4") for i in range(20)]
    prov = FakeProvider(cfg, default=many)
    monkeypatch.setattr(visuals, "get_providers", lambda c: [prov])
    nar = make_narration([(0.0, 3.0, "a"), (3.15, 9.0, "b"), (9.15, 12.5, "c")], duration=12.9)

    shots = plan_shots(cfg, nar, tmp_path / "work")

    assert_covers(shots, 12.9, 4.0)
    assert [(s.start, s.end) for s in shots] == [(0.0, 3.15), (3.15, 6.15), (6.15, 9.15), (9.15, 12.9)]
    assert len({s.clip.path for s in shots}) == len(shots)
    # count = n_shots + 2 spares, min_seconds = the shot length
    assert prov.calls[0] == ("a", 3.15, 3)
    assert prov.calls[1][0] == "b" and prov.calls[1][2] == 4 and abs(prov.calls[1][1] - 3.0) < 1e-9
    # the fake returns the same top clips for every query: once they are all used,
    # plan_shots asks the provider for a deeper list instead of reusing one
    assert prov.calls[2][0] == "c" and prov.calls[2][2] == 3
    assert prov.calls[3][0] == "c" and prov.calls[3][2] == 6
    plan = json.loads((tmp_path / "work" / "shots.json").read_text())
    assert len(plan) == 4 and plan[-1]["end"] == 12.9


def test_plan_shots_falls_through_providers(cfg, monkeypatch, tmp_path):
    first = FakeProvider(cfg, name="first", default=[clip("only.mp4")])
    second = FakeProvider(cfg, name="second", default=[clip(f"s{i}.mp4") for i in range(10)])
    monkeypatch.setattr(visuals, "get_providers", lambda c: [first, second])
    nar = make_narration([(0.0, 8.0, "space")], duration=8.0)

    shots = plan_shots(cfg, nar, tmp_path)

    assert_covers(shots, 8.0, 4.0)
    assert [s.clip.path.name for s in shots] == ["only.mp4", "s0.mp4"]
    assert first.calls == [("space", 4.0, 4)]
    assert second.calls == [("space", 4.0, 3)]  # 1 still missing + 2 spares


def test_plan_shots_retries_stock_with_simpler_query(cfg, monkeypatch, tmp_path):
    stock = FakeProvider(cfg, name="stock", stock=True, table={"ocean": [clip("ocean.mp4")]})
    later = FakeProvider(cfg, name="later", default=[clip("later.mp4")])
    monkeypatch.setattr(visuals, "get_providers", lambda c: [stock, later])
    nar = make_narration([(0.0, 3.0, "dark ocean waves")], duration=3.0)

    shots = plan_shots(cfg, nar, tmp_path)

    assert [s.clip.path.name for s in shots] == ["ocean.mp4"]
    assert [c[0] for c in stock.calls] == ["dark ocean waves", "ocean"]
    assert later.calls == []


def test_plan_shots_uses_topic_as_fallback_query(cfg, monkeypatch, tmp_path):
    stock = FakeProvider(cfg, name="stock", stock=True, table={"octopuses hearts": [clip("o.mp4")]})
    monkeypatch.setattr(visuals, "get_providers", lambda c: [stock])
    nar = make_narration([(0.0, 3.0, "xyzzy")], duration=3.0)
    shots = plan_shots(cfg, nar, tmp_path, topic="Why octopuses have three hearts")
    assert shots[0].clip.path.name == "o.mp4"


def test_plan_shots_avoids_reuse_across_segments(cfg, monkeypatch, tmp_path):
    shared = [clip("x.mp4"), clip("y.mp4"), clip("z.mp4")]
    prov = FakeProvider(cfg, default=shared)
    monkeypatch.setattr(visuals, "get_providers", lambda c: [prov])
    nar = make_narration([(0.0, 3.0, "a"), (3.0, 6.0, "b"), (6.0, 9.0, "c")], duration=9.0)
    shots = plan_shots(cfg, nar, tmp_path)
    assert sorted(s.clip.path.name for s in shots) == ["x.mp4", "y.mp4", "z.mp4"]


def test_plan_shots_reuses_when_candidates_run_out(cfg, monkeypatch, tmp_path):
    prov = FakeProvider(cfg, default=[clip("a.mp4"), clip("b.mp4")])
    monkeypatch.setattr(visuals, "get_providers", lambda c: [prov])
    nar = make_narration([(0.0, 7.5, "q"), (7.5, 15.0, "r")], duration=15.2)
    shots = plan_shots(cfg, nar, tmp_path)
    assert_covers(shots, 15.2, 4.0)
    names = [s.clip.path.name for s in shots]
    assert set(names) == {"a.mp4", "b.mp4"}
    assert all(x != y for x, y in zip(names, names[1:]))  # never the same clip twice in a row


def test_plan_shots_skips_unavailable_providers(cfg, monkeypatch, tmp_path):
    class NoKey(FakeProvider):
        available = False

    offline = NoKey(cfg, name="nokey", stock=True, default=[clip("never.mp4")])
    good = FakeProvider(cfg, name="good", default=[clip("g.mp4")])
    monkeypatch.setattr(visuals, "get_providers", lambda c: [offline, good])
    shots = plan_shots(cfg, make_narration([(0.0, 2.0, "dark ocean")], 2.0), tmp_path)
    assert shots[0].clip.path.name == "g.mp4"
    assert offline.calls == []


def test_stock_providers_report_availability(cfg, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.setenv("PIXABAY_API_KEY", "pk")
    assert not PexelsProvider(cfg).available
    prov = PixabayProvider(cfg)
    assert prov.available
    prov._session = FakeSession(lambda p: FakeResponse(403, text="[ERROR 403] API key invalid"), pixabay.API_URL)
    prov.search("x", 4, 1)
    assert not prov.available


def test_plan_shots_survives_a_failing_provider(cfg, monkeypatch, tmp_path):
    bad = FakeProvider(cfg, name="bad", error=RuntimeError("kaboom"))
    good = FakeProvider(cfg, name="good", default=[clip("g.mp4")])
    monkeypatch.setattr(visuals, "get_providers", lambda c: [bad, good])
    shots = plan_shots(cfg, make_narration([(0.0, 2.0, "q")], 2.0), tmp_path)
    assert shots[0].clip.path.name == "g.mp4"


def test_plan_shots_fills_empty_segments_from_other_footage(cfg, monkeypatch, tmp_path):
    prov = FakeProvider(cfg, table={"found": [clip("f1.mp4"), clip("f2.mp4")]})
    monkeypatch.setattr(visuals, "get_providers", lambda c: [prov])
    nar = make_narration([(0.0, 3.0, "found"), (3.0, 6.0, "missing")], duration=6.0)
    shots = plan_shots(cfg, nar, tmp_path)
    assert_covers(shots, 6.0, 4.0)
    assert [s.clip.path.name for s in shots] == ["f1.mp4", "f2.mp4"]


def test_plan_shots_generated_safety_net(cfg, monkeypatch, tmp_path):
    empty = FakeProvider(cfg, name="stock", stock=True)
    net = FakeProvider(cfg, name="generated", default=[clip("gen.mp4", source="generated")], spares=0)
    monkeypatch.setattr(visuals, "get_providers", lambda c: [empty])
    monkeypatch.setattr(visuals, "_provider_class", lambda name: (lambda c: net))
    shots = plan_shots(cfg, make_narration([(0.0, 3.0, "q")], 3.0), tmp_path)
    assert shots[0].clip.source == "generated"
    assert net.calls == [("q", 3.0, 1)]


def test_plan_shots_raises_when_nothing_at_all(cfg, monkeypatch, tmp_path):
    empty = FakeProvider(cfg, name="generated")
    monkeypatch.setattr(visuals, "get_providers", lambda c: [empty])
    with pytest.raises(AutoShortsError, match="no background visuals"):
        plan_shots(cfg, make_narration([(0.0, 3.0, "q")], 3.0), tmp_path)
    with pytest.raises(AutoShortsError):
        plan_shots(cfg, make_narration([], 0.0), tmp_path)


@needs_ffmpeg
def test_plan_shots_end_to_end_with_local_and_generated(small_video, monkeypatch, tmp_path):
    """Real providers: an empty local folder falls through to real generated clips."""
    small_video.visuals.providers = ["pexels", "local", "generated"]
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    nar = make_narration([(0.0, 2.5, "galaxy stars"), (2.6, 5.0, "black hole")], duration=5.3)
    shots = plan_shots(small_video, nar, tmp_path / "work")
    assert_covers(shots, 5.3, 4.0)
    assert {s.clip.source for s in shots} == {"generated"}
    assert all(s.clip.path.exists() for s in shots)
    assert shots[0].clip.path != shots[1].clip.path
