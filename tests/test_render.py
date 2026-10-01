"""Tests for autoshorts.render: frame planning, filter graphs, concat lists, real tiny renders."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path, PureWindowsPath

import pytest

from autoshorts import render
from autoshorts.config import Config
from autoshorts.models import ClipAsset, Narration
from autoshorts.render import (
    TAIL_SECONDS,
    build_fallback_command,
    build_final_command,
    build_shot_command,
    clip_offset,
    concat_list_text,
    concat_shots,
    final_audio_filter,
    final_video_filter,
    make_thumbnail,
    render_video,
    shot_frames,
    video_duration,
)
from autoshorts.utils import AutoShortsError, ffprobe_json, media_duration
from autoshorts.visuals import Shot

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed"
)

W, H, FPS = 108, 192, 15
NARRATION_SECONDS = 2.0

ASS_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, \
Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, \
MarginV, Encoding
Style: Box,DejaVu Sans,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Box,,0,0,0,,{{\\pos(0,0)\\p1}}m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}
"""


# --------------------------------------------------------------------------- helpers


def ff(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, cwd=cwd)


def small_cfg(base: Path, **music) -> Config:
    cfg = Config(base_dir=base)
    cfg.video.width, cfg.video.height, cfg.video.fps = W, H, FPS
    cfg.video.preset = "ultrafast"
    cfg.captions.fonts_dir = "fonts"
    for key, value in music.items():
        setattr(cfg.music, key, value)
    return cfg


def streams(path: Path) -> dict[str, dict]:
    return {s["codec_type"]: s for s in ffprobe_json(path)["streams"]}


def frame_brightness(video: Path, at: float) -> float:
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at}", "-i", str(video),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    ).stdout
    assert len(raw) == W * H
    return sum(raw) / len(raw)


