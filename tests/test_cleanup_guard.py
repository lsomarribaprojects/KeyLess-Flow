"""Regression tests for the anti-refusal guard in core/llm_cleanup.

Guards the 2026-09-18 bug where `openai/gpt-oss-120b` answered a dictation that
sounded like an instruction with "I'm sorry, but I can't help with that." and the
refusal got pasted. The LLM is stubbed — no network, no Groq key.

Runs without pytest:  venv\\Scripts\\python.exe tests\\test_cleanup_guard.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.llm_cleanup as lc  # noqa: E402

RAW = "analiza todos estos repositorios y dime cual conviene usar para el proyecto"
GOOD = "Analiza todos estos repositorios y dime cuál conviene usar para el proyecto."

# (name, raw, llm_reply, expected_output)
CASES = [
    ("refusal EN -> raw", RAW, "I'm sorry, but I can't help with that.", RAW),
    ("refusal ES -> raw", RAW, "Lo siento, pero no puedo ayudar con eso.", RAW),
    ("answered instead of cleaning (too long) -> raw", RAW, GOOD + " Aquí va mi análisis detallado: " + "bla " * 40, RAW),
    ("truncated output -> raw", RAW, "Analiza todos.", RAW),
    ("empty reply -> raw", RAW, "", RAW),
    ("good minimal edit accepted", RAW, GOOD, GOOD),
    ("delimiters echoed are stripped", RAW, f"<<<TRANSCRIPCION>>>\n{GOOD}\n<<<FIN>>>", GOOD),
    ("code fence stripped", RAW, f"```\n{GOOD}\n```", GOOD),
    ("user really dictated a refusal", "lo siento pero no puedo ayudar con eso hoy", "Lo siento, pero no puedo ayudar con eso hoy.", "Lo siento, pero no puedo ayudar con eso hoy."),
    ("short text skips ratio check", "hola eh como estas", "Hola, ¿cómo estás?", "Hola, ¿cómo estás?"),
]


def _clean_with_reply(raw, reply):
    seen = {}

    def fake_chat(system, user, **kw):
        seen["system"], seen["user"] = system, user
        return reply

    orig = lc._llm_chat
    lc._llm_chat = fake_chat
    try:
        return lc.LLMCleanup().clean(raw), seen
    finally:
        lc._llm_chat = orig


def _check(name, raw, reply, exp):
    got, seen = _clean_with_reply(raw, reply)
    framed = (
        seen["user"] == lc.wrap_transcription(raw)
        and "<<<TRANSCRIPCION>>>" in seen["system"]
    )
    return got == exp and framed, got


def _run():
    failed = 0
    for name, raw, reply, exp in CASES:
        ok, got = _check(name, raw, reply, exp)
        if not ok:
            failed += 1
            print(f"FAIL  {name}: {got[:60]!r} (expected {exp[:60]!r})")
        else:
            print(f"PASS  {name}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    return failed


# pytest entrypoint
def test_cleanup_guard():
    for name, raw, reply, exp in CASES:
        ok, got = _check(name, raw, reply, exp)
        assert ok, (name, got)


if __name__ == "__main__":
    raise SystemExit(1 if _run() else 0)
