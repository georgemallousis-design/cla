"""LLM script generators: a local Ollama server or any OpenAI-compatible chat API.

Both use plain ``requests`` (no SDKs). A reply is parsed with :func:`extract_json`,
then checked by ``validate_script``; when either fails the model is asked again with
the error message, up to ``MAX_ATTEMPTS`` times.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from ..config import Config
from ..models import VideoScript
from ..utils import AutoShortsError, http_session, log
from . import ScriptGenerator, prompts, resolve_format
from .validate import ScriptValidationError, target_words, validate_script

MAX_ATTEMPTS = 3
HTTP_RETRIES = 3  # for network errors, HTTP 429 and 5xx (OpenAI-compatible APIs)
MAX_RETRY_WAIT = 30.0
OLLAMA_NUM_CTX = 4096

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


# --------------------------------------------------------------------------- JSON extraction


def _balanced_object(text: str, start: int) -> str | None:
    """The {...} starting at ``start``, honouring strings and escapes; None if unterminated."""
    depth, in_str, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _loads_lenient(candidate: str) -> Any:
    try:
        return json.loads(candidate, strict=False)
    except json.JSONDecodeError:
        pass
    fixed = _TRAILING_COMMA_RE.sub(r"\1", candidate)
    fixed = fixed.replace("\u201c", '"').replace("\u201d", '"')
    return json.loads(fixed, strict=False)


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM reply.

    Handles code fences, prose before/after the object, trailing commas, literal
    newlines inside strings and smart quotes. Raises ScriptValidationError otherwise.
    """
    if not text or not text.strip():
        raise ScriptValidationError("the reply was empty")
    fenced = _FENCE_RE.findall(text)
    sources = [*fenced, text] if fenced else [text]
    last_error = "no JSON object found in the reply"
    for source in sources:
        if source.lstrip().startswith("["):  # a bare list of segments
            try:
                items = _loads_lenient(source.strip())
            except json.JSONDecodeError:
                items = None
            if isinstance(items, list):
                return {"segments": items}
        start = source.find("{")
        while start != -1:
            candidate = _balanced_object(source, start)
            if candidate is None:
                last_error = "the JSON object is cut off (missing closing brace); keep the reply shorter"
                break
            try:
                data = _loads_lenient(candidate)
            except json.JSONDecodeError as exc:
                last_error = f"invalid JSON ({exc.msg} at line {exc.lineno} column {exc.colno})"
            else:
                if isinstance(data, dict):
                    return data
            start = source.find("{", start + len(candidate))
    raise ScriptValidationError(last_error)


# --------------------------------------------------------------------------- shared generator


class LLMGenerator(ScriptGenerator):
    """Prompt -> chat call -> extract JSON -> validate, with self-correcting retries."""

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.session = http_session()

    def _chat(self, messages: list[dict[str, str]]) -> str:
        raise NotImplementedError

    def generate(self, topic: str, fmt: str) -> VideoScript:
        fmt = resolve_format(fmt)
        messages = prompts.build_messages(self.cfg, topic, fmt)
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            log.info("%s: writing %s script for %r (attempt %d/%d)", self.name, fmt, topic, attempt, MAX_ATTEMPTS)
            reply = self._chat(messages)
            try:
                data = extract_json(reply)
                return validate_script(
                    data,
                    topic=topic,
                    fmt=fmt,
                    target_words=target_words(self.cfg),
                    language=self.cfg.script.language,
                )
            except ScriptValidationError as exc:
                last_error = str(exc)
                log.warning("%s: unusable script (%s)", self.name, last_error)
                messages = [
                    *messages,
                    {"role": "assistant", "content": reply[:3000]},
                    {"role": "user", "content": prompts.repair_prompt(self.cfg, last_error)},
                ]
        raise AutoShortsError(
            f"{self.name} did not produce a usable script after {MAX_ATTEMPTS} attempts "
            f"(last problem: {last_error}). Try a larger model or set script.provider to 'offline'."
        )


