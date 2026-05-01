"""Global hotkeys + mouse button trigger.

Modes:
  1. Hold Ctrl+Alt (Option)        → normal recording (hold-to-talk)
  2. Double-tap Ctrl                → hands-free (tap Ctrl again to stop)
  3. Hold configured mouse button   → normal recording (opt-in via settings)

Emits:
  - pressed / released          → regular transcription
  - hands_free_started / hands_free_stopped → hands-free recording lifecycle
"""
import os
import sys
import time
import datetime
from pynput import keyboard, mouse
from PyQt6.QtCore import QObject, pyqtSignal
from config import DOUBLE_TAP_INTERVAL, CTRL_TAP_MAX_DURATION, get_setting, APP_DATA_DIR


# Debug log file — always writes (tiny footprint) so we can diagnose hotkey
# issues from a packaged .app without stdout visibility.
_LOG_PATH = os.path.join(APP_DATA_DIR, "hotkey.log")


def _log(msg: str):
    try:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(_LOG_PATH, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


_MOUSE_BUTTON_MAP = {
    "middle": mouse.Button.middle,
    "x1": getattr(mouse.Button, "x1", None),
    "x2": getattr(mouse.Button, "x2", None),
}


class HotkeyListener(QObject):
    pressed = pyqtSignal()
    released = pyqtSignal()
    transform_triggered = pyqtSignal(int)  # index 0..7 (Option+1..8)
    hands_free_started = pyqtSignal()
    hands_free_stopped = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._ctrl_held = False
        self._alt_held = False
        self._shift_held = False
        self._cmd_held = False
        self._recording = False
        self._hands_free = False
        self._command_mode = False
        self._kb_listener: keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener | None = None

        # Double-tap state — only "clean" Ctrl taps count (Ctrl pressed and
        # released without any other key in between, and held < CTRL_TAP_MAX_DURATION).
        self._ctrl_press_time = 0.0       # when current Ctrl press started
        self._ctrl_pure = True             # False if any other key pressed while Ctrl held
        self._last_ctrl_tap_release = 0.0  # release time of last clean tap
        self._ctrl_tap_count = 0

    def start(self):
        _log("HotkeyListener.start() called")
        self._kb_listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._kb_listener.daemon = True
        self._kb_listener.start()
        _log(f"keyboard.Listener started. running={self._kb_listener.running}")

        mb_name = get_setting("mouse_button_hotkey")
        if mb_name and _MOUSE_BUTTON_MAP.get(mb_name):
            try:
                self._mouse_listener = mouse.Listener(on_click=self._on_click)
                self._mouse_listener.daemon = True
                self._mouse_listener.start()
            except Exception as e:
                print(f"Mouse listener unavailable: {e}")

    def stop(self):
        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener = None
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None

    # --- Mouse ---
    def _on_click(self, x, y, button, pressed):
        mb_name = get_setting("mouse_button_hotkey")
        target = _MOUSE_BUTTON_MAP.get(mb_name)
        if target is None or button != target:
            return
        if pressed and not self._recording:
            self._recording = True
            self._hands_free = False
            self.pressed.emit()
        elif not pressed and self._recording and not self._hands_free and not self._command_mode:
            self._recording = False
            self.released.emit()

    # --- Keyboard ---
    def _key_char(self, key):
        try:
            return (getattr(key, "char", None) or "").lower()
        except Exception:
            return ""

    def _on_press(self, key):
        is_ctrl = key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r)
        is_alt = key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r,
                         keyboard.Key.alt_gr)
        is_shift = key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r)
        is_cmd = key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r)

        # Hands-free stop: a Ctrl press while in hands-free recording stops it.
        # Detected on press (not release) so a quick tap stops promptly.
        if is_ctrl and self._hands_free and self._recording:
            self._hands_free = False
            self._recording = False
            self._ctrl_held = True
            self._ctrl_pure = False  # this press doesn't count as a new tap
            self._ctrl_tap_count = 0
            self.released.emit()
            self.hands_free_stopped.emit()
            return

        if is_cmd:
            if not self._cmd_held:
                self._cmd_held = True
                if self._ctrl_held:
                    self._ctrl_pure = False
        elif is_ctrl:
            if not self._ctrl_held:  # ignore OS auto-repeat
                self._ctrl_held = True
                self._ctrl_press_time = time.time()
                self._ctrl_pure = True  # assume pure until contaminated
                # Any other modifier already held contaminates this Ctrl press.
                if self._alt_held or self._shift_held or self._cmd_held:
                    self._ctrl_pure = False
        elif is_alt:
            if not self._alt_held:
                self._alt_held = True
                if self._ctrl_held:
                    self._ctrl_pure = False
        elif is_shift:
            if not self._shift_held:
                self._shift_held = True
                if self._ctrl_held:
                    self._ctrl_pure = False
        else:
            # Any non-modifier key while Ctrl is held contaminates the tap.
            if self._ctrl_held:
                self._ctrl_pure = False

        # Global utility hotkeys (only when idle — not during recording)
        if not self._recording:
            ch = self._key_char(key)
            # Option+1..8 → fire transform N (Wispr Flow convention)
            # Note: on macOS, Option+digit produces special characters:
            #   Option+1 = ¡, Option+2 = ™, Option+3 = £, Option+4 = ¢, Option+5 = ∞,
            #   Option+6 = §, Option+7 = ¶, Option+8 = •
            # We match on these SYMBOLS because that's what pynput reports after
            # macOS applies the dead-key layer. Doc this clearly for users.
            if self._alt_held and not self._ctrl_held and not self._cmd_held and not self._shift_held:
                _OPT_DIGIT_MAP = {"¡": 0, "™": 1, "£": 2, "¢": 3, "∞": 4, "§": 5, "¶": 6, "•": 7}
                # Also support plain digits in case layout differs
                if ch in _OPT_DIGIT_MAP:
                    idx = _OPT_DIGIT_MAP[ch]
                elif ch and ch in "12345678":
                    idx = int(ch) - 1
                else:
                    idx = None
                if idx is not None:
                    self.transform_triggered.emit(idx)
                    return

        if self._recording:
            return

        # Normal hold: Ctrl+Alt
        if self._ctrl_held and self._alt_held:
            self._recording = True
            self._hands_free = False
            self._command_mode = False
            _log("emit pressed (Ctrl+Alt hold)")
            self.pressed.emit()

    def _on_release(self, key):
        is_ctrl = key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r)
        is_alt = key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r,
                         keyboard.Key.alt_gr)
        is_shift = key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r)
        is_cmd = key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r)

        if is_cmd:
            self._cmd_held = False
        elif is_ctrl:
            was_held = self._ctrl_held
            was_pure = self._ctrl_pure
            press_duration = time.time() - self._ctrl_press_time if was_held else 999.0
            self._ctrl_held = False
            # Reset purity for the next Ctrl press cycle.
            self._ctrl_pure = True

            # Double-tap detection: only count as a tap if Ctrl was pressed
            # alone (pure) AND released quickly (under CTRL_TAP_MAX_DURATION).
            # This rules out Ctrl+letter (Ctrl+C, Ctrl+V, …) and Ctrl held as
            # a sustained modifier — neither counts toward hands-free.
            if was_held and was_pure and press_duration <= CTRL_TAP_MAX_DURATION and not self._recording:
                now = time.time()
                if now - self._last_ctrl_tap_release < DOUBLE_TAP_INTERVAL:
                    self._ctrl_tap_count += 1
                else:
                    self._ctrl_tap_count = 1
                self._last_ctrl_tap_release = now

                if self._ctrl_tap_count >= 2:
                    self._ctrl_tap_count = 0
                    self._hands_free = True
                    self._recording = True
                    _log("emit pressed (double-tap Ctrl, hands-free)")
                    self.pressed.emit()
                    self.hands_free_started.emit()
                    return
            else:
                # Contaminated, too long, or already recording — invalidate streak.
                self._ctrl_tap_count = 0
        elif is_alt:
            self._alt_held = False
        elif is_shift:
            self._shift_held = False

        if not self._recording or self._hands_free:
            return

        # Normal hold ends when either Ctrl or Alt released
        if not (self._ctrl_held and self._alt_held):
            self._recording = False
            _log("emit released")
            self.released.emit()
