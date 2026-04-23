#!/usr/bin/env python3
"""SFlow — Voice-to-text desktop tool. Groq Whisper + optional local parakeet,
LLM cleanup, per-app tone, Command Mode, Liquid Glass pill."""

import os
import sys
import signal
import subprocess
import threading
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu,
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox,
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon, QPixmap, QAction

from ui.pill_widget import PillWidget
from ui.hub_window import HubWindow
from core.recorder import AudioRecorder
from core.transcriber import Transcriber
from core.transcriber_groq import GroqTranscriber
from core.hotkey import HotkeyListener
from core.paste import paste_text, paste_last_transcript, save_frontmost_app
from core.command_mode import CommandModeHandler, copy_selection
from core.transform import TransformHandler
from core.relaunch import relaunch_app
from core.logger import log, log_exc
from db.database import TranscriptionDB
from web.server import start_web_server
from config import LOGO_PATH, APP_DATA_DIR, AUDIO_DIR, get_setting


def _ensure_accessibility() -> bool:
    """Check Accessibility permission. Triggers macOS prompt on first call.

    After every .app rebuild the ad-hoc code signature changes, so macOS
    silently revokes Accessibility — keystroke paste then fails without an
    error. We detect that and open the Privacy panel so the user can re-add
    SFlow without hunting through System Settings.
    """
    trusted = True
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        trusted = bool(AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True}))
    except Exception:
        return True

    if not trusted:
        try:
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            ])
        except Exception:
            pass
        try:
            QMessageBox.warning(
                None,
                "SFlow necesita Accessibility",
                "Después de un rebuild macOS revoca el permiso. Abre System Settings → "
                "Privacy & Security → Accessibility y vuelve a marcar SFlow. "
                "Luego reinicia la app desde el menu del tray.",
            )
        except Exception:
            pass
    return trusted


_LAUNCH_AGENT_LABEL = "so.saasfactory.sflow"
_PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{_LAUNCH_AGENT_LABEL}.plist")


class FirstRunDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SFlow — Setup")
        self.setFixedWidth(420)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Ingresa tu Groq API Key para transcripciones:"))

        link = QLabel('<a href="https://console.groq.com/keys">Obtener gratis en console.groq.com/keys</a>')
        link.setOpenExternalLinks(True)
        layout.addWidget(link)

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("gsk_...")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.key_input)

        save_btn = QPushButton("Guardar y continuar")
        save_btn.clicked.connect(self._save_key)
        layout.addWidget(save_btn)

        self.setLayout(layout)

    def _save_key(self):
        key = self.key_input.text().strip()
        if not key.startswith("gsk_") or len(key) < 20:
            QMessageBox.warning(self, "Error", "La clave debe comenzar con 'gsk_' y tener al menos 20 caracteres.")
            return

        env_path = os.path.join(APP_DATA_DIR, ".env")
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        with open(env_path, "w") as f:
            f.write(f"GROQ_API_KEY={key}\n")

        os.environ["GROQ_API_KEY"] = key
        self.accept()


def _is_launch_at_login() -> bool:
    return os.path.exists(_PLIST_PATH)


