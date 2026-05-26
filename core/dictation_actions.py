"""Post-transcription verbal actions. Inspired by Wispr Flow's 'press enter'.

Detects trailing voice commands (in Spanish + English), strips them from the
text, and returns a list of actions to perform after paste.

Supported actions: press_enter.
"""
import re
import sys
import subprocess


ACTION_PRESS_ENTER = "press_enter"


_PRESS_ENTER_SUFFIX = re.compile(
    r"[\s,;.:]*\b(press\s+enter|presion[ae]r?\s+enter|enter\s+final|da\s+enter|dale\s+enter)\b[\s.,!?]*$",
    re.IGNORECASE,
)


def extract_actions(text: str) -> tuple[str, list[str]]:
    """Return (cleaned_text, actions). Currently supports trailing press-enter."""
    if not text:
        return text, []
    actions = []
    cleaned = _PRESS_ENTER_SUFFIX.sub("", text).rstrip(" .,;:!?")
    if cleaned != text:
        actions.append(ACTION_PRESS_ENTER)
    return cleaned, actions


def perform_actions(actions: list[str]):
    for a in actions:
        if a == ACTION_PRESS_ENTER:
            _press_enter()


def _press_enter():
    if sys.platform == "win32":
        from core.platform._windows import press_enter as _impl
        return _impl()
    # Native CGEvent Enter (keycode 36 = Return)
    try:
        from Quartz import CGEventCreateKeyboardEvent, CGEventPost, kCGHIDEventTap
        down = CGEventCreateKeyboardEvent(None, 36, True)
        CGEventPost(kCGHIDEventTap, down)
        up = CGEventCreateKeyboardEvent(None, 36, False)
        CGEventPost(kCGHIDEventTap, up)
        return
    except Exception:
        pass
    # Fallback: AppleScript
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to key code 36'],
            check=True, timeout=1,
        )
    except Exception:
        pass
