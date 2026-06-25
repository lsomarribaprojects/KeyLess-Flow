"""Local transcription via mlx-whisper (Whisper on Apple MLX).

Benchmark ganador (M-series, audio de voz real español):
  - whisper-small-mlx: 1.0s (10s audio) — 10× realtime, $0, offline, 244MB

Opcional: `pip install mlx-whisper` (añade ~1.5GB con sus deps si no existen).
"""
import io
import tempfile
import os
from config import LOCAL_MODEL_ID, whisper_language


class LocalTranscriber:
    """mlx-whisper backend. Lazy-loads model + first-run downloads from HF."""

    def __init__(self):
        self._tried_import = False
        self._import_error: str | None = None

    @property
    def available(self) -> bool:
        if not self._tried_import:
            try:
                import mlx_whisper  # noqa: F401
                self._tried_import = True
            except Exception as e:
                self._tried_import = True
                self._import_error = str(e)
                return False
        return self._import_error is None

    def transcribe(self, wav_buffer: io.BytesIO, vocabulary_prompt: str = "") -> str:
        if not self.available:
            raise RuntimeError(f"mlx-whisper not available: {self._import_error}")

        import mlx_whisper

        wav_buffer.seek(0)
        data = wav_buffer.read()
        if len(data) < 100:
            return ""

        # mlx-whisper expects path or array — use temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            kwargs = {
                "path_or_hf_repo": LOCAL_MODEL_ID,
                # None → mlx-whisper auto-detects the language.
                "language": whisper_language() or None,
                "temperature": 0.0,
            }
            if vocabulary_prompt:
                kwargs["initial_prompt"] = vocabulary_prompt
            result = mlx_whisper.transcribe(tmp_path, **kwargs)
            return (result.get("text") or "").strip()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @property
    def model_id(self) -> str:
        return LOCAL_MODEL_ID
