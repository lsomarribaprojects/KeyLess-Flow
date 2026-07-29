import os
import sys
import json
from dotenv import load_dotenv

# Make Python's SSL use the OS cert store BEFORE any HTTP client (httpx, requests,
# urllib) initializes its own context. Needed on corporate networks that
# intercept TLS with a custom CA (IDS/Zscaler/Netskope) — without this the
# Groq SDK fails with CERTIFICATE_VERIFY_FAILED because the certifi bundle
# doesn't know about the company CA. Safe everywhere: on a clean network it
# just falls back to the system roots, which is the same as the default.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass


def _get_resource_dir() -> str:
    """Read-only bundled assets (logo, etc). PyInstaller puts them in sys._MEIPASS."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _get_data_dir() -> str:
    """Writable user data (DB, .env).

    Mac bundle → ~/Library/Application Support/SFlow
    Windows    → %APPDATA%\\KeyLessFlow (always, even in dev — keeps a single
                 location for settings/db so dev and packaged runs share state)
    Mac dev    → project root (legacy behaviour)
    """
    if sys.platform == "win32":
        from core.platform._windows import data_dir as _win_data_dir
        return _win_data_dir()
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
        "whisper_language": "auto",     # "auto" (detect es/en/…) | "es" | "en"
        "confirm_paste": False,         # show a tray toast confirming every successful paste
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

# Whisper language. "auto" lets the model detect es/en/etc per request (the
# right default for bilingual users). A concrete code ("es"/"en") forces it.
WHISPER_LANGUAGE = "auto"


def whisper_language() -> str:
    """Effective Whisper language code. Returns "" for auto-detect, which the
    transcribers translate to "omit the language field" so Whisper picks it."""
    val = get_setting("whisper_language", WHISPER_LANGUAGE)
    return "" if val in ("", "auto", None) else str(val)


# --- Long-audio chunking ---
# Recordings longer than this are split into <=this-sized windows (snapped to
# silence) and transcribed chunk-by-chunk. Keeps every request well under
# Groq's 25 MB cap and the backend's 60s function timeout, so dictation of
# 30 min / 1 h / 2 h works the same as a 10-second clip. 10 min @ 32 kbps
# MP3 ≈ 2.4 MB.
CHUNK_MAX_SECONDS = 600

# Soft warning while recording: past this many seconds the app sends a tray
# notification (transcription still works — chunking handles any length).
RECORDING_WARN_SECONDS = 45 * 60

# --- KeyLess by Sinsajo backend (Pro plan only) ---
# Where the desktop app POSTs to /api/transcribe, /api/auth/activate and
# /api/llm when the user is on the managed tier. Override via env var if you
# self-host or run the backend locally during development.
#
# IMPORTANT (2026-07-18): keylessflow.app does NOT resolve — the custom domain
# was never configured in DNS/Vercel. The canonical production host is the
# Vercel project domain (verified live: /api/llm responds 401 there). When the
# custom domain is actually purchased + configured, flip this default back and
# Vercel will serve both hosts during the transition.
KEYLESSFLOW_API_URL = os.getenv(
    "KEYLESSFLOW_API_URL", "https://keylessflow-web.vercel.app",
)

# --- App version (read by the auto-updater to compare against GitHub releases) ---
# Bump in lock-step with installer.iss MyAppVersion and the GitHub release tag.
APP_VERSION = "1.2.1"

# Auto-update polls this URL for the latest release tag + installer asset.
# Public endpoint, no auth needed (subject to GitHub's 60 req/hour unauth limit
# — well within our once-per-day check cadence).
UPDATE_FEED_URL = (
    "https://api.github.com/repos/lsomarribaprojects/KeyLess-Flow/releases/latest"
)

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
PILL_WIDTH_RECORDING = 110         # +10 so the wider waveform area can fit a brand caption underneath
PILL_WIDTH_STATUS = 52
PILL_HEIGHT = 34                    # idle / status height
PILL_HEIGHT_RECORDING = 50          # taller in recording: wave on top + "Sinsajo Creators" caption below
PILL_OPACITY = 0.90
PILL_CORNER_RADIUS = 17
PILL_MARGIN_BOTTOM = 14
LOGO_SIZE = 22

# Brand caption shown below the waveform when actively recording.
PILL_BRAND_CAPTION = "Sinsajo Creators"

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
# After the 2nd clean Ctrl tap we WAIT this long before committing to mic
# hands-free, so a 3rd tap (system-audio hands-free) can still override it.
# Must be comfortably larger than a human's tap-to-tap gap or the 3rd tap is
# unreachable — measured: a relaxed triple-tap runs ~150-250ms between taps,
# so 200ms was too tight (the mic HF fired first and the 3rd tap became a
# "stop"). 350ms makes the triple-tap reliably reachable; the only cost is a
# barely-perceptible delay before mic hands-free starts (it's not push-to-talk).
TRIPLE_TAP_DEFER = 0.35

# --- Database (writable user data) ---
DB_PATH = os.path.join(_DATA_DIR, "transcriptions.db")
DICTIONARY_PATH = os.path.join(_DATA_DIR, "dictionary.txt")
AUDIO_DIR = os.path.join(_DATA_DIR, "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

APP_DATA_DIR = _DATA_DIR
