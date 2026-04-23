"""Relaunch SFlow in-place.

In bundle mode → `open -n` the .app (spawns fresh instance, then current quits).
In dev mode → `os.execv` replaces the current Python process.

Why we need this: macOS only re-prompts for permissions (mic, accessibility,
input monitoring) when the binary actually restarts. A "Reiniciar" button
saves the user from quitting the menu-bar app and reopening it manually.
"""
import os
import sys
import subprocess
import time

from core.logger import log, log_exc


def _bundle_path() -> str | None:
    """Return /Applications/SFlow.app (or wherever the bundle lives) when frozen."""
    if not getattr(sys, "frozen", False):
        return None
    # sys.executable → /Applications/SFlow.app/Contents/MacOS/SFlow
    exe_dir = os.path.dirname(sys.executable)
    bundle = os.path.abspath(os.path.join(exe_dir, "..", ".."))
    if bundle.endswith(".app") and os.path.isdir(bundle):
        return bundle
    return None


def relaunch_app(delay_ms: int = 250):
    """Spawn a new SFlow instance and quit the current one.

    delay_ms: wait before quitting so the new process has time to grab the
    accessibility/mic handles cleanly. 250ms is enough on M-series.
    """
    bundle = _bundle_path()
    log(f"relaunch requested. bundle={bundle} frozen={getattr(sys, 'frozen', False)}")

    try:
        if bundle:
            subprocess.Popen(
                ["/usr/bin/open", "-n", "-a", bundle],
                start_new_session=True,
            )
            time.sleep(delay_ms / 1000.0)
            os._exit(0)
        else:
            python = sys.executable
            args = [python] + sys.argv
            time.sleep(delay_ms / 1000.0)
            os.execv(python, args)
    except Exception as e:
        log_exc("relaunch FAILED", e)
        raise
