#!/usr/bin/env python3
"""SFlow — Voice-to-text desktop tool. Groq Whisper + optional local parakeet,
LLM cleanup, per-app tone, Command Mode, Liquid Glass pill."""

import os
import sys
import signal
import subprocess
import threading
import traceback
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu,
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox,
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon, QPixmap, QAction

from ui.pill_widget import PillWidget
from ui.hub_window import HubWindow
from ui.red_dot_indicator import RedDotIndicator
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

    Windows: no equivalent permission needed — returns True immediately.
    """
    if sys.platform == "win32":
        return True

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
                "KeyLess by Sinsajo necesita Accessibility",
                "Después de un rebuild macOS revoca el permiso. Abre System Settings → "
                "Privacy & Security → Accessibility y vuelve a marcar KeyLess by Sinsajo. "
                "Luego reinicia la app desde el menu del tray.",
            )
        except Exception:
            pass
    return trusted


_LAUNCH_AGENT_LABEL = "com.sinsajocreators.keylessflow"
_PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{_LAUNCH_AGENT_LABEL}.plist")


class FirstRunDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KeyLess by Sinsajo — Setup")
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
    if sys.platform == "win32":
        from core.platform._windows import is_launch_at_login as _impl
        return _impl()
    return os.path.exists(_PLIST_PATH)


def _set_launch_at_login(enabled: bool):
    if sys.platform == "win32":
        from core.platform._windows import set_launch_at_login as _impl
        return _impl(enabled)
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


def _handle_pro_connect():
    """Tray menu → 'Conectar con cuenta Pro…' → paste activation code from /account."""
    from PyQt6.QtWidgets import QInputDialog, QMessageBox
    from core import auth as pro_auth

    code, ok = QInputDialog.getText(
        None,
        "Conectar con KeyLess by Sinsajo Pro",
        "Pega tu código de activación (formato KF-XXXX-XXXX-XXXX).\n"
        "Lo encuentras en keylessflow.app/account después de suscribirte.",
    )
    if not ok or not code.strip():
        return

    result = pro_auth.activate(code)
    if result.get("ok"):
        record = result.get("record") or {}
        plan = (record.get("plan") or "pro").capitalize()
        email = record.get("email") or "tu cuenta"
        QMessageBox.information(
            None,
            "¡Conectado!",
            f"Plan {plan} activo en {email}.\n\n"
            "A partir de ahora todas las transcripciones pasan por nuestro "
            "backend — ya no necesitas tu Groq key.\n\n"
            "Reinicia KeyLess by Sinsajo para aplicar el cambio.",
        )
    else:
        err = result.get("error") or "Error desconocido"
        upgrade = result.get("upgrade_url") or ""
        msg = err
        if upgrade:
            msg += f"\n\nSuscríbete en: {upgrade}"
        QMessageBox.warning(None, "No se pudo conectar", msg)


def _handle_pro_signout():
    """Tray menu → 'Cerrar sesión Pro'. Falls back to BYOK on restart."""
    from PyQt6.QtWidgets import QMessageBox
    from core import auth as pro_auth

    confirm = QMessageBox.question(
        None,
        "Cerrar sesión Pro",
        "Vas a volver al modo Free (BYOK). Necesitarás tu propia Groq API key.\n\n"
        "¿Continuar?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if confirm != QMessageBox.StandardButton.Yes:
        return
    pro_auth.sign_out()
    QMessageBox.information(
        None,
        "Sesión cerrada",
        "Listo. Reinicia KeyLess by Sinsajo para volver a modo Free.",
    )


def _setup_tray(app: QApplication, port: int, open_hub) -> QSystemTrayIcon:
    pixmap = QPixmap(LOGO_PATH)
    if pixmap.isNull():
        icon = QIcon()
    else:
        icon = QIcon(pixmap.scaled(22, 22, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    tray = QSystemTrayIcon(icon, app)

    menu = QMenu()

    status = QAction("KeyLess by Sinsajo — Activo", menu)
    status.setEnabled(False)
    menu.addAction(status)
    menu.addSeparator()

    hub_action = QAction("Abrir Hub  (⌘⇧H)", menu)
    hub_action.triggered.connect(open_hub)
    menu.addAction(hub_action)

    dashboard = QAction(f"Dashboard web (:{port})", menu)
    dashboard.triggered.connect(
        lambda: __import__("webbrowser").open(f"http://localhost:{port}")
    )
    menu.addAction(dashboard)
    menu.addSeparator()

    # ---- Pro account section -----------------------------------------------
    from core import auth as pro_auth
    pro_summary = pro_auth.get_account_summary()
    if pro_summary:
        plan_label = (pro_summary.get("plan") or "pro").capitalize()
        email = pro_summary.get("email") or ""
        status_text = f"Pro: {plan_label}"
        if email:
            status_text += f" — {email}"
        pro_status_action = QAction(status_text, menu)
        pro_status_action.setEnabled(False)
        menu.addAction(pro_status_action)

        signout_action = QAction("Cerrar sesión Pro", menu)
        signout_action.triggered.connect(_handle_pro_signout)
        menu.addAction(signout_action)
    else:
        # Upsell first (drives revenue), then the "I already paid" path.
        from config import KEYLESSFLOW_API_URL
        subscribe_action = QAction("Suscribirse a Pro $9.99/mo…", menu)
        subscribe_action.triggered.connect(
            lambda: __import__("webbrowser").open(
                f"{KEYLESSFLOW_API_URL.rstrip('/')}/#precios"
            )
        )
        menu.addAction(subscribe_action)

        connect_action = QAction("Conectar con cuenta Pro…", menu)
        connect_action.triggered.connect(_handle_pro_connect)
        menu.addAction(connect_action)

    menu.addSeparator()

    login_label = "Iniciar con Windows" if sys.platform == "win32" else "Iniciar con macOS"
    login_action = QAction(login_label, menu)
    login_action.setCheckable(True)
    login_action.setChecked(_is_launch_at_login())
    login_action.toggled.connect(_set_launch_at_login)
    menu.addAction(login_action)
    menu.addSeparator()

    relaunch_action = QAction("Reiniciar KeyLess by Sinsajo", menu)
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
    tray.setToolTip("KeyLess by Sinsajo — Voice to Text")
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
        self.red_dot = RedDotIndicator()
        self.hub = HubWindow(self.db)

        self._selected_text_snapshot = ""
        self._last_text: str = ""  # For "paste last transcript" hotkey

        # Set by main() after the tray icon exists; called as notify(msg) to
        # surface user-facing toasts (e.g. when auto-paste landed in the wrong
        # window because the user switched apps during transcription).
        self.notify = lambda msg: None  # default no-op

        self.pill.visualizer.set_audio_queue(self.recorder.audio_queue)

        # Signals — all QueuedConnection (pynput emits from its own thread)
        self.hotkey.pressed.connect(self._on_hotkey_pressed, Qt.ConnectionType.QueuedConnection)
        self.hotkey.released.connect(self._on_hotkey_released, Qt.ConnectionType.QueuedConnection)
        self.hotkey.transform_triggered.connect(self._on_transform, Qt.ConnectionType.QueuedConnection)
        self.hotkey.hands_free_started.connect(self.red_dot.start, Qt.ConnectionType.QueuedConnection)
        self.hotkey.hands_free_stopped.connect(self.red_dot.stop, Qt.ConnectionType.QueuedConnection)

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
        try:
            save_frontmost_app()
            self.recorder.start()
            self.pill.set_state(PillWidget.STATE_RECORDING)
        except Exception as e:
            log_exc("hotkey_pressed crashed (suppressed)", e)
            try:
                self.pill.set_state(PillWidget.STATE_ERROR)
            except Exception:
                pass

    @pyqtSlot()
    def _on_hotkey_released(self):
        try:
            duration = self.recorder.stop()
            self.pill.set_state(PillWidget.STATE_PROCESSING)

            if duration < 0.3:
                self.pill.set_state(PillWidget.STATE_IDLE)
                return

            # MP3 for upload (~8x smaller than WAV) — Groq decodes server-side.
            upload_buffer = self.recorder.get_mp3_buffer()
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
                args=(upload_buffer, recording_duration, audio_path),
                daemon=True,
            ).start()
        except Exception as e:
            log_exc("hotkey_released crashed (suppressed)", e)
            try:
                self.pill.set_state(PillWidget.STATE_ERROR)
            except Exception:
                pass

    def _transcribe_worker(self, upload_buffer, duration, audio_path=None):
        size_kb = len(upload_buffer.getvalue()) / 1024 if hasattr(upload_buffer, "getvalue") else 0
        log(f"transcribe start: duration={duration:.2f}s, upload_size={size_kb:.1f}KB, audio_path={audio_path}")
        try:
            text, model_id = self.transcriber.transcribe(upload_buffer)
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
            status = paste_text(final_text)
            log(f"paste status={status}")
            # If the foreground window changed while we were transcribing, the
            # OS may have blocked our SetForegroundWindow → auto-paste landed
            # in the wrong place. Tell the user the text is in the clipboard
            # so they can Ctrl+V manually wherever they want it.
            if status == "focus_lost":
                preview = final_text[:60] + ("…" if len(final_text) > 60 else "")
                self.notify(f"Texto en clipboard — Ctrl+V para pegar:\n{preview}")
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


# Module-level: keeps the socket alive for the lifetime of the process.
# Garbage collection would close it and re-allow a second instance — hence module scope.
_SINGLE_INSTANCE_SOCKET = None
_SINGLE_INSTANCE_PORT = 56789  # arbitrary high port; safe to change if it ever collides


def _ensure_single_instance() -> bool:
    """Returns True if this is the only instance; False if another is already running.

    Strategy: bind a TCP socket on 127.0.0.1:<known-port>. Bind succeeds only
    for the first process; subsequent attempts fail with EADDRINUSE. Cross-
    platform (works on Win/Mac/Linux), no file locks to clean up on crash,
    no PID-file staleness to worry about. The kernel releases the port the
    moment the process dies.
    """
    global _SINGLE_INSTANCE_SOCKET
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        s.listen(1)
        _SINGLE_INSTANCE_SOCKET = s  # keep ref so socket isn't GC-closed
        return True
    except OSError:
        s.close()
        return False


def _install_safe_excepthook():
    """PyQt6 6.5+ aborts the process when a Qt slot raises an unhandled
    exception (QMessageLogger::fatal). Install a hook that logs instead of
    killing the app — defensive last-resort safety net."""
    def _hook(exc_type, exc_value, exc_tb):
        try:
            tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            log(f"unhandled exception (suppressed by excepthook)\n{tb}", level="ERROR")
        except Exception:
            pass
    sys.excepthook = _hook
    try:
        threading.excepthook = lambda args: _hook(args.exc_type, args.exc_value, args.exc_traceback)
    except Exception:
        pass


def main():
    _install_safe_excepthook()

    app = QApplication(sys.argv)
    app.setApplicationName("KeyLess by Sinsajo")
    app.setQuitOnLastWindowClosed(False)

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # ---- Single-instance guard --------------------------------------------
    # Prevent the "double paste" bug where two copies of the app each react
    # to the global hotkey. Without this, the installer's "Iniciar con
    # Windows" + manual launch can spawn two trays at once.
    if not _ensure_single_instance():
        QMessageBox.information(
            None,
            "KeyLess by Sinsajo",
            "KeyLess by Sinsajo ya está corriendo. Revisa el ícono en la bandeja "
            "del sistema (junto al reloj).",
        )
        sys.exit(0)

    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        dialog = FirstRunDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)

    if sys.platform == "darwin":
        try:
            import AppKit
            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        except Exception:
            pass
    # Windows: QSystemTrayIcon already keeps the app dock-less.

    port = start_web_server()
    _ensure_accessibility()

    sflow = SFlowApp()
    sflow.start()

    def open_hub():
        if sys.platform == "darwin":
            try:
                import AppKit
                AppKit.NSApp.activateIgnoringOtherApps_(True)
            except Exception:
                pass
        sflow.hub.show()
        sflow.hub.raise_()
        sflow.hub.activateWindow()

    tray = _setup_tray(app, port, open_hub)

    # Wire SFlowApp.notify() to the tray's balloon notification so transcription
    # paste-failures (and other user-facing events) can surface quietly.
    def _notify(msg: str):
        try:
            tray.showMessage(
                "KeyLess by Sinsajo",
                msg,
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
        except Exception:
            pass
    sflow.notify = _notify

    # ---- Auto-updater -----------------------------------------------------
    # Polls GitHub Releases ~10 s after launch (don't compete with audio init).
    # Subsequent checks are rate-limited to once per 24 h via settings.
    from core.updater import UpdateChecker
    updater = UpdateChecker()

    def _on_update_available(info):
        # Tray balloon — user clicks once to start the download+install dance.
        try:
            tray.showMessage(
                "Nueva versión disponible",
                f"KeyLess by Sinsajo {info.version} listo para instalar. "
                f"Click el ícono del tray → 'Actualizar a {info.version}'.",
                QSystemTrayIcon.MessageIcon.Information,
                6000,
            )
        except Exception:
            pass
        # Inject a dynamic menu action so the user can trigger the update on demand.
        update_action = QAction(f"Actualizar a {info.version}…", tray.contextMenu())
        def _do_update():
            try:
                tray.showMessage(
                    "Descargando actualización",
                    "Te avisamos cuando termine. La app se reiniciará sola.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
            except Exception:
                pass
            updater.download_and_run_async(info, on_done=app.quit)
        update_action.triggered.connect(_do_update)
        # Insert at the top of the tray menu so it's obvious.
        existing_menu = tray.contextMenu()
        if existing_menu.actions():
            existing_menu.insertAction(existing_menu.actions()[0], update_action)
            existing_menu.insertSeparator(existing_menu.actions()[1])
        else:
            existing_menu.addAction(update_action)

    updater.update_available.connect(_on_update_available, Qt.ConnectionType.QueuedConnection)
    updater.error.connect(
        lambda msg: log(f"updater error (suppressed): {msg}", level="WARN"),
        Qt.ConnectionType.QueuedConnection,
    )

    from PyQt6.QtCore import QTimer
    QTimer.singleShot(10_000, lambda: updater.check_async(force=False))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
