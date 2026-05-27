"""Paste text into the foreground app. Two backends:

1. KEYSTROKE (default): CGEventKeyboardSetUnicodeString — inyecta el texto
   directamente como eventos de teclado Unicode. NO toca el clipboard del
   usuario. Más privacy-friendly y más rápido para strings pequeños.

2. CLIPBOARD (fallback): NSPasteboard + Cmd+V. Preserva y restaura el
   clipboard del usuario alrededor del paste, pero hay una ventana (~0.5s)
   donde otro listener podría leer el texto intermedio.

La elección se hace por setting `paste_backend` ("keystroke" | "clipboard").
"""
import sys
import time
import subprocess
from config import get_setting


_saved_app: str | None = None
_saved_clipboard: str | None = None


# ---------- Focus management ----------
def save_frontmost_app():
    if sys.platform == "win32":
        from core.platform._windows import save_frontmost_app as _impl
        return _impl()
    global _saved_app
    try:
        from AppKit import NSWorkspace
        active = NSWorkspace.sharedWorkspace().frontmostApplication()
        if active is not None:
            name = str(active.localizedName() or "")
            if name and name != "SFlow":
                _saved_app = name
                return
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first process whose frontmost is true'],
            capture_output=True, text=True, timeout=2,
        )
        name = result.stdout.strip()
        if name and name != "SFlow":
            _saved_app = name
    except Exception:
        pass


def _restore_focus():
    global _saved_app
    if not _saved_app:
        return
    try:
        from AppKit import NSWorkspace, NSRunningApplication
        ws = NSWorkspace.sharedWorkspace()
        # Try to find the saved app by name and activate it natively
        for app in ws.runningApplications():
            if str(app.localizedName() or "") == _saved_app:
                # NSApplicationActivateIgnoringOtherApps = 1 << 1
                try:
                    app.activateWithOptions_(1 << 1)
                    time.sleep(0.08)
                    return
                except Exception:
                    break
    except Exception:
        pass
    # Fallback: AppleScript
    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "{_saved_app}" to activate'],
            check=True, timeout=2,
        )
        time.sleep(0.12)
    except Exception:
        pass


# ---------- Clipboard (legacy path) ----------
def _clipboard_read() -> str:
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString
        pb = NSPasteboard.generalPasteboard()
        val = pb.stringForType_(NSPasteboardTypeString)
        return str(val) if val else ""
    except Exception:
        try:
            r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=1)
            return r.stdout
        except Exception:
            return ""


def _clipboard_write(text: str):
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
    except Exception:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def _cmd_v():
    subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to keystroke "v" using command down'],
        check=True,
    )


def _paste_via_clipboard(text: str):
    global _saved_clipboard
    _saved_clipboard = _clipboard_read()
    _clipboard_write(text)
    _restore_focus()
    _cmd_v()
    # Restore user's original clipboard after brief delay so paste completes
    if _saved_clipboard is not None:
        def _restore():
            time.sleep(0.5)
            try:
                _clipboard_write(_saved_clipboard)
            except Exception:
                pass
        import threading
        threading.Thread(target=_restore, daemon=True).start()


# ---------- Keystroke injection (default path) ----------
def _type_via_cgevent(text: str) -> bool:
    """Synthesize Unicode keyboard events. Returns True on success, False if CGEvent unavailable."""
    try:
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventKeyboardSetUnicodeString,
            CGEventPost,
            kCGHIDEventTap,
        )
    except Exception as e:
        print(f"CGEvent unavailable: {e}")
        return False

    # Chunk the text to avoid OS rate-limiting. Experimentally 20 chars per
    # event works well on macOS; some apps drop characters on very large events.
    CHUNK = 20
    i = 0
    n = len(text)
    while i < n:
        piece = text[i:i + CHUNK]
        # Key DOWN with unicode payload
        down = CGEventCreateKeyboardEvent(None, 0, True)
        CGEventKeyboardSetUnicodeString(down, len(piece), piece)
        CGEventPost(kCGHIDEventTap, down)
        # Key UP (mirror)
        up = CGEventCreateKeyboardEvent(None, 0, False)
        CGEventKeyboardSetUnicodeString(up, len(piece), piece)
        CGEventPost(kCGHIDEventTap, up)
        i += CHUNK
        if i < n:
            time.sleep(0.004)  # brief pause so apps flush characters
    return True


# ---------- Public API ----------
def paste_text(text: str) -> str:
    """Insert text into the saved frontmost app. Routes via keystroke by default.

    Returns a status string ('ok' | 'focus_lost' | 'no_target') so the caller
    can surface a tray notification when auto-paste likely missed its target.
    Mac path always returns 'ok' for backward compatibility (legacy behaviour
    was fire-and-forget; we keep that on Mac until we add equivalent detection).
    """
    if sys.platform == "win32":
        from core.platform._windows import paste_text as _impl
        return _impl(text)
    global _saved_app
    if not text:
        _saved_app = None
        return "ok"

    backend = get_setting("paste_backend", "keystroke")
    streaming = get_setting("streaming_paste_enabled", False)

    # Restore focus BEFORE typing so chars land in the right window
    _restore_focus()

    if backend == "keystroke":
        if streaming and len(text) > 40:
            # For "streaming" feel with keystroke, we send chars in bursts
            parts = text.split(" ")
            for i, p in enumerate(parts):
                chunk = p + (" " if i < len(parts) - 1 else "")
                if not _type_via_cgevent(chunk):
                    # Fallback mid-operation
                    _paste_via_clipboard(text[sum(len(x) + 1 for x in parts[:i]):])
                    break
                time.sleep(0.02)
        else:
            ok = _type_via_cgevent(text)
            if not ok:
                _paste_via_clipboard(text)
    else:
        _paste_via_clipboard(text)

    _saved_app = None
    return "ok"


def paste_last_transcript(text: str):
    """Alternative entry point for the 'paste last' hotkey — no focus restore
    because the user invoked it from the app they want the paste in."""
    if sys.platform == "win32":
        from core.platform._windows import paste_last_transcript as _impl
        return _impl(text)
    backend = get_setting("paste_backend", "keystroke")
    if backend == "keystroke":
        if not _type_via_cgevent(text):
            _clipboard_write(text)
            _cmd_v()
    else:
        _clipboard_write(text)
        _cmd_v()
