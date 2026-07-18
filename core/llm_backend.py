"""Router for LLM chat calls (transcription cleanup + Option+N transforms).

Two paths, mirroring the transcriber router:
  - BYOK / owner  → a local GROQ_API_KEY exists → call Groq chat directly.
  - Managed / Pro → no local key → POST to the KeyLess backend /api/llm proxy,
                    authenticated with the Pro desktop token.

This is THE fix for "managed Pro users got raw transcription": before, cleanup
and transforms instantiated Groq directly with an empty key and silently
returned the text unchanged. Now managed users route through the backend so
cleanup + tone + transforms actually run.
"""
import os
import json
import urllib.error
import urllib.request

from config import KEYLESSFLOW_API_URL, LLM_CLEANUP_MODEL


class LLMUnavailable(Exception):
    """Raised when neither a local key nor a Pro token is available, so callers
    can fall back to returning the text unchanged."""


def _has_local_key() -> bool:
    return bool(os.getenv("GROQ_API_KEY", ""))


def chat(system: str, user: str, temperature: float = 0.0, max_tokens: int = 1500) -> str:
    """Run one system+user chat completion and return the assistant text.

    Routes to Groq directly when a local key exists, else to the backend proxy.
    Raises LLMUnavailable when no path is possible."""
    if _has_local_key():
        return _chat_groq_direct(system, user, temperature, max_tokens)
    return _chat_backend(system, user, temperature, max_tokens)


def _chat_groq_direct(system: str, user: str, temperature: float, max_tokens: int) -> str:
    from groq import Groq

    client = Groq(api_key=os.getenv("GROQ_API_KEY", ""), timeout=10.0)
    completion = client.chat.completions.create(
        model=LLM_CLEANUP_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (completion.choices[0].message.content or "").strip()


def _chat_backend(system: str, user: str, temperature: float, max_tokens: int) -> str:
    from core.auth import get_pro_token

    token = get_pro_token()
    if not token:
        raise LLMUnavailable("no local Groq key and no Pro token")

    url = f"{KEYLESSFLOW_API_URL.rstrip('/')}/api/llm"
    payload = json.dumps(
        {
            "system": system,
            "user": user,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "KeyLessFlow/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 402 = plan/trial gate; anything else = backend/Groq error. Surface as
        # LLMUnavailable so callers fall back to the raw (uncleaned) text
        # rather than crashing the paste.
        raise LLMUnavailable(f"backend llm HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise LLMUnavailable(f"backend llm unreachable: {e}") from e
    return (data.get("text") or "").strip()
