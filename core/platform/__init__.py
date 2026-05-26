"""Platform abstraction layer.

Dispatches OS-specific work (paste, foreground-app detection, autostart, etc.)
to a per-platform backend. Existing macOS code paths remain in their original
modules (core/paste.py, core/context.py, ...) and are still authoritative on
Darwin. On Windows we route through `_windows.py` early-returns at each
Mac-specific call site.

Why this exists: KeyLessFlow is a Windows port of the upstream macOS-only
sflow. Keeping Mac code untouched while adding Windows implementations next to
it minimises regression risk until we can validate Mac end-to-end again.
"""
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Used by save_frontmost_app / focus_mode to skip our own window when it
# happens to be the foreground app at the moment of detection.
SELF_APP_NAME_MAC = "SFlow"
SELF_APP_NAME_WIN = "KeyLessFlow"


def backend():
    """Return the platform backend module, or None on macOS (Mac code lives
    in its original locations and is not routed through here)."""
    if IS_WINDOWS:
        from . import _windows
        return _windows
    return None
