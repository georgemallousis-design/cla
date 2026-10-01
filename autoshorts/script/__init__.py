"""Script generation: topic -> VideoScript.

Contract
--------
``get_generator(cfg)`` returns a ScriptGenerator chosen by ``cfg.script.provider``:

* ``ollama``            -> script.llm.OllamaGenerator           (local, free)
* ``openai_compatible`` -> script.llm.OpenAICompatibleGenerator  (Groq/Gemini/OpenRouter free tiers)
* ``offline``           -> script.offline.OfflineGenerator       (built-in content bank, no network)
* ``auto``              -> first that is usable: Ollama reachable -> API key present -> offline

``generate(topic, fmt)`` must return a valid VideoScript (see models.py) whose spoken
length targets ``cfg.video.target_seconds`` (~2.6 spoken words per second), or raise
AutoShortsError. ``fmt`` is one of FORMATS; "random" is resolved by the caller.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import Config
from ..models import VideoScript

FORMATS = ("facts", "story", "quiz", "motivation", "explainer")


class ScriptGenerator(ABC):
    name: str = "base"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @abstractmethod
    def generate(self, topic: str, fmt: str) -> VideoScript: ...


def get_generator(cfg: Config) -> ScriptGenerator:
    raise NotImplementedError
