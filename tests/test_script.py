"""Tests for script generation: JSON extraction, validation, prompts, LLM clients (mocked),
the offline generator, the content bank and provider auto-selection. All offline."""
from __future__ import annotations

import json
import logging
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import requests

from autoshorts.config import Config
from autoshorts.models import Segment, VideoScript
from autoshorts.script import (
    FORMATS,
    FallbackGenerator,
    ScriptGenerator,
    get_generator,
    resolve_format,
)
from autoshorts.script import llm, prompts
from autoshorts.script.llm import (
    OllamaGenerator,
    OpenAICompatibleGenerator,
    extract_json,
    ollama_has_model,
)
from autoshorts.script.offline import OfflineGenerator, load_bank, match_score
from autoshorts.script.validate import (
    ScriptValidationError,
    clean_hashtags,
    count_words,
    script_words,
    target_words,
    trim_to_budget,
    validate_script,
)
from autoshorts.utils import AutoShortsError

# --------------------------------------------------------------------------- helpers


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    c = Config()
    c.base_dir = tmp_path
    return c


def make_data(n_segments: int = 8, words_per_segment: int = 20, **extra: Any) -> dict:
    """A plausible LLM reply as a dict."""
    segs = [{"text": "Did you know this hook works so well?", "visual_query": "ocean waves"}]
    for i in range(1, n_segments - 1):
        segs.append({"text": " ".join([f"word{i}"] * words_per_segment) + ".", "visual_query": f"scene {i}"})
    segs.append({"text": "That is the payoff. Follow for more.", "visual_query": "sunset"})
    data = {
        "title": "Ocean Facts You Never Knew",
        "segments": segs,
        "description": "Amazing facts about the ocean.",
        "hashtags": ["ocean", "facts"],
    }
    data.update(extra)
    return data


def good_reply(cfg: Config) -> str:
    """A JSON reply whose length is right on target."""
    target = target_words(cfg)
    per = target // 8
    return json.dumps(make_data(n_segments=10, words_per_segment=per))


class FakeResponse:
    def __init__(self, status: int = 200, data: Any = None, text: str = "", headers: dict | None = None):
        self.status_code = status
        self._data = data
        self.text = text or (json.dumps(data) if data is not None else "")
        self.headers = headers or {}

    def json(self) -> Any:
        if self._data is None:
            raise ValueError("no json")
        return self._data


class FakeSession:
    """Stands in for requests.Session; replies are popped from queues, calls are recorded."""

    def __init__(self, posts: list | None = None, gets: list | None = None):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.headers: dict[str, str] = {}

    @staticmethod
    def _next(queue: list) -> FakeResponse:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url: str, **kw: Any) -> FakeResponse:
        self.post_calls.append({"url": url, **kw})
        return self._next(self.posts)

    def get(self, url: str, **kw: Any) -> FakeResponse:
        self.get_calls.append({"url": url, **kw})
        return self._next(self.gets)


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch):
    """Install a FakeSession for all HTTP made by script.llm; returns a factory."""

    def install(posts: list | None = None, gets: list | None = None) -> FakeSession:
        session = FakeSession(posts, gets)
        monkeypatch.setattr(llm, "http_session", lambda: session)
        return session

    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    return install


def ollama_reply(content: str) -> FakeResponse:
    return FakeResponse(200, {"model": "llama3.1:8b", "message": {"role": "assistant", "content": content}, "done": True})


def openai_reply(content: str) -> FakeResponse:
    return FakeResponse(200, {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]})


# --------------------------------------------------------------------------- JSON extraction


