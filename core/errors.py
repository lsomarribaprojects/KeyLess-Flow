"""Classify transcription failures into user-facing kinds.

The old error path showed the raw exception ("Falló la transcripción:
APIConnectionError…"). Users don't know what that means or what to do. Now
every failure maps to a KIND with a plain-Spanish message and a retryable
flag the transcriber router uses for backoff.

Kinds:
  offline        no network / DNS / socket / timeout  → retry, keep audio
  rate_limited   HTTP 429 (Groq free-tier throttling)   → retry, keep audio
  provider_down  HTTP 5xx / backend 5xx                 → retry, keep audio
  auth           401 / bad key / expired Pro session    → NOT retryable
  quota          402 / plan exhausted                   → NOT retryable
  too_large      413                                    → NOT retryable
  unknown        anything else                          → NOT retryable
"""
from __future__ import annotations

KIND_OFFLINE = "offline"
KIND_RATE = "rate_limited"
KIND_PROVIDER = "provider_down"
KIND_AUTH = "auth"
KIND_QUOTA = "quota"
KIND_SIZE = "too_large"
KIND_UNKNOWN = "unknown"

RETRYABLE = {KIND_OFFLINE, KIND_RATE, KIND_PROVIDER}

_MESSAGES = {
    KIND_OFFLINE: (
        "Sin conexión a internet. Tu audio quedó guardado — "
        "clic en esta notificación para reintentar, o desde el Hub → Historial."
    ),
    KIND_RATE: (
        "Groq está limitando las peticiones (límite del nivel gratuito). "
        "Espera un minuto y reintenta — tu audio quedó guardado."
    ),
    KIND_PROVIDER: (
        "El servicio de transcripción está caído temporalmente. "
        "Tu audio quedó guardado — reintenta en unos minutos."
    ),
    KIND_AUTH: "Tu API key o sesión no es válida. Revísala en el Hub → Ajustes.",
    KIND_QUOTA: "Se agotó la cuota de tu plan. Mejora el plan o espera al próximo ciclo.",
    KIND_SIZE: "El audio es demasiado grande para una sola petición.",
}


def _status_of(exc: BaseException):
    for attr in ("status_code", "status", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def classify(exc: BaseException) -> tuple[str, str, bool]:
    """Returns (kind, friendly_message, retryable)."""
    name = type(exc).__name__
    msg = (str(exc) or "").lower()
    status = _status_of(exc)

    def _is(kind: str):
        text = _MESSAGES.get(kind) or f"Falló la transcripción: {str(exc) or name}"
        return kind, text, kind in RETRYABLE

    # Fail-fast kinds first — a 401 must never be retried 4 times.
    if name == "AuthenticationError" or status == 401 or " 401" in msg \
            or "invalid api key" in msg or "sesión pro expiró" in msg or "no hay sesión pro" in msg:
        return _is(KIND_AUTH)
    if status == 402 or " 402" in msg or "suscripción no está activa" in msg \
            or "quota" in msg or "trial" in msg:
        return _is(KIND_QUOTA)
    if status == 413 or " 413" in msg or "too large" in msg or "file_too_large" in msg:
        return _is(KIND_SIZE)
    if name == "RateLimitError" or status == 429 or " 429" in msg or "rate limit" in msg \
            or "rate_limit" in msg:
        return _is(KIND_RATE)
    if (status is not None and status >= 500) or name in ("InternalServerError",) \
            or "backend error 5" in msg or "groq_5" in msg or "bad gateway" in msg \
            or "service unavailable" in msg:
        return _is(KIND_PROVIDER)
    if name in ("APIConnectionError", "APITimeoutError", "ConnectionError", "TimeoutError",
                "URLError", "RemoteDisconnected", "ConnectionResetError") \
            or "network error" in msg or "timed out" in msg or "getaddrinfo" in msg \
            or "connection" in msg or "unreachable" in msg or "name resolution" in msg \
            or (isinstance(exc, OSError) and not isinstance(exc, PermissionError)):
        return _is(KIND_OFFLINE)
    return _is(KIND_UNKNOWN)
