"""Personal vocabulary dictionary. Fed to Whisper as `prompt=` hint.

Whisper uses the prompt as vocabulary bias — boosts recognition of proper
nouns, jargon, acronyms, etc. The `prompt` is NOT prepended to the output.

File format: plain text, one term or phrase per line, # comments allowed.
Max ~224 tokens is Whisper's hard limit — keep concise.
"""
import os
from config import DICTIONARY_PATH


_DEFAULT_SEED = """# KeyLess by Sinsajo Personal Dictionary
# One word, name, or phrase per line. Used as Whisper vocabulary hint.
# Example entries below — edit to taste. Lines starting with # are ignored.

KeyLess by Sinsajo
Sinsajo Creators
Groq
Whisper
Parakeet
"""


def _ensure_file():
    if not os.path.exists(DICTIONARY_PATH):
        os.makedirs(os.path.dirname(DICTIONARY_PATH), exist_ok=True)
        with open(DICTIONARY_PATH, "w") as f:
            f.write(_DEFAULT_SEED)


def load_terms() -> list[str]:
    _ensure_file()
    terms = []
    with open(DICTIONARY_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return terms


def as_whisper_prompt(max_chars: int = 800) -> str:
    """Pack terms into a comma-separated hint, truncated to avoid token cap."""
    terms = load_terms()
    if not terms:
        return ""
    joined = ", ".join(terms)
    if len(joined) > max_chars:
        joined = joined[:max_chars].rsplit(",", 1)[0]
    return joined