class TestExtractJson:
    def test_plain_object(self):
        assert extract_json('{"title": "x", "segments": []}') == {"title": "x", "segments": []}

    def test_code_fence_with_prose(self):
        text = 'Sure! Here is your script:\n```json\n{"title": "Fenced", "n": 1}\n```\nEnjoy!'
        assert extract_json(text)["title"] == "Fenced"

    def test_bare_fence(self):
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_leading_and_trailing_prose(self):
        assert extract_json('Okay. {"a": {"b": [1, 2]}} Hope that helps.') == {"a": {"b": [1, 2]}}

    def test_trailing_commas(self):
        text = '{"title": "t", "segments": [{"text": "a", "visual_query": "b",},], "hashtags": ["x",],}'
        data = extract_json(text)
        assert data["segments"][0]["text"] == "a" and data["hashtags"] == ["x"]

    def test_braces_and_escaped_quotes_inside_strings(self):
        text = 'prefix {"text": "a } brace and a \\"quote\\" {here}", "n": 2} suffix'
        assert extract_json(text) == {"text": 'a } brace and a "quote" {here}', "n": 2}

    def test_literal_newline_inside_string(self):
        assert extract_json('{"text": "line one\nline two"}')["text"] == "line one\nline two"

    def test_skips_invalid_braced_prose_before_object(self):
        assert extract_json('Use the format {title, segments}. {"title": "Real"}') == {"title": "Real"}

    def test_bare_segment_list(self):
        assert extract_json('[{"text": "a"}, {"text": "b"},]') == {"segments": [{"text": "a"}, {"text": "b"}]}

    def test_smart_quotes(self):
        assert extract_json("{\u201ctitle\u201d: \u201cSmart\u201d}") == {"title": "Smart"}

    @pytest.mark.parametrize(
        "text, needle",
        [
            ("", "empty"),
            ("   ", "empty"),
            ("no json here", "no JSON object"),
            ('{"title": "cut off', "cut off"),
            ("[1, 2", "no JSON object"),
        ],
    )
    def test_failures(self, text, needle):
        with pytest.raises(ScriptValidationError) as exc:
            extract_json(text)
        assert needle in str(exc.value)


# --------------------------------------------------------------------------- validation


