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
from config import (
    DOUBLE_TAP_INTERVAL, CTRL_TAP_MAX_DURATION, TRIPLE_TAP_DEFER,
    get_setting, APP_DATA_DIR,
)


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

# Windows virtual-key codes for ground-truth modifier probing.
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt

# A non-modifier key arriving this soon after a hold-combo started means the
# user was typing a KEYBOARD SHORTCUT (Ctrl+Shift+V, Ctrl+Shift+flecha…), not
# starting a capture — the hold is canceled. Kept under main.py's 0.3s discard
# threshold so the aborted stub is dropped silently.
HOLD_CANCEL_WINDOW = 0.25


class HotkeyListener(QObject):
    pressed = pyqtSignal()
    released = pyqtSignal()
    transform_triggered = pyqtSignal(int)  # index 0..7 (Option+1..8)
    hands_free_started = pyqtSignal()
    hands_free_stopped = pyqtSignal()
    # System-audio (WASAPI loopback). Two activation paths, mirroring mic:
    #   HOLD          → Alt+Shift held (system_audio_pressed → …_released)
    #   HANDS-FREE    → triple-tap Ctrl (…_pressed + system_hands_free_started
    #                    → …_released + system_hands_free_stopped on next Ctrl)
    # The main pressed/released signals fire in BOTH modes so main.py can
    # route the recorder in one place; hands-free signals only convey state
    # to the UI.
    system_audio_pressed = pyqtSignal()
    system_audio_released = pyqtSignal()
    system_hands_free_started = pyqtSignal()
    system_hands_free_stopped = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._ctrl_held = False
        self._alt_held = False
        self._shift_held = False
        self._cmd_held = False
        # AltGr (right Alt on ES/DE/intl layouts). Windows injects a phantom
        # LCtrl+RAlt for it, which used to masquerade as our Ctrl+Alt mic combo
        # and fire ghost recordings while typing @ # etc. Tracked separately so
        # we can neutralize the phantom Ctrl and ignore AltGr as a hotkey mod.
        self._altgr_held = False
        self._recording = False
        self._hands_free = False
        self._command_mode = False
        # True while the current hold is a system-audio (loopback) capture,
        # so _on_release routes to the right signal. Reset at every release.
        self._system_audio_mode = False
        # When the current HOLD recording started — used by the shortcut-
        # cancel window. Hands-free never uses it.
        self._hold_started_at = 0.0
        # Ground-truth keyboard probe (GetAsyncKeyState on Windows). The OS
        # hook can MISS release events (UAC prompt, lock screen, elevated
        # windows), leaving e.g. _ctrl_held stuck True — then a lone Shift
        # press "completed" a phantom Ctrl+Shift and started recording. Before
        # acting we reconcile tracked flags against the real key state.
        # Injectable for tests: probe(vk) -> True/False, or None = unknown.
        self._vk_probe = None
        self._kb_listener: keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener | None = None

        # Double-tap state — only "clean" Ctrl taps count (Ctrl pressed and
        # released without any other key in between, and held < CTRL_TAP_MAX_DURATION).
        self._ctrl_press_time = 0.0       # when current Ctrl press started
        self._ctrl_pure = True             # False if any other key pressed while Ctrl held
        self._last_ctrl_tap_release = 0.0  # release time of last clean tap
        self._ctrl_tap_count = 0
        # Mic hands-free start is deferred (TRIPLE_TAP_DEFER) after the 2nd tap
        # so a 3rd tap can override into system-audio hands-free instead. This
        # timer holds the pending start; canceled by the 3rd tap or a fresh
        # sequence.
        import threading as _threading  # local import so hotkey.py has no top-level threading dep
        self._threading = _threading
        self._pending_hf_timer: _threading.Timer | None = None

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
        # Cancel any pending mic-HF timer so a shutdown doesn't fire it after
        # the app is trying to exit.
        self._cancel_pending_mic_hf()

    # ------------------------------------------------------ pending mic-HF
    def _schedule_pending_mic_hf(self):
        """Start (or restart) the defer window for mic hands-free.

        Held long enough (TRIPLE_TAP_DEFER) that a 3rd Ctrl tap can override
        into system-audio hands-free before mic HF commits."""
        self._cancel_pending_mic_hf()
        t = self._threading.Timer(TRIPLE_TAP_DEFER, self._fire_pending_mic_hf)
        t.daemon = True
        self._pending_hf_timer = t
        t.start()

    def _cancel_pending_mic_hf(self):
        t = self._pending_hf_timer
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass
            self._pending_hf_timer = None

    def _fire_pending_mic_hf(self):
        """Runs on the Timer thread when the 200 ms window elapses without a
        3rd tap. Guards against races (recording already started elsewhere)."""
        self._pending_hf_timer = None
        if self._recording:
            return  # something else grabbed the recorder
        self._ctrl_tap_count = 0
        self._hands_free = True
        self._recording = True
        self._system_audio_mode = False
        _log("emit pressed (double-tap Ctrl, mic HF)")
        # PyQt signals are thread-safe across QueuedConnection — main.py's
        # slots already use QueuedConnection so this is safe from the Timer.
        self.pressed.emit()
        self.hands_free_started.emit()

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

    def _vk_down(self, vk: int):
        """Real, current state of a virtual key. None = can't know (non-Win,
        probe failure) — caller must then trust the tracked flag."""
        if self._vk_probe is not None:
            return self._vk_probe(vk)
        if sys.platform == "win32":
            try:
                import ctypes
                return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
            except Exception:
                return None
        return None

    def _reconcile_modifiers(self):
        """Heal tracked flags that went stale because the OS hook missed a
        key-up (UAC/lock screen/elevated windows swallow hook events). Only
        CLEARS flags the OS says are up — never sets them, so AltGr
        neutralization (tracked=False while OS says Ctrl down) survives."""
        if self._ctrl_held and self._vk_down(VK_CONTROL) is False:
            _log("reconcile: stale Ctrl cleared (missed key-up)")
            self._ctrl_held = False
            self._ctrl_pure = False
            self._ctrl_tap_count = 0
        if self._shift_held and self._vk_down(VK_SHIFT) is False:
            _log("reconcile: stale Shift cleared (missed key-up)")
            self._shift_held = False
        if self._alt_held and self._vk_down(VK_MENU) is False:
            _log("reconcile: stale Alt cleared (missed key-up)")
            self._alt_held = False

    def _on_press(self, key):
        # Heal any stale modifier BEFORE deciding anything — otherwise a
        # missed Ctrl key-up makes a lone Shift press look like Ctrl+Shift.
        self._reconcile_modifiers()

        is_altgr = key == keyboard.Key.alt_gr
        is_ctrl = key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r)
        # NOTE: alt_gr is deliberately NOT counted as Alt — see AltGr handling
        # just below. Right Alt on a US layout is alt_r (no phantom Ctrl) and
        # still counts as Alt.
        is_alt = key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r)
        is_shift = key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r)
        is_cmd = key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r)

        # AltGr: Windows delivers a phantom LCtrl right before it on ES/intl
        # layouts. Undo that phantom Ctrl and treat AltGr as a NON-modifier so
        # typing @ # € never trips our Ctrl+Alt / Ctrl+Shift combos.
        if is_altgr:
            self._altgr_held = True
            self._ctrl_held = False
            self._ctrl_pure = False
            self._ctrl_tap_count = 0
            return

        # Hands-free stop: a Ctrl press while in hands-free recording stops
        # whichever mode is active — mic OR system-audio. Detected on press
        # (not release) so a quick tap ends the session promptly.
        if is_ctrl and self._hands_free and self._recording:
            was_system = self._system_audio_mode
            self._hands_free = False
            self._recording = False
            self._system_audio_mode = False
            self._ctrl_held = True
            self._ctrl_pure = False  # this press doesn't count as a new tap
            self._ctrl_tap_count = 0
            if was_system:
                _log("emit system_audio_released (HF stop)")
                self.system_audio_released.emit()
                self.system_hands_free_stopped.emit()
            else:
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
            # Shortcut cancel: a real key right after a HOLD combo means the
            # user typed Ctrl+Shift+V / Ctrl+Shift+flecha / Ctrl+Alt+E — a
            # keyboard shortcut, not a capture. Abort the hold; main.py's
            # <0.3s rule discards the stub silently. Hands-free is exempt
            # (people type while a meeting records).
            if (
                self._recording
                and not self._hands_free
                and (time.time() - self._hold_started_at) < HOLD_CANCEL_WINDOW
            ):
                was_system = self._system_audio_mode
                self._recording = False
                self._system_audio_mode = False
                if was_system:
                    _log("hold canceled: shortcut key during Ctrl+Shift")
                    self.system_audio_released.emit()
                else:
                    _log("hold canceled: shortcut key during Ctrl+Alt")
                    self.released.emit()
                return

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
                # macOS: Option+digit produces special glyphs (¡™£¢∞§¶•).
                # Windows: Alt+digit produces the plain digit character.
                _OPT_DIGIT_MAP = {"¡": 0, "™": 1, "£": 2, "¢": 3, "∞": 4, "§": 5, "¶": 6, "•": 7}
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

        # System-audio hold: Ctrl+Shift (no Alt) → capture the PC's playback
        # via WASAPI loopback. Moved off Alt+Shift because (a) Windows binds
        # Alt+Shift to the input-language switch and eats it, and (b) AltGr on
        # ES layouts is a phantom Ctrl+Alt that routed Alt+Shift to the mic.
        # Ctrl+Shift has neither problem. Requiring !Alt keeps it unambiguous
        # vs the Ctrl+Alt mic hold.
        if self._ctrl_held and self._shift_held and not self._alt_held:
            self._recording = True
            self._hands_free = False
            self._command_mode = False
            self._system_audio_mode = True
            self._hold_started_at = time.time()
            _log("emit system_audio_pressed (Ctrl+Shift hold)")
            self.system_audio_pressed.emit()
            return

        # Normal hold: Ctrl+Alt
        if self._ctrl_held and self._alt_held:
            self._recording = True
            self._hands_free = False
            self._command_mode = False
            self._system_audio_mode = False
            self._hold_started_at = time.time()
            _log("emit pressed (Ctrl+Alt hold)")
            self.pressed.emit()

    def _on_release(self, key):
        is_altgr = key == keyboard.Key.alt_gr
        is_ctrl = key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r)
        is_alt = key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r)
        is_shift = key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r)
        is_cmd = key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r)

        # AltGr release — clear our flag. The phantom Ctrl release that Windows
        # pairs with it is harmless: _ctrl_held is already False so the tap
        # logic below treats it as a no-op.
        if is_altgr:
            self._altgr_held = False
            return

        if is_cmd:
            self._cmd_held = False
        elif is_ctrl:
            was_held = self._ctrl_held
            was_pure = self._ctrl_pure
            press_duration = time.time() - self._ctrl_press_time if was_held else 999.0
            self._ctrl_held = False
            # Reset purity for the next Ctrl press cycle.
            self._ctrl_pure = True

            # Double/triple-tap detection: only count as a tap if Ctrl was
            # pressed alone (pure) AND released quickly. Rules out Ctrl+letter
            # combos and long modifier holds.
            if was_held and was_pure and press_duration <= CTRL_TAP_MAX_DURATION and not self._recording:
                now = time.time()
                if now - self._last_ctrl_tap_release < DOUBLE_TAP_INTERVAL:
                    self._ctrl_tap_count += 1
                else:
                    self._ctrl_tap_count = 1
                self._last_ctrl_tap_release = now

                # 2nd tap: SCHEDULE mic hands-free for 200 ms later. If a
                # 3rd tap arrives within that window, the timer is cancelled
                # and we route to system-audio hands-free instead.
                if self._ctrl_tap_count == 2:
                    _log(f"2-tap Ctrl — mic HF scheduled ({int(TRIPLE_TAP_DEFER*1000)} ms)")
                    self._schedule_pending_mic_hf()
                    return

                # 3rd tap: cancel pending mic, start system-audio hands-free.
                if self._ctrl_tap_count >= 3:
                    self._cancel_pending_mic_hf()
                    self._ctrl_tap_count = 0
                    self._hands_free = True
                    self._recording = True
                    self._system_audio_mode = True
                    _log("emit system_audio_pressed (triple-tap Ctrl, HF)")
                    self.system_audio_pressed.emit()
                    self.system_hands_free_started.emit()
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

        # System-audio hold ends when EITHER Ctrl or Shift releases. Hands-
        # free path bailed above so this only runs for HOLD.
        if self._system_audio_mode:
            if not (self._ctrl_held and self._shift_held):
                self._recording = False
                self._system_audio_mode = False
                _log("emit system_audio_released (Ctrl+Shift hold end)")
                self.system_audio_released.emit()
            return

        # Normal hold ends when either Ctrl or Alt released
        if not (self._ctrl_held and self._alt_held):
            self._recording = False
            _log("emit released")
            self.released.emit()
