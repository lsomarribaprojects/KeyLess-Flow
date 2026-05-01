import os
import sys
import json
from dotenv import load_dotenv


def _get_resource_dir() -> str:
    """Read-only bundled assets (logo, etc). PyInstaller puts them in sys._MEIPASS."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _get_data_dir() -> str:
    """Writable user data (DB, .env). In bundle → ~/Library/Application Support/SFlow/."""
    if getattr(sys, "frozen", False):
        return os.path.expanduser("~/Library/Application Support/SFlow")
    return os.path.dirname(os.path.abspath(__file__))


_RESOURCE_DIR = _get_resource_dir()
_DATA_DIR = _get_data_dir()

if getattr(sys, "frozen", False):
    os.makedirs(_DATA_DIR, exist_ok=True)

load_dotenv(os.path.join(_DATA_DIR, ".env"))

# --- Settings file (runtime-mutable via UI) ---
SETTINGS_PATH = os.path.join(_DATA_DIR, "settings.json")


def _default_settings() -> dict:
    return {
        "transcribe_backend": "groq",   # "groq" | "local"
        "llm_cleanup_enabled": False,  # OFF por default: fidelidad > limpieza. Opt-in en Hub si se desea auto-puntuacion.
        "llm_model": "llama-3.3-70b-versatile",  # modelo con mejor instruction-following (menos alucinaciones)
        "context_aware_tone": True,
        "smart_commands_enabled": True,
        "personal_dictionary_enabled": True,
        "liquid_glass_enabled": False,
        "streaming_paste_enabled": False,
        "mouse_button_hotkey": None,  # None | "middle" | "x1" | "x2"
        "command_mode_enabled": True,
        "paste_backend": "keystroke",  # "keystroke" | "clipboard"
        "save_audio_for_retry": True,
        "history_hotkey_enabled": True,
        "sound_on_start": False,
        "sound_on_done": False,
        "snippets_enabled": True,
        "focus_mode_enabled": False,
        "focus_mode_apps": [],
        "transform_prompts": [
            {"label": "Más conciso", "prompt": "Haz este texto más conciso preservando el significado clave."},
            {"label": "Más formal", "prompt": "Reescribe este texto en tono formal profesional."},
            {"label": "Más casual", "prompt": "Reescribe este texto en tono casual amigable."},
            {"label": "Traducir a inglés", "prompt": "Traduce este texto a inglés natural."},
            {"label": "Bullet points", "prompt": "Convierte este texto en una lista de bullet points concisos."},
            {"label": "Corregir ortografía", "prompt": "Corrige solo errores ortográficos y de puntuación, preserva exactamente el resto."},
            {"label": "Expandir idea", "prompt": "Expande esta idea en un párrafo completo y bien estructurado."},
            {"label": "Resumir", "prompt": "Resume este texto en 1-2 oraciones."},
        ],
    }


def load_settings() -> dict:
    defaults = _default_settings()
    if not os.path.exists(SETTINGS_PATH):
        return defaults
    try:
        with open(SETTINGS_PATH) as f:
            loaded = json.load(f)
        defaults.update(loaded)
        return defaults
    except Exception:
        return defaults


def save_settings(data: dict):
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)


_SETTINGS = load_settings()


def get_setting(key: str, default=None):
    return _SETTINGS.get(key, default)


def set_setting(key: str, value):
    _SETTINGS[key] = value
    save_settings(_SETTINGS)


# --- Groq API ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "whisper-large-v3-turbo"
LLM_CLEANUP_MODEL = "llama-3.3-70b-versatile"  # mejor fidelidad que 8b (~300-500ms vs 100-200ms)
WHISPER_LANGUAGE = "es"

# --- Local model (mlx-whisper, optional) ---
# Benchmark-winning default: whisper-small-mlx → 1s per 10s audio, 244MB.
# Alternatives: "mlx-community/whisper-tiny-mlx" (faster for short clips),
# "mlx-community/whisper-large-v3-turbo" (highest quality, slower).
LOCAL_MODEL_ID = "mlx-community/whisper-small-mlx"

# --- Audio ---
SAMPLE_RATE = 16000
CHANNELS = 1
AUDIO_DTYPE = "int16"
BLOCK_SIZE = 1024

# --- UI ---
PILL_WIDTH_IDLE = 34
PILL_WIDTH_RECORDING = 100
PILL_WIDTH_STATUS = 52
PILL_HEIGHT = 34
PILL_OPACITY = 0.90
PILL_CORNER_RADIUS = 17
PILL_MARGIN_BOTTOM = 14
LOGO_SIZE = 22

LOGO_PATH = os.path.join(_RESOURCE_DIR, "logo_small.png")

# --- Audio Visualizer ---
NUM_BARS = 20
VIZ_FPS = 60
BAR_DECAY = 0.85
# Fine-tune knob para el visualizer dB-scaled. ~1.0 = neutro. Subir si las
# barras se ven muy timidas, bajar si saturan. Ya NO es multiplicador raw
# de FFT (eso se reescribio en ui/audio_visualizer.py).
BAR_GAIN = 2.3

# --- Hotkey ---
DOUBLE_TAP_INTERVAL = 0.4
# Ctrl held longer than this is a "hold", not a "tap" — protects against
# accidentally counting a long Ctrl press as part of a double-tap.
CTRL_TAP_MAX_DURATION = 0.25

# --- Database (writable user data) ---
DB_PATH = os.path.join(_DATA_DIR, "transcriptions.db")
DICTIONARY_PATH = os.path.join(_DATA_DIR, "dictionary.txt")
AUDIO_DIR = os.path.join(_DATA_DIR, "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

APP_DATA_DIR = _DATA_DIR