class TestValidate:
    def test_target_words(self, cfg):
        assert target_words(cfg) == round(65 * 2.6) == 169
        cfg.video.target_seconds = 30
        assert target_words(cfg) == 78

    def test_count_words(self):
        assert count_words("Octopuses have three hearts \u2014 and blue blood!") == 7
        assert count_words("  ") == 0
        assert count_words("\u4f60\u597d\u4e16\u754c") > 0  # CJK has no spaces

    def test_normalizes_everything(self):
        data = make_data(
            title="  #Wow   The " + "very " * 30 + "long title  ",
            description="Great facts here #ocean #DeepSea about the sea.",
            hashtags=["#Facts", "Ocean", "deep sea!", "facts", "", "#OCEAN"],
        )
        data["segments"].insert(2, {"text": "   ", "visual_query": "empty"})
        data["segments"][1]["text"] = "  Hook:   lots   of\n spaces [dramatic music] here (pause) \U0001F419 **bold**  "
        s = validate_script(data, topic="ocean", fmt="facts", target_words=100)
        assert isinstance(s, VideoScript)
        assert len(s.title) <= 90 and not s.title.endswith(" ") and "#" not in s.title
        assert s.title.startswith("The very")
        assert s.segments[1].text == "lots of spaces here bold"
        assert all(seg.text for seg in s.segments)
        assert len(s.segments) == 8  # the blank segment was dropped
        assert "#" not in s.description and s.description == "Great facts here about the sea."
        assert s.hashtags == ["facts", "ocean", "deepsea"]
        assert s.format == "facts" and s.topic == "ocean" and s.language == "en"

    def test_title_trimmed_at_word_boundary(self):
        title = "Word " * 40
        s = validate_script(make_data(title=title), topic="t", fmt="facts", target_words=100)
        assert len(s.title) <= 90 and s.title.split()[-1] == "Word"

    def test_hashtags_max_ten_and_string_input(self):
        tags = clean_hashtags("#a1 b2, c3;d4 " + " ".join(f"t{i}" for i in range(20)))
        assert tags[:4] == ["a1", "b2", "c3", "d4"] and len(tags) == 10
        assert all(re.fullmatch(r"[a-z0-9]+", t) for t in tags)

    def test_missing_visual_query_uses_topic_keywords(self):
        data = make_data()
        for seg in data["segments"]:
            seg.pop("visual_query")
        s = validate_script(data, topic="Deep sea creatures of the ocean", fmt="facts", target_words=100)
        assert all(seg.visual_query == "deep sea creatures" for seg in s.segments)

    def test_visual_query_limited_to_four_words(self):
        data = make_data()
        data["segments"][0]["visual_query"] = "A huge octopus swimming in the deep blue ocean!"
        s = validate_script(data, topic="x", fmt="facts", target_words=100)
        assert s.segments[0].visual_query == "huge octopus swimming deep"

    def test_aliases_wrappers_and_string_segments(self):
        inner = make_data()
        inner["segments"] = [{"narration": f"Line number {i} has several words in it.", "visual": "city night"} for i in range(6)]
        s = validate_script({"script": inner}, topic="cities", fmt="facts", target_words=40)
        assert len(s.segments) == 6 and s.segments[0].visual_query == "city night"

        plain = {"title": "T", "segments": [f"Sentence {i} with enough words to count." for i in range(5)]}
        s2 = validate_script(plain, topic="space travel", fmt="facts", target_words=40)
        assert s2.segments[0].visual_query == "space travel" and s2.description == s2.hook

    def test_missing_title_falls_back_to_hook(self):
        s = validate_script(make_data(title=""), topic="t", fmt="facts", target_words=100)
        assert s.title == "Did you know this hook works so well?"

    def test_budget_trimming_keeps_hook_and_last(self):
        data = make_data(n_segments=10, words_per_segment=40)  # ~340 words
        s = validate_script(data, topic="t", fmt="facts", target_words=169)
        assert script_words(s.segments) <= int(169 * 1.25)
        assert s.segments[0].text == data["segments"][0]["text"]
        assert s.segments[-1].text == data["segments"][-1]["text"]
        assert len(s.segments) >= 3

    def test_no_trimming_within_budget(self):
        data = make_data(n_segments=8, words_per_segment=30)  # ~196 words, under 211
        s = validate_script(data, topic="t", fmt="facts", target_words=169)
        assert len(s.segments) == 8

    def test_quiz_trimming_drops_question_with_answer(self):
        segs = [Segment("Three quick questions. Ready?", "q")]
        for i in range(3):
            segs.append(Segment(f"Question {i}: what is it? " + "pad " * 20 + "Three, two, one.", "q"))
            segs.append(Segment(f"Answer {i}. " + "pad " * 25, "a"))
        segs.append(Segment("Comment your score. Follow for more.", "end"))
        trimmed = trim_to_budget(segs, max_words=script_words(segs) - 10, fmt="quiz")
        texts = [s.text for s in trimmed]
        assert not any(t.startswith("Question 2") or t.startswith("Answer 2") for t in texts)
        assert sum(t.startswith("Question") for t in texts) == sum(t.startswith("Answer") for t in texts) == 2
        assert texts[0].startswith("Three quick") and texts[-1].startswith("Comment")

    def test_too_few_segments(self):
        data = {"title": "t", "segments": [{"text": "one"}, {"text": "  "}, {"text": "two"}]}
        with pytest.raises(ScriptValidationError, match="segment"):
            validate_script(data, topic="t", fmt="facts", target_words=10)

    def test_too_short(self):
        data = make_data(n_segments=6, words_per_segment=3)
        with pytest.raises(ScriptValidationError, match="too short"):
            validate_script(data, topic="t", fmt="facts", target_words=169)
        # the same data passes when length checks are disabled (offline bank)
        assert validate_script(data, topic="t", fmt="facts", target_words=169, min_ratio=0)

    @pytest.mark.parametrize("bad", [None, [], "text", {"title": "no segments"}, {"segments": "not a list"}])
    def test_structure_errors(self, bad):
        with pytest.raises(ScriptValidationError):
            validate_script(bad, topic="t", fmt="facts", target_words=10)


# --------------------------------------------------------------------------- prompts


