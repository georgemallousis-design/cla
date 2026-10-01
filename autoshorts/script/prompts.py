"""Prompts that make an LLM return a VideoScript as strict JSON.

The system prompt holds the rules shared by every format plus the format guide;
the user prompt names the topic and repeats the length target, which small local
models otherwise forget.
"""
from __future__ import annotations

from ..config import Config
from .validate import target_words

LANGUAGE_NAMES = {
    "ar": "Arabic", "bg": "Bulgarian", "cs": "Czech", "da": "Danish", "de": "German",
    "el": "Greek", "en": "English", "es": "Spanish", "fi": "Finnish", "fr": "French",
    "he": "Hebrew", "hi": "Hindi", "hu": "Hungarian", "id": "Indonesian", "it": "Italian",
    "ja": "Japanese", "ko": "Korean", "ms": "Malay", "nl": "Dutch", "no": "Norwegian",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sv": "Swedish",
    "th": "Thai", "tl": "Filipino", "tr": "Turkish", "uk": "Ukrainian", "vi": "Vietnamese",
    "zh": "Chinese",
}

JSON_SHAPE = (
    '{"title": "...", "segments": [{"text": "...", "visual_query": "..."}], '
    '"description": "...", "hashtags": ["...", "..."]}'
)

FORMAT_GUIDES = {
    "facts": (
        "FORMAT: FACTS. Give 5-8 surprising facts about the topic, one fact per segment, each "
        "followed by a vivid comparison or a short 'why it matters'. Every fact must be true, "
        "well established and checkable in an encyclopedia; skip myths and 'scientists say' "
        "claims you cannot verify. Build up: save the most mind-blowing fact for near the end."
    ),
    "story": (
        "FORMAT: STORY. Tell a short ORIGINAL fictional story inspired by the topic, clearly framed "
        "as a story: the hook or the second segment must make it obvious it is fiction (for example "
        "'Here is a short story...' or 'Imagine...'), and the description must say it is a short "
        "fictional story. Never claim it happened to you, to the narrator or to a real person, and "
        "do not use real people's names. Arc: hook, setup, rising tension, twist or climax, "
        "resolution with a one-line takeaway. Present tense, concrete sensory details, simple words."
    ),
    "quiz": (
        "FORMAT: QUIZ. Ask 3-4 questions about the topic, each with one clear, verifiable answer. "
        "Put each question in its own segment (you may offer two or three options) and end that "
        "segment with a short pause line such as 'Three, two, one.' The NEXT segment reveals the "
        "answer with one sentence of explanation. Mix easy and tricky questions. Do not invent "
        "statistics about how many people get them right. The final segment asks viewers to "
        "comment their score."
    ),
    "motivation": (
        "FORMAT: MOTIVATION. A warm, direct motivational talk about the topic, spoken to the viewer "
        "as 'you'. One clear idea per segment, practical and concrete, building to an empowering "
        "final line. Write original lines. Only quote a real person if you are certain of the exact "
        "words and who said them; otherwise do not attribute anything. No promises of money, "
        "health or guaranteed success, and no therapy or medical advice."
    ),
    "explainer": (
        "FORMAT: EXPLAINER. Explain how or why the topic works so a curious twelve-year-old gets "
        "it. The hook poses the question, then go step by step, one step per segment, with cause "
        "and effect in plain words and one everyday analogy. Use only well-established science or "
        "history. The final segment delivers the 'aha' answer to the hook's question."
    ),
}


def language_name(code: str) -> str:
    code = (code or "en").strip()
    base = code.split("-")[0].split("_")[0].lower()
    return LANGUAGE_NAMES.get(base, f"the language with code '{code}'")


def word_range(target: int) -> tuple[int, int]:
    """Acceptable spoken-word range: target +-10%."""
    return round(target * 0.9), round(target * 1.1)


def system_prompt(cfg: Config, fmt: str) -> str:
    target = target_words(cfg)
    lo, hi = word_range(target)
    lang = language_name(cfg.script.language)
    guide = FORMAT_GUIDES.get(fmt, FORMAT_GUIDES["facts"])
    return f"""You write voice-over scripts for vertical short videos (TikTok, YouTube Shorts). \
A text-to-speech voice reads the script over stock footage with big word-by-word captions.

Reply with ONLY one valid JSON object, no markdown, no code fences, no comments, exactly this shape:
{JSON_SHAPE}

RULES
- Language: write title, every segment text, description and hashtags in {lang}. visual_query is always English.
- Length: all segment texts together must contain {lo}-{hi} spoken words (target {target} words, \
about {cfg.video.target_seconds} seconds of narration). Count them.
- Use 6-10 segments. Each segment is 1-3 short sentences and one beat of the video; the background footage changes with every segment.
- segments[0] is the HOOK: at most 12 words, a scroll-stopping line that opens a curiosity gap. \
Never start with "In this video", "Hey guys", "Welcome back" or similar.
- The LAST segment is a short payoff that closes the loop opened by the hook, then a soft call to action such as "Follow for more."
- Write for the ear: short sentences, everyday words, no emojis, no hashtags, no stage directions, no lists or markdown. \
Avoid symbols and abbreviations a voice could misread (write "percent", "kilometers").
- visual_query: 1-4 concrete, filmable English nouns or scenes for a stock-footage search, e.g. "octopus underwater", \
"city skyline night", "hourglass sand". No names of real people, no brands or logos, no on-screen text.
- Accuracy: only state facts that are true and widely accepted. If unsure of a fact, leave it out. \
Nothing political, hateful, sexual or controversial, and no conspiracy theories.
- No medical, legal or financial advice.
- title: catchy, under 70 characters (never more than 90), no hashtags, no emojis.
- description: 1-2 sentences for the video description, no hashtags.
- hashtags: 3-8 relevant single words, lowercase, without the '#'.

{guide}"""


def user_prompt(cfg: Config, topic: str, fmt: str) -> str:
    target = target_words(cfg)
    lo, hi = word_range(target)
    topic = " ".join((topic or "").split())[:200].replace('"', "'")
    subject = f'Topic: "{topic}"' if topic else "Topic: choose a fascinating, evergreen topic that suits this format."
    return (
        f"{subject}\nFormat: {fmt}\n"
        f"Write the script now: {lo}-{hi} spoken words in 6-10 segments, a hook of at most 12 words, "
        f"and reply with the JSON object only."
    )


def build_messages(cfg: Config, topic: str, fmt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt(cfg, fmt)},
        {"role": "user", "content": user_prompt(cfg, topic, fmt)},
    ]


def repair_prompt(cfg: Config, error: str) -> str:
    """Follow-up message after an unusable reply; ``error`` explains what was wrong."""
    lo, hi = word_range(target_words(cfg))
    return (
        f"That reply could not be used: {error}\n"
        f"Reply again with ONLY the corrected JSON object in this shape: {JSON_SHAPE}. "
        f"Keep {lo}-{hi} spoken words across 6-10 segments."
    )
