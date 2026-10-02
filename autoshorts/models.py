"""Plain data objects passed between pipeline stages.

Every stage consumes and produces these, so stages stay swappable:

    topic -> VideoScript -> Narration -> captions (.ass) + list[ClipAsset] -> RenderResult -> VideoJob
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Segment:
    """One narration beat. Visuals change on segment boundaries."""

    text: str  # spoken text, 1-3 sentences
    visual_query: str  # 1-4 English keywords for stock footage, e.g. "octopus underwater"


@dataclass
class VideoScript:
    topic: str
    format: str  # facts | story | quiz | motivation | explainer
    title: str  # <= 90 chars, no hashtags
    segments: list[Segment]  # segments[0] is the hook
    description: str  # 1-3 sentences, no hashtags
    hashtags: list[str] = field(default_factory=list)  # lowercase, no leading '#'
    language: str = "en"
    narrator: str = ""  # "male" | "female" | "" (reddit format picks the voice from this)

    @property
    def hook(self) -> str:
        return self.segments[0].text if self.segments else ""

    def narration_text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VideoScript":
        segs = [Segment(text=s["text"], visual_query=s.get("visual_query", "")) for s in d["segments"]]
        return cls(
            topic=d.get("topic", ""),
            format=d.get("format", "facts"),
            title=d["title"],
            segments=segs,
            description=d.get("description", ""),
            hashtags=list(d.get("hashtags", [])),
            language=d.get("language", "en"),
            narrator=d.get("narrator", "") or "",
        )


@dataclass
class WordTiming:
    word: str  # as spoken/displayed, punctuation kept
    start: float  # seconds from start of the narration audio
    end: float


@dataclass
class SpeechResult:
    """Output of a single TTS call (one segment)."""

    audio_path: Path  # .wav or .mp3
    duration: float  # seconds, measured with ffprobe
    words: list[WordTiming]  # relative to the start of this audio file


@dataclass
class TimedSegment:
    segment: Segment
    start: float
    end: float


@dataclass
class Narration:
    """Full voice-over: all segments concatenated into one audio file."""

    audio_path: Path  # .wav, 48 kHz stereo
    duration: float
    words: list[WordTiming]  # absolute times in the full audio
    segments: list[TimedSegment]
    engine: str = ""  # which TTS engine produced it


@dataclass
class ClipAsset:
    """A background visual: a video file or a still image (gets Ken Burns motion)."""

    path: Path
    kind: str = "video"  # video | image
    duration: float | None = None  # None for images
    source: str = ""  # pexels | pixabay | local | generated
    query: str = ""
    attribution: str = ""  # e.g. "Video by Jane Doe on Pexels"
    width: int | None = None
    height: int | None = None
    start_offset: float | None = None  # where to start inside a long video; None = renderer decides


@dataclass
class RenderResult:
    video_path: Path
    duration: float
    thumbnail_path: Path | None = None


@dataclass
class UploadResult:
    platform: str  # youtube | tiktok
    ok: bool
    id: str = ""  # platform video/publish id
    url: str = ""
    error: str = ""


@dataclass
class VideoJob:
    """Everything known about one generated video. Saved as job.json in its folder."""

    id: str
    topic: str
    folder: Path
    script: VideoScript | None = None
    narration: Narration | None = None
    clips: list[ClipAsset] = field(default_factory=list)
    render: RenderResult | None = None
    uploads: list[UploadResult] = field(default_factory=list)
    created_at: str = ""
