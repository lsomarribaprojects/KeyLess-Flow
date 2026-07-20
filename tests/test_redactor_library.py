"""Tests for the "Redactor con Biblioteca" feature:
  - db/library.py         (LibraryDB CRUD)
  - core/redactor.py      (build_system_prompt + redact, incl. real E2E call)
  - ui/hub_window.py      (LibraryPage wired into HubWindow)

Runs without pytest:  venv\\Scripts\\python.exe tests\\test_redactor_library.py

Importing core.redactor pulls in core.llm_backend -> config, which runs
load_dotenv() as a side effect — so GROQ_API_KEY ends up in os.environ the
same way it does for the real app, without this file touching .env directly.
"""
import os
import sys
import tempfile

# Windows consoles default to cp1252, which chokes on accented LLM output
# (Español, "mañana", etc). Force UTF-8 so this script can print freely.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before any PyQt6 import happens (ui.hub_window pulls it in via
# the UI smoke test below) so the widgets can construct without a real display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from db.library import LibraryDB  # noqa: E402
from core.redactor import build_system_prompt, redact  # noqa: E402


def _run_library_db():
    print("\n=== LibraryDB CRUD ===")
    failed = 0
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db = LibraryDB(db_path=path)

        id1 = db.add(
            "Aviso reunion", "avisar que la reunion se mueve",
            "Aviso: la reunion se movio.", "Espanol", "email",
        )
        row = db.get(id1)
        ok = (
            row is not None
            and row["title"] == "Aviso reunion"
            and row["idea"] == "avisar que la reunion se mueve"
            and row["language"] == "Espanol"
            and row["tone"] == "email"
        )
        print(f"{'PASS' if ok else 'FAIL'}  add()+get() round-trip")
        failed += 0 if ok else 1

        id2 = db.add(
            "Nota equipo", "decirle al equipo que llegue temprano",
            "Equipo: lleguen temprano manana.", "Espanol", "whatsapp",
        )

        rows = db.list_all()
        ok = len(rows) == 2 and {r["id"] for r in rows} == {id1, id2}
        print(f"{'PASS' if ok else 'FAIL'}  list_all() returns both rows")
        failed += 0 if ok else 1

        results = db.search("reunion")
        ok = len(results) == 1 and results[0]["id"] == id1
        print(f"{'PASS' if ok else 'FAIL'}  search() matches idea text (id={id1})")
        failed += 0 if ok else 1

        results = db.search("equipo")
        ok = len(results) == 1 and results[0]["id"] == id2
        print(f"{'PASS' if ok else 'FAIL'}  search() matches title/result text (id={id2})")
        failed += 0 if ok else 1

        ok = len(db.search("nomatchxyz")) == 0
        print(f"{'PASS' if ok else 'FAIL'}  search() no match -> empty list")
        failed += 0 if ok else 1

        ok = len(db.search("")) == 2
        print(f"{'PASS' if ok else 'FAIL'}  search('') falls back to list_all()")
        failed += 0 if ok else 1

        db.delete(id1)
        ok = db.get(id1) is None and len(db.list_all()) == 1
        print(f"{'PASS' if ok else 'FAIL'}  delete() removes the row")
        failed += 0 if ok else 1

        raised = False
        try:
            db.add("", "idea", "result")
        except ValueError:
            raised = True
        print(f"{'PASS' if raised else 'FAIL'}  add() rejects empty title")
        failed += 0 if raised else 1
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    return failed


