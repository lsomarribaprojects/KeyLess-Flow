"""Strip known Whisper hallucination artifacts from transcriptions.

Whisper (incl. Groq's large-v3-turbo) hallucinates a FINITE, well-known set of
"subtitle-credit" phrases on non-speech / silent / noisy audio: bare credit
URLs ("www.feyyaz.tv"), "Thank you", "Thanks for watching", "Please subscribe",
"Subtitles by the Amara.org community", "Gracias por ver el video", "Suscríbete",
etc. These come from the subtitle corpora Whisper was trained on — the user
never said them.

Real-world reproduction (2026-07-14): a 53s clip with no clear speech produced
ONLY `www.keyLess.com www.feyyaz.tv` (with the vocab prompt biasing the brand
term) or `Thank you. you` (without it).

Strategy — conservative, trailing-anchored so real dictated text survives:
  - Bare credit URLs (www. / http) are stripped only at the END of the text
    (a spoken domain rarely includes "www."; the hallucination always does).
  - Credit PHRASES are stripped only when they are the trailing tail (so
    "gracias por ver el reporte" mid-sentence is untouched — only a trailing
    "gracias por ver el video" is).
  - A couple of pure artifacts (feyyaz / amara.org) are always fake → removed
    anywhere.
If nothing but artifacts remains, the result is "" and the caller shows
"no se detectó voz" instead of pasting garbage.
"""
import re
import unicodedata

# Always-fake tokens: these strings never appear in real user dictation. The
# surrounding \S* eats the whole URL that carries them (e.g. "www.feyyaz.tv",
# "Amara.org") so no "www." stub is left behind.
_ALWAYS_FAKE = re.compile(r"\S*(?:feyyaz|feyyat|amara\.org)\S*", re.IGNORECASE)

# One trailing artifact: a bare credit URL, or a known credit phrase, sitting at
# the very end of the text (optionally followed by punctuation). Peeled in a
# loop so several stacked artifacts all come off.
_TRAILING_ARTIFACT = re.compile(
    r"(?:"
    r"(?:https?://|www\.)\S+"                          # bare credit URL
    r"|thanks?(?:\s+you)?\s+for\s+watching"
    r"|thank\s+you(?:\s*\.?\s+you)*"
    r"|(?:please\s+)?(?:like\s+and\s+)?subscribe\b[^.!?]*"
    r"|subtitles?\s+by\b[^.!?]*"
    r"|subt[ií]tulos\b[^.!?]*"
    r"|gracias\s+por\s+ver(?:\s+el\s+v[ií]deo)?"
    r"|suscr[ií]bete\b[^.!?]*"
    r")\s*[.,!?…]*\s*$",
    re.IGNORECASE,
)


def _is_latin_letter(ch: str) -> bool:
    try:
        return "LATIN" in unicodedata.name(ch)
    except ValueError:
        return False


def _foreign_script_dominant(text: str, threshold: float = 0.5) -> bool:
    """True if >= threshold of the alphabetic chars are NON-Latin script.

    Whisper hallucinates whole phrases in Japanese ("お待ちしております"),
    Chinese ("谢谢观看"), Korean, etc. on non-speech audio. A user dictating
    in Spanish/English never produces that, so a CJK/Cyrillic/etc.-dominant
    result is a hallucination. Accented Latin (á é ñ ü) stays 'LATIN' so real
    Spanish is safe. NOTE: assumes a Latin-script target language (es/en) —
    revisit if the app ever supports dictation in a non-Latin language.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    non_latin = sum(1 for c in letters if not _is_latin_letter(c))
    return non_latin / len(letters) >= threshold


def strip(text: str) -> str:
    """Remove trailing/known Whisper hallucination artifacts. Returns cleaned
    text, or "" if the whole thing was an artifact."""
    if not text:
        return text
    # Whole-output non-Latin script → hallucination (Japanese/Chinese/etc.).
    if _foreign_script_dominant(text):
        return ""
    # Remove always-fake tokens (feyyaz/amara URLs) first so their leftover
    # doesn't block the trailing-phrase peeler.
    out = _ALWAYS_FAKE.sub("", text)
    # Then peel trailing artifacts repeatedly (e.g. two stacked credit URLs).
    prev = None
    while prev != out:
        prev = out
        out = _TRAILING_ARTIFACT.sub("", out).rstrip(" .,!?…-\n\t")
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out
