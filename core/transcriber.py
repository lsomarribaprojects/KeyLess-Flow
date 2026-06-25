"""Transcription router. Orchestrates the full pipeline:

   audio → ProTranscriber (our backend, quota-enforced) → smart_commands
       → LLM cleanup (tone-aware) → text

Every user routes through ProTranscriber (Free trial OR paid). The backend
gates by plan + monthly quota. No BYOK path — eliminated to keep one code
path and one source of truth on usage.
"""
import io
from config import get_setting
from core.transcriber_local import LocalTranscriber
from core.transcriber_pro import ProTranscriber
from core.llm_cleanup import LLMCleanup
from core.smart_commands import apply as apply_smart_commands
from core.dictionary import as_whisper_prompt
from core.context import tone_for_active_app
from core.snippets_matcher import apply as apply_snippets


class Transcriber:
    def __init__(self):
        # LocalTranscriber kept around for optional offline mode (Apple Silicon
        # mlx-whisper) — it ignores backend account state. We don't auto-route
        # to it; user has to flip transcribe_backend = "local" explicitly.
        self._local = LocalTranscriber()
        self._pro = ProTranscriber()
        self._cleanup = LLMCleanup()

    def _pick_backend(self):
        # Explicit opt-in for local model (Apple Silicon only, mlx-whisper).
        # Everything else goes through our backend.
        if get_setting("transcribe_backend", "pro") == "local" and self._local.available:
            return self._local
        return self._pro

    def transcribe(self, audio_buffer) -> tuple[str, str]:
        """Returns (final_text, model_id_used).

        `audio_buffer` is an MP3/WAV BytesIO (backend sniffs the header), OR a
        list of such buffers for long recordings chunked by the recorder. Each
        chunk is transcribed independently and the raw text is concatenated
        before post-processing runs once over the whole transcript."""
        backend = self._pick_backend()

        # Whisper vocabulary hint (only Groq supports it meaningfully; local ignores)
        vocab = ""
        if get_setting("personal_dictionary_enabled", True):
            vocab = as_whisper_prompt()

        if isinstance(audio_buffer, (list, tuple)):
            parts = []
            for buf in audio_buffer:
                chunk_text = backend.transcribe(buf, vocabulary_prompt=vocab)
                if chunk_text:
                    parts.append(chunk_text.strip())
            raw = " ".join(parts)
        else:
            raw = backend.transcribe(audio_buffer, vocabulary_prompt=vocab)
        if not raw:
            return "", backend.model_id

        # Smart commands — regex pass, cheap
        if get_setting("smart_commands_enabled", True):
            raw = apply_smart_commands(raw)

        # LLM cleanup — tone-aware
        if get_setting("llm_cleanup_enabled", True):
            tone = "default"
            if get_setting("context_aware_tone", True):
                try:
                    tone = tone_for_active_app()
                except Exception:
                    tone = "default"
            raw = self._cleanup.clean(raw, tone=tone)

        # Snippets — run LAST so expansions are inserted verbatim, not cleaned
        if get_setting("snippets_enabled", True):
            try:
                raw = apply_snippets(raw)
            except Exception:
                pass

        return raw.strip(), backend.model_id
