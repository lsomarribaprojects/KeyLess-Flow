"""LLM post-processing via Groq Llama — removes filler, fixes punctuation.

Adds ~100-300ms latency. This is THE feature that separates SFlow from
commodity dictation apps. System prompt adapts per active app (context-aware).
"""
import os
from groq import Groq
from config import LLM_CLEANUP_MODEL


_BASE_RULES = """Eres un corrector MINIMO de transcripciones de voz. Tu trabajo es PRESERVAR la transcripcion casi intacta, solo haciendo los cambios ESTRICTAMENTE necesarios.

REGLA DE ORO: Si dudas, NO cambies. Devolver el texto tal cual es SIEMPRE aceptable.

Lo unico que puedes hacer:
- Agregar puntos, comas, y signos de interrogacion donde sean evidentes
- Capitalizar inicio de oraciones y nombres propios obvios
- Eliminar SOLO muletillas muy evidentes cuando son relleno puro: "eh", "um" (UNICAMENTE estas dos)

PROHIBIDO (bajo cualquier circunstancia):
- Reformular, parafrasear, o reescribir cualquier frase
- Reemplazar palabras por sinonimos
- Agregar palabras que no esten en la transcripcion original
- Eliminar "pues", "bueno", "este", "o sea" (son parte del habla natural del usuario)
- Quitar repeticiones intencionales o enfaticas
- Cambiar el orden de palabras
- Traducir o cambiar idioma
- Agregar saludos, despedidas, o frases de cortesia
- Agregar o modificar emojis
- Agregar markdown o formato

Devuelve SOLO el texto resultante, sin comentarios ni explicaciones.

Ejemplos (input → output):
1. "hola eh como estas"             → "Hola, ¿cómo estás?"
2. "bueno pues ya termine el task"  → "Bueno, pues ya terminé el task."  (preserva "bueno pues")
3. "o sea no se que hacer"          → "O sea, no sé qué hacer."  (preserva "o sea")
4. "luis me dijo que compre dos"    → "Luis me dijo que compre dos."
5. "dale al boton verde um arriba"  → "Dale al botón verde arriba."  (solo eliminar "um")"""


TONE_PROFILES = {
    "casual": "Tono: casual, natural. Permite emojis si el contexto sugiere chat.",
    "formal": "Tono: formal, profesional. Sin emojis. Puntuación rigurosa.",
    "code": "Contexto: código. Preserva símbolos, nombres en inglés, camelCase, snake_case. NO corrijas términos técnicos.",
    "email": "Contexto: email. Formal pero amigable. Saluda solo si se dicta. Estructura párrafos.",
    "chat": "Contexto: mensaje corto (Slack/WhatsApp/Discord). Conciso. Emojis permitidos si encajan.",
    "note": "Contexto: nota personal. Mantén el tono del que habla, mínima edición.",
    "default": "Tono: neutral.",
}


class LLMCleanup:
    def __init__(self):
        self._client = None

    def _get_client(self) -> Groq:
        if self._client is None:
            key = os.getenv("GROQ_API_KEY", "")
            if not key:
                raise ValueError("GROQ_API_KEY not configured")
            self._client = Groq(api_key=key, timeout=8.0)
        return self._client

    def clean(self, text: str, tone: str = "default") -> str:
        if not text or len(text.strip()) < 3:
            return text

        tone_rule = TONE_PROFILES.get(tone, TONE_PROFILES["default"])
        system_prompt = f"{_BASE_RULES}\n\n{tone_rule}"

        try:
            completion = self._get_client().chat.completions.create(
                model=LLM_CLEANUP_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,  # determinista: 0 randomness para evitar alucinaciones
                max_tokens=1500,
            )
            cleaned = completion.choices[0].message.content.strip()
            # Strip markdown code fences if LLM added them
            if cleaned.startswith("```") and cleaned.endswith("```"):
                cleaned = cleaned.strip("`").strip()
            return cleaned or text
        except Exception:
            return text
