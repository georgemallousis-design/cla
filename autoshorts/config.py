"""Configuration: defaults below, overridden by config.yaml, secrets from env/.env.

Secrets (API keys, tokens) never live in config.yaml; the config only names the
environment variable that holds them (``*_env`` fields). Use ``Config.secret()``.
"""
from __future__ import annotations

import dataclasses
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class VideoConfig:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    # TikTok Creator Rewards only pays for videos longer than 60 s; YouTube Shorts
    # accepts up to 180 s. The script generator aims for this length.
    target_seconds: int = 65
    max_seconds: int = 175  # hard cap, keeps uploads valid as Shorts
    crf: int = 20
    preset: str = "medium"  # x264 preset; "veryfast" for slow machines
    audio_bitrate: str = "192k"


@dataclass
class OllamaConfig:
    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"


@dataclass
class OpenAICompatibleConfig:
    # Any OpenAI-compatible chat API with a free tier, e.g.
    #   Groq:       https://api.groq.com/openai/v1  (llama-3.3-70b-versatile)
    #   Gemini:     https://generativelanguage.googleapis.com/v1beta/openai  (gemini-2.0-flash)
    #   OpenRouter: https://openrouter.ai/api/v1  (any ":free" model)
    base_url: str = "https://api.groq.com/openai/v1"
    model: str = "llama-3.3-70b-versatile"
    api_key_env: str = "LLM_API_KEY"


@dataclass
class ScriptConfig:
    provider: str = "auto"  # auto | ollama | openai_compatible | offline
    format: str = "random"  # facts | story | quiz | motivation | explainer | random
    language: str = "en"
    temperature: float = 0.9
    timeout: int = 180
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    openai_compatible: OpenAICompatibleConfig = field(default_factory=OpenAICompatibleConfig)


@dataclass
class EdgeTTSConfig:
    voice: str = "en-US-AndrewNeural"
    rate: str = "+5%"
    pitch: str = "+0Hz"
    volume: str = "+0%"


@dataclass
class EspeakConfig:
    voice: str = "en-us"
    speed: int = 165  # words per minute


@dataclass
class Pyttsx3Config:
    rate: int = 185
    voice: str | None = None  # substring of a voice name/id, None = system default


@dataclass
class TTSConfig:
    provider: str = "auto"  # auto | edge | pyttsx3 | espeak
    segment_gap: float = 0.15  # silence between segments, seconds
    edge: EdgeTTSConfig = field(default_factory=EdgeTTSConfig)
    espeak: EspeakConfig = field(default_factory=EspeakConfig)
    pyttsx3: Pyttsx3Config = field(default_factory=Pyttsx3Config)


@dataclass
class VisualsConfig:
    # Tried in order per segment; "generated" never fails, so keep it last.
    providers: list[str] = field(default_factory=lambda: ["pexels", "pixabay", "local", "generated"])
    pexels_api_key_env: str = "PEXELS_API_KEY"
    pixabay_api_key_env: str = "PIXABAY_API_KEY"
    local_dir: str = "assets/backgrounds"
    cache_dir: str = "cache/clips"
    max_shot_seconds: float = 4.0  # long segments are split into several shots
    timeout: int = 60


@dataclass
class CaptionsConfig:
    font: str = "auto"  # font family name, "auto" picks a bold system font
    fonts_dir: str = "assets/fonts"  # extra .ttf/.otf files available to the renderer
    font_size: int = 88
    words_per_caption: int = 3
    max_chars: int = 20
    uppercase: bool = True
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFE135"
    outline_color: str = "#000000"
    outline: int = 6
    shadow: int = 3
    position: float = 0.68  # vertical centre of captions, fraction of frame height
    pop: bool = True  # small scale-in animation per caption
    show_title: bool = True  # hook/title banner at the top for the first seconds
    title_seconds: float = 3.0


@dataclass
class MusicConfig:
    enabled: bool = True
    dir: str = "assets/music"
    volume: float = 0.12  # relative to narration
    duck: bool = True  # lower music while the voice is speaking


