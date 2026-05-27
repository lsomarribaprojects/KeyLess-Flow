"""Windows backend for KeyLessFlow.

Implements the platform-specific operations that the upstream macOS code does
via PyObjC/AppleScript: paste, foreground-app save/restore, launch-at-login,
press-enter, copy-selection, etc.

Design notes:
  - Keystroke injection uses `pynput.keyboard.Controller` (cross-platform,
    already a project dep). Lower-level SendInput via ctypes is a fallback if
    pynput proves unreliable in the wild.
  - SetForegroundWindow has Windows restrictions (only the foreground process
    or one allowed by AllowSetForegroundWindow can set foreground). When the
    user holds Ctrl+Alt to record we're not foreground; the workaround used
    here is to attach our thread's input queue to the target window's thread,
    which bypasses the restriction. See _restore_focus for details.
"""
from __future__ import annotations

import os
import sys
import time
import webbrowser
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "KeyLessFlow"
APP_AUTHOR = False  # platformdirs: False → no author subdir (just %APPDATA%\KeyLessFlow)
_REGISTRY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REGISTRY_RUN_VALUE = "KeyLessFlow"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def data_dir() -> str:
    """Writable user data directory. Equivalent of macOS ~/Library/Application Support/SFlow."""
    path = user_data_dir(APP_NAME, appauthor=APP_AUTHOR)
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Foreground app save/restore
# ---------------------------------------------------------------------------
_saved_hwnd: int | None = None


def _own_window_titles() -> set[str]:
    # Lightweight self-detection: KeyLessFlow Qt widgets typically don't have
    # informative titles (the pill is borderless). We treat any HWND whose
    # process equals our PID as "self".
    return set()


def _hwnd_owner_pid(hwnd: int) -> int:
    import win32process
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        return int(pid)
    except Exception:
        return 0


def save_frontmost_app() -> None:
    """Remember the HWND that had focus before we steal it for recording UI."""
    global _saved_hwnd
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return
        # Skip if the foreground window already belongs to us
        if _hwnd_owner_pid(hwnd) == os.getpid():
            return
        _saved_hwnd = int(hwnd)
    except Exception:
        pass


def restore_focus() -> None:
    """Re-focus the HWND that was active before recording.

    SetForegroundWindow has strict rules on Windows. The reliable workaround
    when our thread isn't the foreground one is to AttachThreadInput to the
    target window's thread, then set focus, then detach.
    """
    global _saved_hwnd
    if not _saved_hwnd:
        return
    try:
        import win32gui
        import win32process
        import win32con
        import ctypes

        target_hwnd = _saved_hwnd
        # Bring back from minimized if needed
        if win32gui.IsIconic(target_hwnd):
            win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)

        fg = win32gui.GetForegroundWindow()
        if fg == target_hwnd:
            return

        target_tid, _ = win32process.GetWindowThreadProcessId(target_hwnd)
        current_tid = ctypes.windll.kernel32.GetCurrentThreadId()

        attached = False
        if target_tid and target_tid != current_tid:
            attached = bool(ctypes.windll.user32.AttachThreadInput(current_tid, target_tid, True))
        try:
            ctypes.windll.user32.BringWindowToTop(target_hwnd)
            ctypes.windll.user32.SetForegroundWindow(target_hwnd)
            # Give the OS a beat to actually switch focus before we type
            time.sleep(0.05)
        finally:
            if attached:
                ctypes.windll.user32.AttachThreadInput(current_tid, target_tid, False)
    except Exception:
        pass


def detect_active_app() -> tuple[str, str]:
    """Returns (exe_name, window_title). Used by tone-by-app routing."""
    try:
        import win32gui
        import win32process
        import psutil

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "", ""
        title = win32gui.GetWindowText(hwnd) or ""
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not pid:
            return "", title
        try:
            exe = psutil.Process(pid).name() or ""
        except Exception:
            exe = ""
        return exe, title
    except Exception:
        return "", ""


# ---------------------------------------------------------------------------
# Clipboard + keystroke
# ---------------------------------------------------------------------------
_keyboard_controller = None


