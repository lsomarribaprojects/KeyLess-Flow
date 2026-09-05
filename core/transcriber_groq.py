import io
import os
import time
from groq import Groq, APITimeoutError, APIConnectionError
from config import GROQ_MODEL, whisper_language

from core.logger import log, log_exc


_HALLUCINATION_MARKERS = (
    "gracias por ver el video",
    "gracias por ver este video",
    "gracias por ver.",
    "subtitulado por la comunidad",
    "subtítulos por la comunidad",
    "subtitulos realizados por la comunidad",
    "subtítulos realizados por la comunidad",
    "amara.org",
    "suscríbete al canal",
    "suscribete al canal",
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "see you next time",
    "estoy listo para ayudarte",
    "¿qué transcripción de voz necesitas",
    "que transcripcion de voz necesitas",
)


def _is_hallucination(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _HALLUCINATION_MARKERS)


class GroqTranscriber:
    """Cloud transcription via Groq Whisper Large v3 Turbo."""

    def __init__(self):
        self._client = None

    def _get_client(self) -> Groq:
        if self._client is None:
            key = os.getenv("GROQ_API_KEY", "")
            if not key:
                raise ValueError("GROQ_API_KEY not configured")
            # 300s overall: chunking keeps each request to ~10 min of audio,
            # but upload of multi-MB chunks on slow connections is the long
            # pole. Groq turbo runs much faster than realtime.
            self._client = Groq(api_key=key, timeout=300.0)
        return self._client

    def transcribe(self, audio_buffer: io.BytesIO, vocabulary_prompt: str = "") -> str:
        audio_buffer.seek(0)
        data = audio_buffer.read()
        if len(data) < 100:
            return ""
        size_mb = len(data) / (1024 * 1024)
        # Filename extension hints the format to Groq's decoder — we send MP3
        # by default (see AudioRecorder.get_mp3_buffer). WAV still works if
        # callers pass a WAV buffer for whatever reason.
        is_wav = data[:4] == b"RIFF"
        filename = "recording.wav" if is_wav else "recording.mp3"
        kwargs = dict(
            file=(filename, data),
            model=GROQ_MODEL,
            response_format="text",
            temperature=0.0,
        )
        lang = whisper_language()
        if lang:  # omit when auto-detecting so Whisper picks the language
            kwargs["language"] = lang
        if vocabulary_prompt:
            kwargs["prompt"] = vocabulary_prompt

        # Retries live in core.transcriber (one policy for every backend, with
        # error classification) — this is a single attempt on purpose.
        t0 = time.time()
        transcription = self._get_client().audio.transcriptions.create(**kwargs)
        elapsed = time.time() - t0
        log(f"groq ok: size={size_mb:.2f}MB elapsed={elapsed:.1f}s")
        text = transcription.strip() if isinstance(transcription, str) else str(transcription).strip()
        if _is_hallucination(text):
            return ""
        return text

    @property
    def model_id(self) -> str:
        return GROQ_MODEL
