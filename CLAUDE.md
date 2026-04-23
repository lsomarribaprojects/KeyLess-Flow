# CLAUDE.md — SFlow Development Instructions

## What is SFlow?

SFlow is a macOS voice-to-text desktop tool that replaces Wispr Flow ($15/month). It captures audio via global hotkeys, transcribes using Groq Whisper API (~$0.02/hour), and auto-pastes text wherever the cursor is. It includes a floating pill UI overlay, real-time audio visualization, SQLite history, and a web dashboard.

## Quick Start (Dev Mode)

```bash
# 1. Install system dependency
brew install portaudio

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Edit .env and add your GROQ_API_KEY (get one at https://console.groq.com/keys)

# 5. Run
python3 main.py
```

## Build Desktop App (.app bundle)

```bash
# Recommended: full install dance (build + ditto + kill old proc + relaunch + open Accessibility panel)
bash install.sh

# Or just build the bundle without installing
bash build.sh
```

`install.sh` exists because `build.sh` alone leaves you with a broken paste —
see "Critical: ad-hoc rebuild → silent Accessibility revocation" below.

The .app bundle is self-contained (~118MB, parakeet/mlx-whisper excluded).
No Python, no venv, no terminal needed. On first launch, if no API key exists
in `~/Library/Application Support/SFlow/.env`, a dialog asks for it. The app
lives in the menu bar (no Dock icon).

### Local backend (opt-in, Apple Silicon)
```bash
source venv/bin/activate
pip install mlx-whisper
# Hub → Ajustes → Backend → mlx-whisper
```
First transcription downloads the model (~244MB for `whisper-small-mlx`).

### Build Requirements
- Python 3.12+ with venv
- PyInstaller (installed automatically by build.sh)
- portaudio (`brew install portaudio`)

## macOS Permissions Required

- **Accessibility**: System Settings → Privacy & Security → Accessibility → add your Terminal/IDE
- **Microphone**: Automatically requested on first use
- **Input Monitoring**: May be required for pynput — add your Terminal/IDE

## Benchmark: local model selection (M-series, Spanish voice, real samples)

| Model | short 3s | medium 10s | long 30s | Notes |
|---|---|---|---|---|
| whisper-tiny-mlx | 0.31s 🥇 | 3.13s | 10.91s | fastest for short clips, weaker punctuation |
| whisper-base-mlx | 2.57s | 5.09s | 6.34s | ok |
| **whisper-small-mlx** | 1.11s | **1.05s** 🥇 | **3.92s** 🥇 | **default** — best overall |
| whisper-large-v3-turbo | 4.68s | 5.11s | 10.70s | slower than cloud Groq |
| parakeet-tdt-0.6b-v3 | 2.87s | 4.21s | 7.70s | promising but slow in practice |
| faster-whisper (CT2) | — | — | — | not tested: poor on ARM |

Bench script: `/tmp/sflow_bench/bench.py` (generates voice via `say -v Paulina`,
16kHz mono WAV, warms up each model, records hot inference time).

## Project Structure (v2.5 — with Hub + CGEvent paste + benchmarked local)

```
sflow/
├── main.py                      # Tray + controller (regular + command-mode flows)
├── config.py                    # Constants + runtime settings (settings.json)
├── sflow.spec                   # PyInstaller spec
├── build.sh                     # icns → PyInstaller → sign
├── ui/
│   ├── pill_widget.py           # NSPanel + Liquid Glass (NSVisualEffectView)
│   ├── audio_visualizer.py      # FFT + spring physics 60Hz
│   └── settings_dialog.py       # QDialog for all toggles
├── core/
│   ├── recorder.py              # sounddevice capture
│   ├── transcriber.py           # Router (backend → commands → LLM cleanup)
│   ├── transcriber_groq.py      # Groq Whisper Large v3 Turbo
│   ├── transcriber_local.py     # parakeet-mlx (optional, offline, Apple Silicon)
│   ├── llm_cleanup.py           # Groq Llama 3.1 8B instant — filler removal + tone
│   ├── context.py               # NSWorkspace frontmost app → tone profile
│   ├── dictionary.py            # Personal vocab → Whisper prompt hint
│   ├── smart_commands.py        # "nueva línea" → \n, "coma" → ", ", etc.
│   ├── command_mode.py          # Select+speak+LLM transform flow
│   ├── hotkey.py                # 4 modes (hold, double-tap, command, mouse)
│   └── clipboard.py             # Focus save/restore + streaming paste
├── db/database.py               # SQLite history (model column tracks backend used)
├── web/server.py                # Flask dashboard localhost:5678
└── ~/Library/Application Support/SFlow/
    ├── .env                     # GROQ_API_KEY
    ├── settings.json            # User toggles (generated on save)
    ├── dictionary.txt           # Personal vocabulary (one term per line)
    └── transcriptions.db        # History
```

## Hotkeys (v2.5)

