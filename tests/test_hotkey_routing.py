"""Regression tests for the hotkey state machine routing.

Guards the bug where triple-tap Ctrl (system-audio hands-free) was unreachable
because the mic hands-free timer fired too soon (200ms) and the 3rd tap became
a "stop". The defer window was widened to TRIPLE_TAP_DEFER (0.35s). See
core/hotkey.py + config.TRIPLE_TAP_DEFER.

Runs WITHOUT pytest:  venv/Scripts/python.exe tests/test_hotkey_routing.py
Runs WITH pytest too (test_* functions), no extra deps required.

These drive the state machine directly with synthetic pynput key events — no
OS keyboard hook, no audio device — so they are deterministic and CI-safe.
"""
import os
import sys
import time

# Make the repo root importable whether run via pytest or directly as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from pynput import keyboard as kb

import core.hotkey as H

# One QApplication for the whole module — signals need it to exist.
_APP = QApplication.instance() or QApplication([])


def _pump(seconds: float):
    """Spin the Qt event loop so cross-thread (Timer) queued signals deliver."""
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.005)


def _listener():
    hl = H.HotkeyListener()
    fired: list[str] = []
    hl.system_audio_pressed.connect(lambda: fired.append("SYS"))
    hl.pressed.connect(lambda: fired.append("MIC"))
    return hl, fired


def _tap(hl):
    hl._on_press(kb.Key.ctrl_l)
    time.sleep(0.03)
    hl._on_release(kb.Key.ctrl_l)


def test_ctrl_alt_hold_is_mic():
    hl, fired = _listener()
    hl._on_press(kb.Key.ctrl_l)
    hl._on_press(kb.Key.alt)
    _pump(0.05)
    assert fired == ["MIC"], fired


def test_ctrl_shift_hold_is_system_audio():
    # Ctrl+Shift replaced Alt+Shift (Alt+Shift = Windows lang switch, and AltGr
    # is a phantom Ctrl+Alt on ES layouts). See core/hotkey.py.
    hl, fired = _listener()
    hl._on_press(kb.Key.ctrl_l)
    hl._on_press(kb.Key.shift)
    _pump(0.05)
    assert fired == ["SYS"], fired


def test_altgr_alone_does_not_record():
    # Windows delivers phantom LCtrl + AltGr on ES/intl layouts when typing
    # @ # etc. Must NOT trigger the mic (used to fire Ctrl+Alt ghost records).
    hl, fired = _listener()
    hl._on_press(kb.Key.ctrl_l)   # phantom Ctrl Windows injects
    hl._on_press(kb.Key.alt_gr)
    _pump(0.05)
    assert fired == [], fired


def test_altgr_shift_does_not_record():
    hl, fired = _listener()
    hl._on_press(kb.Key.ctrl_l)
    hl._on_press(kb.Key.alt_gr)
    hl._on_press(kb.Key.shift)
    _pump(0.05)
    assert fired == [], fired


def test_double_tap_is_mic_handsfree():
    hl, fired = _listener()
    _tap(hl)
    time.sleep(0.15)
    _tap(hl)
    _pump(0.6)
    assert fired == ["MIC"], fired


def test_triple_tap_is_system_audio_at_human_speed():
    # 220ms between taps is a relaxed human triple-tap — used to fail (fell
    # through to mic double-tap) with the old 200ms defer window.
    hl, fired = _listener()
    _tap(hl)
    time.sleep(0.22)
    _tap(hl)
    time.sleep(0.22)
    _tap(hl)
    _pump(0.6)
    assert fired == ["SYS"], fired


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}  -> got {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