def _set_launch_at_login(enabled: bool):
    if enabled:
        if getattr(sys, "frozen", False):
            exe = sys.executable
        else:
            exe = os.path.abspath(sys.argv[0])

        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCH_AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>"""
        os.makedirs(os.path.dirname(_PLIST_PATH), exist_ok=True)
        with open(_PLIST_PATH, "w") as f:
            f.write(plist)
        subprocess.run(["launchctl", "load", _PLIST_PATH], capture_output=True)
    else:
        if os.path.exists(_PLIST_PATH):
            subprocess.run(["launchctl", "unload", _PLIST_PATH], capture_output=True)
            os.remove(_PLIST_PATH)


def _setup_tray(app: QApplication, port: int, open_hub) -> QSystemTrayIcon:
    pixmap = QPixmap(LOGO_PATH)
    if pixmap.isNull():
        icon = QIcon()
    else:
        icon = QIcon(pixmap.scaled(22, 22, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    tray = QSystemTrayIcon(icon, app)

    menu = QMenu()

    status = QAction("SFlow — Activo", menu)
    status.setEnabled(False)
    menu.addAction(status)
    menu.addSeparator()

    hub_action = QAction("Abrir Hub  (⌘⇧H)", menu)
    hub_action.triggered.connect(open_hub)
    menu.addAction(hub_action)

    dashboard = QAction(f"Dashboard web (:{port})", menu)
    dashboard.triggered.connect(lambda: subprocess.run(["open", f"http://localhost:{port}"], capture_output=True))
    menu.addAction(dashboard)
    menu.addSeparator()

    login_action = QAction("Iniciar con macOS", menu)
    login_action.setCheckable(True)
    login_action.setChecked(_is_launch_at_login())
    login_action.toggled.connect(_set_launch_at_login)
    menu.addAction(login_action)
    menu.addSeparator()

    relaunch_action = QAction("Reiniciar SFlow", menu)
    relaunch_action.triggered.connect(relaunch_app)
    menu.addAction(relaunch_action)

    quit_action = QAction("Salir", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)

    # Also open hub on single left-click on the tray icon
    def _activate(reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            open_hub()
    tray.activated.connect(_activate)

    tray.setContextMenu(menu)
    tray.setToolTip("SFlow — Voice to Text")
    tray.show()
    return tray


class SFlowApp(QObject):
    """Main controller. Wires hotkey -> recorder -> transcriber -> clipboard,
    plus Command Mode side-channel."""

    transcription_done = pyqtSignal(str, float, str)  # text, duration, model_id
    transcription_error = pyqtSignal(str)
    command_done = pyqtSignal(str)
    command_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.recorder = AudioRecorder()
        self.transcriber = Transcriber()
        self.groq_raw = GroqTranscriber()  # raw STT for command mode (no LLM cleanup)
        self.command = CommandModeHandler()
        self.transform = TransformHandler()
        self.db = TranscriptionDB()
        self.hotkey = HotkeyListener()
        self.pill = PillWidget()
        self.hub = HubWindow(self.db)

        self._selected_text_snapshot = ""
        self._last_text: str = ""  # For "paste last transcript" hotkey

        self.pill.visualizer.set_audio_queue(self.recorder.audio_queue)

        # Signals — all QueuedConnection (pynput emits from its own thread)
        self.hotkey.pressed.connect(self._on_hotkey_pressed, Qt.ConnectionType.QueuedConnection)
        self.hotkey.released.connect(self._on_hotkey_released, Qt.ConnectionType.QueuedConnection)
        self.hotkey.command_pressed.connect(self._on_command_pressed, Qt.ConnectionType.QueuedConnection)
        self.hotkey.command_released.connect(self._on_command_released, Qt.ConnectionType.QueuedConnection)
        self.hotkey.hub_requested.connect(self._on_hub_requested, Qt.ConnectionType.QueuedConnection)
        self.hotkey.paste_last_requested.connect(self._on_paste_last, Qt.ConnectionType.QueuedConnection)
        self.hotkey.transform_triggered.connect(self._on_transform, Qt.ConnectionType.QueuedConnection)

        self.transcription_done.connect(self._on_transcription_done, Qt.ConnectionType.QueuedConnection)
        self.transcription_error.connect(self._on_transcription_error, Qt.ConnectionType.QueuedConnection)
        self.command_done.connect(self._on_command_done, Qt.ConnectionType.QueuedConnection)
        self.command_error.connect(self._on_transcription_error, Qt.ConnectionType.QueuedConnection)

    def start(self):
        self.hotkey.start()
        self.pill.show()
        self.pill.set_state(PillWidget.STATE_IDLE)

    # ------- Regular transcription flow -------
    @pyqtSlot()
    def _on_hotkey_pressed(self):
        save_frontmost_app()
        self.recorder.start()
        self.pill.set_state(PillWidget.STATE_RECORDING)

    @pyqtSlot()
    def _on_hotkey_released(self):
        duration = self.recorder.stop()
        self.pill.set_state(PillWidget.STATE_PROCESSING)

        if duration < 0.3:
            self.pill.set_state(PillWidget.STATE_IDLE)
            return

        wav_buffer = self.recorder.get_wav_buffer()
        recording_duration = self.recorder.get_duration()

        # Persist WAV so the user can re-transcribe from the Hub later
        audio_path = None
        if get_setting("save_audio_for_retry", True):
            import uuid
            audio_path = os.path.join(AUDIO_DIR, f"{uuid.uuid4().hex}.wav")
            try:
                self.recorder.save_wav_to(audio_path)
            except Exception as e:
                print(f"audio save failed: {e}")
                audio_path = None

        threading.Thread(
            target=self._transcribe_worker,
            args=(wav_buffer, recording_duration, audio_path),
            daemon=True,
        ).start()

    def _transcribe_worker(self, wav_buffer, duration, audio_path=None):
        log(f"transcribe start: duration={duration:.2f}s, audio_path={audio_path}")
        try:
            text, model_id = self.transcriber.transcribe(wav_buffer)
            log(f"transcribe ok: model={model_id}, chars={len(text) if text else 0}, text[:60]={(text or '')[:60]!r}")
            if text:
                self._pending_audio_path = audio_path
                self.transcription_done.emit(text, duration, model_id)
            else:
                log("transcribe returned empty text", level="WARN")
                self.transcription_error.emit("No speech detected")
        except Exception as e:
            log_exc("transcribe FAILED", e)
            self.transcription_error.emit(str(e))

    @pyqtSlot(str, float, str)
    def _on_transcription_done(self, text: str, duration: float, model_id: str):
        log(f"transcription_done: chars={len(text)}, text[:60]={text[:60]!r}")
        final_text = text
        try:
            paste_text(final_text)
            log("paste ok")
        except Exception as e:
            log_exc("paste FAILED", e)
        self._last_text = final_text
        audio_path = getattr(self, "_pending_audio_path", None)
        self._pending_audio_path = None
        try:
            self.db.insert(
                text=final_text, duration_seconds=duration,
                model=model_id, audio_path=audio_path,
            )
        except Exception as e:
            log_exc("db.insert FAILED", e)
        self.pill.set_state(PillWidget.STATE_DONE)

    @pyqtSlot()
    def _on_hub_requested(self):
        # Temporarily activate the app so the Hub can receive keyboard focus
        # even though we're in accessory (menu-bar-only) policy.
        try:
            import AppKit
            AppKit.NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            pass
        self.hub.show()
        self.hub.raise_()
        self.hub.activateWindow()

    @pyqtSlot()
    def _on_paste_last(self):
        # Prefer the in-memory last; fall back to DB most recent
        text = self._last_text
        if not text:
            rows = self.db.get_recent(limit=1)
            if rows:
                text = rows[0].get("text") or ""
        if text:
            paste_last_transcript(text)

    @pyqtSlot(int)
    def _on_transform(self, index: int):
        """Option+N — transform selected text via Llama with the Nth custom prompt."""
        save_frontmost_app()
        selection = copy_selection()
        if not selection:
            self.pill.set_state(PillWidget.STATE_ERROR)
            return
        self.pill.set_state(PillWidget.STATE_PROCESSING)

        def worker():
            try:
                result = self.transform.run(index, selection)
                self.command_done.emit(result)
            except Exception as e:
                self.command_error.emit(str(e))
        threading.Thread(target=worker, daemon=True).start()

    @pyqtSlot(str)
    def _on_transcription_error(self, error: str):
        log(f"ERROR state: {error}", level="ERROR")
        self.pill.set_state(PillWidget.STATE_ERROR)

    # ------- Command Mode flow -------
    @pyqtSlot()
    def _on_command_pressed(self):
        save_frontmost_app()
        # Snapshot selection BEFORE we grab focus for recording
        self._selected_text_snapshot = copy_selection()
        self.recorder.start()
        self.pill.set_state(PillWidget.STATE_RECORDING)

    @pyqtSlot()
    def _on_command_released(self):
        duration = self.recorder.stop()
        self.pill.set_state(PillWidget.STATE_PROCESSING)

        if duration < 0.3:
            self.pill.set_state(PillWidget.STATE_IDLE)
            self._selected_text_snapshot = ""
            return

        wav_buffer = self.recorder.get_wav_buffer()
        selection = self._selected_text_snapshot
        self._selected_text_snapshot = ""
        threading.Thread(
            target=self._command_worker,
            args=(wav_buffer, selection, duration),
            daemon=True,
        ).start()

    def _command_worker(self, wav_buffer, selection, duration):
        try:
            # Command Mode always uses Groq (fast cloud STT) — bypass local backend
            voice = self.groq_raw.transcribe(wav_buffer)
            if not voice:
                self.command_error.emit("No voice command detected")
                return
            result = self.command.transform(voice, selection)
            # Persist both voice command and result for history
            try:
                self.db.insert(
                    text=f"[CMD] {voice} → {result[:200]}",
                    duration_seconds=duration,
                    model="command-mode",
                )
            except Exception:
                pass
            self.command_done.emit(result)
        except Exception as e:
            self.command_error.emit(str(e))

    @pyqtSlot(str)
    def _on_command_done(self, result: str):
        paste_text(result)
        self._last_text = result
        self.pill.set_state(PillWidget.STATE_DONE)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SFlow")
    app.setQuitOnLastWindowClosed(False)

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        dialog = FirstRunDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)

    try:
        import AppKit
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    except Exception:
        pass

    port = start_web_server()
    _ensure_accessibility()

    sflow = SFlowApp()
    sflow.start()

    def open_hub():
        try:
            import AppKit
            AppKit.NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            pass
        sflow.hub.show()
        sflow.hub.raise_()
        sflow.hub.activateWindow()

    tray = _setup_tray(app, port, open_hub)  # noqa: F841

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
