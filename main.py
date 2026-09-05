#!/usr/bin/env python3
"""SFlow — Voice-to-text desktop tool. Groq Whisper + optional local parakeet,
LLM cleanup, per-app tone, Command Mode, Liquid Glass pill."""

import os
import sys
import signal
import subprocess
import threading
import time
import traceback
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu,
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox,
)
from PyQt6.QtCore import Qt, QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon, QPixmap, QAction

from ui.pill_widget import PillWidget
from ui.hub_window import HubWindow
from ui.red_dot_indicator import RedDotIndicator
from core.recorder import AudioRecorder
from core.transcriber import Transcriber
from core.hotkey import HotkeyListener
from core.paste import paste_text, paste_last_transcript, save_frontmost_app
from core.command_mode import copy_selection
from core.dictation_actions import extract_actions, perform_actions
from core.transform import TransformHandler
from core.errors import classify as classify_error
from core import usage as usage_meter
from core import retention as audio_retention
from core.relaunch import relaunch_app
from core.logger import log, log_exc
from db.database import TranscriptionDB
from web.server import start_web_server
from config import LOGO_PATH, APP_DATA_DIR, AUDIO_DIR, CHUNK_MAX_SECONDS, RECORDING_WARN_SECONDS, get_setting


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
    """First-run authentication: every user (Free or Pro) needs a KeyLess
    by Sinsajo account. Free Trial is 30 days × 8h/mes; Pro is unlimited.

    No more BYOK — we proxy all transcription through our backend so we can:
      - bill / cap properly
      - keep the user's Groq key out of the picture
      - audit usage per account
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KeyLess by Sinsajo — Conectar")
        self.setFixedWidth(460)

        from config import KEYLESSFLOW_API_URL

        layout = QVBoxLayout()
        layout.setSpacing(12)

        title = QLabel("<b>Conecta tu cuenta para empezar</b>")
        title.setStyleSheet("font-size: 15px;")
        layout.addWidget(title)

        info = QLabel(
            "KeyLess by Sinsajo funciona con tu cuenta. "
            "<b>Prueba gratis 30 días</b> con 8 horas de dictado al mes."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888;")
        layout.addWidget(info)

        # --- Sign up button: opens browser at signup page ---
        signup_btn = QPushButton("¿No tienes cuenta? Crear gratis →")
        signup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        signup_url = f"{KEYLESSFLOW_API_URL.rstrip('/')}/signup?utm_source=desktop"
        signup_btn.clicked.connect(lambda: __import__("webbrowser").open(signup_url))
        layout.addWidget(signup_btn)

        # --- Separator ---
        sep = QLabel("─" * 30 + "   o   " + "─" * 30)
        sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sep.setStyleSheet("color: #555; margin: 8px 0;")
        layout.addWidget(sep)

        # --- Activation code ---
        layout.addWidget(QLabel(
            "Ya tienes cuenta? Pega tu código de activación:"
        ))
        code_help = QLabel(
            f'<a href="{KEYLESSFLOW_API_URL.rstrip("/")}/account">'
            "Encuéntralo en tu cuenta → /account</a>"
        )
        code_help.setOpenExternalLinks(True)
        code_help.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(code_help)

        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("KF-XXXX-XXXX-XXXX")
        # Force uppercase as user types so the format matches the backend.
        self.code_input.textChanged.connect(
            lambda t: self.code_input.setText(t.upper()) if t != t.upper() else None,
        )
        layout.addWidget(self.code_input)

        save_btn = QPushButton("Conectar")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._activate)
        layout.addWidget(save_btn)

        # --- BYOK / workshop path: bring your own Groq key, no account -------
        sep2 = QLabel("─" * 30 + "   o   " + "─" * 30)
        sep2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sep2.setStyleSheet("color: #555; margin: 8px 0;")
        layout.addWidget(sep2)

        byok_label = QLabel(
            "<b>¿Workshop / tienes tu propia API key?</b> Consigue una gratis en "
            '<a href="https://console.groq.com/keys">console.groq.com/keys</a> '
            "y pégala aquí — sin cuenta, sin límites nuestros."
        )
        byok_label.setOpenExternalLinks(True)
        byok_label.setWordWrap(True)
        byok_label.setStyleSheet("color: #888;")
        layout.addWidget(byok_label)

        self.byok_input = QLineEdit()
        self.byok_input.setPlaceholderText("gsk_...")
        self.byok_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.byok_input)

        byok_btn = QPushButton("Usar mi API key")
        byok_btn.clicked.connect(self._use_own_key)
        layout.addWidget(byok_btn)

        self.setLayout(layout)

    def _use_own_key(self):
        from core.byok import validate_groq_key, save_groq_key
        key = self.byok_input.text().strip()
        ok, msg = validate_groq_key(key)
        if not ok:
            QMessageBox.warning(self, "Key no válida", msg)
            return
        save_groq_key(key)
        QMessageBox.information(
            self,
            "¡Lista tu key!",
            f"{msg}\n\nYa puedes dictar: mantén Ctrl+Alt y habla.\n"
            "Tu key quedó guardada localmente (solo en esta máquina).",
        )
        self.accept()

    def _activate(self):
        code = self.code_input.text().strip().upper()
        if not code:
            QMessageBox.warning(self, "Falta el código", "Pega tu código de activación o crea una cuenta gratis.")
            return

        # Hit /api/auth/activate — the same endpoint the tray menu uses.
        from core import auth as pro_auth
        result = pro_auth.activate(code)
        if not result.get("ok"):
            err = result.get("error") or "No se pudo activar"
            upgrade = result.get("upgrade_url") or ""
            msg = err
            if upgrade:
                msg += f"\n\n¿Quieres suscribirte? {upgrade}"
            QMessageBox.warning(self, "Error", msg)
            return

        record = result.get("record") or {}
        plan = (record.get("plan") or "free").capitalize()
        email = record.get("email") or ""
        QMessageBox.information(
            self,
            "¡Conectado!",
            f"Plan {plan} activo{' en ' + email if email else ''}.\n\n"
            "Ya puedes dictar. Mantén Ctrl+Alt para grabar.",
        )
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
    # dashboard_url carries the per-launch access token — the dashboard now
    # rejects any request without it (local-process + DNS-rebinding hardening).
    from web.server import dashboard_url
    dashboard.triggered.connect(
        lambda: __import__("webbrowser").open(dashboard_url(port))
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

        # Quick links to web account portal
        from config import KEYLESSFLOW_API_URL
        account_action = QAction("Mi cuenta (uso, plan, facturación)", menu)
        account_url = f"{KEYLESSFLOW_API_URL.rstrip('/')}/account"
        account_action.triggered.connect(
            lambda: __import__("webbrowser").open(account_url),
        )
        menu.addAction(account_action)

        # Upgrade is meaningful for Free-trial users; harmless for Pro.
        upgrade_action = QAction("Mejorar plan…", menu)
        upgrade_url = f"{KEYLESSFLOW_API_URL.rstrip('/')}/#precios"
        upgrade_action.triggered.connect(
            lambda: __import__("webbrowser").open(upgrade_url),
        )
        menu.addAction(upgrade_action)

        signout_action = QAction("Cerrar sesión", menu)
        signout_action.triggered.connect(_handle_pro_signout)
        menu.addAction(signout_action)
    else:
        # Should not reach here after the auth gate in main(), but kept as a
        # safety net in case auth.json is wiped while the app is running.
        from config import KEYLESSFLOW_API_URL
        signin_action = QAction("Conectar con tu cuenta…", menu)
        signin_action.triggered.connect(_handle_pro_connect)
        menu.addAction(signin_action)

        signup_action = QAction("Crear cuenta gratis…", menu)
        signup_url = f"{KEYLESSFLOW_API_URL.rstrip('/')}/signup?utm_source=desktop_tray"
        signup_action.triggered.connect(
            lambda: __import__("webbrowser").open(signup_url),
        )
        menu.addAction(signup_action)

    menu.addSeparator()

    # ---- Language quick-switch -------------------------------------------
    # Whisper auto-detects Spanish + English well by default. This lets the
    # user force one when auto-detect drifts (e.g. code-switching mid-sentence).
    from PyQt6.QtGui import QActionGroup
    from config import set_setting  # local import to avoid circular
    lang_menu = QMenu("Idioma de transcripción", menu)
    lang_group = QActionGroup(lang_menu)
    lang_group.setExclusive(True)
    lang_actions: list[tuple[QAction, str]] = []
    lang_choices = [
        ("Auto (detecta)", "auto"),
        ("Español", "es"),
        ("English", "en"),
    ]
    for label, code in lang_choices:
        act = QAction(label, lang_menu)
        act.setCheckable(True)
        lang_group.addAction(act)
        lang_menu.addAction(act)
        lang_actions.append((act, code))
        act.triggered.connect(lambda _checked, c=code: set_setting("whisper_language", c))

    def _refresh_lang_marks():
        cur = get_setting("whisper_language", "auto") or "auto"
        for a, c in lang_actions:
            a.setChecked(c == cur)

    _refresh_lang_marks()
    lang_menu.aboutToShow.connect(_refresh_lang_marks)
    menu.addMenu(lang_menu)

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

    transcription_done = pyqtSignal(str, float, str, int)  # text, duration, model_id, failed_chunks
    transcription_progress = pyqtSignal(int, int)  # current_chunk, total_chunks
    transcription_error = pyqtSignal(str)
    command_done = pyqtSignal(str)
    command_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.recorder = AudioRecorder()
        self.transcriber = Transcriber()
        self.transform = TransformHandler()
        self.db = TranscriptionDB()
        self.hotkey = HotkeyListener()
        self.pill = PillWidget()
        self.red_dot = RedDotIndicator()
        self.hub = HubWindow(self.db)

        self._selected_text_snapshot = ""
        self._last_text: str = ""  # For "paste last transcript" hotkey
        # Path of the rolling-checkpoint WAV for the *current* recording.
        # Set on press, consumed on release. Survives crashes so the user
        # can recover from the Hub.
        self._active_audio_path: str | None = None
        # Row id of the most recent FAILED transcription (tray-balloon click
        # retries it) and the row being retried right now (so success
        # updates that row instead of inserting a duplicate).
        self._last_failed_row_id: int | None = None
        self._retry_row_id: int | None = None

        # Set by main() after the tray icon exists; called as notify(msg) to
        # surface user-facing toasts (e.g. when auto-paste landed in the wrong
        # window because the user switched apps during transcription).
        self.notify = lambda msg: None  # default no-op

        # Live recording clock → drives the pill's elapsed display and the
        # long-recording warning. Covers hold AND hands-free (both emit
        # pressed/released).
        self._rec_start = 0.0
        self._rec_warned = False
        # Mic-health watchdog: warn the user ONCE per recording if sounddevice
        # reports input overflows. A few are noise; a sustained run means the
        # mic / USB / system is dropping audio and the recording is degraded.
        self._mic_dropout_warned = False
        self._rec_tick = QTimer()
        self._rec_tick.setInterval(1000)
        self._rec_tick.timeout.connect(self._on_rec_tick)

        self.pill.visualizer.set_audio_queue(self.recorder.audio_queue)

        # Signals — all QueuedConnection (pynput emits from its own thread)
        self.hotkey.pressed.connect(self._on_hotkey_pressed, Qt.ConnectionType.QueuedConnection)
        self.hotkey.released.connect(self._on_hotkey_released, Qt.ConnectionType.QueuedConnection)
        self.hotkey.transform_triggered.connect(self._on_transform, Qt.ConnectionType.QueuedConnection)
        self.hotkey.hands_free_started.connect(self.red_dot.start, Qt.ConnectionType.QueuedConnection)
        self.hotkey.hands_free_stopped.connect(self.red_dot.stop, Qt.ConnectionType.QueuedConnection)
        # System-audio: two activation paths — Ctrl+Shift HOLD, or triple-tap
        # Ctrl (hands-free). Both funnel through system_audio_pressed /
        # …_released; the recorder switches to WASAPI loopback in either
        # case. hands-free variant also drives the red-dot indicator so the
        # user has visual proof recording is still on.
        self.hotkey.system_audio_pressed.connect(self._on_system_audio_pressed, Qt.ConnectionType.QueuedConnection)
        self.hotkey.system_audio_released.connect(self._on_system_audio_released, Qt.ConnectionType.QueuedConnection)
        self.hotkey.system_hands_free_started.connect(self.red_dot.start, Qt.ConnectionType.QueuedConnection)
        self.hotkey.system_hands_free_stopped.connect(self.red_dot.stop, Qt.ConnectionType.QueuedConnection)
        self.hotkey.system_hands_free_started.connect(self._ensure_pill_on_top, Qt.ConnectionType.QueuedConnection)
        # Belt-and-suspenders: hands_free start ALREADY triggers _on_hotkey_pressed
        # (which sets pill to RECORDING). But user reports the wave isn't visible
        # in hands-free on Windows — likely the pill is being covered by another
        # window in z-order. Explicitly raise it to the top on every hands-free
        # start so the wave is unmistakable.
        self.hotkey.hands_free_started.connect(self._ensure_pill_on_top, Qt.ConnectionType.QueuedConnection)

        self.transcription_done.connect(self._on_transcription_done, Qt.ConnectionType.QueuedConnection)
        self.transcription_progress.connect(self._on_transcription_progress, Qt.ConnectionType.QueuedConnection)
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
            # Generate the audio path BEFORE recording so the recorder can
            # write progressively. Survives an app crash / OS kill at any
            # point — the partial WAV is recoverable from the Hub.
            self._active_audio_path = None
            if get_setting("save_audio_for_retry", True):
                import uuid
                self._active_audio_path = os.path.join(
                    AUDIO_DIR, f"{uuid.uuid4().hex}.wav",
                )
            self.recorder.start(checkpoint_path=self._active_audio_path)
            self.pill.set_state(PillWidget.STATE_RECORDING)
            # Force the pill above whatever else is on screen so the wave is
            # always visible while recording (covers Ctrl+Alt hold AND the
            # double-tap hands-free path, where the hands_free_started signal
            # ALSO calls _ensure_pill_on_top as a safety net).
            self._ensure_pill_on_top()
            self._rec_start = time.time()
            self._rec_warned = False
            self._mic_dropout_warned = False
            self.pill.set_elapsed(0)
            self._rec_tick.start()
        except Exception as e:
            log_exc("hotkey_pressed crashed (suppressed)", e)
            try:
                self.pill.set_state(PillWidget.STATE_ERROR)
            except Exception:
                pass

    @pyqtSlot(int, int)
    def _on_transcription_progress(self, current: int, total: int):
        """Bridge transcriber's per-chunk progress to the pill so the user
        sees '2/6' on long uploads instead of a silent spinner."""
        try:
            self.pill.set_processing_progress(current, total)
        except Exception:
            pass

    # ------- System-audio (WASAPI loopback) flow -------
    @pyqtSlot()
    def _on_system_audio_pressed(self):
        """Ctrl+Shift held (or triple-tap Ctrl) → start capturing whatever the PC is playing
        (WhatsApp audio, YouTube, Zoom). Reuses the full transcribe pipeline;
        only the recorder's input source differs."""
        try:
            save_frontmost_app()
            self._active_audio_path = None
            if get_setting("save_audio_for_retry", True):
                import uuid
                self._active_audio_path = os.path.join(
                    AUDIO_DIR, f"{uuid.uuid4().hex}.wav",
                )
            self.recorder.start(checkpoint_path=self._active_audio_path, mode="system")
            self.pill.set_state(PillWidget.STATE_RECORDING)
            self._ensure_pill_on_top()
            self._rec_start = time.time()
            self._rec_warned = False
            self._mic_dropout_warned = False
            self.pill.set_elapsed(0)
            self._rec_tick.start()
            try:
                self.notify("Grabando audio del sistema (loopback)")
            except Exception:
                pass
        except Exception as e:
            log_exc("system_audio_pressed crashed (suppressed)", e)
            # WASAPI unavailable / no output device / etc. — surface the
            # error explicitly instead of silently doing nothing.
            try:
                self.pill.set_state(PillWidget.STATE_ERROR)
                self.notify(f"No pude capturar audio del sistema: {e}")
            except Exception:
                pass

    @pyqtSlot()
    def _on_system_audio_released(self):
        """Release path is identical to mic mode — hand the buffer off to
        the same transcribe worker."""
        self._on_hotkey_released()

    @pyqtSlot()
    def _ensure_pill_on_top(self):
        """Make sure the pill is shown and on top of the z-order. Safe to
        call multiple times — no-op if already visible. Used as a defensive
        guard when entering any recording state."""
        try:
            if not self.pill.isVisible():
                self.pill.show()
            self.pill.raise_()
        except Exception as e:
            log_exc("ensure_pill_on_top failed (suppressed)", e)

    def _on_rec_tick(self):
        """Once per second while recording: update the pill clock and fire a
        one-shot warning past the long-recording mark."""
        elapsed = time.time() - self._rec_start
        try:
            self.pill.set_elapsed(elapsed)
        except Exception:
            pass
        if not self._rec_warned and elapsed >= RECORDING_WARN_SECONDS:
            self._rec_warned = True
            mins = int(RECORDING_WARN_SECONDS // 60)
            try:
                self.notify(
                    f"Llevás {mins} min grabando. Se transcribe igual (se divide "
                    f"en partes), pero acordate de parar cuando termines."
                )
            except Exception:
                pass
        # Mic health: warn ONCE if sounddevice has reported sustained input
        # overflows. Threshold = 3 within the recording lifetime — isolated
        # xruns under load are normal and shouldn't spam the user.
        if not self._mic_dropout_warned and self.recorder.overflow_count >= 3:
            self._mic_dropout_warned = True
            try:
                self.notify(
                    "⚠ El micrófono está perdiendo audio. Revisa la conexión "
                    "(USB) y cierra apps que usen el mic en paralelo. "
                    "La grabación sigue, pero parte del audio puede salir cortada."
                )
            except Exception:
                pass

    @pyqtSlot()
    def _on_hotkey_released(self):
        try:
            self._rec_tick.stop()
            duration = self.recorder.stop()
            self.pill.set_state(PillWidget.STATE_PROCESSING)

            if duration < 0.3:
                # Discard the rolling-checkpoint file written during the tap.
                self.recorder.discard_checkpoint(self._active_audio_path)
                self._active_audio_path = None
                self.pill.set_state(PillWidget.STATE_IDLE)
                return

            # Silence guard: an effectively-silent capture (system-audio
            # loopback with nothing playing, or a dead/wrong mic) makes Whisper
            # hallucinate the vocabulary prompt (the personal dictionary) and we
            # used to paste that garbage. Detect near-digital-silence and bail
            # with a clear message instead. Threshold is deliberately low so
            # quiet-but-real speech still transcribes.
            SILENCE_PEAK = 60  # int16 peak; loopback-with-nothing-playing is 0
            if self.recorder.max_amplitude() < SILENCE_PEAK:
                self.recorder.discard_checkpoint(self._active_audio_path)
                self._active_audio_path = None
                self.pill.set_state(PillWidget.STATE_IDLE)
                self.notify(
                    "No se detectó audio. Si grabás el sonido del sistema, "
                    "revisá que algo se esté reproduciendo y que salga por el "
                    "dispositivo de salida por defecto."
                )
                return

            # MP3 for upload (~8x smaller than WAV) — Groq decodes server-side.
            # Long recordings are split into silence-aligned chunks so no single
            # request hits Groq's 25 MB cap or the backend's 60s timeout.
            recording_duration = self.recorder.get_duration()
            if recording_duration > CHUNK_MAX_SECONDS:
                upload_buffer = self.recorder.get_mp3_chunks(CHUNK_MAX_SECONDS)
                log(f"long recording {recording_duration:.0f}s → {len(upload_buffer)} chunk(s)")
            else:
                upload_buffer = self.recorder.get_mp3_buffer()

            # Audio was already written progressively by the rolling checkpoint
            # during recording — no second save_wav_to() needed. The path is
            # whatever we generated on press.
            audio_path = self._active_audio_path
            self._active_audio_path = None

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

    def _transcribe_worker(self, upload_buffer, duration, audio_path=None, retry_row_id=None):
        if isinstance(upload_buffer, (list, tuple)):
            size_kb = sum(len(b.getvalue()) for b in upload_buffer) / 1024
        else:
            size_kb = len(upload_buffer.getvalue()) / 1024 if hasattr(upload_buffer, "getvalue") else 0
        log(f"transcribe start: duration={duration:.2f}s, upload_size={size_kb:.1f}KB, audio_path={audio_path}")
        try:
            # Surface chunk progress in the pill ("2/6") so long-recording
            # transcription doesn't *look* hung. Only meaningful when the
            # upload was chunked (a list of buffers); single-buffer ignores.
            def _emit_progress(current: int, total: int):
                try:
                    self.transcription_progress.emit(current, total)
                except Exception:
                    pass
            text, model_id, failed_chunks = self.transcriber.transcribe(
                upload_buffer, on_progress=_emit_progress,
            )
            log(
                f"transcribe ok: model={model_id}, chars={len(text) if text else 0}, "
                f"failed_chunks={failed_chunks}, text[:60]={(text or '')[:60]!r}"
            )
            if text:
                self._pending_audio_path = audio_path
                self._retry_row_id = retry_row_id
                self.transcription_done.emit(text, duration, model_id, failed_chunks)
            else:
                # Empty result — usually a silent mic / wrong input device.
                # NEVER fail silently: tell the user, and point at the saved
                # audio so nothing is lost.
                self._pending_audio_path = None
                log("transcribe returned empty text", level="WARN")
                msg = "No se detectó voz. Acercate al micrófono y revisá que sea el correcto."
                if audio_path:
                    msg += " El audio quedó guardado: reintentá desde el Hub."
                self.transcription_error.emit(msg)
        except Exception as e:
            self._pending_audio_path = None
            log_exc("transcribe FAILED", e)
            # Friendly, actionable message by failure kind (offline / 429 /
            # 5xx / auth / quota) instead of a raw exception name.
            kind, msg, _retryable = classify_error(e)
            if audio_path and retry_row_id is None:
                # Record the failure as a row so the Hub shows it with a
                # "Re-transcribir" action — before, the WAV was orphaned and
                # the promised "reintentá desde el Hub" was impossible.
                try:
                    self._last_failed_row_id = self.db.insert(
                        text="", duration_seconds=duration, model="failed",
                        audio_path=audio_path, status="failed", error_message=kind,
                    )
                except Exception as db_e:
                    log_exc("failed-row insert FAILED", db_e)
            elif retry_row_id is not None:
                self._last_failed_row_id = retry_row_id
            self.transcription_error.emit(msg)

    @pyqtSlot(str, float, str, int)
    def _on_transcription_done(self, text: str, duration: float, model_id: str, failed_chunks: int):
        log(f"transcription_done: chars={len(text)}, failed_chunks={failed_chunks}, text[:60]={text[:60]!r}")
        # Trailing voice actions — "dale enter" / "press enter" at the end of
        # the dictation strips the phrase and presses Enter after the paste
        # lands (Wispr Flow parity).
        final_text, actions = extract_actions(text)
        if not final_text:
            final_text, actions = text, []
        try:
            status = paste_text(final_text)
            log(f"paste status={status}")
            # If the foreground window changed while we were transcribing, the
            # OS may have blocked our SetForegroundWindow → auto-paste landed
            # in the wrong place. Tell the user the text is in the clipboard
            # so they can Ctrl+V manually wherever they want it.
            if status in ("focus_lost", "no_target"):
                preview = final_text[:60] + ("…" if len(final_text) > 60 else "")
                self.notify(f"Texto en el portapapeles — Ctrl+V para pegar:\n{preview}")
            elif actions:
                # Only press Enter when the paste actually landed where the
                # user was typing — never fire it into an unknown window.
                perform_actions(actions)
            if status not in ("focus_lost", "no_target") and get_setting("confirm_paste", False):
                words = len(final_text.split())
                self.notify(f"✓ Pegado · {words} palabra(s)")
            # Surface partial-chunk failures explicitly so the user knows the
            # text they got is incomplete and where to recover the rest.
            if failed_chunks:
                self.notify(
                    f"⚠ {failed_chunks} parte(s) no se transcribieron. "
                    "El audio completo quedó guardado — reintenta desde el Hub."
                )
        except Exception as e:
            log_exc("paste FAILED", e)
            # Paste crashed but we still have the text — make sure it's recoverable.
            try:
                preview = final_text[:60] + ("…" if len(final_text) > 60 else "")
                self.notify(f"No pude pegar automáticamente. Texto en el portapapeles — Ctrl+V:\n{preview}")
            except Exception:
                pass
        self._last_text = final_text
        audio_path = getattr(self, "_pending_audio_path", None)
        self._pending_audio_path = None
        try:
            retry_id = self._retry_row_id
            self._retry_row_id = None
            if retry_id is not None:
                # Successful retry: heal the failed row (status -> ok).
                self.db.update_text(retry_id, final_text)
                if self._last_failed_row_id == retry_id:
                    self._last_failed_row_id = None
            else:
                self.db.insert(
                    text=final_text, duration_seconds=duration,
                    model=model_id, audio_path=audio_path,
                )
        except Exception as e:
            log_exc("db write FAILED", e)
        self._after_usage_change()
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
        # Surface the reason so a failed dictation is never silent.
        try:
            self.notify(error)
        except Exception:
            pass

    # ------- Usage meter / retry / retention -------
    def _after_usage_change(self):
        try:
            self.update_tray_tooltip()
            warn = usage_meter.budget_warning(self.db)
            if warn:
                self.notify(warn)
        except Exception as e:
            log_exc("usage hook failed (suppressed)", e)

    def update_tray_tooltip(self):
        cb = getattr(self, "set_tooltip", None)
        if cb:
            try:
                cb(usage_meter.tooltip(self.db))
            except Exception:
                pass

    @pyqtSlot()
    def retry_last_failed(self):
        """Tray balloon clicked -> re-transcribe the most recent failed recording."""
        row_id = self._last_failed_row_id
        if row_id is None:
            row = self.db.last_failed()
            row_id = row["id"] if row else None
        if row_id is None:
            return
        self.retry_row(row_id)

    def retry_row(self, row_id: int):
        """Re-transcribe a saved WAV through the normal pipeline; on success
        the text is pasted at the cursor and the row is marked ok."""
        import wave
        row = self.db.get(row_id)
        path = (row or {}).get("audio_path")
        if not path or not os.path.exists(path):
            self.notify("No encuentro el audio de ese dictado para reintentar.")
            return
        try:
            with wave.open(path, "rb") as wf:
                duration = wf.getnframes() / float(wf.getframerate() or 16000)
        except Exception:
            duration = 0.0
        try:
            upload = AudioRecorder.encode_wav_to_mp3_chunks(path, max_seconds=CHUNK_MAX_SECONDS)
        except Exception as e:
            log_exc("retry: could not encode wav", e)
            self.notify("No pude leer el audio guardado.")
            return
        save_frontmost_app()
        self.pill.set_state(PillWidget.STATE_PROCESSING)
        self.notify("Reintentando la transcripción…")
        threading.Thread(
            target=self._transcribe_worker,
            args=(upload, duration, path, row_id), daemon=True,
        ).start()

    def run_retention(self):
        try:
            audio_retention.prune(
                self.db, AUDIO_DIR,
                retention_days=int(get_setting("audio_retention_days", 7)),
                failed_retention_days=int(get_setting("audio_failed_retention_days", 30)),
                max_mb=int(get_setting("audio_max_mb", 500)),
            )
        except Exception as e:
            log_exc("retention failed (suppressed)", e)

    # NOTE: the old voice "Command Mode" (Ctrl+Shift = speak an instruction to
    # transform selected text) was removed in the Windows port: its hotkey now
    # belongs to system-audio capture, and its slots were dead code (never
    # wired to any signal). Text transforms live on in Alt+1..8 (_on_transform),
    # which shares this completion slot:
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

    # Authentication gate: every user (Free trial or Pro) needs a token from
    # /api/auth/activate — UNLESS the user has explicitly opted into BYOK mode
    # (transcribe_backend = "byok" + a real GROQ_API_KEY in %APPDATA%\KeyLessFlow\.env).
    # BYOK is the owner / self-hosted path; production users go through the
    # backend so plans + quotas are enforced server-side.
    from core import auth as pro_auth
    from config import GROQ_API_KEY
    backend_setting = get_setting("transcribe_backend", "pro")
    byok_active = backend_setting == "byok" and bool(GROQ_API_KEY)
    if not byok_active and not pro_auth.is_pro():
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
    sflow.set_tooltip = lambda text: tray.setToolTip(text)
    sflow.update_tray_tooltip()
    # Tray balloon click -> retry the last failed transcription.
    tray.messageClicked.connect(sflow.retry_last_failed)
    # Audio retention: prune 30 s after launch (off the startup path), then daily.
    QTimer.singleShot(30_000, sflow.run_retention)
    _retention_timer = QTimer()
    _retention_timer.setInterval(24 * 60 * 60 * 1000)
    _retention_timer.timeout.connect(sflow.run_retention)
    _retention_timer.start()
    sflow._retention_timer = _retention_timer  # keep a reference alive

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
