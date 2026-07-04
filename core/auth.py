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

import json
import os
import urllib.error
import urllib.request
from typing import Optional, TypedDict

from config import APP_DATA_DIR, KEYLESSFLOW_API_URL

_AUTH_PATH = os.path.join(APP_DATA_DIR, "auth.json")


class AuthRecord(TypedDict, total=False):
    token: str           # the kfd_... HMAC token returned by /api/auth/activate
    email: str
    plan: str            # "pro" | "team"
    expires_at: str      # ISO-8601, informational only — backend re-validates


def _read() -> Optional[AuthRecord]:
    try:
        with open(_AUTH_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("token"), str):
            return data  # type: ignore[return-value]
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _write(record: AuthRecord) -> None:
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    tmp = _AUTH_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
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