class TestPrompts:
    @pytest.mark.parametrize("fmt", FORMATS)
    def test_system_prompt_rules(self, cfg, fmt):
        text = prompts.system_prompt(cfg, fmt)
        assert "169" in text and "152-186" in text  # target and +-10% range
        assert "12 words" in text and "In this video" in text
        assert "If unsure of a fact, leave it out" in text
        assert "JSON" in text and '"visual_query"' in text
        assert "6-10 segments" in text and "Follow for more" in text
        assert "No medical, legal or financial advice" in text
        assert f"FORMAT: {fmt.upper()}" in text

    def test_format_specific_rules(self, cfg):
        assert "fictional" in prompts.system_prompt(cfg, "story")
        assert "Never claim it happened to you" in prompts.system_prompt(cfg, "story")
        assert "Three, two, one" in prompts.system_prompt(cfg, "quiz")
        assert "Only quote a real person if you are certain" in prompts.system_prompt(cfg, "motivation")

    def test_word_target_follows_config(self, cfg):
        cfg.video.target_seconds = 40
        assert str(round(40 * 2.6)) in prompts.system_prompt(cfg, "facts")
        assert "94-114" in prompts.user_prompt(cfg, "x", "facts")

    def test_language(self, cfg):
        cfg.script.language = "es"
        assert "in Spanish" in prompts.system_prompt(cfg, "facts")
        assert "visual_query is always English" in prompts.system_prompt(cfg, "facts")
        assert prompts.language_name("pt-BR") == "Portuguese"
        assert "xx" in prompts.language_name("xx")

    def test_user_prompt_and_messages(self, cfg):
        msgs = prompts.build_messages(cfg, '  black   "holes" ', "quiz")
        assert [m["role"] for m in msgs] == ["system", "user"]
        assert "Topic: \"black 'holes'\"" in msgs[1]["content"] and "Format: quiz" in msgs[1]["content"]
        assert "choose a fascinating" in prompts.user_prompt(cfg, "", "facts")

    def test_repair_prompt(self, cfg):
        text = prompts.repair_prompt(cfg, "script too short")
        assert "script too short" in text and "152-186" in text


# --------------------------------------------------------------------------- Ollama


class TestOllama:
    def test_success_and_payload(self, cfg, fake_http):
        session = fake_http(posts=[ollama_reply(good_reply(cfg))])
        cfg.script.temperature = 0.7
        script = OllamaGenerator(cfg).generate("ocean", "facts")
        assert isinstance(script, VideoScript) and script.format == "facts" and script.topic == "ocean"
        call = session.post_calls[0]
        assert call["url"] == "http://localhost:11434/api/chat"
        body = call["json"]
        assert body["format"] == "json" and body["stream"] is False
        assert body["options"]["temperature"] == 0.7 and body["model"] == "llama3.1:8b"
        assert body["messages"][0]["role"] == "system"
        assert call["timeout"] == cfg.script.timeout

    def test_retries_after_bad_json_with_error_message(self, cfg, fake_http):
        session = fake_http(posts=[ollama_reply("Sorry, I cannot do JSON today."), ollama_reply(good_reply(cfg))])
        script = OllamaGenerator(cfg).generate("ocean", "facts")
        assert script.title
        assert len(session.post_calls) == 2
        retry_msgs = session.post_calls[1]["json"]["messages"]
        assert retry_msgs[-2]["role"] == "assistant"
        assert "could not be used" in retry_msgs[-1]["content"] and "no JSON object" in retry_msgs[-1]["content"]

    def test_retries_after_validation_failure(self, cfg, fake_http):
        short = json.dumps(make_data(n_segments=6, words_per_segment=2))
        session = fake_http(posts=[ollama_reply(short), ollama_reply(good_reply(cfg))])
        OllamaGenerator(cfg).generate("ocean", "facts")
        assert "too short" in session.post_calls[1]["json"]["messages"][-1]["content"]

    def test_gives_up_after_three_attempts(self, cfg, fake_http):
        session = fake_http(posts=[ollama_reply("nope")] * 3)
        with pytest.raises(AutoShortsError, match="after 3 attempts"):
            OllamaGenerator(cfg).generate("ocean", "facts")
        assert len(session.post_calls) == 3

    def test_not_reachable(self, cfg, fake_http):
        fake_http(posts=[requests.ConnectionError("refused")])
        with pytest.raises(AutoShortsError) as exc:
            OllamaGenerator(cfg).generate("ocean", "facts")
        msg = str(exc.value)
        assert "Ollama not reachable at http://localhost:11434" in msg
        assert "https://ollama.com" in msg and "ollama pull llama3.1:8b" in msg

    def test_model_missing(self, cfg, fake_http):
        fake_http(posts=[FakeResponse(404, {"error": "model 'llama3.1:8b' not found"})])
        with pytest.raises(AutoShortsError, match="ollama pull llama3.1:8b"):
            OllamaGenerator(cfg).generate("ocean", "facts")

    def test_timeout(self, cfg, fake_http):
        fake_http(posts=[requests.Timeout("slow")])
        with pytest.raises(AutoShortsError, match="did not answer"):
            OllamaGenerator(cfg).generate("ocean", "facts")

    def test_model_name_matching(self):
        assert ollama_has_model("llama3.1:8b", ["llama3.1:8b"])
        assert ollama_has_model("mistral", ["mistral:latest"])
        assert not ollama_has_model("llama3.1:8b", ["llama3.1:70b", "mistral:latest"])
        assert not ollama_has_model("x", [])


