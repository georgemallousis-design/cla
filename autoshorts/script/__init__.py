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
AutoShortsError. ``fmt`` is one of FORMATS or "random": generators resolve "random"
themselves (the offline bank picks the format of the entry that best matches the topic),
and the returned script's ``format`` is always one of FORMATS.

In ``auto`` mode the returned generator is a FallbackGenerator: if the chosen LLM fails
at generation time, the next usable option (ending with offline) is tried, so batch
runs keep going. Explicit providers never fall back silently.
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod

from ..config import Config
from ..models import VideoScript
from ..utils import AutoShortsError, log

FORMATS = ("facts", "story", "quiz", "motivation", "explainer")
PROVIDERS = ("auto", "ollama", "openai_compatible", "offline")


class ScriptGenerator(ABC):
    name: str = "base"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @abstractmethod
    def generate(self, topic: str, fmt: str) -> VideoScript: ...


def resolve_format(fmt: str | None, rng: random.Random | None = None) -> str:
    """Validate ``fmt``; None/""/"random" picks one of FORMATS at random."""
    value = (fmt or "random").strip().lower()
    if value == "random":
        return (rng or random).choice(FORMATS)
    if value not in FORMATS:
        raise AutoShortsError(f"unknown script format '{fmt}'; use one of: {', '.join(FORMATS)}, random")
    return value


class FallbackGenerator(ScriptGenerator):
    """Tries each generator in order until one returns a script (used by provider 'auto')."""

    def __init__(self, cfg: Config, generators: list[ScriptGenerator]):
        super().__init__(cfg)
        if not generators:
            raise ValueError("FallbackGenerator needs at least one generator")
        self.generators = generators
        self.name = generators[0].name
        self.last_used = ""  # name of the generator that produced the last script

    @property
    def primary(self) -> ScriptGenerator:
        return self.generators[0]

    def generate(self, topic: str, fmt: str) -> VideoScript:
        errors: list[str] = []
        for gen in self.generators:
            try:
                script = gen.generate(topic, fmt)
            except AutoShortsError as exc:
                errors.append(f"{gen.name}: {exc}")
                log.warning("script generator '%s' failed (%s); trying the next one", gen.name, exc)
                continue
            self.last_used = gen.name
            return script
        raise AutoShortsError("every script generator failed:\n  " + "\n  ".join(errors))


def _normalize_provider(provider: str | None) -> str:
    value = (provider or "auto").strip().lower().replace("-", "_")
    return {"openai": "openai_compatible", "groq": "openai_compatible"}.get(value, value)


def _auto_generator(cfg: Config) -> ScriptGenerator:
    from .llm import OllamaGenerator, OpenAICompatibleGenerator, ollama_has_model, ollama_models
    from .offline import OfflineGenerator

    chain: list[ScriptGenerator] = []
    reasons: list[str] = []
    ocfg = cfg.script.ollama
    installed = ollama_models(cfg)
    if installed is None:
        reasons.append(f"Ollama not reachable at {ocfg.base_url}")
    elif not ollama_has_model(ocfg.model, installed):
        log.warning("Ollama is running but model '%s' is not installed; run 'ollama pull %s'", ocfg.model, ocfg.model)
        reasons.append(f"Ollama model '{ocfg.model}' not installed")
    else:
        chain.append(OllamaGenerator(cfg))
        reasons.append(f"Ollama reachable at {ocfg.base_url}")

    key_env = cfg.script.openai_compatible.api_key_env
    if Config.secret(key_env):
        chain.append(OpenAICompatibleGenerator(cfg))
        reasons.append(f"{key_env} is set")
    else:
        reasons.append(f"no {key_env} in environment")

    chain.append(OfflineGenerator(cfg))
    log.info(
        "script generator: %s (%s)%s",
        chain[0].name,
        "; ".join(reasons),
        "" if len(chain) == 1 else f"; fallback: {', '.join(g.name for g in chain[1:])}",
    )
    return chain[0] if len(chain) == 1 else FallbackGenerator(cfg, chain)


def get_generator(cfg: Config) -> ScriptGenerator:
    provider = _normalize_provider(cfg.script.provider)
    if provider == "auto":
        return _auto_generator(cfg)
    if provider == "ollama":
        from .llm import OllamaGenerator

        return OllamaGenerator(cfg)
    if provider == "openai_compatible":
        from .llm import OpenAICompatibleGenerator

        return OpenAICompatibleGenerator(cfg)
    if provider == "offline":
        from .offline import OfflineGenerator

        return OfflineGenerator(cfg)
    raise AutoShortsError(
        f"unknown script.provider '{cfg.script.provider}'; use one of: {', '.join(PROVIDERS)}"
    )