def system_font() -> Path | None:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(candidate).is_file():
            return Path(candidate)
    return None


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    """Tiny source media in a folder whose name has a space and an apostrophe."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("render") / "my folder" / "it's media"
    d.mkdir(parents=True)
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=1.2", "-pix_fmt", "yuv420p", "wide.mp4", cwd=d)  # short
    ff("-f", "lavfi", "-i", "testsrc=s=120x400:r=30:d=4", "-pix_fmt", "yuv420p", "tall.mp4", cwd=d)  # long
    ff("-f", "lavfi", "-i", "mandelbrot=s=300x200", "-frames:v", "1", "still.png", cwd=d)
    (d / "still.png").rename(d / "still 100% image.png")  # '%' must not be read as a sequence pattern
    ff("-f", "lavfi", "-i", f"sine=f=440:d={NARRATION_SECONDS}:r=22050", "voice.wav", cwd=d)  # mono 22 kHz
    ff("-f", "lavfi", "-i", "sine=f=220:d=0.7", "-ac", "2", "music.wav", cwd=d)  # shorter than video: loops
    (d / "captions.ass").write_text(ASS_TEMPLATE.format(w=W, h=H), encoding="utf-8")
    font = system_font()
    if font:
        (d / "fonts").mkdir()
        shutil.copy2(font, d / "fonts" / font.name)
    return {
        "dir": d,
        "wide": d / "wide.mp4",
        "tall": d / "tall.mp4",
        "image": d / "still 100% image.png",
        "voice": d / "voice.wav",
        "music": d / "music.wav",
        "ass": d / "captions.ass",
    }


def narration_for(media: dict[str, Path]) -> Narration:
    return Narration(audio_path=media["voice"], duration=NARRATION_SECONDS, words=[], segments=[], engine="test")


# --------------------------------------------------------------------------- timing


def test_video_duration_adds_tail_and_caps():
    cfg = Config()
    assert video_duration(cfg, 60.0) == pytest.approx(60.0 + TAIL_SECONDS)
    cfg.video.max_seconds = 30
    assert video_duration(cfg, 60.0) == 30.0


def test_shot_frames_tile_exactly_without_drift():
    clip = ClipAsset(Path("x.mp4"))
    shots = [Shot(clip, i * 1.3, (i + 1) * 1.3) for i in range(7)]  # 1.3 s * 15 fps = 19.5 frames each
    total = 7 * 1.3 + TAIL_SECONDS
    plan = shot_frames(shots, total, FPS)
    assert len(plan) == 7
    assert sum(frames for _, frames in plan) == round(total * FPS)
    assert all(abs(frames - 19.5) <= 1 for _, frames in plan[:-1])
    assert plan[-1][1] >= 19 + round(TAIL_SECONDS * FPS) - 1  # last shot also covers the tail


def test_shot_frames_drops_empty_and_out_of_range_shots_and_sorts():
    clip = ClipAsset(Path("x.mp4"))
    a, b, c, d = Shot(clip, 0.0, 1.0), Shot(clip, 1.0, 1.01), Shot(clip, 1.01, 2.0), Shot(clip, 5.0, 6.0)
    plan = shot_frames([c, a, d, b], total=2.0, fps=FPS)
    assert [s for s, _ in plan] == [a, c]  # b rounds to 0 frames, d starts after the end
    assert sum(f for _, f in plan) == 30


def test_clip_offset_is_deterministic_bounded_and_varies():
    offsets = [clip_offset(i, "ocean.mp4", 3.0, 20.0) for i in range(8)]
    assert offsets == [clip_offset(i, "ocean.mp4", 3.0, 20.0) for i in range(8)]
    assert all(0.0 <= o <= 20.0 - 3.0 - render.LOOP_MARGIN for o in offsets)
    assert len(set(offsets)) > 1
    assert clip_offset(0, "short.mp4", 3.0, 3.05) == 0.0


# --------------------------------------------------------------------------- commands and filters


def test_shot_command_long_video_seeks_and_covers():
    clip = ClipAsset(Path("/clips/long.mp4"), kind="video")
    args = build_shot_command(clip, "out.mp4", index=3, frames=45, width=W, height=H, fps=FPS, src_duration=30.0)
    assert "-stream_loop" not in args
    offset = float(args[args.index("-ss") + 1])
    assert 0 < offset <= 30.0 - 3.0
    assert args.index("-ss") < args.index("-i")
    vf = args[args.index("-vf") + 1]
    assert f"fps={FPS}" in vf
    assert f"scale={W}:{H}:force_original_aspect_ratio=increase" in vf and f"crop={W}:{H}" in vf
    assert vf.endswith("format=yuv420p") and "fade" not in vf
    assert args[args.index("-frames:v") + 1] == "45"
    assert {"-an", "libx264", "yuv420p"} <= set(args)
    assert args[-1] == "out.mp4"


def test_shot_command_short_or_unknown_video_loops():
    clip = ClipAsset(Path("short.mp4"))
    for src_duration in (1.0, None):
        args = build_shot_command(clip, "o.mp4", index=0, frames=45, width=W, height=H, fps=FPS,
                                  src_duration=src_duration)
        assert args[:4] == ["-stream_loop", "-1", "-i", "short.mp4"]
        assert "-ss" not in args


def test_shot_command_image_gets_alternating_ken_burns():
    clip = ClipAsset(Path("pic.jpg"), kind="image")
    vf0 = build_shot_command(clip, "o.mp4", index=0, frames=60, width=W, height=H, fps=FPS)
    vf1 = build_shot_command(clip, "o.mp4", index=1, frames=60, width=W, height=H, fps=FPS)
    vf0, vf1 = vf0[vf0.index("-vf") + 1], vf1[vf1.index("-vf") + 1]
    for vf in (vf0, vf1):
        assert f"scale={2 * W}:{2 * H}" in vf  # pre-scaled to 2x before zoompan
        assert f"d=60:s={W}x{H}:fps={FPS}" in vf
    assert "z='1+" in vf0 and "z='1.12-" in vf1  # zoom in, then zoom out
    args = build_shot_command(clip, "o.mp4", index=0, frames=60, width=W, height=H, fps=FPS)
    assert args[:6] == ["-f", "image2", "-pattern_type", "none", "-i", "pic.jpg"]  # '%' in names is literal


def test_only_first_shot_fades_in():
    clip = ClipAsset(Path("a.mp4"))
    first = build_shot_command(clip, "o.mp4", index=0, frames=30, width=W, height=H, fps=FPS, fade_in=True)
    assert "fade=t=in:s=0:n=4" in first[first.index("-vf") + 1]
    fallback = build_fallback_command("o.mp4", frames=30, width=W, height=H, fps=FPS, fade_in=False)
    assert "fade" not in fallback[fallback.index("-vf") + 1]
    assert fallback[:3] == ["-f", "lavfi", "-i"]


def test_concat_list_escapes_quotes_and_uses_forward_slashes():
    text = concat_list_text(["shot_000.mp4", "it's here.mp4", PureWindowsPath(r"C:\Users\Bob\it's.mp4")])
    lines = text.splitlines()
    assert lines[0] == "ffconcat version 1.0"
    assert lines[1] == "file 'shot_000.mp4'"
    assert lines[2] == "file 'it'\\''s here.mp4'"
    assert lines[3] == "file 'C:/Users/Bob/it'\\''s.mp4'"
    assert text.endswith("\n")


def test_final_video_filter_variants():
    assert final_video_filter("captions.ass", "fonts") == (
        "[0:v:0]tpad=stop_mode=clone:stop_duration=1,subtitles=captions.ass:fontsdir=fonts,format=yuv420p[vout]"
    )
    assert "fontsdir" not in final_video_filter("captions.ass", None)
    assert "subtitles" not in final_video_filter(None)


def test_final_audio_filter_without_music():
    graph = final_audio_filter(3.0)
    assert graph.startswith("[1:a:0]aformat=")
    assert "apad=whole_dur=3.000" in graph and "alimiter=limit=0.95" in graph
    assert "[2:a" not in graph and "amix" not in graph and graph.endswith("[aout]")


def test_final_audio_filter_music_without_ducking():
    graph = final_audio_filter(10.0, music_volume=0.12, duck=False)
    assert "[2:a:0]" in graph and "atrim=duration=10.000" in graph and "volume=0.12" in graph
    assert "afade=t=out:st=8.500:d=1.500" in graph
    assert "amix=inputs=2:duration=first:normalize=0" in graph
    assert "sidechaincompress" not in graph and "asplit" not in graph


def test_final_audio_filter_with_ducking():
    graph = final_audio_filter(10.0, music_volume=0.2, duck=True)
    assert "asplit=2[voice][sidechain]" in graph
    assert "[bgm][sidechain]sidechaincompress=" in graph
    assert graph.index("sidechaincompress") < graph.index("afade=t=out")
    assert graph.endswith("[aout]")


def test_final_audio_filter_short_video_fade_fits():
    assert "afade=t=out:st=0.000:d=1.000" in final_audio_filter(1.0, music_volume=0.1)


def test_build_final_command_inputs_and_encoding(tmp_path):
    cfg = small_cfg(tmp_path)
    with_music = build_final_command(cfg, background="background.mp4", narration_audio="voice.wav",
                                     out_path="out.mp4", duration=12.4, fonts_dir="fonts", music_path="m.mp3")
    i = with_music.index("-stream_loop")
    assert with_music[i:i + 4] == ["-stream_loop", "-1", "-i", "m.mp3"]
    assert with_music[:4] == ["-i", "background.mp4", "-i", "voice.wav"]
    graph = with_music[with_music.index("-filter_complex") + 1]
    assert "subtitles=captions.ass:fontsdir=fonts" in graph and "sidechaincompress" in graph
    for flag, value in [("-t", "12.400"), ("-c:v", "libx264"), ("-profile:v", "high"), ("-level:v", "4.2"),
                        ("-pix_fmt", "yuv420p"), ("-r", str(FPS)), ("-g", str(2 * FPS)), ("-c:a", "aac"),
                        ("-ar", "48000"), ("-ac", "2"), ("-movflags", "+faststart")]:
        assert with_music[with_music.index(flag) + 1] == value
    no_music = build_final_command(cfg, background="b.mp4", narration_audio="v.wav", out_path="o.mp4", duration=5)
    assert "-stream_loop" not in no_music and "[2:a" not in no_music[no_music.index("-filter_complex") + 1]
    cfg.music.volume = 0
    muted = build_final_command(cfg, background="b.mp4", narration_audio="v.wav", out_path="o.mp4",
                                duration=5, music_path="m.mp3")
    assert "m.mp3" not in muted


# --------------------------------------------------------------------------- real ffmpeg runs


@needs_ffmpeg
def test_concat_demuxer_handles_apostrophes(tmp_path):
    folder = tmp_path / "my folder"
    folder.mkdir()
    names = ["it's one.mp4", "two.mp4"]
    for name in names:
        ff("-f", "lavfi", "-i", f"color=c=red:s={W}x{H}:r={FPS}", "-frames:v", "6", *render._shot_encode_args(FPS),
           name, cwd=folder)
    out = concat_shots([folder / n for n in names], folder / "background.mp4")
    assert media_duration(out) == pytest.approx(12 / FPS, abs=0.02)


@needs_ffmpeg
def test_render_video_full_pipeline_with_ducked_music(media, caplog):
    cfg = small_cfg(media["dir"], enabled=True, duck=True, volume=0.15)
    work = media["dir"] / "work dir"
    shots = [
        Shot(ClipAsset(media["wide"], kind="video", duration=1.2), 0.0, 0.7),  # loops
        Shot(ClipAsset(media["image"], kind="image"), 0.7, 1.4),  # Ken Burns
        Shot(ClipAsset(media["tall"], kind="video"), 1.4, NARRATION_SECONDS),  # offset into a long clip
    ]
    out = media["dir"] / "job" / "video.mp4"
    with caplog.at_level(logging.WARNING, logger="autoshorts"):
        result = render_video(cfg, narration_for(media), shots, media["ass"], out, music_path=media["music"],
                              workdir=work)
    assert not caplog.records, [r.getMessage() for r in caplog.records]  # no clip fell back

    assert result.video_path == out.resolve() and out.is_file()
    assert result.thumbnail_path is None
    expected = NARRATION_SECONDS + TAIL_SECONDS
    assert result.duration == pytest.approx(expected, abs=0.1)
    info = streams(out)
    video, audio = info["video"], info["audio"]
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", W, H)
    assert video["pix_fmt"] == "yuv420p" and video["r_frame_rate"] == f"{FPS}/1"
    assert float(video["duration"]) == pytest.approx(expected, abs=0.1)
    assert (audio["codec_name"], int(audio["sample_rate"]), audio["channels"]) == ("aac", 48000, 2)
    assert float(audio["duration"]) == pytest.approx(expected, abs=0.1)
    assert not out.with_name("video.part.mp4").exists()
    assert sorted(p.name for p in work.glob("shot_*.mp4")) == ["shot_000.mp4", "shot_001.mp4", "shot_002.mp4"]
    assert (work / "captions.ass").is_file() and (work / "background.mp4").is_file()
    assert (work / "fonts").is_dir() == (system_font() is not None)

    thumb = make_thumbnail(out, media["dir"] / "job" / "thumbnail.jpg", at=1.0)
    assert thumb.is_file() and thumb.stat().st_size > 0
    assert streams(thumb)["video"]["width"] == W


@needs_ffmpeg
def test_render_video_burns_captions_and_survives_bad_clips(media, tmp_path, caplog):
    """No music, temp workdir; unusable clips fall back to a dark background, and the
    full-frame white ASS box (shown 0-1 s) proves the captions were burned in."""
    cfg = small_cfg(media["dir"], enabled=False)
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    shots = [
        Shot(ClipAsset(tmp_path / "missing.mp4"), 0.0, 1.0),
        Shot(ClipAsset(broken), 1.0, NARRATION_SECONDS),
    ]
    out = tmp_path / "out dir" / "video.mp4"
    with caplog.at_level(logging.WARNING, logger="autoshorts"):
        result = render_video(cfg, narration_for(media), shots, media["ass"], out)
    assert sum("plain background" in r.getMessage() for r in caplog.records) == 2
    assert result.duration == pytest.approx(NARRATION_SECONDS + TAIL_SECONDS, abs=0.1)
    assert frame_brightness(out, 0.5) > 200  # white caption box over everything
    assert frame_brightness(out, 1.6) < 60  # dark fallback background, box gone
    assert set(streams(out)) == {"video", "audio"}


@needs_ffmpeg
def test_render_video_without_captions_and_capped_length(media, tmp_path):
    cfg = small_cfg(media["dir"], enabled=True, duck=False)
    cfg.video.max_seconds = 1
    shots = [Shot(ClipAsset(media["tall"]), 0.0, NARRATION_SECONDS)]
    result = render_video(cfg, narration_for(media), shots, None, tmp_path / "v.mp4", music_path=media["music"])
    assert result.duration == pytest.approx(1.0, abs=0.1)


def test_render_video_rejects_bad_input(media, tmp_path):
    cfg = small_cfg(tmp_path)
    shot = Shot(ClipAsset(media["tall"]), 0.0, 1.0)
    with pytest.raises(AutoShortsError, match="no shots"):
        render_video(cfg, narration_for(media), [], media["ass"], tmp_path / "v.mp4")
    missing_voice = Narration(audio_path=tmp_path / "nope.wav", duration=1.0, words=[], segments=[])
    with pytest.raises(AutoShortsError, match="narration audio"):
        render_video(cfg, missing_voice, [shot], media["ass"], tmp_path / "v.mp4")
    with pytest.raises(AutoShortsError, match="captions file"):
        render_video(cfg, narration_for(media), [shot], tmp_path / "nope.ass", tmp_path / "v.mp4",
                     workdir=tmp_path / "w")
    assert not (tmp_path / "v.mp4").exists()
