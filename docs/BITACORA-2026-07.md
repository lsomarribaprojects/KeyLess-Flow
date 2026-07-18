# Bitácora — KeyLess by Sinsajo — julio 2026

Registro de todo lo hecho en la sesión de auditoría + blindaje + mejoras
(2026-07-11 → 2026-07-18). Cada punto fue **verificado con evidencia** (tests,
logs o reproducción real), no "debería funcionar".

## 1. Bugs encontrados y arreglados (app de escritorio)

| # | Bug | Root cause | Fix | Evidencia |
|---|---|---|---|---|
| 1 | Audio del sistema no arrancaba con Alt+Shift | Windows secuestra Alt+Shift (cambio de idioma) y AltGr (teclado ES) llega como LCtrl+RAlt fantasma → ruteaba al mic | Atajo movido a **Ctrl+Shift**; AltGr rastreado y neutralizado; defer del triple-tap 200→350 ms (`TRIPLE_TAP_DEFER`) | `tests/test_hotkey_routing.py` 6/6; confirmado en vivo por Luis |
| 2 | Pegaba el diccionario personal ("KeyLess Fow, Sinsajo Creators, Groq…") | Captura silenciosa + prompt de vocabulario → Whisper eco del prompt | Guardia de silencio: `recorder.max_amplitude() < 60` → "No se detectó audio" | probado con frames sintéticos y reales |
| 3 | Pegaba `www.keyLess.com www.feyyaz.tv` | Alucinación clásica de Whisper en audio sin voz (créditos de subtítulos del corpus de entrenamiento) | `core/hallucination_filter.py` — URLs de crédito y frases ("thank you", "gracias por ver…") ancladas al final | re-transcripción del WAV real: antes basura → después `""` |
| 4 | Escribió en japonés (お待ちしております) | Misma alucinación, variante CJK | Detección de escritura no-latina dominante (>50% letras) → descarta | WAV real re-transcrito → `""`; 14/14 tests |
| 5 | Usuarios Pro (managed) recibían texto crudo sin limpieza | `llm_cleanup`/`transform` leían `GROQ_API_KEY` local; backend no tenía endpoint LLM | `core/llm_backend.py` (router BYOK-directo vs backend) + `POST /api/llm` en keylessflow-web (gated, modelo fijo, topes) | limpieza real BYOK e2e; ruteo unit-tested; backend typecheck OK |
| 6 | "dale enter" no hacía nada (código muerto) | `dictation_actions` nunca se importó | Cableado en `_on_transcription_done`: extrae la frase, pega, y presiona Enter SOLO si el paste aterrizó | 3 tests en `test_security_hardening.py` |
| 7 | Command Mode por voz (slots muertos) | Sin señal en el port Windows; hotkey reasignado | Código muerto eliminado; transforms viven en Alt+1..8 | `import main` OK |

## 2. Auditoría de seguridad (2026-07-11) y remediación (2026-07-18)

| Severidad | Hallazgo | Estado |
|---|---|---|
| **CRÍTICO** (descubierto después) | **20 WAVs de dictados personales trackeados en el repo PÚBLICO** (`audio/`, branch `windows-port`, tags v1.1.0/v1.1.1) | ✅ `audio/` gitignorado + `git rm --cached`; historia purgada con `git filter-repo` + force-push; backup pre-purga en `%LOCALAPPDATA%\KeyLessFlow\repo-backup-*.bundle`. ⚠ GitHub puede cachear objetos huérfanos: pedir purga a soporte GitHub (pendiente, manual) |
| MEDIO | Updater ejecutaba el instalador descargado sin verificar integridad | ✅ Verificación SHA256 contra asset `.sha256` del release (falla cerrado); `build.ps1` genera el checksum |
| MEDIO | Dashboard local exponía todas las transcripciones sin auth (proceso local o DNS-rebinding) | ✅ Token por-arranque + cookie HttpOnly + validación de Host; 4 tests |
| BAJO-MEDIO | Token Pro en plaintext en `auth.json` | ✅ DPAPI (`CryptProtectData`) con migración transparente; 2 tests. Residual: `.env` (Groq key BYOK) sigue plaintext — dotenv no soporta cifrado; documentado |
| BAJO | Dashboard carga Tailwind/fonts de CDN | ⏳ Pendiente (privacidad menor; local-only) |
| — | Limpio: sin secretos en git, SQL parametrizado, sin `shell=True`/`eval`/`pickle`, Flask solo loopback, `debug=False` | ✓ |

## 3. Backend (keylessflow-web)

- **`POST /api/llm`** — proxy chat blindado: auth (token kfd_/JWT), gate de
  plan/trial (`checkAccess` en `lib/quota.ts`, no consume cuota de audio),
  modelo fijo server-side, tope 20k chars de entrada / 2k tokens de salida.
- `/account`: botones de descarga **Windows + macOS** (antes solo Windows).
- Build de producción local pasa; `GROQ_API_KEY` confirmada en Vercel prod.
- Deploy: push a `main` → Vercel auto-deploy (git integration activa).
  Rollback: `vercel rollback` o dashboard → Promote previous.

## 4. Modelo de negocio (decidido 2026-07-11)

- **Managed**: trial 30d/8h → **Basic $8/mes** (mic+limpieza) / **Pro $14/mes**
  (+audio del sistema, transforms, grabaciones largas). Costo de servir ~$0.04/h
  → margen enorme (uso real de Luis: 841 dictados / 6h ≈ $0.24).
- **BYOK $49 único**: su propia Groq key, todas las features, página `/byok`.
- UNA org Groq paga (no farmear cuentas free: viola ToS, riesgo de ban masivo);
  pool de N keys solo como failover legítimo.

## 5. Pendientes (roadmap corto)

1. **Publicar release v1.2.0** en GitHub (installer + `.sha256`; la landing
   apunta a `/releases/latest`). Ojo: subir también el `.dmg` de Mac o el link
   Mac del /account dará 404.
2. **Deploy web** a producción (push `main`) + verificar `/api/llm` en vivo.
3. Pedir a GitHub soporte la purga de objetos huérfanos (WAVs).
4. Pool de Groq keys + panel `/admin/usage` + alertas de gasto (plan aprobado).
5. Página `/byok` + licencia Stripe $49.
6. UI de onboarding BYOK en la app (pegar `gsk_…` y validar).
7. PWA móvil (grabar → transcribir → copiar); nativa después.
8. macOS: validar port completo (hotkeys usan Ctrl/Alt igual; loopback requiere
   BlackHole).

## 6. Cómo verificar todo (rápido)

```powershell
.\venv\Scripts\python.exe tests\test_hotkey_routing.py        # 6/6
.\venv\Scripts\python.exe tests\test_hallucination_filter.py  # 14/14
.\venv\Scripts\python.exe tests\test_security_hardening.py    # 10/10
.\venv\Scripts\python.exe tools\usage_report.py               # contador de uso
.\build.ps1 -Installer                                        # build + sha256
```
