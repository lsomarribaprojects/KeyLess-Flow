"""Pro-tier transcription backend — POSTs audio to KeyLess by Sinsajo's API.

Counterpart to `core.transcriber_groq`. Same `transcribe(audio_buffer, ...)`
contract so the router can swap one for the other based on auth state.

The backend (`/api/transcribe`) handles:
  - JWT/HMAC verification + subscription gating
  - Forwarding to Groq Whisper with Sinsajo's master key
  - Usage logging

So the desktop side stays small: just multipart upload + parse JSON.
"""
from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import uuid

from config import KEYLESSFLOW_API_URL, GROQ_MODEL, whisper_language
from core.auth import get_pro_token, sign_out
from core.logger import log, log_exc


class ProTranscriber:
    """Cloud transcription via our backend proxy (Pro subscription)."""

    def __init__(self):
        self._url = f"{KEYLESSFLOW_API_URL.rstrip('/')}/api/transcribe"

    def transcribe(self, audio_buffer: io.BytesIO, vocabulary_prompt: str = "") -> str:
        token = get_pro_token()
        if not token:
            raise RuntimeError(
                "No hay sesión Pro activa. Reconecta desde el tray "
                "(Conectar con cuenta Pro)."
            )

        audio_buffer.seek(0)
        data = audio_buffer.read()
        if len(data) < 100:
            return ""

        # Sniff WAV vs MP3 so the multipart filename matches the codec
        is_wav = data[:4] == b"RIFF"
        filename = "recording.wav" if is_wav else "recording.mp3"
        mime = "audio/wav" if is_wav else "audio/mpeg"

        boundary = f"----KeyLessFlow{uuid.uuid4().hex}"
        body = _build_multipart(
            boundary=boundary,
            filename=filename,
            mime=mime,
            audio=data,
            language=whisper_language(),
            prompt=vocabulary_prompt,
        )

        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "KeyLessFlow/1.0",
            },
        )

        t0 = time.time()
        try:
            # 300s: chunking keeps each request small, but a slow uplink on a
            # ~10 min chunk (2-3 MB) still needs generous headroom.
            with urllib.request.urlopen(req, timeout=300) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8"))
            except Exception:
                err = {}
            err_code = err.get("error", "")
            if e.code == 401:
                # Token invalid or expired — wipe local creds so the user
                # is prompted to re-activate.
                sign_out()
                raise RuntimeError(
                    "Tu sesión Pro expiró. Reconecta desde el tray."
                ) from e
            if e.code == 402:
                raise RuntimeError(
                    "Tu suscripción no está activa. Renueva en tu cuenta."
                ) from e
            raise RuntimeError(f"Backend error {e.code}: {err_code}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise RuntimeError(f"Network error reaching backend: {e}") from e

        elapsed = time.time() - t0
        log(
            f"pro ok: size_kb={len(data)/1024:.1f} elapsed={elapsed:.1f}s "
            f"server_elapsed_ms={payload.get('elapsed_ms')}"
        )
        return (payload.get("text") or "").strip()

    @property
    def model_id(self) -> str:
        return f"{GROQ_MODEL} (via pro proxy)"


def _build_multipart(
    *,
    boundary: str,
    filename: str,
    mime: str,
    audio: bytes,
    language: str,
    prompt: str,
) -> bytes:
    """Hand-rolled multipart/form-data so we don't depend on requests-toolbelt.

    Fields: file (binary), language (text), prompt (text, optional).
    """
    crlf = b"\r\n"
    parts: list[bytes] = []

    def text_field(name: str, value: str) -> None:
        parts.append(f"--{boundary}".encode("ascii"))
        parts.append(
            f'Content-Disposition: form-data; name="{name}"'.encode("ascii")
        )
        parts.append(b"")
        parts.append(value.encode("utf-8"))

    def file_field(name: str, fname: str, content_type: str, data: bytes) -> None:
        parts.append(f"--{boundary}".encode("ascii"))
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{fname}"'.encode("ascii")
        )
        parts.append(f"Content-Type: {content_type}".encode("ascii"))
        parts.append(b"")
        parts.append(data)

    file_field("file", filename, mime, audio)
    if language:  # omit when auto-detecting so Whisper picks the language
        text_field("language", language)
    if prompt:
        text_field("prompt", prompt)

    parts.append(f"--{boundary}--".encode("ascii"))
    parts.append(b"")

    return crlf.join(parts)