@dataclass
class MetadataConfig:
    extra_hashtags: list[str] = field(default_factory=lambda: ["shorts"])
    max_hashtags: int = 8
    ai_disclosure: bool = True  # append an "AI voice" note to descriptions


@dataclass
class YouTubeUploadConfig:
    enabled: bool = False
    client_secrets: str = "secrets/client_secret.json"
    token_file: str = "secrets/youtube_token.json"
    privacy: str = "private"  # private | unlisted | public
    category_id: str = "27"  # 27 = Education, 24 = Entertainment
    made_for_kids: bool = False
    contains_synthetic_media: bool = True


@dataclass
class TikTokUploadConfig:
    enabled: bool = False
    access_token_env: str = "TIKTOK_ACCESS_TOKEN"
    # inbox: sends to the TikTok app's inbox as a draft you finish and post by hand
    #        (works for any developer app).
    # direct: posts immediately; needs an audited app for public visibility.
    mode: str = "inbox"
    privacy_level: str = "SELF_ONLY"  # direct mode only
    is_aigc: bool = True


@dataclass
class UploadConfig:
    youtube: YouTubeUploadConfig = field(default_factory=YouTubeUploadConfig)
    tiktok: TikTokUploadConfig = field(default_factory=TikTokUploadConfig)


@dataclass
class TopicsConfig:
    file: str = "topics.txt"  # one topic per line; '#' comments allowed
    state_file: str = "state/used_topics.json"
    allow_repeats: bool = False


@dataclass
class Config:
    output_dir: str = "output"
    log_level: str = "INFO"
    keep_intermediate: bool = False  # keep per-segment audio, shots, etc.
    video: VideoConfig = field(default_factory=VideoConfig)
    script: ScriptConfig = field(default_factory=ScriptConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    visuals: VisualsConfig = field(default_factory=VisualsConfig)
    captions: CaptionsConfig = field(default_factory=CaptionsConfig)
    music: MusicConfig = field(default_factory=MusicConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    topics: TopicsConfig = field(default_factory=TopicsConfig)

    # Directory that relative paths in the config resolve against.
    base_dir: Path = field(default_factory=Path.cwd, repr=False)

    def path(self, p: str | Path) -> Path:
        """Resolve a config path relative to the config file's directory."""
        p = Path(p).expanduser()
        return p if p.is_absolute() else (self.base_dir / p)

    @staticmethod
    def secret(env_name: str) -> str | None:
        """Read a secret from the environment (.env is loaded by load_config)."""
        value = os.environ.get(env_name, "").strip()
        return value or None


def _build(cls: type, data: Any, where: str) -> Any:
    if not dataclasses.is_dataclass(cls):
        return data
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ValueError(f"config section '{where}' must be a mapping, got {type(data).__name__}")
    hints = {f.name: f for f in dataclasses.fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in hints or key == "base_dir":
            print(f"warning: unknown config key '{where}.{key}' ignored", file=sys.stderr)
            continue
        default = hints[key].default_factory() if hints[key].default_factory is not dataclasses.MISSING else None
        if dataclasses.is_dataclass(default):
            kwargs[key] = _build(type(default), value, f"{where}.{key}")
        else:
            kwargs[key] = value
    return cls(**kwargs)


def load_config(path: str | Path | None = None) -> Config:
    """Load config.yaml (if present) on top of the defaults, and .env into os.environ.

    ``path=None`` looks for ./config.yaml and silently falls back to defaults.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is required, but stay usable
        load_dotenv = None

    cfg_path = Path(path) if path else Path("config.yaml")
    base_dir = cfg_path.resolve().parent if cfg_path.exists() else Path.cwd()
    if load_dotenv is not None:
        load_dotenv(base_dir / ".env", override=False)

    data: dict[str, Any] = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    elif path is not None:
        raise FileNotFoundError(f"config file not found: {cfg_path}")

    cfg = _build(Config, data, "config")
    cfg.base_dir = base_dir
    return cfg
