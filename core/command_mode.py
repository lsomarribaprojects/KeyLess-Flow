"""Command Mode: select text → hotkey → speak a command → LLM transforms selection.

Example:
  1. User selects "this text is too long and rambling"
  2. Holds Ctrl+Shift hotkey, says "hazlo más conciso"
  3. Releases. SFlow grabs the selection via Cmd+C, sends selection + voice
     command to Llama, pastes the result back (Cmd+V) replacing the selection.

If no selection exists, we treat the voice as a freeform Llama query and
paste the response where the cursor is.
"""
import os
import sys
import time
import subprocess
from groq import Groq
from config import LLM_CLEANUP_MODEL


_COMMAND_SYSTEM = """Eres un asistente que transforma texto según instrucciones del usuario.

Reglas críticas:
- Devuelve SOLO el texto transformado, sin comentarios ni explicaciones
- NO uses markdown salvo que la instrucción lo pida explícitamente
- Preserva el idioma del texto original a menos que la instrucción pida traducir
- Si la instrucción no es clara, haz la interpretación más razonable
- NO saludes ni te despidas
- Si no hay texto seleccionado, la instrucción es una pregunta/petición libre — respóndela directamente"""


def _read_clipboard() -> str:
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString
        pb = NSPasteboard.generalPasteboard()
        val = pb.stringForType_(NSPasteboardTypeString)
        return str(val) if val else ""
    except Exception:
        pass
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=1)
        return result.stdout
    except Exception:
        return ""


def copy_selection() -> str:
    """Cmd+C (Mac) / Ctrl+C (Win) then read clipboard. Compares to a pre-snapshot
    to detect whether a selection actually landed. Restores the original
    clipboard so the user's copied content is preserved.

    Returns the selected text, or "" if nothing was selected.
    """
    if sys.platform == "win32":
        from core.platform._windows import copy_selection as _impl
        return _impl()
    before = _read_clipboard()
    try:
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "c" using command down'],
            check=True, timeout=1,
        )
    except Exception:
        return ""
    time.sleep(0.08)
    after = _read_clipboard()
    selected = after if (after and after != before) else ""

    # Restore user's previous clipboard (runs after we return so the caller
    # gets the selection text first; we're on a worker thread anyway).
    if before is not None and before != after:
        def _restore():
            time.sleep(0.05)
            try:
                from AppKit import NSPasteboard, NSPasteboardTypeString
                pb = NSPasteboard.generalPasteboard()
                pb.clearContents()
                pb.setString_forType_(before, NSPasteboardTypeString)
            except Exception:
                pass
        import threading
        threading.Thread(target=_restore, daemon=True).start()

    return selected


class CommandModeHandler:
    def __init__(self):
        self._client = None

    def _get_client(self) -> Groq:
        if self._client is None:
            key = os.getenv("GROQ_API_KEY", "")
            if not key:
                raise ValueError("GROQ_API_KEY not configured")
            self._client = Groq(api_key=key, timeout=12.0)
        return self._client

    def transform(self, voice_command: str, selected_text: str) -> str:
        voice_command = (voice_command or "").strip()
        if not voice_command:
            return selected_text

        if selected_text:
            user_msg = f"TEXTO SELECCIONADO:\n{selected_text}\n\nINSTRUCCIÓN:\n{voice_command}"
        else:
            user_msg = voice_command

        try:
            completion = self._get_client().chat.completions.create(
                model=LLM_CLEANUP_MODEL,
                messages=[
                    {"role": "system", "content": _COMMAND_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.4,
                max_tokens=2000,
            )
            result = completion.choices[0].message.content.strip()
            if result.startswith("```") and result.endswith("```"):
                result = result.strip("`").strip()
            return result or selected_text
        except Exception as e:
            print(f"Command mode LLM failed: {e}")
            return selected_text
