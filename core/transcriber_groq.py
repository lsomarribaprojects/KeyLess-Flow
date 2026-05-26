import io
import os
import time
from groq import Groq, APITimeoutError, APIConnectionError
from config import GROQ_MODEL, WHISPER_LANGUAGE

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
            # 120s overall covers ~5-10 min of audio (Groq turbo runs much
            # faster than realtime, but upload of multi-MB WAVs on slow
            # connections is the long pole).
            self._client = Groq(api_key=key, timeout=120.0)
        return self._client

    def transcribe(self, wav_buffer: io.BytesIO, vocabulary_prompt: str = "") -> str:
        wav_buffer.seek(0)
        data = wav_buffer.read()
        if len(data) < 100:
            return ""
        size_mb = len(data) / (1024 * 1024)
        kwargs = dict(
            file=("recording.wav", data),
            model=GROQ_MODEL,
            language=WHISPER_LANGUAGE,
            response_format="text",
            temperature=0.0,
        )
        if vocabulary_prompt:
            kwargs["prompt"] = vocabulary_prompt

        # Retry once on transient network errors — covers Groq's occasional
        # 502s and our own flaky residential uplink.
        last_exc = None
        for attempt in (1, 2):
            try:
                t0 = time.time()
                transcription = self._get_client().audio.transcriptions.create(**kwargs)
                elapsed = time.time() - t0
                log(f"groq ok: attempt={attempt} size={size_mb:.2f}MB elapsed={elapsed:.1f}s")
                text = transcription.strip() if isinstance(transcription, str) else str(transcription).strip()
                if _is_hallucination(text):
                    return ""
                return text
            except (APITimeoutError, APIConnectionError) as e:
                last_exc = e
                log(f"groq transient fail (attempt {attempt}/{2}): size={size_mb:.2f}MB err={type(e).__name__}", level="WARN")
                if attempt == 1:
                    time.sleep(0.8)
                    continue
        # Both attempts failed — surface the original error type
        raise last_exc

    @property
    def model_id(self) -> str:
        return GROQ_MODEL