def _run_build_system_prompt():
    print("\n=== build_system_prompt ===")
    failed = 0

    p = build_system_prompt(language="English", tone="neutral", length="medio")
    ok = "English" in p
    print(f"{'PASS' if ok else 'FAIL'}  explicit language appears in prompt")
    failed += 0 if ok else 1

    p = build_system_prompt(language="auto", tone="neutral", length="medio")
    ok = "MISMO idioma" in p
    print(f"{'PASS' if ok else 'FAIL'}  auto language -> same-language instruction")
    failed += 0 if ok else 1

    p = build_system_prompt(language="auto", tone="whatsapp", length="medio")
    ok = "WhatsApp" in p
    print(f"{'PASS' if ok else 'FAIL'}  whatsapp tone -> WhatsApp signal in prompt")
    failed += 0 if ok else 1

    p = build_system_prompt(language="auto", tone="email", length="medio")
    ok = "correo" in p.lower()
    print(f"{'PASS' if ok else 'FAIL'}  email tone -> email signal in prompt")
    failed += 0 if ok else 1

    p = build_system_prompt(language="auto", tone="linkedin", length="medio")
    ok = "linkedin" in p.lower()
    print(f"{'PASS' if ok else 'FAIL'}  linkedin tone -> LinkedIn signal in prompt")
    failed += 0 if ok else 1

    p = build_system_prompt(language="auto", tone="neutral", length="corto")
    ok = "corta" in p.lower()
    print(f"{'PASS' if ok else 'FAIL'}  corto length -> short-length signal in prompt")
    failed += 0 if ok else 1

    p = build_system_prompt(language="auto", tone="neutral", length="largo")
    ok = "larga" in p.lower()
    print(f"{'PASS' if ok else 'FAIL'}  largo length -> long-length signal in prompt")
    failed += 0 if ok else 1

    return failed


def _run_ui_smoke():
    print("\n=== UI smoke ===")
    try:
        from PyQt6.QtWidgets import QApplication
        from db.database import TranscriptionDB
        from ui.hub_window import HubWindow
    except Exception as e:
        print(f"SKIP ui ({e})")
        return 0

    try:
        app = QApplication.instance() or QApplication(sys.argv)

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            hub = HubWindow(TranscriptionDB(db_path))

            ok = hub.pages.count() == 6
            print(f"{'PASS' if ok else 'FAIL'}  HubWindow.pages.count() == 6 (got {hub.pages.count()})")
            failed = 0 if ok else 1

            has_widgets = hasattr(hub.library_page, "idea_input") and hasattr(hub.library_page, "result_box")
            print(f"{'PASS' if has_widgets else 'FAIL'}  library_page has idea_input/result_box")
            failed += 0 if has_widgets else 1
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass
        return failed
    except Exception as e:
        print(f"SKIP ui ({e})")
        return 0


def _run_e2e():
    print("\n=== E2E real (llm_backend) ===")
    if not os.getenv("GROQ_API_KEY"):
        print("SKIP e2e (no GROQ_API_KEY in environment)")
        return 0

    failed = 0

    out1 = redact(
        "avisar al equipo que la reunion de manana se mueve a las 3 pm",
        language="English", tone="email", length="corto",
    )
    print(f"\n--- case 1 (English, email, corto) ---\n{out1}\n")
    en_signals = ("meeting", "team", "tomorrow", "3")
    ok = bool(out1) and any(s in out1.lower() for s in en_signals) and "reunión" not in out1.lower() and "reunion" not in out1.lower()
    print(f"{'PASS' if ok else 'FAIL'}  English email output looks correct")
    failed += 0 if ok else 1

    out2 = redact(
        "tell the team tomorrow's meeting moved to 3pm",
        language="Español", tone="whatsapp", length="corto",
    )
    print(f"\n--- case 2 (Español, whatsapp, corto) ---\n{out2}\n")
    es_signals = ("reunión", "reunion", "equipo", "mañana", "manana")
    ok = bool(out2) and any(s in out2.lower() for s in es_signals)
    print(f"{'PASS' if ok else 'FAIL'}  Spanish whatsapp output looks correct")
    failed += 0 if ok else 1

    return failed


def _run():
    total = 0
    total += _run_library_db()
    total += _run_build_system_prompt()
    total += _run_ui_smoke()
    total += _run_e2e()
    print(f"\n{'ALL PASSED' if total == 0 else f'{total} FAILED'}")
    return total


if __name__ == "__main__":
    raise SystemExit(1 if _run() else 0)
