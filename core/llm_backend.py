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

import re
from config import KEYLESSFLOW_API_URL, LLM_MODEL_CANDIDATES, get_setting


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


_ACTIVE_MODEL: str | None = None   # first candidate that answered this session
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)  # qwen3 reasoning tags


def _candidates() -> list[str]:
    cands = list(LLM_MODEL_CANDIDATES)
    pinned = get_setting("llm_model", "auto")
    if pinned and pinned != "auto" and pinned not in cands:
        cands.insert(0, str(pinned))
    if _ACTIVE_MODEL in cands:
        cands.remove(_ACTIVE_MODEL)
        cands.insert(0, _ACTIVE_MODEL)
    return cands


def _model_missing(exc: BaseException) -> bool:
    name = type(exc).__name__
    msg = str(exc).lower()
    status = getattr(exc, "status_code", None)
    return (
        name == "NotFoundError" or status == 404
        or "model_not_found" in msg or "does not exist" in msg
        or "decommissioned" in msg or "has been deprecated" in msg
    )


def _chat_groq_direct(system: str, user: str, temperature: float, max_tokens: int) -> str:
    """Direct Groq chat with model fallback. Walks LLM_MODEL_CANDIDATES and
    skips retired models (404 / model_not_found) instead of failing — Groq
    rotates its catalog and a hardcoded id took down cleanup, transforms and
    the Redactor at once."""
    global _ACTIVE_MODEL
    from groq import Groq
    from core.logger import log

    client = Groq(api_key=os.getenv("GROQ_API_KEY", ""), timeout=20.0)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last: BaseException | None = None
    for model in _candidates():
        kwargs = dict(model=model, messages=messages, temperature=temperature,
                      max_tokens=max_tokens)
        if model.startswith("openai/gpt-oss"):
            # Reasoning model: we want the answer fast, not a chain of thought.
            kwargs["reasoning_effort"] = "low"
        try:
            try:
                completion = client.chat.completions.create(**kwargs)
            except Exception as e:
                if "reasoning" in str(e).lower() and "reasoning_effort" in kwargs:
                    kwargs.pop("reasoning_effort")
                    completion = client.chat.completions.create(**kwargs)
                else:
                    raise
        except Exception as e:
            if _model_missing(e):
                log(f"llm: model {model} unavailable, trying next ({type(e).__name__})", level="WARN")
                last = e
                continue
            raise
        if _ACTIVE_MODEL != model:
            log(f"llm: using model {model}")
            _ACTIVE_MODEL = model
        text = completion.choices[0].message.content or ""
        return _THINK_RE.sub("", text).strip()
    raise LLMUnavailable(f"no Groq chat model available ({last})")


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
