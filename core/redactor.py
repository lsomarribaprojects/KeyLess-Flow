"""Redactor — convierte una idea suelta (dictada o escrita) en un texto
bien redactado y listo para usar (email, WhatsApp, post de LinkedIn, etc.).

No graba audio ni transforma selección — toma texto libre (`idea`) y
devuelve el resultado final vía core/llm_backend (BYOK/Groq o backend Pro).
"""
from core.llm_backend import chat as _llm_chat, LLMUnavailable


# ---------- Opciones expuestas a la UI (clave -> etiqueta en español) ----------
LANGUAGES = {
    "auto": "Auto",
    "Español": "Español",
    "English": "English",
    "Português": "Português",
    "Français": "Français",
}

TONES = {
    "neutral": "Neutral",
    "formal": "Formal",
    "casual": "Casual",
    "email": "Email",
    "whatsapp": "WhatsApp",
    "linkedin": "LinkedIn",
}

LENGTHS = {
    "corto": "Corto",
    "medio": "Medio",
    "largo": "Largo",
}


_TONE_INSTRUCTIONS = {
    "neutral": "Tono neutral, claro y directo.",
    "formal": "Tono formal y profesional, cuidando la cortesía y evitando coloquialismos.",
    "casual": "Tono casual y cercano, como hablándole a un colega o amigo.",
    "email": (
        "Formato de correo electrónico: incluye un saludo breve al inicio y un "
        "cierre breve al final (por ejemplo 'Saludos' o equivalente en el idioma "
        "pedido). Tono profesional pero no rígido."
    ),
    "whatsapp": (
        "Formato de mensaje de WhatsApp: directo, de 1 a 3 líneas como máximo, "
        "sin saludo formal ni firma. Se permite el uso de emojis si encajan de "
        "forma natural, pero no son obligatorios."
    ),
    "linkedin": (
        "Formato de post de LinkedIn: tono profesional pero con voz propia, "
        "pensado para generar interés o discusión. Puede usar saltos de línea "
        "cortos para facilitar la lectura."
    ),
}

_LENGTH_INSTRUCTIONS = {
    "corto": "Extensión corta: 1 a 2 oraciones como máximo.",
    "medio": "Extensión media: aproximadamente un párrafo.",
    "largo": "Extensión larga: varios párrafos si el contenido lo amerita.",
}


def build_system_prompt(language: str = "auto", tone: str = "neutral", length: str = "medio") -> str:
    """Build the system prompt for the redactor LLM call. Pure/testable —
    no network call."""
    lines = [
        "Eres un redactor profesional. Convierte la idea del usuario en un "
        "texto bien redactado y listo para usar.",
        "",
        "Reglas críticas:",
        "- Devuelve SOLO el texto final, sin comentarios ni explicaciones",
        "- NO uses markdown a menos que el tono pedido lo requiera",
        "- Preserva la intención original de la idea",
        "- NO inventes datos, nombres, fechas ni cifras que no estén en la idea",
    ]

    if language == "auto":
        lines.append("- Responde en el MISMO idioma de la idea.")
    else:
        lines.append(f"- Escribe el resultado en {language}.")

    tone_instr = _TONE_INSTRUCTIONS.get(tone, _TONE_INSTRUCTIONS["neutral"])
    lines.append(f"- {tone_instr}")

    length_instr = _LENGTH_INSTRUCTIONS.get(length, _LENGTH_INSTRUCTIONS["medio"])
    lines.append(f"- {length_instr}")

    return "\n".join(lines)


def redact(idea: str, language: str = "auto", tone: str = "neutral", length: str = "medio") -> str:
    """Turn a loose idea into a polished, ready-to-send text.

    Raises RuntimeError (with a user-facing Spanish message) when no LLM
    path is available, so callers can surface it without crashing."""
    idea = (idea or "").strip()
    if not idea:
        return ""

    system = build_system_prompt(language, tone, length)
    try:
        result = _llm_chat(
            system=system,
            user=idea,
            temperature=0.4,
            max_tokens=1200,
        )
    except LLMUnavailable as e:
        raise RuntimeError("Configura tu cuenta o API key para redactar") from e

    if result.startswith("```") and result.endswith("```"):
        result = result.strip("`").strip()
    return result