def _kb():
    global _keyboard_controller
    if _keyboard_controller is None:
        from pynput.keyboard import Controller
        _keyboard_controller = Controller()
    return _keyboard_controller


def _clipboard_read() -> str:
    try:
        import pyperclip
        return pyperclip.paste() or ""
    except Exception:
        return ""


def _clipboard_write(text: str) -> None:
    import pyperclip
    pyperclip.copy(text)


def _send_ctrl_v() -> None:
    from pynput.keyboard import Key
    kb = _kb()
    kb.press(Key.ctrl)
    try:
        kb.press("v")
        kb.release("v")
    finally:
        kb.release(Key.ctrl)


def _send_ctrl_c() -> None:
    from pynput.keyboard import Key
    kb = _kb()
    kb.press(Key.ctrl)
    try:
        kb.press("c")
        kb.release("c")
    finally:
        kb.release(Key.ctrl)


PASTE_OK = "ok"                 # paste landed in the saved window
PASTE_FOCUS_LOST = "focus_lost" # foreground changed during transcription — paste may have missed
PASTE_NO_TARGET = "no_target"   # no saved HWND (caller never called save_frontmost_app)


def paste_text(text: str) -> str:
    """Paste `text` into the previously focused window.

    Returns one of PASTE_OK / PASTE_FOCUS_LOST / PASTE_NO_TARGET so the
    caller (main.py) can surface a notification when the auto-paste likely
    missed its target.

    Strategy:
      - Write to clipboard, try SetForegroundWindow on saved HWND, send Ctrl+V.
      - LEAVE the text in clipboard for 10s (not the 0.5s the old code did)
        so the user can manually Ctrl+V if the auto-paste went to the wrong
        window. After 10s we restore the user's previous clipboard.
      - If at paste time the user is on a different window than the saved
        target AND SetForegroundWindow doesn't switch us back, we report
        PASTE_FOCUS_LOST so main.py can tray-notify.
    """
    global _saved_hwnd
    if not text:
        _saved_hwnd = None
        return PASTE_NO_TARGET

    target_hwnd = _saved_hwnd
    if not target_hwnd:
        # No saved target — write to clipboard so the user has the text, send
        # Ctrl+V anyway in case they're focused on a text field.
        _clipboard_write(text)
        try:
            _send_ctrl_v()
        except Exception:
            pass
        return PASTE_NO_TARGET

    previous = _clipboard_read()
    status = PASTE_OK
    try:
        _clipboard_write(text)
        restore_focus()
        # Sample foreground AFTER restore_focus + a short settle. If it's
        # still not our target, the OS blocked the switch (typical when our
        # process isn't in the foreground group).
        time.sleep(0.05)
        try:
            import win32gui
            fg_now = win32gui.GetForegroundWindow()
            if fg_now != target_hwnd:
                status = PASTE_FOCUS_LOST
        except Exception:
            pass
        _send_ctrl_v()
    except Exception:
        pass
    finally:
        _saved_hwnd = None

    # Restore the user's original clipboard, but only after a generous delay
    # so the transcription text remains paste-able via manual Ctrl+V for ~10s.
    if previous and previous != text:
        def _restore():
            time.sleep(10.0)
            try:
                # Only restore if OUR text is still on the clipboard — if the
                # user copied something else in the meantime, don't trample it.
                if _clipboard_read() == text:
                    _clipboard_write(previous)
            except Exception:
                pass
        import threading
        threading.Thread(target=_restore, daemon=True).start()

    return status


def paste_last_transcript(text: str) -> None:
    """No focus restore — the user invoked it inside the target window."""
    if not text:
        return
    previous = _clipboard_read()
    try:
        _clipboard_write(text)
        _send_ctrl_v()
    finally:
        if previous:
            def _restore():
                time.sleep(0.5)
                try:
                    _clipboard_write(previous)
                except Exception:
                    pass
            import threading
            threading.Thread(target=_restore, daemon=True).start()


