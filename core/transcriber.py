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
from core.hallucination_filter import strip as strip_hallucinations
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
        # BYOK = the owner / power-user mode. Goes direct to Groq with the
        # user's own GROQ_API_KEY (read from %APPDATA%\KeyLessFlow\.env). No
        # backend round-trip, no auth, no quota gating. Opt-in via
        # transcribe_backend = "byok" — does NOT show up by default in the
        # settings UI; intended for the project owner or self-hosted users.
        from core.transcriber_groq import GroqTranscriber
        self._byok = GroqTranscriber()
        self._cleanup = LLMCleanup()

    def _pick_backend(self):
        # Explicit opt-in for local model (Apple Silicon only, mlx-whisper).
        # Everything else goes through our backend, UNLESS the user has
        # explicitly flipped to BYOK (direct-to-Groq, owner mode).
        backend = get_setting("transcribe_backend", "pro")
        if backend == "local" and self._local.available:
            return self._local
        if backend == "byok":
            return self._byok
        return self._pro

    def transcribe(self, audio_buffer, on_progress=None) -> tuple[str, str, int]:
        """Returns (final_text, model_id_used, failed_chunks).

        `audio_buffer` is an MP3/WAV BytesIO (backend sniffs the header), OR a
        list of such buffers for long recordings chunked by the recorder. Each
        chunk is transcribed independently — a single chunk failure (network
        blip, Groq 5xx, timeout) used to nuke the whole transcription. Now we
        catch per-chunk, return what survived, and report the failure count so
        the caller can surface "N de M partes fallaron — audio guardado para
        reintentar". If EVERY chunk fails we raise so the error path triggers.

        `on_progress(current, total)` — optional callback fired once at start
        (0, total) and after each chunk completes. Used by the UI to show
        "Transcribiendo 2/6" inside the pill during long-recording uploads."""
        from core.logger import log, log_exc
        backend = self._pick_backend()

        # Whisper vocabulary hint (only Groq supports it meaningfully; local ignores)
        vocab = ""
        if get_setting("personal_dictionary_enabled", True):
            vocab = as_whisper_prompt()

        failed_chunks = 0
        if isinstance(audio_buffer, (list, tuple)):
            total = len(audio_buffer)
            parts: list[str] = []
            if on_progress:
                try:
                    on_progress(0, total)
                except Exception:
                    pass
            for i, buf in enumerate(audio_buffer):
                try:
                    chunk_text = backend.transcribe(buf, vocabulary_prompt=vocab)
                except Exception as e:
                    failed_chunks += 1
                    log_exc(f"chunk {i + 1}/{total} failed (continuing with rest)", e)
                    if on_progress:
                        try:
                            on_progress(i + 1, total)
                        except Exception:
                            pass
                    continue
                if chunk_text:
                    parts.append(chunk_text.strip())
                if on_progress:
                    try:
                        on_progress(i + 1, total)
                    except Exception:
                        pass
            if not parts:
                raise RuntimeError(
                    f"Todos los chunks fallaron ({failed_chunks}/{total}). "
                    "Tu audio quedó guardado — reintenta desde el Hub."
                )
            if failed_chunks:
                log(
                    f"partial transcription: {failed_chunks}/{total} chunks failed",
                    level="WARN",
                )
            raw = " ".join(parts)
        else:
            raw = backend.transcribe(audio_buffer, vocabulary_prompt=vocab)

        # Strip known Whisper hallucinations (credit URLs, "thank you", etc.)
        # that the model invents on non-speech audio — BEFORE the empty check,
        # so an all-hallucination result becomes "" and the caller shows
        # "no se detectó voz" instead of pasting garbage like "www.feyyaz.tv".
        if raw:
            raw = strip_hallucinations(raw)

        if not raw:
            return "", backend.model_id, failed_chunks

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

        return raw.strip(), backend.model_id, failed_chunks
