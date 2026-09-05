# KeyLess by Sinsajo (desktop app)

Voice-to-text para Windows (port de "SFlow"/macOS). Mantenés un atajo, hablás,
soltás y el texto se pega donde está el cursor. También transcribe **el audio que
suena en la PC** (WhatsApp, videos, reuniones). Lo usa Luis (dueño, modo BYOK con
su propia Groq key) y se distribuye a asistentes de workshops (BYOK, gratis) y a
usuarios managed (backend `keylessflow-web`, trial → suscripción).
"Funcionando" = Ctrl+Alt dicta y pega; Ctrl+Shift transcribe el audio del sistema;
sin errores al final de la grabación; Hub abre desde la bandeja.

## Cómo correrlo
```powershell
# Dev (usa la misma carpeta de datos que la app instalada: %LOCALAPPDATA%\KeyLessFlow)
.\venv\Scripts\python.exe main.py
# Tests (sin pytest; correr cada archivo). 52 checks + E2E real del Redactor contra Groq.
foreach ($t in Get-ChildItem tests\test_*.py) { .\venv\Scripts\python.exe $t.FullName }
# Build + instalador (.exe + .sha256). Instalar en silencio:
.\build.ps1 -Installer
.\dist\KeyLessFlow-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS
# Release público: tag vX.Y.Z → GitHub Actions compila Win/Mac/Linux y sube assets; luego:
bash tools/release_checksum.sh vX.Y.Z        # publica .sha256 verificado (el updater lo exige)
# y en keylessflow-web: APP_VERSION en src/app/page.tsx → push main (Vercel auto-deploy)
```
Secretos: `.env` en `%LOCALAPPDATA%\KeyLessFlow\` (`GROQ_API_KEY`), nunca en el repo.
Verificar un arranque REAL (no "hay un proceso"): esperar la línea `retention:` en
`%LOCALAPPDATA%\KeyLessFlow\sflow.log` ~30 s tras lanzar, sin `Traceback`.

## Arquitectura en 5 líneas
1. `core/hotkey.py` (pynput, hilo propio) → señales Qt **QueuedConnection** → `main.py`.
2. `core/recorder.py` graba mic (sounddevice) o loopback WASAPI (`soundcard`), guarda WAV
   checkpoint en `audio/`, sube MP3 en chunks de 10 min.
3. `core/transcriber.py`: backend (Groq directo si hay key local, si no `/api/transcribe`)
   con retry+backoff → `hallucination_filter` → smart_commands → LLM cleanup
   (`core/llm_backend.py`, lista de modelos con fallback) → snippets → paste.
4. `db/database.py` SQLite (`transcriptions`, filas `status ok|failed`), `db/library.py`,
   `db/snippets.py`. `core/retention.py` poda audio; `core/usage.py` mide uso.
5. `ui/hub_window.py` = Hub (Home/Historial/Diccionario/Snippets/Redactor/Ajustes) +
   pill flotante. `web/server.py` = dashboard Flask local con token.

## Decisiones y por qué (no las deshagas sin leer esto)
- **Ctrl+Shift = audio del sistema, NO Alt+Shift**: Windows secuestra Alt+Shift (idioma) y
  en teclados ES la Alt derecha es AltGr = Ctrl+Alt fantasma. `hotkey.py` neutraliza AltGr
  y reconcilia modificadores con `GetAsyncKeyState` (el hook pierde key-ups en UAC/lock).
- **Lista de modelos LLM con fallback** (`config.LLM_MODEL_CANDIDATES`, espejo en
  `keylessflow-web/src/app/api/llm/route.ts`): Groq retiró `llama-3.3-70b` sin aviso y
  rompió limpieza/transforms/Redactor. Nunca hardcodear un solo modelo.
- **Retry solo en errores transitorios** (`core/errors.py`): 401/402/413 fallan rápido;
  reintentar un 401 cuatro veces solo hace esperar al usuario.
- **Fallos = filas `status='failed'`** en la DB: sin fila el WAV quedaba huérfano y
  "reintentá desde el Hub" era imposible. Clic en la notificación reintenta.
- **Retención 7d ok / 30d fallidos / tope 500 MB**: la poda existía y nadie la llamaba
  (Luis tenía 1.3 GB). Los fallidos nunca se evictan por tamaño.
- **Dashboard local con token + Host check; token Pro cifrado DPAPI; updater falla cerrado
  sin `.sha256`** — auditoría de seguridad de julio (ver bitácora §2).
- **`KEYLESSFLOW_API_URL` = `keylessflow-web.vercel.app`**: `keylessflow.app` no existe en
  DNS (nunca se compró). Cambiar solo cuando el dominio esté configurado en Vercel.
- **Negocio**: managed trial 30d/8h → Basic $8 / Pro $14; BYOK $49 único; workshop = BYOK
  gratis con su propia key. Detalle: memoria del proyecto + `docs/BITACORA-2026-07.md` §4.

## Estado actual (2026-09-05)
- **Hecho y verificado**: v1.3.1 publicada (release con Win/Mac/Linux + checksum), instalada
  en la máquina de Luis y en uso. Landing en v1.3.1. Filtros de alucinación, guardia de
  silencio, retry, filas fallidas, retención (liberó 1.19 GB), medidor de uso, Redactor,
  onboarding BYOK, ayuda en Ajustes, `/comunidad` en vivo.
- **Bloqueado por acción de Luis**: el proyecto Supabase (`aaiqjtgrsogknmngvjxu`) está
  **pausado** (free tier, inactividad) → `/api/community`, `/api/waitlist`, activación y
  transcripción managed dan 500. Siguiente paso: Luis lo restaura en supabase.com →
  verificar con `POST /api/waitlist` (200) y `node scripts/community_e2e.mjs` (web repo).
- **Pendiente (orden sugerido)**: correr el SQL de `community_leads` en el editor de Supabase;
  comprar/configurar dominio; Stripe keys en prod (hoy nadie puede pagar); pool de Groq keys
  + panel `/admin/usage` + alertas de gasto (plan aprobado en bitácora); página `/byok` +
  licencia $49; ticket a GitHub para purgar objetos huérfanos (WAVs); PWA móvil.
- **Deuda conocida**: `.env` (Groq key) sigue en texto plano; dashboard local carga Tailwind
  de CDN; macOS port sin validar en hardware (loopback requiere BlackHole).

## Trampas
- **Un `import` local dentro de una función que sombrea un nombre de módulo** hace que
  TODA la función lo trate como local → `UnboundLocalError` al arrancar (así crasheó
  v1.3.0). `py_compile` e `import main` NO lo detectan; `tests/test_startup_guards.py` sí.
- **PowerShell 5.1 + `$ErrorActionPreference=Stop`**: cualquier línea en stderr de pip /
  PyInstaller / ISCC aborta el script. `build.ps1` ya lo maneja; no lo "simplifiques".
- **Variables de Vercel**: setear SOLO con `printf '%s' | vercel env add` — `Out-File`/`echo`
  de PowerShell agregan un BOM que rompe el header `Authorization` (pasó con la service
  key). `vercel env pull` devuelve VACÍO para variables sensibles: no sirve para inspeccionar.
- **Un proceso `KeyLessFlow.exe` vivo no prueba nada**: tras un crash en `main()` queda un
  stub de ~1 MB. Verificá con el log (`retention:`) o el puerto 56789 (lock de instancia).
- **La consola de Windows (cp1252) no imprime CJK/emoji**: correr tests/scripts con
  `PYTHONIOENCODING=utf-8` o `$env:PYTHONIOENCODING='utf-8'`.
- **OneDrive bloquea archivos de `build/`** unos segundos tras un build; `build.ps1`
  reintenta. Si `dist/` falla, esperar y repetir.
- **El token de git no tiene scope `workflow`**: cambios en `.github/workflows/` no se
  pueden pushear (hay un stash `ci-sha256-workflow` esperando un PAT con ese scope).
- **Whisper alucina en audio sin voz** (URLs, "Thank you", japonés): el filtro está anclado
  al final del texto a propósito; no lo conviertas en un filtro global de URLs.
- **Red de esta máquina**: DNS/TLS a GitHub y Supabase fallan a ratos. `gh` puede dar
  "err" mientras la REST API vía PowerShell funciona; los scripts ya reintentan.

Historia completa, evidencia y post-mortems: `docs/BITACORA-2026-07.md`. Guía para
asistentes: `docs/WORKSHOP.md`. Versión anterior de este archivo: `CLAUDE.md.bak-2026-09-05`.