def _error_text(resp: requests.Response) -> str:
    """Short, key-free error description from an API error response."""
    try:
        body = resp.json()
        err = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(err, dict):
            err = err.get("message") or err
        text = str(err)
    except ValueError:
        text = resp.text or ""
    return " ".join(text.split())[:300]


# --------------------------------------------------------------------------- Ollama


def _normalize_model(name: str) -> str:
    name = name.strip()
    return name if ":" in name else f"{name}:latest"


def ollama_models(cfg: Config, timeout: float = 2.0) -> list[str] | None:
    """Installed Ollama model names, or None when the server is not reachable."""
    url = cfg.script.ollama.base_url.rstrip("/") + "/api/tags"
    try:
        resp = http_session().get(url, timeout=timeout)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        models = resp.json().get("models") or []
    except (ValueError, AttributeError):
        return []
    names: list[str] = []
    for m in models:
        if isinstance(m, dict):
            names.extend(str(m[k]) for k in ("name", "model") if m.get(k))
    return names


def ollama_has_model(model: str, installed: list[str]) -> bool:
    wanted = _normalize_model(model)
    return any(_normalize_model(name) == wanted for name in installed)


class OllamaGenerator(LLMGenerator):
    """Local, free: https://ollama.com (default model llama3.1:8b)."""

    name = "ollama"

    def _chat(self, messages: list[dict[str, str]]) -> str:
        ocfg = self.cfg.script.ollama
        url = ocfg.base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": ocfg.model,
            "messages": messages,
            "format": "json",
            "stream": False,
            "options": {"temperature": self.cfg.script.temperature, "num_ctx": OLLAMA_NUM_CTX},
        }
        try:
            resp = self.session.post(url, json=payload, timeout=self.cfg.script.timeout)
        except requests.Timeout:
            raise AutoShortsError(
                f"Ollama did not answer within {self.cfg.script.timeout}s - try a smaller model "
                "or raise script.timeout in config.yaml"
            ) from None
        except requests.RequestException:
            raise AutoShortsError(
                f"Ollama not reachable at {ocfg.base_url} - install it from https://ollama.com, start it, "
                f"and run 'ollama pull {ocfg.model}' (or set script.provider to 'offline')"
            ) from None
        if resp.status_code == 404:
            raise AutoShortsError(
                f"Ollama model '{ocfg.model}' is not installed - run 'ollama pull {ocfg.model}' "
                f"({_error_text(resp)})"
            )
        if resp.status_code != 200:
            raise AutoShortsError(f"Ollama returned HTTP {resp.status_code}: {_error_text(resp)}")
        try:
            return str(resp.json()["message"]["content"] or "")
        except (ValueError, KeyError, TypeError):
            raise AutoShortsError("Ollama returned an unexpected response (no message content)") from None


# --------------------------------------------------------------------------- OpenAI-compatible


