"""Tests for the BYOK (workshop) onboarding + the Ajustes help section.

Runs without pytest:  venv\\Scripts\\python.exe tests\\test_byok_and_help.py

The valid-key test uses the real GROQ_API_KEY from the local .env (owner
machine) — it makes one free metadata call to Groq. Skipped if absent.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_bad_format_rejected_offline():
    from core.byok import validate_groq_key
    ok, msg = validate_groq_key("not-a-key")
    assert not ok and "formato" in msg.lower()
    ok, _ = validate_groq_key("")
    assert not ok


def test_invalid_key_rejected_by_groq():
    from core.byok import validate_groq_key
    ok, msg = validate_groq_key("gsk_" + "x" * 48)
    assert not ok, f"key falsa aceptada?: {msg}"


def test_real_key_accepted():
    from core.byok import validate_groq_key
    real = os.getenv("GROQ_API_KEY", "")
    if not real:
        print("  (skip: no GROQ_API_KEY local)")
        return
    ok, msg = validate_groq_key(real)
    assert ok, f"key real rechazada: {msg}"


def test_save_groq_key_upserts_env():
    from core.byok import save_groq_key
    with tempfile.TemporaryDirectory() as d:
        # Existing .env: other vars preserved, old key line replaced
        with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
            f.write("OTHER_VAR=hello\nGROQ_API_KEY=gsk_old\n")
        prev = os.environ.get("GROQ_API_KEY")
        try:
            path = save_groq_key("gsk_newkey123456789012345", data_dir=d)
            raw = open(path, encoding="utf-8").read()
            assert "OTHER_VAR=hello" in raw, "borro otras vars del .env"
            assert "gsk_old" not in raw, "no reemplazo la key vieja"
            assert "GROQ_API_KEY=gsk_newkey123456789012345" in raw
            assert os.environ["GROQ_API_KEY"] == "gsk_newkey123456789012345"
        finally:
            if prev is not None:
                os.environ["GROQ_API_KEY"] = prev


def test_help_dialog_and_settings_button():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.hub_window import HelpDialog, SettingsPage, HELP_HTML
    # The reference must document every command family we ship.
    for needle in ("Ctrl + Alt", "Ctrl + Shift", "Triple-tap", "nueva línea",
                   "dale enter", "Alt+1", "Redactor", "Snippets"):
        assert needle in HELP_HTML, f"ayuda no documenta: {needle}"
    dlg = HelpDialog()
    assert dlg.windowTitle().startswith("Cómo usar")
    page = SettingsPage()
    assert hasattr(page, "help_btn") and page.help_btn.text() == "Ver comandos y atajos"


def test_firstrun_dialog_has_byok_path():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from main import FirstRunDialog
    dlg = FirstRunDialog()
    assert hasattr(dlg, "byok_input"), "FirstRunDialog sin input BYOK"
    assert dlg.byok_input.placeholderText().startswith("gsk_")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
