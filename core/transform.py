"""Execute Option+N transforms — Llama reshapes the selected text with the
user-configured prompt.

No voice recording involved. User selects text, hits Option+N, gets the
transform pasted back replacing the selection.
"""
from config import get_setting
from core.llm_backend import chat as _llm_chat, LLMUnavailable


_SYSTEM = """Eres un asistente que transforma texto según una instrucción dada.

Reglas críticas:
- Devuelve SOLO el texto transformado, sin comentarios ni explicaciones
- NO uses markdown a menos que la instrucción lo pida
- Preserva el idioma del original a menos que se pida traducir
- NO saludes ni te despidas"""


class TransformHandler:
    def get_prompt(self, index: int) -> tuple[str, str]:
        """Return (label, prompt) for transform at index. Empty if out of range."""
        prompts = get_setting("transform_prompts", [])
        if 0 <= index < len(prompts):
            p = prompts[index]
            return p.get("label", f"Transform {index+1}"), p.get("prompt", "")
        return "", ""

    def run(self, index: int, selected_text: str) -> str:
        """Apply the transform at `index` to the selected_text. Returns new text."""
        if not selected_text:
            return selected_text
        _, prompt = self.get_prompt(index)
        if not prompt:
            return selected_text

        user_msg = f"INSTRUCCIÓN: {prompt}\n\nTEXTO:\n{selected_text}"
        try:
            result = _llm_chat(
                system=_SYSTEM,
                user=user_msg,
                temperature=0.3,
                max_tokens=2000,
            )
        except LLMUnavailable:
            return selected_text
        except Exception as e:
            print(f"transform {index} failed: {e}")
            return selected_text
        if result.startswith("```") and result.endswith("```"):
            result = result.strip("`").strip()
        return result or selected_text