def copy_selection() -> str:
    """Ctrl+C the current selection, diff against pre-snapshot, restore clipboard."""
    before = _clipboard_read()
    try:
        _send_ctrl_c()
    except Exception:
        return ""
    time.sleep(0.08)
    after = _clipboard_read()
    selected = after if (after and after != before) else ""

    if before is not None and before != after:
        def _restore():
            time.sleep(0.05)
            try:
                _clipboard_write(before)
            except Exception:
                pass
        import threading
        threading.Thread(target=_restore, daemon=True).start()

    return selected


def press_enter() -> None:
    from pynput.keyboard import Key
    kb = _kb()
    kb.press(Key.enter)
    kb.release(Key.enter)


# ---------------------------------------------------------------------------
# System integration
# ---------------------------------------------------------------------------
def open_url(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def is_launch_at_login() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_RUN_KEY, 0, winreg.KEY_READ) as k:
            try:
                value, _ = winreg.QueryValueEx(k, _REGISTRY_RUN_VALUE)
                return bool(value)
            except FileNotFoundError:
                return False
    except Exception:
        return False


def set_launch_at_login(enabled: bool) -> None:
    import winreg
    if enabled:
        # In bundle: sys.executable IS the .exe. In dev: sys.executable is python.exe
        # and we need to wrap with `pythonw.exe path\to\main.py` to avoid console window.
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}"'
        else:
            # pythonw to suppress console; fall back to python if missing
            pyw = Path(sys.executable).with_name("pythonw.exe")
            interpreter = str(pyw) if pyw.exists() else sys.executable
            script = os.path.abspath(sys.argv[0])
            cmd = f'"{interpreter}" "{script}"'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, _REGISTRY_RUN_VALUE, 0, winreg.REG_SZ, cmd)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, _REGISTRY_RUN_VALUE)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def hide_from_dock() -> None:
    """No-op on Windows. There's no Dock; the tray icon is the entire UI footprint."""
    pass


def ensure_accessibility() -> bool:
    """No-op on Windows. Global hotkeys and SendInput work without TCC-style grants."""
    return True


# ---------------------------------------------------------------------------
# Window decoration (Liquid-Glass-like blur via DWM Mica on Win11)
# ---------------------------------------------------------------------------
def setup_floating_window(qwidget) -> None:
    """Best-effort floating overlay setup. Qt's flags already cover most of it
    on Windows; here we additionally mark the window as a tool window via
    WS_EX_TOOLWINDOW so it doesn't appear in Alt+Tab."""
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = int(qwidget.winId())
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000

        user32 = ctypes.windll.user32
        # Use GetWindowLongPtrW on 64-bit Windows
        get_long = user32.GetWindowLongPtrW if hasattr(user32, "GetWindowLongPtrW") else user32.GetWindowLongW
        set_long = user32.SetWindowLongPtrW if hasattr(user32, "SetWindowLongPtrW") else user32.SetWindowLongW
        get_long.restype = ctypes.c_ssize_t
        set_long.restype = ctypes.c_ssize_t

        ex_style = get_long(hwnd, GWL_EXSTYLE)
        new_style = ex_style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        set_long(hwnd, GWL_EXSTYLE, new_style)
    except Exception:
        pass


def setup_visual_effect(qwidget) -> bool:
    """Win11 Mica blur via DwmSetWindowAttribute. Returns True on success."""
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = int(qwidget.winId())
        DWMWA_SYSTEMBACKDROP_TYPE = 38  # Win11 22H2+
        DWMSBT_MAINWINDOW = 2           # Mica
        value = ctypes.c_int(DWMSBT_MAINWINDOW)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_SYSTEMBACKDROP_TYPE),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return result == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Relaunch
# ---------------------------------------------------------------------------
def get_bundle_path() -> str | None:
    """Frozen executable path on Windows (parent dir of the .exe). None in dev."""
    if not getattr(sys, "frozen", False):
        return None
    return os.path.dirname(sys.executable)


def relaunch(delay_ms: int = 250) -> None:
    """Spawn a fresh instance, then exit the current one."""
    import subprocess
    if getattr(sys, "frozen", False):
        subprocess.Popen([sys.executable], close_fds=True)
        time.sleep(delay_ms / 1000.0)
        os._exit(0)
    else:
        python = sys.executable
        args = [python] + sys.argv
        time.sleep(delay_ms / 1000.0)
        os.execv(python, args)