# --------------------------------------------------------------------------- OpenAI-compatible


class TestOpenAICompatible:
    @pytest.fixture(autouse=True)
    def key(self, cfg, monkeypatch):  # after cfg, which clears the variable
        monkeypatch.setenv("LLM_API_KEY", "sk-test-secret-123")

    def test_success_and_request(self, cfg, fake_http):
        fenced = "Here you go:\n```json\n" + good_reply(cfg) + "\n```"
        session = fake_http(posts=[openai_reply(fenced)])
        script = OpenAICompatibleGenerator(cfg).generate("ocean", "quiz")
        assert script.format == "quiz"
        call = session.post_calls[0]
        assert call["url"] == "https://api.groq.com/openai/v1/chat/completions"
        assert call["headers"]["Authorization"] == "Bearer sk-test-secret-123"
        body = call["json"]
        assert body["response_format"] == {"type": "json_object"}
        assert body["model"] == cfg.script.openai_compatible.model
        assert body["temperature"] == cfg.script.temperature

    def test_400_retries_without_response_format(self, cfg, fake_http, caplog):
        session = fake_http(
            posts=[
                FakeResponse(400, {"error": {"message": "response_format is not supported"}}),
                openai_reply(good_reply(cfg)),
                openai_reply(good_reply(cfg)),
            ]
        )
        gen = OpenAICompatibleGenerator(cfg)
        with caplog.at_level(logging.INFO, logger="autoshorts"):
            gen.generate("ocean", "facts")
        assert "response_format" in session.post_calls[0]["json"]
        assert "response_format" not in session.post_calls[1]["json"]
        assert gen.json_mode is False
        gen.generate("ocean", "facts")  # later calls skip JSON mode straight away
        assert "response_format" not in session.post_calls[2]["json"]
        assert "sk-test-secret-123" not in caplog.text

    def test_retry_after_bad_json(self, cfg, fake_http):
        session = fake_http(posts=[openai_reply('{"title": "broken", "segments": ['), openai_reply(good_reply(cfg))])
        OpenAICompatibleGenerator(cfg).generate("ocean", "facts")
        assert len(session.post_calls) == 2
        assert "cut off" in session.post_calls[1]["json"]["messages"][-1]["content"]

    def test_rate_limit_backoff(self, cfg, fake_http):
        session = fake_http(
            posts=[FakeResponse(429, {"error": "slow down"}, headers={"Retry-After": "1"}), openai_reply(good_reply(cfg))]
        )
        OpenAICompatibleGenerator(cfg).generate("ocean", "facts")
        assert len(session.post_calls) == 2

    def test_bad_key_message_does_not_leak_key(self, cfg, fake_http):
        fake_http(posts=[FakeResponse(401, {"error": {"message": "Invalid API Key"}})])
        with pytest.raises(AutoShortsError) as exc:
            OpenAICompatibleGenerator(cfg).generate("ocean", "facts")
        assert "LLM_API_KEY" in str(exc.value) and "sk-test-secret-123" not in str(exc.value)

    def test_missing_key(self, cfg, fake_http, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY")
        fake_http(posts=[])
        with pytest.raises(AutoShortsError, match="LLM_API_KEY"):
            OpenAICompatibleGenerator(cfg).generate("ocean", "facts")

    def test_network_error(self, cfg, fake_http):
        fake_http(posts=[requests.ConnectionError("x")] * 3)
        with pytest.raises(AutoShortsError, match="could not reach"):
            OpenAICompatibleGenerator(cfg).generate("ocean", "facts")


# --------------------------------------------------------------------------- offline generator


class TestOffline:
    @pytest.mark.parametrize("fmt", FORMATS)
    def test_every_format(self, cfg, fmt):
        script = OfflineGenerator(cfg, rng=random.Random(0)).generate("", fmt)
        assert isinstance(script, VideoScript) and script.format == fmt
        assert len(script.segments) >= 6 and script.language == "en"

    def test_topic_matching(self, cfg):
        gen = OfflineGenerator(cfg, rng=random.Random(0))
        assert "Octopus" in gen.generate("octopus", "facts").title
        assert "Sky" in gen.generate("Why is the sky blue?", "explainer").title
        assert "Space" in gen.generate("planets quiz", "quiz").title

    def test_unknown_topic_warns_and_uses_format(self, cfg, caplog):
        with caplog.at_level(logging.WARNING, logger="autoshorts"):
            script = OfflineGenerator(cfg, rng=random.Random(3)).generate("cryptocurrency taxes", "motivation")
        assert script.format == "motivation"
        assert "offline mode ignores unknown topics" in caplog.text

    def test_does_not_repeat_and_saves_state(self, cfg):
        gen = OfflineGenerator(cfg, rng=random.Random(5))
        n_story = sum(e["format"] == "story" for e in load_bank())
        titles = [gen.generate("", "story").title for _ in range(n_story)]
        assert len(set(titles)) == n_story  # cycles through all before repeating
        nxt = gen.generate("", "story").title
        assert nxt != titles[-1]
        state = json.loads((cfg.path("state") / "offline_used.json").read_text())
        assert state["used"][-1] == nxt

    def test_avoids_last_used_when_alternatives_match(self, cfg):
        gen = OfflineGenerator(cfg, rng=random.Random(0))
        first = gen.generate("ocean", "facts").title
        second = gen.generate("ocean", "facts").title
        assert first != second

    def test_corrupt_state_is_ignored(self, cfg):
        state = cfg.path("state")
        state.mkdir(parents=True)
        (state / "offline_used.json").write_text("{not json", encoding="utf-8")
        assert OfflineGenerator(cfg).generate("", "facts").title

    def test_short_target_trims_but_keeps_quiz_pairs(self, cfg):
        cfg.video.target_seconds = 30
        script = OfflineGenerator(cfg, rng=random.Random(0)).generate("geography", "quiz")
        assert script_words(script.segments) <= int(target_words(cfg) * 1.25)
        questions = [s for s in script.segments[1:-1] if "?" in s.text]
        assert len(script.segments) - 2 == 2 * len(questions)

    def test_random_format_resolved(self, cfg):
        assert OfflineGenerator(cfg, rng=random.Random(1)).generate("", "random").format in FORMATS

    def test_match_score(self):
        entry = {"topic": "octopus ocean animals", "title": "The Octopus", "hashtags": ["octopus"], "segments": []}
        assert match_score("octopus facts", entry) > match_score("ocean", entry) > 0
        assert match_score("crazy facts", entry) == 0


# --------------------------------------------------------------------------- content bank


BANK = load_bank()


class TestContentBank:
    def test_size_and_formats(self):
        assert len(BANK) >= 30
        counts = Counter(e["format"] for e in BANK)
        assert set(counts) == set(FORMATS)
        assert all(counts[f] >= 4 for f in FORMATS), counts
        assert len({e["title"] for e in BANK}) == len(BANK), "titles must be unique"

    @pytest.mark.parametrize("entry", BANK, ids=[e["title"][:40] for e in BANK])
    def test_entry(self, entry):
        assert set(entry) >= {"topic", "format", "title", "segments", "description", "hashtags"}
        cfg = Config()
        script = validate_script(entry, topic=entry["topic"], fmt=entry["format"], target_words=target_words(cfg))
        # nothing was trimmed or rewritten by validation
        assert [s.text for s in script.segments] == [s["text"] for s in entry["segments"]]
        assert [s.visual_query for s in script.segments] == [s["visual_query"] for s in entry["segments"]]
        assert script.title == entry["title"] and len(entry["title"]) <= 90
        assert script.hashtags == entry["hashtags"] and 3 <= len(entry["hashtags"]) <= 10
        assert script.description == entry["description"] and "#" not in entry["description"]
        words = script_words(script.segments)
        assert 120 <= words <= 185, words
        assert 6 <= len(script.segments) <= 10
        assert count_words(script.hook) <= 12
        assert "in this video" not in script.hook.lower()
        assert "follow" in script.segments[-1].text.lower()
        for seg in entry["segments"]:
            assert 1 <= len(seg["visual_query"].split()) <= 4
            assert re.fullmatch(r"[a-z0-9 ]+", seg["visual_query"]), seg["visual_query"]
            assert seg["text"].isascii()
        if entry["format"] == "story":
            assert "fictional" in entry["description"].lower()
            assert any(w in script.hook.lower() for w in ("story", "imagine"))
        if entry["format"] == "quiz":
            assert sum("?" in s.text for s in script.segments[1:-1]) >= 3


# --------------------------------------------------------------------------- selection


class TestGetGenerator:
    def test_explicit_providers(self, cfg):
        for provider, cls in [
            ("offline", OfflineGenerator),
            ("ollama", OllamaGenerator),
            ("openai_compatible", OpenAICompatibleGenerator),
            ("openai-compatible", OpenAICompatibleGenerator),
        ]:
            cfg.script.provider = provider
            assert isinstance(get_generator(cfg), cls)

    def test_unknown_provider(self, cfg):
        cfg.script.provider = "chatgpt-plus"
        with pytest.raises(AutoShortsError, match="unknown script.provider"):
            get_generator(cfg)

    def test_auto_prefers_ollama(self, cfg, fake_http):
        session = fake_http(gets=[FakeResponse(200, {"models": [{"name": "llama3.1:8b", "model": "llama3.1:8b"}]})])
        gen = get_generator(cfg)
        assert isinstance(gen, FallbackGenerator) and isinstance(gen.primary, OllamaGenerator)
        assert isinstance(gen.generators[-1], OfflineGenerator) and gen.name == "ollama"
        assert session.get_calls[0]["url"] == "http://localhost:11434/api/tags"
        assert session.get_calls[0]["timeout"] == 2.0

    def test_auto_skips_ollama_without_model(self, cfg, fake_http, caplog):
        fake_http(gets=[FakeResponse(200, {"models": [{"name": "mistral:latest"}]})])
        with caplog.at_level(logging.WARNING, logger="autoshorts"):
            gen = get_generator(cfg)
        assert isinstance(gen, OfflineGenerator)
        assert "ollama pull llama3.1:8b" in caplog.text

    def test_auto_uses_api_key(self, cfg, fake_http, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "k")
        fake_http(gets=[requests.ConnectionError("down")])
        gen = get_generator(cfg)
        assert isinstance(gen, FallbackGenerator) and isinstance(gen.primary, OpenAICompatibleGenerator)
        assert [g.name for g in gen.generators] == ["openai_compatible", "offline"]

    def test_auto_offline(self, cfg, fake_http, caplog):
        fake_http(gets=[requests.ConnectionError("down")])
        with caplog.at_level(logging.INFO, logger="autoshorts"):
            gen = get_generator(cfg)
        assert isinstance(gen, OfflineGenerator)
        assert "script generator: offline" in caplog.text and "not reachable" in caplog.text

    def test_fallback_on_generation_failure(self, cfg, fake_http):
        fake_http(
            gets=[FakeResponse(200, {"models": [{"name": "llama3.1:8b"}]})],
            posts=[requests.ConnectionError("died")],
        )
        gen = get_generator(cfg)
        script = gen.generate("octopus", "facts")
        assert gen.last_used == "offline" and "Octopus" in script.title

    def test_fallback_all_fail(self, cfg):
        class Broken(ScriptGenerator):
            name = "broken"

            def generate(self, topic, fmt):
                raise AutoShortsError("nope")

        with pytest.raises(AutoShortsError, match="every script generator failed"):
            FallbackGenerator(cfg, [Broken(cfg), Broken(cfg)]).generate("t", "facts")


def test_resolve_format():
    assert resolve_format("Quiz") == "quiz"
    assert resolve_format(None, random.Random(0)) in FORMATS
    assert resolve_format("random") in FORMATS
    with pytest.raises(AutoShortsError, match="unknown script format"):
        resolve_format("poem")
