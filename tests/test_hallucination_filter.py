"""Regression tests for the Whisper hallucination filter (core/hallucination_filter).

Guards the 2026-07-14 bug where non-speech audio produced pasted garbage like
"www.keyLess.com www.feyyaz.tv" / "Thank you. you". The filter must kill those
while leaving legitimate dictation — including real trailing words and
mid-sentence domains — untouched.

Runs without pytest:  venv\\Scripts\\python.exe tests\\test_hallucination_filter.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.hallucination_filter import strip  # noqa: E402

# (input, expected) — hallucinations become "", legit text is unchanged.
CASES = [
    ("www.keyLess.com www.feyyaz.tv", ""),
    ("Thank you. you", ""),
    ("www.feyyat.com", ""),
    ("Gracias por ver el video", ""),
    ("Subtitles by the Amara.org community", ""),
    # non-Latin script hallucinations (Japanese / Chinese / Korean)
    ("お待ちしております", ""),  # お待ちしております
    ("谢谢观看", ""),  # 谢谢观看 = thanks for watching
    ("안녕하세요", ""),  # 안녕하세요
    # accented Spanish must survive (still Latin script)
    ("El niño comió pigüe en Ñuñoa", "El niño comió pigüe en Ñuñoa"),
    (
        "quiero un filtro que permita filtrar la tabla www.keyLessFlow.com www.feyyaz.tv",
        "quiero un filtro que permita filtrar la tabla",
    ),
    # legit text — must stay identical (no false positives)
    ("Gracias por ver el reporte del mes y avisame", "Gracias por ver el reporte del mes y avisame"),
    ("manda el link a keylessflow.com", "manda el link a keylessflow.com"),
    ("el reporte ya quedo listo, gracias", "el reporte ya quedo listo, gracias"),
    ("necesito que te suscribas al plan Pro", "necesito que te suscribas al plan Pro"),
]


def _run():
    failed = 0
    for inp, exp in CASES:
        got = strip(inp)
        ok = got == exp
        if not ok:
            failed += 1
            print(f"FAIL  {inp[:45]!r} -> {got!r} (expected {exp!r})")
        else:
            print(f"PASS  {inp[:45]!r} -> {got!r}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    return failed


# pytest entrypoints
def test_hallucinations_removed():
    for inp, exp in CASES:
        if exp == "":
            assert strip(inp) == "", (inp, strip(inp))


def test_legit_text_untouched():
    for inp, exp in CASES:
        if exp:
            assert strip(inp) == exp, (inp, strip(inp))


if __name__ == "__main__":
    raise SystemExit(1 if _run() else 0)
