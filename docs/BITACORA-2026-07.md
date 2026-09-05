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

## 6b. Feature: Redactor con Biblioteca (2026-07-18)

Pedido de Luis: "dale una oración/idea y que ayude a redactarla, eligiendo
idioma". Implementado (spec cerrada → subagente Sonnet → re-verificado en vivo
por el orquestador):

- `core/redactor.py` — `redact(idea, language, tone, length)` vía
  `llm_backend.chat` (BYOK directo / managed por `/api/llm`, sin código nuevo
  de red). Idiomas: Auto/ES/EN/PT/FR · Tonos: Neutral/Formal/Casual/Email/
  WhatsApp/LinkedIn · Largo: Corto/Medio/Largo.
- `db/library.py` — `LibraryDB` (SQL parametrizado): guardar/buscar/cargar/
  borrar redacciones.
- `ui/hub_window.py` — página `LibraryPage` (índice 4; Ajustes pasó a 5).
- `tests/test_redactor_library.py` — 17 checks: CRUD, prompts, smoke UI
  offscreen, y **E2E real contra Groq** (ES→EN email; EN→ES whatsapp) con
  outputs impresos. Ejecutado 2 veces (agente + orquestador): ALL PASSED.

## 6. Cómo verificar todo (rápido)

```powershell
.\venv\Scripts\python.exe tests\test_hotkey_routing.py        # 6/6
.\venv\Scripts\python.exe tests\test_hallucination_filter.py  # 14/14
.\venv\Scripts\python.exe tests\test_security_hardening.py    # 10/10
.\venv\Scripts\python.exe tools\usage_report.py               # contador de uso
.\build.ps1 -Installer                                        # build + sha256
```

## 7. Goal 2026-09: "grabaciones que terminan en error" + retención + control de uso + comunidad

Disparador: Luis reportó errores al final de grabaciones (internet, "errores del
sistema"). Se revisó el camino completo grabar → transcribir → pegar.

### Bugs reales encontrados (con evidencia)
| # | Bug | Evidencia | Fix |
|---|---|---|---|
| 1 | **Groq retiró `llama-3.3-70b-versatile`** → limpieza LLM, transforms Alt+N y Redactor devolvían 404 (el "error del sistema") | `test_redactor_library` falló con `model_not_found`; `/models` ya no lo lista | `LLM_MODEL_CANDIDATES` con **fallback automático** en desktop (`core/llm_backend.py`) y backend (`/api/llm`). Hoy usa `openai/gpt-oss-120b` (verificado en vivo). La próxima rotación de Groq no rompe nada. |
| 2 | Reintentos solo en el cliente Groq, 2 intentos, solo timeouts; el backend managed no reintentaba nada | lectura de `transcriber_groq.py` | Política única en el router (`core/transcriber.py`): 4 intentos, backoff 1/2/4 s, solo para errores transitorios (offline/429/5xx). 401/402/413 fallan rápido. |
| 3 | Errores crudos al usuario ("APIConnectionError…") | mensajes en `main.py` | `core/errors.py` clasifica → mensaje en español accionable por tipo. |
| 4 | **Una transcripción fallida no dejaba fila en la DB** → el WAV quedaba huérfano y "reintentá desde el Hub" era imposible | `_transcribe_worker` except-path | Fila `status='failed'` visible en el Hub (tarjeta ámbar) con «Re-transcribir»; el **clic en la notificación** reintenta el último fallo; al recuperar, la fila sana a `ok`. |
| 5 | `prune_old_audio_paths()` existía pero **nadie lo llamaba** → `audio/` crecía sin límite | grep | `core/retention.py`: OK 7 días, fallidos 30 días, huérfanos 1 día, tope 500 MB (evicta OK más viejos, nunca fallidos). Corre a los 30 s del arranque y cada 24 h. Ajustable en settings. |
| 6 | Home del Hub mostraba atajos viejos (Cmd+Shift+H, Command Mode) | UI | Corregido. |

### Nuevo: control de uso en la app
`core/usage.py` + tarjeta "📊 Uso este mes" en Hub → Home: grabado / presupuesto
(editable, default 8 h) / te quedan / hoy / ≈ costo. Barra de color (azul <80 %,
ámbar <100 %, rojo). Tooltip del tray con el resumen. Avisos únicos al 80 % y 100 %.
Fuente de verdad: `transcriptions.db` (solo filas OK) — funciona igual en BYOK y managed.

### Nuevo: comunidad / workshop (web)
- `/comunidad`: nombre + email + WhatsApp → `/api/community` → aparecen descargas
  Win/Mac + guía de 3 pasos para la Groq key gratis.
- Captura de lead **sin migración**: crea un usuario de Supabase Auth (metadata con
  contacto) → cae en el mismo funnel (perfil + trial). Además intenta insertar en
  `community_leads` (SQL en `supabase/schema.sql`, tolerante si aún no existe).

### Tests: `tests/test_reliability.py` (9) — clasificación, retry/backoff, no-retry en 401,
filas fallidas + migración, política de retención (edad, fallidos, huérfanos, tope), uso.
Suite total: 49 + Redactor E2E real.

### Hallazgo crítico de producción (2026-09-04, durante el goal)
`/api/community` y `/api/waitlist` devolvían 500 `store_failed`. Log de Vercel:
`TypeError: Cannot convert argument to a ByteString because the character at
index 7 has a value of 65279` → un **BOM (U+FEFF)** justo después de `Bearer ` =
la `SUPABASE_SERVICE_ROLE_KEY` de producción tenía un carácter invisible al
inicio (típico de pegar desde un archivo guardado con `Out-File` en PowerShell,
que escribe UTF-8 con BOM). Consecuencia: **TODA llamada admin a Supabase en
prod estaba rota** (activación, transcripción managed, waitlist, comunidad),
probablemente desde que se configuró la variable. Fix: re-set desde `.env.local`
validando forma JWT y sin BOM, vía stdin (`printf '%s'`), redeploy, verificado
con `/api/waitlist` 200 + E2E de `/api/community` (crea y borra usuario).

Lección: `vercel env pull` devuelve **vacío** para variables marcadas sensibles —
no sirve para inspeccionar valores en prod (el "SITE_URL vacío" de julio fue un
falso positivo). Para setear variables usar siempre `printf '%s' | vercel env add`
(nunca `Out-File`/`echo` de PowerShell).

Herramientas nuevas: `tools/release_checksum.sh vX.Y.Z` (publica el `.sha256`
verificando round-trip — evita el checksum vacío que casi bloqueó los updates);
`build.ps1` tolera archivos bloqueados por OneDrive al limpiar `build/`.
