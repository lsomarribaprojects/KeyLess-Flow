"""BYOK (Bring Your Own Key) onboarding — used by the workshop flow.

A user pastes their own Groq API key (free at console.groq.com/keys); we
validate it against Groq with a zero-cost call, persist it to the app's .env,
and flip the backend to "byok". No account, no signup, no quota on our side —
their key, their limits. Costs us $0 per workshop attendee.
"""
import json
import os
import urllib.error
import urllib.request

from config import APP_DATA_DIR, set_setting

_MODELS_URL = "https://api.groq.com/openai/v1/models"


def validate_groq_key(key: str, timeout: float = 12.0) -> tuple[bool, str]:
    """Check the key against Groq with a free metadata call (GET /models).

    Returns (ok, message). Never raises: network problems come back as
    (False, reason) so the dialog can show something actionable.
    """
    key = (key or "").strip()
    if not key.startswith("gsk_") or len(key) < 20:
        return False, "El formato no parece una Groq API key (debe empezar con gsk_)."
    req = urllib.request.Request(
        _MODELS_URL,
        headers={"Authorization": f"Bearer {key}", "User-Agent": "KeyLessFlow/1.2"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        n = len(data.get("data") or [])
        return True, f"Key válida ({n} modelos disponibles)."
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "Groq rechazó la key (inválida o revocada). Revisa que la copiaste completa."
        return False, f"Groq respondió {e.code} — intenta de nuevo en un momento."
    except Exception as e:
        return False, f"No se pudo contactar a Groq: {e}"


def save_groq_key(key: str, data_dir: str = None) -> str:
    """Persist GROQ_API_KEY into <data_dir>/.env (upsert, preserving other
    lines), activate it for the CURRENT process, and flip the backend setting
    to byok. Returns the .env path."""
    key = (key or "").strip()
    d = data_dir or APP_DATA_DIR
    os.makedirs(d, exist_ok=True)
    env_path = os.path.join(d, ".env")

    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    lines = [ln for ln in lines if not ln.strip().startswith("GROQ_API_KEY")]
    lines.append(f"GROQ_API_KEY={key}")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # Live activation — transcriber_groq/llm_backend read os.getenv at call
    # time, and config.GROQ_API_KEY is refreshed for anything that imported it.
    os.environ["GROQ_API_KEY"] = key
    try:
        import config
        config.GROQ_API_KEY = key
    except Exception:
        pass
    if data_dir is None:  # skip settings mutation in tests
        set_setting("transcribe_backend", "byok")
    return env_path