class OpenAICompatibleGenerator(LLMGenerator):
    """Any OpenAI-compatible /chat/completions API (Groq, Gemini, OpenRouter free tiers)."""

    name = "openai_compatible"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.json_mode = True  # switched off if the server rejects response_format

    def _api_key(self) -> str:
        env = self.cfg.script.openai_compatible.api_key_env
        key = Config.secret(env)
        if not key:
            raise AutoShortsError(
                f"no API key for {self.cfg.script.openai_compatible.base_url}: put {env}=... in your .env "
                "(free keys: https://console.groq.com/keys, https://aistudio.google.com/apikey, "
                "https://openrouter.ai/keys) or set script.provider to 'ollama' or 'offline'"
            )
        return key

    def _post(self, url: str, payload: dict, headers: dict[str, str]) -> requests.Response:
        """POST with backoff on network errors, 429 and 5xx."""
        for attempt in range(HTTP_RETRIES):
            last = attempt == HTTP_RETRIES - 1
            try:
                resp = self.session.post(url, json=payload, headers=headers, timeout=self.cfg.script.timeout)
            except requests.RequestException as exc:
                if last:
                    raise AutoShortsError(
                        f"could not reach {self.cfg.script.openai_compatible.base_url}: {type(exc).__name__}. "
                        "Check your internet connection and script.openai_compatible.base_url"
                    ) from None
                wait = 2.0 ** (attempt + 1)
            else:
                if (resp.status_code == 429 or resp.status_code >= 500) and not last:
                    wait = _retry_after(resp, default=2.0 ** (attempt + 1))
                else:
                    return resp
            log.warning("LLM API busy or unreachable, retrying in %.0fs", wait)
            time.sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def _chat(self, messages: list[dict[str, str]]) -> str:
        ocfg = self.cfg.script.openai_compatible
        url = ocfg.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"}
        payload: dict[str, Any] = {
            "model": ocfg.model,
            "messages": messages,
            "temperature": self.cfg.script.temperature,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        resp = self._post(url, payload, headers)
        _raise_if_model_gone(resp, ocfg.base_url, ocfg.model)
        if resp.status_code == 400 and "response_format" in payload:
            log.info("%s rejected JSON mode (%s); retrying without it", ocfg.base_url, _error_text(resp))
            self.json_mode = False
            payload = {k: v for k, v in payload.items() if k != "response_format"}
            resp = self._post(url, payload, headers)
        _raise_for_api_status(resp, ocfg.base_url, ocfg.model, ocfg.api_key_env)
        try:
            choice = resp.json()["choices"][0]
            content = choice["message"].get("content")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AutoShortsError(f"unexpected response from {ocfg.base_url} (no choices[0].message)") from None
        if choice.get("finish_reason") == "length":
            log.warning("LLM reply was cut off at the token limit")
        if isinstance(content, list):  # some servers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return str(content or "")


def _retry_after(resp: requests.Response, default: float) -> float:
    try:
        value = float(resp.headers.get("Retry-After", ""))
    except ValueError:
        value = default
    return max(0.0, min(value, MAX_RETRY_WAIT))


_MODEL_GONE_RE = re.compile(r"decommission|model_not_found|does not exist|no longer (?:available|supported)", re.I)


def _raise_if_model_gone(resp: requests.Response, base_url: str, model: str) -> None:
    """Providers retire models (Groq shut llama-3.3-70b-versatile down in August 2026) and
    answer 400/404 'model_decommissioned' / 'model_not_found'. Say so plainly, instead of
    mistaking it for a rejected JSON mode or a wrong base_url."""
    if resp.status_code not in (400, 404):
        return
    try:
        raw = resp.text or ""
    except Exception:  # pragma: no cover - defensive
        raw = ""
    detail = _error_text(resp)
    if _MODEL_GONE_RE.search(detail) or _MODEL_GONE_RE.search(raw[:2000]):
        raise AutoShortsError(
            f"model '{model}' is no longer available at {base_url}; set script.openai_compatible.model "
            f"in config.yaml to a current model (Groq: openai/gpt-oss-120b, list: "
            f"https://console.groq.com/docs/models): {detail}"
        )


def _raise_for_api_status(resp: requests.Response, base_url: str, model: str, key_env: str) -> None:
    code = resp.status_code
    if code == 200:
        return
    _raise_if_model_gone(resp, base_url, model)
    detail = _error_text(resp)
    if code in (401, 403):
        raise AutoShortsError(f"{base_url} rejected the API key (HTTP {code}); check {key_env} in .env: {detail}")
    if code == 404:
        raise AutoShortsError(
            f"{base_url} returned 404 - check script.openai_compatible.base_url and that model '{model}' exists: {detail}"
        )
    if code == 429:
        raise AutoShortsError(f"{base_url} rate limit reached (HTTP 429); wait a minute or make fewer videos per batch: {detail}")
    raise AutoShortsError(f"{base_url} returned HTTP {code}: {detail}")
