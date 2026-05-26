"""Focus Mode — block activation of distracting apps while recording.

Uses NSWorkspace's `didActivateApplicationNotification` observer. When a
distracting app is activated AND focus mode is ON AND a dictation is in
progress, we immediately re-activate the previously focused (non-distracting)
app. The user can override by toggling focus mode off from the tray.

This is best-effort — apps that brute-force focus (e.g. full-screen launchers)
may win the race. It's designed as a gentle nudge, not a hard lock.
"""
from PyQt6.QtCore import QObject, pyqtSignal
from config import get_setting


_BUILTIN_DISTRACTORS = {
    # bundle ids of common distractors — exact match
    "com.tinyspeck.slackmacgap",   # Slack
    "com.hnc.Discord",
    "com.twitter.tweetdeck",
    "com.apple.mail",
    "com.google.Chrome",           # often triggers distraction
}


class FocusController(QObject):
    blocked = pyqtSignal(str)  # app name when we bounced away

    def __init__(self):
        super().__init__()
        self._active = False
        self._anchor_bundle = None  # app we want to return to
        self._observer_registered = False
        self._observer_obj = None

    def enter(self, anchor_bundle: str | None):
        """Called when dictation starts. Remember the anchor and install observer."""
        if not get_setting("focus_mode_enabled", False):
            return
        self._active = True
        self._anchor_bundle = anchor_bundle
        self._install_observer()

    def leave(self):
        self._active = False
        self._anchor_bundle = None
        self._remove_observer()

    def _distractors(self) -> set[str]:
        # User custom list from settings + builtins
        custom = get_setting("focus_mode_apps", []) or []
        return _BUILTIN_DISTRACTORS.union(custom)

    def _install_observer(self):
        if self._observer_registered:
            return
        # Windows v1: focus mode is a no-op. The NSWorkspace observer pattern
        # has no direct equivalent; a SetWinEventHook-based version can land
        # later if users actually use focus mode.
        import sys as _sys
        if _sys.platform == "win32":
            return
        try:
            from AppKit import NSWorkspace, NSWorkspaceDidActivateApplicationNotification
            from Foundation import NSObject, NSNotificationCenter
            import objc

            controller = self

            class _Observer(NSObject):
                def handle_(self, notification):
                    try:
                        info = notification.userInfo()
                        if info is None:
                            return
                        app = info.objectForKey_("NSWorkspaceApplicationKey")
                        if app is None:
                            return
                        bundle = str(app.bundleIdentifier() or "")
                        name = str(app.localizedName() or "")
                        if not controller._active:
                            return
                        if bundle == "so.saasfactory.sflow" or name == "SFlow":
                            return
                        if bundle in controller._distractors():
                            # Bounce back to anchor
                            try:
                                from AppKit import NSWorkspace as _WS
                                ws = _WS.sharedWorkspace()
                                for running in ws.runningApplications():
                                    rb = str(running.bundleIdentifier() or "")
                                    if rb == controller._anchor_bundle:
                                        running.activateWithOptions_(1 << 1)
                                        break
                            except Exception:
                                pass
                            controller.blocked.emit(name or bundle)
                    except Exception:
                        pass

            obs = _Observer.alloc().init()
            wc = NSWorkspace.sharedWorkspace().notificationCenter()
            wc.addObserver_selector_name_object_(
                obs, objc.selector(obs.handle_, signature=b"v@:@"),
                NSWorkspaceDidActivateApplicationNotification,
                None,
            )
            self._observer_obj = obs
            self._observer_registered = True
        except Exception as e:
            print(f"focus mode observer failed: {e}")

    def _remove_observer(self):
        if not self._observer_registered:
            return
        try:
            from AppKit import NSWorkspace
            wc = NSWorkspace.sharedWorkspace().notificationCenter()
            wc.removeObserver_(self._observer_obj)
        except Exception:
            pass
        self._observer_obj = None
        self._observer_registered = False