| Combo | Mode |
|---|---|
| Ctrl+Alt hold | Regular recording |
| Double-tap Ctrl, tap again to stop | Hands-free |
| Ctrl+Shift hold | **Command Mode** — transforms selected text via LLM |
| Mouse button (middle/Mouse4/Mouse5) | Regular recording (opt-in, Settings) |
| Cmd+Shift+H | Open Hub (history + dictionary + settings) |
| Cmd+Ctrl+V | Paste Last Transcript (Wispr Flow convention) |
| Trailing "press enter" / "dale enter" | Auto-press Enter after paste |

## Architecture & Data Flow (v2)

### Regular transcription
```
Hotkey Press (pynput thread)
  → [QueuedConnection] → save_frontmost_app() + recorder.start()
  → pill.set_state(RECORDING) → FFT visualizer at 60fps

Hotkey Release
  → [QueuedConnection] → recorder.stop()
  → pill.set_state(PROCESSING)
  → background Thread: Transcriber.transcribe(wav)
      → backend = GroqTranscriber OR LocalTranscriber (setting-driven)
      → vocabulary = dictionary.as_whisper_prompt()  (Whisper `prompt=` hint)
      → raw = backend.transcribe(wav, vocabulary)
      → raw = smart_commands.apply(raw)              (regex pass)
      → tone = context.tone_for_active_app()
      → final = llm_cleanup.clean(raw, tone)         (Llama 3.1 8B instant, ~150ms)
      → (final, model_id)
  → [QueuedConnection] → paste_text() + db.insert(model=model_id) + DONE
```

### Command Mode
```
Ctrl+Shift Press
  → save_frontmost_app() + copy_selection() (Cmd+C → clipboard diff)
  → recorder.start() + pill.RECORDING

Release
  → recorder.stop() + PROCESSING
  → GroqTranscriber (raw STT, no cleanup) → voice_command
  → CommandModeHandler.transform(voice, selection) → Llama transforms text
  → paste_text(result) → replaces selection
```

## Critical Implementation Details

### 1. Qt Signal Threading (MUST use QueuedConnection)
pynput emits signals from its own thread. Both QObjects live in the main thread, so Qt's `AutoConnection` incorrectly chooses `DirectConnection`. But since `emit()` comes from pynput's thread, UI modifications happen on the wrong thread — undefined behavior on macOS. **Always use explicit `Qt.ConnectionType.QueuedConnection`.**

### 2. macOS Floating Window (MUST use PyObjC)
Qt's `WindowDoesNotAcceptFocus` flag doesn't work properly on macOS. The pill must use native Cocoa APIs via PyObjC to float without stealing focus:
```python
import AppKit, objc
from ctypes import c_void_p

ns_view = objc.objc_object(c_void_p=c_void_p(widget.winId().__int__()))
ns_window = ns_view.window()
ns_window.setLevel_(AppKit.NSFloatingWindowLevel)
ns_window.setStyleMask_(ns_window.styleMask() | AppKit.NSWindowStyleMaskNonactivatingPanel)
ns_window.setHidesOnDeactivate_(False)
ns_window.setCollectionBehavior_(
    AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
    | AppKit.NSWindowCollectionBehaviorStationary
    | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
)
```
This is the same approach used by Spotlight and Wispr Flow itself.

### 3. Auto-Paste (MUST use native AppleScript, not pyautogui)
pyautogui is unreliable on macOS when modifier keys were recently released. Use:
- `save_frontmost_app()` before recording (via AppleScript)
- `pbcopy` to copy text to clipboard
- AppleScript to restore focus to saved app
- AppleScript `keystroke "v" using command down` to paste

### 4. Audio Pipeline (thread-safe)
sounddevice callback runs in audio thread — NEVER touch Qt widgets from it. Use `queue.Queue` as bridge:
- Callback → puts audio chunks in queue
- QTimer on main thread → polls queue → updates visualizer

### 5. Short Recording Filter
Recordings under 0.3 seconds are accidental taps — skip transcription and return to idle.

### 6. Bundle vs Dev Mode (config.py)
`config.py` detects `sys.frozen` to switch between dev and .app bundle:
- **Dev mode**: assets and data live in the project root directory
- **Bundle mode**: read-only assets (logo) come from `sys._MEIPASS`, writable data (DB, .env) goes to `~/Library/Application Support/SFlow/`

### 7. Desktop App Features (main.py)
- **System Tray**: QSystemTrayIcon in menu bar with dashboard link, "Start with macOS" toggle, quit
- **First-Run Dialog**: If GROQ_API_KEY is empty, shows a QDialog to enter it (saves to Application Support)
- **Launch at Login**: Creates/removes a LaunchAgent plist in `~/Library/LaunchAgents/`
- **Hide from Dock**: `NSApplicationActivationPolicyAccessory` via PyObjC (MUST be set AFTER first-run dialog)

