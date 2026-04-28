"""Hands-free recording indicator.

Independent floating widget shown ONLY while hands-free mode is active.
A pulsing red square in the top-right corner reminds the user that the
microphone is still capturing — useful because hands-free has no key held
to give tactile feedback like Ctrl+Alt does.
"""
from ctypes import c_void_p
import AppKit
import objc
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QColor


DOT_SIZE = 18
DOT_MARGIN = 14
PULSE_INTERVAL_MS = 50
PULSE_PERIOD_MS = 1200


class RedDotIndicator(QWidget):
    """Pulsing red square, top-right corner, only visible during hands-free."""

    def __init__(self):
        super().__init__()
        self._pulse_t = 0
        self._alpha = 230

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(DOT_SIZE, DOT_SIZE)

        self._pulse_timer = QTimer()
        self._pulse_timer.setInterval(PULSE_INTERVAL_MS)
        self._pulse_timer.timeout.connect(self._animate_pulse)

        self._position_on_screen()

    def _position_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.right() - DOT_SIZE - DOT_MARGIN
            y = geo.top() + DOT_MARGIN
            self.move(x, y)

    def _setup_native_macos(self):
        ns_view = objc.objc_object(c_void_p=c_void_p(self.winId().__int__()))
        ns_window = ns_view.window()
        ns_window.setLevel_(AppKit.NSFloatingWindowLevel)
        ns_window.setStyleMask_(
            ns_window.styleMask() | AppKit.NSWindowStyleMaskNonactivatingPanel
        )
        ns_window.setHidesOnDeactivate_(False)
        ns_window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self._setup_native_macos()
        except Exception as e:
            print(f"RedDotIndicator native setup failed: {e}")

    def start(self):
        self._pulse_t = 0
        self._position_on_screen()
        self.show()
        self.raise_()
        self._pulse_timer.start()

    def stop(self):
        self._pulse_timer.stop()
        self.hide()

    def _animate_pulse(self):
        self._pulse_t = (self._pulse_t + PULSE_INTERVAL_MS) % PULSE_PERIOD_MS
        # Smooth in/out: 130..255 alpha
        import math
        phase = (self._pulse_t / PULSE_PERIOD_MS) * 2 * math.pi
        norm = (math.sin(phase) + 1) / 2  # 0..1
        self._alpha = int(130 + norm * 125)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Filled red square with subtle rounded corners
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 59, 48, self._alpha))  # Apple system red
        painter.drawRoundedRect(0, 0, DOT_SIZE, DOT_SIZE, 4, 4)
