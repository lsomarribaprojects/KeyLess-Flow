"""Regression tests for the 2026-07 security hardening pass.

Covers:
  1. Local dashboard auth — token/cookie gate + Host check (DNS rebinding).
  2. DPAPI at-rest encryption of the Pro token (Windows) + legacy migration.
  3. Updater SHA256 helper.
  4. Trailing "dale enter" extraction (dictation_actions wiring).

Runs without pytest:  venv\\Scripts\\python.exe tests\\test_security_hardening.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ------------------------------------------------------------------ dashboard
def test_dashboard_requires_token():
    from web import server
    c = server.app.test_client()
    assert c.get("/").status_code == 403
    assert c.get("/api/transcriptions").status_code == 403


def test_dashboard_bad_token_rejected():
    from web import server
    c = server.app.test_client()
    assert c.get("/?token=WRONG").status_code == 403


def test_dashboard_token_exchanges_for_cookie():
    from web import server
    c = server.app.test_client()
    r = c.get(f"/?token={server._ACCESS_TOKEN}")
    assert r.status_code == 302  # redirect strips token from URL
    assert server._COOKIE_NAME in r.headers.get("Set-Cookie", "")
    # cookie persists in test client → subsequent requests pass
    assert c.get("/").status_code == 200
    assert c.get("/api/transcriptions").status_code == 200


def test_dashboard_foreign_host_rejected():
    """DNS rebinding: evil.com resolving to 127.0.0.1 sends Host: evil.com."""
    from web import server
    c = server.app.test_client()
    r = c.get(f"/?token={server._ACCESS_TOKEN}", base_url="http://evil.com")
    assert r.status_code == 403


# ---------------------------------------------------------------------- dpapi
def test_token_encrypted_at_rest_and_roundtrips():
    if sys.platform != "win32":
        return  # DPAPI is Windows-only; passthrough elsewhere
    from core import auth
    orig_path = auth._AUTH_PATH
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(tmp)
    auth._AUTH_PATH = tmp
    try:
        auth._write({"token": "kfd_SECRET123", "email": "x@y.z", "plan": "pro"})
        raw = open(tmp, encoding="utf-8").read()
        assert "kfd_SECRET123" not in raw, "token stored in PLAINTEXT"
        assert "dpapi:" in raw
        rec = auth._read()
        assert rec and rec["token"] == "kfd_SECRET123", "roundtrip failed"
    finally:
        auth._AUTH_PATH = orig_path
        try:
            os.remove(tmp)
        except OSError:
            pass


def test_legacy_plaintext_migrates_on_read():
    if sys.platform != "win32":
        return
    import json
    from core import auth
    orig_path = auth._AUTH_PATH
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    auth._AUTH_PATH = tmp
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"token": "kfd_LEGACY", "plan": "pro"}, f)
        rec = auth._read()
        assert rec and rec["token"] == "kfd_LEGACY"
        raw = open(tmp, encoding="utf-8").read()
        assert "kfd_LEGACY" not in raw, "legacy token was not re-encrypted"
    finally:
        auth._AUTH_PATH = orig_path
        try:
            os.remove(tmp)
        except OSError:
            pass


# --------------------------------------------------------------------- sha256
def test_sha256_file_matches_known_vector():
    from core.updater import _sha256_file
    fd, tmp = tempfile.mkstemp()
    os.write(fd, b"abc")
    os.close(fd)
    try:
        assert _sha256_file(tmp) == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )
    finally:
        os.remove(tmp)


# ----------------------------------------------------------- dictation actions
def test_dale_enter_extracted():
    from core.dictation_actions import extract_actions, ACTION_PRESS_ENTER
    text, actions = extract_actions("manda el reporte dale enter")
    assert text == "manda el reporte" and actions == [ACTION_PRESS_ENTER]
    text, actions = extract_actions("send it now press enter")
    assert text == "send it now" and actions == [ACTION_PRESS_ENTER]


def test_mid_sentence_enter_not_extracted():
    from core.dictation_actions import extract_actions
    text, actions = extract_actions("dale enter cuando quieras y avisame")
    assert actions == [] and text == "dale enter cuando quieras y avisame"


def test_no_action_passthrough():
    from core.dictation_actions import extract_actions
    text, actions = extract_actions("hola como estas")
    assert text == "hola como estas" and actions == []


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