### 8. Port Selection (web/server.py)
Default port is 5678 (not 5000 which conflicts with AirPlay on macOS 12+). Auto-scans for free port.

### 9. Building the .app (IMPORTANT)
- Use `ditto` (not `cp -r`) to copy .app to /Applications — `cp -r` corrupts bundle metadata causing segfaults
- The .icns is auto-generated from logo.png by build.sh if missing
- Ad-hoc signing (`codesign --force --deep --sign -`) is sufficient for personal use
- Remove quarantine after install: `xattr -cr /Applications/SFlow.app`

### 10. Critical: ad-hoc rebuild → silent Accessibility revocation

**Symptom:** After `ditto` of a rebuilt bundle, dictation works (transcription
saves to DB, log shows `paste ok`) but the text never appears in the target
app. Keystrokes are being blocked by the OS silently.

**Root cause:** Each `pyinstaller` run produces a new binary hash. Ad-hoc
signatures change per-build. macOS's TCC database tracks Accessibility
permission by binary hash → it silently revokes trust when the hash changes.
CGEventPost succeeds (no error) but the OS drops the event before it reaches
other apps. The running process (if any) also keeps executing the old
in-memory code while its on-disk binary mismatches, compounding confusion.

**Fix (operational):**
1. Use `install.sh`, NOT `build.sh` + manual `ditto`. It:
   - Builds + dittos
   - Kills any running SFlow (`pgrep -f /Applications/SFlow.app`)
   - Launches fresh instance via `open -n`
   - Opens System Settings → Privacy & Security → Accessibility
2. In the Accessibility panel: remove SFlow (-), add it back (+) pointing at
   /Applications/SFlow.app. Repeat in Input Monitoring.
3. Confirm with a short dictation — text should appear in the frontmost app.

**Fix (permanent, $99/yr):** Enroll in Apple Developer Program and sign with
a Developer ID certificate. Persistent team identifier → TCC preserves trust
across rebuilds. Not worth it for personal use; accept the manual re-approve.

**Detection in code:** `main._ensure_accessibility()` catches this at startup
via `AXIsProcessTrustedWithOptions` and auto-opens the Privacy panel plus a
QMessageBox explaining the fix. This covers the "user rebuilt and the new
process can't paste" case, but NOT the "old process still running with now-
invalidated binary" case — that one requires killing the process, which is
what `install.sh` does.

## Customization

### Hotkeys
Edit `core/hotkey.py`:
- **Hold mode**: Currently Ctrl+Shift. Change `is_ctrl`/`is_shift` checks.
- **Hands-free mode**: Currently double-tap Ctrl within 400ms. Change `DOUBLE_TAP_INTERVAL` in config.py.

### UI Dimensions
Edit `config.py`:
- `PILL_WIDTH_IDLE` (34) — width when just showing logo
- `PILL_WIDTH_RECORDING` (120) — width during recording with bars
- `PILL_WIDTH_STATUS` (52) — width for checkmark/spinner/error
- `PILL_HEIGHT` (34) — height of pill
- `PILL_MARGIN_BOTTOM` (14) — distance from bottom of screen

### Audio
Edit `config.py`:
- `SAMPLE_RATE` (16000) — 16kHz is optimal for speech
- `NUM_BARS` (8) — number of visualizer bars
- `BAR_GAIN` (6.0) — sensitivity of bars
- `BAR_DECAY` (0.80) — how quickly bars fall

## Building from Scratch

If you want to rebuild this project from scratch using Claude, copy the `PRP.md` file and give it to Claude with the instruction: "Build this project following the PRP phases. Execute all phases sequentially, validating each one before moving to the next."

The PRP contains all the architectural decisions, gotchas, and anti-patterns discovered during development. It serves as a complete blueprint.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Pill doesn't appear | Check Accessibility permissions for your terminal |
| Pill appears but steals focus | Verify PyObjC is installed: `python3 -c "import AppKit"` |
| Audio not captured | Check Microphone permissions + verify portaudio: `brew list portaudio` |
| Paste doesn't work | Grant Accessibility permission to terminal; check `save_frontmost_app` |
| Ctrl+C doesn't kill the process | This is handled by `signal.signal(signal.SIGINT, signal.SIG_DFL)` in main.py |
| Short taps trigger transcription | Adjust the 0.3s threshold in `main.py` `_on_hotkey_released` |
| Web dashboard not loading | Port auto-selects from 5678. Check: `lsof -i :5678` |
| .app crashes on launch (segfault) | Was copied with `cp -r` instead of `ditto`. Reinstall with `ditto` |
| .app blocked by macOS | Run `xattr -cr /Applications/SFlow.app` to remove quarantine |
| First-run dialog invisible | Bug if NSApplicationActivationPolicyAccessory is set before dialog. Already fixed |
| Transcription hangs forever | API timeout is 10s. Check your GROQ_API_KEY is valid |
