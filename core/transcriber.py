"""Transcription router. Orchestrates the full pipeline:

   audio → (Groq | Local) → smart_commands → LLM cleanup (tone-aware) → text

All post-processing steps are toggleable via settings.json.
"""
import io
from config import get_setting
from core.transcriber_groq import GroqTranscriber
from core.transcriber_local import LocalTranscriber
from core.transcriber_pro import ProTranscriber
from core.llm_cleanup import LLMCleanup
from core.smart_commands import apply as apply_smart_commands
from core.dictionary import as_whisper_prompt
from core.context import tone_for_active_app
from core.snippets_matcher import apply as apply_snippets
from core import auth as pro_auth


class Transcriber:
    def __init__(self):
        self._groq = GroqTranscriber()
        self._local = LocalTranscriber()
        self._pro = ProTranscriber()
        self._cleanup = LLMCleanup()

    def _pick_backend(self):
        # Pro account beats every other choice. If the user activated a Pro
        # subscription, route through our backend — they expect us to pay
        # for the transcription, not their own Groq key.
        if pro_auth.is_pro():
            return self._pro
        backend = get_setting("transcribe_backend", "groq")
        if backend == "local" and self._local.available:
            return self._local
        return self._groq

    def transcribe(self, audio_buffer: io.BytesIO) -> tuple[str, str]:
        """Returns (final_text, model_id_used). Buffer is MP3 by default but
        WAV is also accepted (backend sniffs the header)."""
        backend = self._pick_backend()

        # Whisper vocabulary hint (only Groq supports it meaningfully; local ignores)
        vocab = ""
        if get_setting("personal_dictionary_enabled", True):
            vocab = as_whisper_prompt()

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
