"""Pro-tier authentication state for the desktop app.

The app has two operational modes:

  BYOK (Bring Your Own Key)  — default for Free users.
      Reads GROQ_API_KEY from %APPDATA%\\KeyLessFlow\\.env. Calls Groq
      directly. Zero coupling to our backend; works offline-ish.

  PRO                        — for paying users.
      Reads a long-lived HMAC token from auth.json (next to .env). All
      transcription requests are POSTed to KEYLESSFLOW_API_URL/api/transcribe
      with the token as a Bearer. Our backend proxies to Groq with our own
      master key, verifies the subscription on every call, and meters usage.

This module hides the storage details. Callers ask:
    `is_pro()` / `get_pro_token()` / `activate(code)` / `sign_out()`
and never touch the JSON directly.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional, TypedDict

from config import APP_DATA_DIR, KEYLESSFLOW_API_URL

_AUTH_PATH = os.path.join(APP_DATA_DIR, "auth.json")

# ---------------------------------------------------------------------------
# At-rest protection for the Pro token.
#
# auth.json used to store the kfd_ token in plaintext — any process running as
# the user could steal it and burn the account's quota. On Windows we now wrap
# it with DPAPI (CryptProtectData): ciphertext only decrypts for THIS Windows
# user on THIS machine. Legacy plaintext files are migrated transparently on
# first read. Non-Windows keeps plaintext (macOS Keychain is a future step).
# ---------------------------------------------------------------------------
_DPAPI_PREFIX = "dpapi:"


def _protect(secret: str) -> str:
    if sys.platform != "win32" or not secret:
        return secret
    try:
        import win32crypt
        blob = win32crypt.CryptProtectData(
            secret.encode("utf-8"), "KeyLessFlow", None, None, None, 0,
        )
        return _DPAPI_PREFIX + base64.b64encode(blob).decode("ascii")
    except Exception:
        return secret  # never brick auth over an encryption failure


def _unprotect(stored: str) -> Optional[str]:
    """Returns the plaintext token, or None if ciphertext can't be decrypted
    (foreign machine/user — treat as signed-out)."""
    if not stored.startswith(_DPAPI_PREFIX):
        return stored  # legacy plaintext
    try:
        import win32crypt
        raw = base64.b64decode(stored[len(_DPAPI_PREFIX):])
        return win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1].decode("utf-8")
    except Exception:
        return None


class AuthRecord(TypedDict, total=False):
    token: str           # the kfd_... HMAC token returned by /api/auth/activate
    email: str
    plan: str            # "pro" | "team"
    expires_at: str      # ISO-8601, informational only — backend re-validates


def _read() -> Optional[AuthRecord]:
    try:
        with open(_AUTH_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not (isinstance(data, dict) and isinstance(data.get("token"), str)):
            return None
        stored = data["token"]
        token = _unprotect(stored)
        if not token:
            return None  # undecryptable ciphertext → signed out
        # Migration: legacy plaintext on Windows → rewrite encrypted.
        if sys.platform == "win32" and not stored.startswith(_DPAPI_PREFIX):
            try:
                _write({**data, "token": token})  # _write re-encrypts
            except Exception:
                pass
        data["token"] = token
        return data  # type: ignore[return-value]
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _write(record: AuthRecord) -> None:
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    on_disk = dict(record)
    if on_disk.get("token"):
        on_disk["token"] = _protect(on_disk["token"])
    tmp = _AUTH_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(on_disk, f, indent=2)
    os.replace(tmp, _AUTH_PATH)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def is_pro() -> bool:
    """Quick check used by the transcriber router to pick backend vs Groq direct."""
    rec = _read()
    return bool(rec and rec.get("token"))


def get_pro_token() -> Optional[str]:
    rec = _read()
    return rec.get("token") if rec else None


def get_account_summary() -> Optional[AuthRecord]:
    """For UI: shows logged-in email + plan in the tray menu."""
    return _read()


def sign_out() -> None:
    """Wipe local credentials. App falls back to BYOK on next launch."""
    try:
        os.remove(_AUTH_PATH)
    except FileNotFoundError:
        pass


class ActivationResult(TypedDict, total=False):
    ok: bool
    error: str
    upgrade_url: str
    record: AuthRecord


def activate(code: str) -> ActivationResult:
    """Exchange an activation code (KF-XXXX-XXXX-XXXX) for a long-lived token.

    Network call to the backend. On success the token is persisted to
    auth.json — subsequent launches start in Pro mode automatically.

    Returns a dict; UI inspects `ok` and shows `error` / opens `upgrade_url`.
    """
    code = (code or "").strip().upper()
    if not code:
        return {"ok": False, "error": "Pega tu código de activación."}

    url = f"{KEYLESSFLOW_API_URL.rstrip('/')}/api/auth/activate"
    payload = json.dumps({"code": code}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "KeyLessFlow/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except Exception:
            err_body = {}
        err_code = err_body.get("error", "")
        return {
            "ok": False,
            "error": _humanize_error(err_code, e.code),
            "upgrade_url": err_body.get("upgrade_url", ""),
        }
    except (urllib.error.URLError, TimeoutError) as e:
        return {
            "ok": False,
            "error": f"No se pudo contactar al servidor ({e}). Revisa tu conexión.",
        }
    except Exception as e:
        return {"ok": False, "error": f"Error inesperado: {e}"}

    if not isinstance(body, dict) or "token" not in body:
        return {"ok": False, "error": "Respuesta inválida del servidor."}

    record: AuthRecord = {
        "token": body["token"],
        "email": body.get("email", ""),
        "plan": body.get("plan", ""),
        "expires_at": body.get("expires_at", ""),
    }
    _write(record)
    return {"ok": True, "record": record}


def _humanize_error(code: str, http_status: int) -> str:
    mapping = {
        "bad_code_format": (
            "El código no tiene el formato correcto. Debe ser KF-XXXX-XXXX-XXXX."
        ),
        "code_not_found": (
            "Código no encontrado. Revisa que sea exactamente el que aparece en tu cuenta."
        ),
        "subscription_required": (
            "Tu cuenta no tiene suscripción activa. Suscríbete primero en el sitio web."
        ),
        "trial_expired": (
            "Tu trial gratuito terminó. Suscríbete a Pro para seguir dictando."
        ),
        "auth_lookup_failed": (
            "El servidor no pudo verificar tu cuenta. Intenta de nuevo en unos minutos."
        ),
    }
    if code in mapping:
        return mapping[code]
    return f"Error del servidor ({http_status}): {code or 'desconocido'}"
