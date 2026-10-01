"""Tests for autoshorts.pipeline with every stage replaced by a fast fake."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest

from autoshorts import pipeline
from autoshorts.config import Config
from autoshorts.models import (
    ClipAsset, Narration, RenderResult, Segment, TimedSegment, UploadResult, VideoScript, WordTiming,
)
from autoshorts.script import resolve_format
from autoshorts.topics import TopicQueue
from autoshorts.utils import AutoShortsError

SEG_SECONDS = 5.0


@dataclass
class FakeShot:
    clip: ClipAsset
    start: float
    end: float


@dataclass
class Fakes:
    """Records stage calls; tweak attributes to change behaviour per test."""

    tmp: Path
    n_segments: int = 6
    seg_seconds: float = SEG_SECONDS
    fail_stage: str = ""
    fail_topics: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    synth_segments: list[int] = field(default_factory=list)
    youtube: object = None  # UploadResult or Exception
    tiktok: object = None

    def install(self, mp: pytest.MonkeyPatch) -> None:
        for name in ("generate_script", "synthesize_narration", "build_captions", "plan_shots", "pick_music",
                     "render_video", "make_thumbnail", "build_metadata", "write_metadata",
                     "upload_youtube", "upload_tiktok"):
            mp.setattr(pipeline, name, getattr(self, name))

    def _check(self, stage: str) -> None:
        self.calls.append(stage)
        if self.fail_stage == stage:
            raise AutoShortsError(f"{stage} broke")

    def generate_script(self, cfg, topic, fmt):
        self._check("script")
        if topic in self.fail_topics:
            raise AutoShortsError(f"no script for {topic}")
        fmt = resolve_format(fmt)  # like real generators, which resolve "random" themselves
        segs = [Segment(text=f"Sentence number {i}.", visual_query=f"query {i}") for i in range(self.n_segments)]
        return VideoScript(topic=topic, format=fmt, title=f"Title about {topic}", segments=segs,
                           description="A description.", hashtags=["facts"])

    def synthesize_narration(self, cfg, script, work):
        self._check("tts")
        self.synth_segments.append(len(script.segments))
        audio = Path(work) / "narration.wav"
        audio.write_bytes(b"RIFF")
        timed, words, t = [], [], 0.0
        for seg in script.segments:
            timed.append(TimedSegment(segment=seg, start=t, end=t + self.seg_seconds - 0.2))
            words.append(WordTiming(word=seg.text.split()[0], start=t, end=t + 0.5))
            t += self.seg_seconds
        return Narration(audio_path=audio, duration=t, words=words, segments=timed, engine="fake")

    def build_captions(self, cfg, narration, script, out_path):
        self._check("captions")
        Path(out_path).write_text("[Script Info]\n", encoding="utf-8")
        return Path(out_path)

    def plan_shots(self, cfg, narration, work, topic=""):
        self._check("visuals")
        clip_dir = self.tmp / "clips"
        clip_dir.mkdir(exist_ok=True)
        clips = []
        for name in ("a", "b"):
            p = clip_dir / f"{name}.mp4"
            p.write_bytes(b"x")
            clips.append(ClipAsset(path=p, duration=10.0, source="local", query=name, attribution=f"by {name}"))
        # three shots, clip "a" used twice
        third = narration.duration / 3
        return [FakeShot(clips[0], 0, third), FakeShot(clips[1], third, 2 * third),
                FakeShot(clips[0], 2 * third, narration.duration)]

    def pick_music(self, cfg):
        self._check("music")
        return None

    def render_video(self, cfg, narration, shots, ass_path, out_path, music_path, workdir):
        self._check("render")
        assert Path(ass_path).is_file()
        (Path(workdir) / "background.mp4").write_bytes(b"bg")
        Path(out_path).write_bytes(b"video")
        return RenderResult(video_path=Path(out_path), duration=narration.duration + 0.4)

    def make_thumbnail(self, video_path, out_path):
        self._check("thumbnail")
        Path(out_path).write_bytes(b"jpg")
        return Path(out_path)

    def build_metadata(self, cfg, script, clips):
        self._check("metadata")
        return {
            "youtube": {"title": script.title, "description": "d", "tags": ["t"]},
            "tiktok": {"caption": f"{script.title} #facts", "privacy_level": "SELF_ONLY", "is_aigc": True},
            "credits": [c.attribution for c in clips],
        }

    def write_metadata(self, folder, meta):
        path = Path(folder) / "metadata.json"
        path.write_text(json.dumps(meta), encoding="utf-8")
        for name in ("youtube.txt", "tiktok.txt", "credits.txt"):
            (Path(folder) / name).write_text("x", encoding="utf-8")
        return path

    def _upload(self, platform, outcome):
        self.calls.append(f"upload:{platform}")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome or UploadResult(platform=platform, ok=True, id="id1", url=f"https://{platform}/id1")

    def upload_youtube(self, cfg, video, meta, thumbnail):
        assert meta["title"].startswith("Title")
        assert thumbnail is None or Path(thumbnail).is_file()
        return self._upload("youtube", self.youtube)

    def upload_tiktok(self, cfg, video, meta):
        assert "caption" in meta
        return self._upload("tiktok", self.tiktok)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.base_dir = tmp_path / "project"
    c.base_dir.mkdir()
    c.script.format = "facts"
    return c


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fakes:
    f = Fakes(tmp=tmp_path)
    f.install(monkeypatch)
    return f


def job_folders(cfg: Config) -> list[Path]:
    out = cfg.path(cfg.output_dir)
    return sorted(p for p in out.iterdir() if p.is_dir()) if out.is_dir() else []


# --------------------------------------------------------------------------- make_video


def test_make_video_creates_complete_job_folder(cfg, fakes):
    job = pipeline.make_video(cfg, topic="Why the sky is blue")

    folder = job.folder
    assert folder.parent == cfg.path("output")
    assert folder.name.endswith("-why-the-sky-is-blue")
    assert len(folder.name.split("-")[0]) == 8 and len(folder.name.split("-")[1]) == 6  # YYYYMMDD-HHMMSS
    for name in ("video.mp4", "thumbnail.jpg", "script.json", "captions.ass", "metadata.json",
                 "youtube.txt", "tiktok.txt", "credits.txt", "job.json"):
        assert (folder / name).is_file(), name
    assert not (folder / "work").exists()
    assert not (folder / "error.txt").exists()

    assert fakes.calls == ["script", "tts", "captions", "visuals", "music", "render", "thumbnail", "metadata"]
    assert [c.path.name for c in job.clips] == ["a.mp4", "b.mp4"]  # unique, in order
    assert job.render.thumbnail_path == folder / "thumbnail.jpg"

    script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
    assert script["title"] == "Title about Why the sky is blue"
    assert len(script["segments"]) == 6

    data = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert data["id"] == folder.name
    assert data["topic"] == "Why the sky is blue"
    assert data["status"] == "done"
    assert data["render"]["video_path"] == "video.mp4"
    assert data["render"]["thumbnail_path"] == "thumbnail.jpg"
    assert data["render"]["duration"] == pytest.approx(30.4)
    assert data["narration"]["audio_path"] == "work/narration.wav"
    assert data["script"]["format"] == "facts"
    assert data["uploads"] == []
    assert data["created_at"]
    assert Path(data["clips"][0]["path"]).name == "a.mp4"

    assert TopicQueue(cfg).is_used("Why the sky is blue")


def test_make_video_takes_next_topic_and_keeps_intermediates(cfg, fakes):
    cfg.path("topics.txt").write_text("First\nSecond\n", encoding="utf-8")
    cfg.keep_intermediate = True
    first = pipeline.make_video(cfg)
    second = pipeline.make_video(cfg)
    assert (first.topic, second.topic) == ("First", "Second")
    assert (first.folder / "work" / "background.mp4").is_file()
    assert TopicQueue(cfg).unused() == []


def test_random_format_and_invalid_format(cfg, fakes):
    cfg.script.format = "random"
    job = pipeline.make_video(cfg, topic="t1")
    assert job.script.format in ("facts", "story", "quiz", "motivation", "explainer")

    job = pipeline.make_video(cfg, topic="t2", fmt="quiz")
    assert job.script.format == "quiz"

    with pytest.raises(AutoShortsError, match="unknown script format"):
        pipeline.make_video(cfg, topic="t3", fmt="poem")
    assert not any(p.name.endswith("-t3") for p in job_folders(cfg))  # failed before creating a folder


def test_failure_leaves_error_txt_and_does_not_mark_topic(cfg, fakes):
    fakes.fail_stage = "render"
    with pytest.raises(AutoShortsError, match="render broke"):
        pipeline.make_video(cfg, topic="Doomed topic")

    [folder] = job_folders(cfg)
    error = (folder / "error.txt").read_text(encoding="utf-8")
    assert "Traceback" in error and "render broke" in error
    assert not (folder / "work").exists()
    data = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert "render broke" in data["error"]
    assert not TopicQueue(cfg).is_used("Doomed topic")


def test_thumbnail_failure_is_not_fatal(cfg, fakes):
    fakes.fail_stage = "thumbnail"
    job = pipeline.make_video(cfg, topic="no thumb")
    assert job.render.thumbnail_path is None
    assert (job.folder / "video.mp4").is_file()


def test_upload_failure_never_loses_the_video(cfg, fakes):
    fakes.youtube = AutoShortsError("quota exceeded")
    fakes.tiktok = RuntimeError("socket closed")
    job = pipeline.make_video(cfg, topic="Uploads", upload_to=("youtube", "tiktok"))

    assert [(u.platform, u.ok) for u in job.uploads] == [("youtube", False), ("tiktok", False)]
    assert "quota exceeded" in job.uploads[0].error
    assert "socket closed" in job.uploads[1].error
    assert (job.folder / "video.mp4").is_file()
    data = json.loads((job.folder / "job.json").read_text(encoding="utf-8"))
    assert data["status"] == "done"
    assert [u["ok"] for u in data["uploads"]] == [False, False]
    assert TopicQueue(cfg).is_used("Uploads")


def test_successful_upload_is_recorded(cfg, fakes):
    job = pipeline.make_video(cfg, topic="Uploads ok", upload_to="yt")
    assert fakes.calls[-1] == "upload:youtube"
    assert job.uploads == [UploadResult(platform="youtube", ok=True, id="id1", url="https://youtube/id1")]


def test_blank_topic_falls_back_to_queue(cfg, fakes):
    cfg.path("topics.txt").write_text("Queued topic\n", encoding="utf-8")
    assert pipeline.make_video(cfg, topic="   ").topic == "Queued topic"


def test_unknown_upload_platform_fails_fast(cfg, fakes):
    with pytest.raises(AutoShortsError, match="unknown upload platform"):
        pipeline.make_video(cfg, topic="x", upload_to=["instagram"])
    assert fakes.calls == []


def test_too_long_narration_is_shortened_and_resynthesized(cfg, fakes):
    cfg.video.max_seconds = 30  # limit 29.5 s; 10 segments x 5 s = 50 s
    fakes.n_segments = 10
    job = pipeline.make_video(cfg, topic="Long one")

    assert fakes.synth_segments[0] == 10
    assert fakes.synth_segments[-1] < 10
    assert fakes.synth_segments[-1] * SEG_SECONDS <= 29.5
    assert len(fakes.synth_segments) <= 4  # first try + at most 3 retries
    texts = [s.text for s in job.script.segments]
    assert texts[0] == "Sentence number 0."  # hook kept
    assert texts[-1] == "Sentence number 9."  # ending kept
    saved = json.loads((job.folder / "script.json").read_text(encoding="utf-8"))
    assert [s["text"] for s in saved["segments"]] == texts


def test_narration_that_cannot_be_shortened_fails(cfg, fakes):
    cfg.video.max_seconds = 30
    fakes.n_segments = 2
    fakes.seg_seconds = 40
    with pytest.raises(AutoShortsError, match="cannot be shortened"):
        pipeline.make_video(cfg, topic="Too long")
    assert fakes.synth_segments == [2]


def test_shortening_gives_up_after_three_retries(cfg, fakes, monkeypatch):
    cfg.video.max_seconds = 30
    fakes.n_segments = 12
    original = fakes.synthesize_narration

    def always_long(c, script, work):
        n = original(c, script, work)
        n.duration = 30.5  # 1 s over the limit however short the script gets
        return n

    monkeypatch.setattr(pipeline, "synthesize_narration", always_long)
    with pytest.raises(AutoShortsError, match="still 30.5s"):
        pipeline.make_video(cfg, topic="Slow voice")
    assert fakes.synth_segments == [12, 11, 10, 9]  # one segment dropped per retry, 3 retries


def test_shorten_script_drops_second_to_last_segments():
    segs = [Segment(text=str(i), visual_query="") for i in range(5)]
    script = VideoScript(topic="t", format="facts", title="T", segments=segs, description="")
    timed = [TimedSegment(segment=s, start=i * 10.0, end=i * 10.0 + 9.0) for i, s in enumerate(segs)]
    narration = Narration(audio_path=Path("n.wav"), duration=49.0, words=[], segments=timed)

    short = pipeline.shorten_script(script, narration, 45.0)
    assert [s.text for s in short.segments] == ["0", "1", "2", "4"]
    short = pipeline.shorten_script(script, narration, 30.0)  # 19 s over: drop 3 and 2
    assert [s.text for s in short.segments] == ["0", "1", "4"]
    short = pipeline.shorten_script(script, narration, 25.0)  # 24 s over: 3 + 2 is not enough
    assert [s.text for s in short.segments] == ["0", "4"]
    short = pipeline.shorten_script(script, narration, 1.0)
    assert [s.text for s in short.segments] == ["0", "4"]
    assert len(script.segments) == 5  # original untouched


def test_topic_matches():
    script = VideoScript(topic="space planets solar system", format="facts", title="Space Facts That Sound Fake",
                         segments=[Segment("s", "q")], description="d", hashtags=["astronomy"])
    assert pipeline.topic_matches("Why there is no sound in space", script)
    assert pipeline.topic_matches("Astronomy facts", script)  # hashtags count
    assert not pipeline.topic_matches("Why everyone needs a small emergency fund", script)
    assert pipeline.topic_matches("Crazy facts", script)  # nothing specific asked for


def _offline_like(fakes: Fakes, about: str) -> None:
    """Make the fake script generator behave like the offline bank: ignore the topic."""
    def generate_script(cfg, topic, fmt):
        fakes._check("script")
        segs = [Segment(text=f"Sentence number {i}.", visual_query="q") for i in range(fakes.n_segments)]
        return VideoScript(topic=about, format=fmt, title=f"All About {about}", segments=segs,
                           description="d", hashtags=[about])
    fakes.generate_script = generate_script


def test_off_topic_script_renames_folder_and_keeps_topic_unused(cfg, fakes, monkeypatch, caplog):
    _offline_like(fakes, "octopus")
    monkeypatch.setattr(pipeline, "generate_script", fakes.generate_script)
    job = pipeline.make_video(cfg, topic="Why the sky is blue")

    assert job.folder.name.endswith("-all-about-octopus")
    assert [p.name for p in job_folders(cfg)] == [job.folder.name]  # the old folder is gone
    assert (job.folder / "video.mp4").is_file() and not (job.folder / "work").exists()
    data = json.loads((job.folder / "job.json").read_text(encoding="utf-8"))
    assert data["id"] == job.folder.name and data["topic"] == "octopus"
    assert not TopicQueue(cfg).is_used("Why the sky is blue")
    assert "no script about 'Why the sky is blue'" in caplog.text


def test_batch_moves_past_off_topic_topics(cfg, fakes, monkeypatch):
    cfg.path("topics.txt").write_text("Emergency funds\nOctopus hearts\n", encoding="utf-8")
    _offline_like(fakes, "octopus")
    monkeypatch.setattr(pipeline, "generate_script", fakes.generate_script)
    jobs = pipeline.make_batch(cfg, 2)

    assert len(jobs) == 2
    queue = TopicQueue(cfg)
    assert queue.is_used("Octopus hearts")  # matched, so it was used
    assert not queue.is_used("Emergency funds")  # kept for when an LLM is set up


def test_job_folder_names_get_a_suffix_when_taken(cfg):
    now = datetime(2026, 1, 2, 3, 4, 5)
    a = pipeline.create_job_folder(cfg, "Same Topic!", now)
    b = pipeline.create_job_folder(cfg, "Same Topic!", now)
    c = pipeline.create_job_folder(cfg, "Same Topic!", now)
    assert [p.name for p in (a, b, c)] == [
        "20260102-030405-same-topic", "20260102-030405-same-topic-2", "20260102-030405-same-topic-3",
    ]


# --------------------------------------------------------------------------- batch


def test_batch_continues_after_a_failure(cfg, fakes):
    cfg.path("topics.txt").write_text("Bad\nGood one\nGood two\nSpare\n", encoding="utf-8")
    fakes.fail_topics = {"Bad"}
    jobs = pipeline.make_batch(cfg, 3)

    assert [j.topic for j in jobs] == ["Good one", "Good two"]
    queue = TopicQueue(cfg)
    assert queue.is_used("Good one") and queue.is_used("Good two")
    assert not queue.is_used("Bad")
    assert queue.unused() == ["Bad", "Spare"]
    failed = [p for p in job_folders(cfg) if (p / "error.txt").exists()]
    assert len(failed) == 1


def test_batch_raises_when_every_video_fails(cfg, fakes):
    cfg.path("topics.txt").write_text("a\nb\n", encoding="utf-8")
    fakes.fail_topics = {"a", "b"}
    with pytest.raises(AutoShortsError, match="every video in the batch failed"):
        pipeline.make_batch(cfg, 2)


def test_batch_stops_when_the_same_error_repeats(cfg, fakes):
    fakes.fail_stage = "captions"
    with pytest.raises(AutoShortsError, match="captions broke"):
        pipeline.make_batch(cfg, 10)
    assert fakes.calls.count("captions") == pipeline.SAME_ERROR_LIMIT


def test_batch_rejects_zero(cfg, fakes):
    with pytest.raises(AutoShortsError):
        pipeline.make_batch(cfg, 0)


# --------------------------------------------------------------------------- upload_job


def test_upload_job_uploads_existing_folder_and_updates_job_json(cfg, fakes):
    job = pipeline.make_video(cfg, topic="Later upload")
    fakes.tiktok = UploadResult(platform="tiktok", ok=False, error="token expired")

    results = pipeline.upload_job(cfg, job.folder, ["youtube,tiktok"])
    assert [(r.platform, r.ok) for r in results] == [("youtube", True), ("tiktok", False)]
    data = json.loads((job.folder / "job.json").read_text(encoding="utf-8"))
    assert [u["platform"] for u in data["uploads"]] == ["youtube", "tiktok"]
    assert data["status"] == "done"  # other fields kept

    pipeline.upload_job(cfg, job.folder, "youtube")
    data = json.loads((job.folder / "job.json").read_text(encoding="utf-8"))
    assert len(data["uploads"]) == 3


def test_upload_job_errors(cfg, fakes, tmp_path):
    with pytest.raises(AutoShortsError, match="not found"):
        pipeline.upload_job(cfg, tmp_path / "nope", ["youtube"])
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AutoShortsError, match="video.mp4"):
        pipeline.upload_job(cfg, empty, ["youtube"])
    (empty / "video.mp4").write_bytes(b"v")
    with pytest.raises(AutoShortsError, match="metadata.json"):
        pipeline.upload_job(cfg, empty, ["youtube"])
    with pytest.raises(AutoShortsError, match="no upload platform"):
        pipeline.upload_job(cfg, empty, [])


# --------------------------------------------------------------------------- helpers


def test_parse_platforms():
    assert pipeline.parse_platforms("youtube,tiktok") == ("youtube", "tiktok")
    assert pipeline.parse_platforms(" TikTok , yt ,youtube") == ("tiktok", "youtube")
    assert pipeline.parse_platforms(["all"]) == ("youtube", "tiktok")
    assert pipeline.parse_platforms("none") == ()
    assert pipeline.parse_platforms(None) == ()
    with pytest.raises(AutoShortsError):
        pipeline.parse_platforms("vimeo")


def test_default_platforms_follow_config():
    cfg = Config()
    assert pipeline.default_platforms(cfg) == ()
    cfg.upload.tiktok.enabled = True
    assert pipeline.default_platforms(cfg) == ("tiktok",)


def test_to_jsonable_makes_paths_relative(tmp_path):
    clip = ClipAsset(path=tmp_path / "job" / "work" / "c.mp4")
    outside = ClipAsset(path=Path("/somewhere/else.mp4"))
    data = pipeline.to_jsonable({"clips": [clip, outside]}, tmp_path / "job")
    assert data["clips"][0]["path"] == "work/c.mp4"
    assert data["clips"][1]["path"] == str(Path("/somewhere/else.mp4"))
    json.dumps(data)


# --------------------------------------------------------------------------- offline fallback / housekeeping


def bank_script(fakes: Fakes) -> None:
    """generate_script returns a script straight from the offline content bank."""
    from autoshorts.script.offline import load_bank

    entry = next(e for e in load_bank() if e["format"] == "facts")

    def generate_script(cfg, topic, fmt):
        fakes.calls.append("script")
        return VideoScript(topic=topic, format="facts", title=entry["title"],
                           segments=[Segment(text=s["text"], visual_query=s["visual_query"]) for s in entry["segments"]],
                           description=entry["description"], hashtags=list(entry["hashtags"]))

    fakes.generate_script = generate_script


def test_offline_fallback_scripts_are_rendered_but_not_uploaded(cfg, fakes, monkeypatch, caplog):
    bank_script(fakes)
    fakes.install(monkeypatch)
    cfg.script.provider = "auto"
    with caplog.at_level("WARNING", logger="autoshorts"):
        job = pipeline.make_video(cfg, topic="Anything", upload_to=("youtube", "tiktok"))
    assert job.render is not None and job.uploads == []
    assert not any(c.startswith("upload:") for c in fakes.calls)
    assert "not uploading" in caplog.text and "autoshorts upload" in caplog.text

    cfg.upload.upload_offline_fallback = True
    job = pipeline.make_video(cfg, topic="Anything else", upload_to=("youtube",))
    assert [u.platform for u in job.uploads] == ["youtube"]

    cfg.upload.upload_offline_fallback, cfg.script.provider = False, "offline"  # chosen on purpose
    job = pipeline.make_video(cfg, topic="Third", upload_to=("youtube",))
    assert [u.platform for u in job.uploads] == ["youtube"]


def test_llm_scripts_are_uploaded(cfg, fakes):
    job = pipeline.make_video(cfg, topic="LLM topic", upload_to=("youtube",))
    assert [u.platform for u in job.uploads] == ["youtube"]


def test_make_video_runs_housekeeping(cfg, fakes, monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline, "housekeeping", lambda c: seen.append(c))
    pipeline.make_video(cfg, topic="Housekeeping")
    assert seen == [cfg]
