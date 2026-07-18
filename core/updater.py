"""Auto-update — poll GitHub Releases, notify, download, run installer.

Flow:
  1. App starts; we schedule a check 10 s later (gives the tray + audio
     subsystems time to settle so a network burst doesn't compete with them).
  2. We GET /releases/latest from GitHub. JSON contains `tag_name` and
     `assets[]`. We pick the .exe asset; ignore .zip/.dmg/etc.
  3. Compare the tag (stripped of leading 'v') against `config.APP_VERSION`.
     If newer, surface a tray balloon: "KeyLess by Sinsajo 1.1.0 disponible…".
  4. User clicks → download asset to %TEMP%\\KeyLessFlow-Setup.exe →
     spawn it with /SILENT /CLOSEAPPLICATIONS so Inno gracefully kills the
     running app, installs over Program Files, and re-launches the new exe.
  5. Current process exits cleanly so the installer can replace its binary.

Rate-limiting: we remember `last_update_check` in settings.json and only
re-check after 24 h. Forces a check via the tray "Buscar actualizaciones".
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from config import APP_VERSION, UPDATE_FEED_URL, get_setting, set_setting
from core.logger import log, log_exc


CHECK_INTERVAL_SECONDS = 24 * 60 * 60  # 1 day between auto-checks


def _parse_version(v: str) -> tuple[int, ...]:
    """Split 'v1.2.3' → (1, 2, 3) for tuple comparison.

    Non-numeric suffixes (e.g. '1.2.3-beta') are stripped so beta builds
    still compare cleanly against stable. If the input is unparsable,
    return (0,) so we don't accidentally suggest a downgrade.
    """
    v = (v or "").lstrip("v").strip()
    parts: list[int] = []
    for chunk in v.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def _pick_exe_asset(assets: list[dict]) -> Optional[dict]:
    """From a GitHub release's assets[], pick the Windows installer."""
    for a in assets:
        name = (a.get("name") or "").lower()
        if name.endswith(".exe") and "setup" in name:
            return a
    # Fallback: first .exe at all
    for a in assets:
        if (a.get("name") or "").lower().endswith(".exe"):
            return a
    return None


def _sha256_file(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class UpdateInfo:
    """Bag of info about an available update."""
    def __init__(self, version: str, download_url: str, notes: str = "",
                 sha256_url: str = ""):
        self.version = version
        self.download_url = download_url
        self.notes = notes
        # URL of the companion "<installer>.sha256" release asset. When
        # present, the downloaded exe MUST match or we refuse to run it —
        # keeps a tampered/corrupted asset from executing silently.
        self.sha256_url = sha256_url

    def __repr__(self) -> str:
        return f"UpdateInfo({self.version!r}, url=…{self.download_url[-30:]})"


class UpdateChecker(QObject):
    """Qt-friendly update poller. Signals fire on the main thread so the
    tray can show notifications without thread-safety dances.

    Signals:
        update_available(UpdateInfo) — newer version found on GitHub
        download_progress(int)      — 0-100 during download
        ready_to_install(str)       — path to the downloaded installer
        error(str)                  — human-readable failure reason
    """
    update_available = pyqtSignal(object)        # UpdateInfo
    download_progress = pyqtSignal(int)
    ready_to_install = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    # ------------------------------------------------------------------ check
    def check_async(self, force: bool = False) -> None:
        """Spawn a background thread to hit GitHub. Returns immediately.

        `force=True` bypasses the 24 h rate-limit (use for a manual
        "Buscar actualizaciones" menu action).
        """
        threading.Thread(
            target=self._check_worker,
            args=(force,),
            daemon=True,
            name="KFUpdateChecker",
        ).start()

    def _check_worker(self, force: bool) -> None:
        if not force and not self._is_due_for_check():
            return
        try:
            info = self._fetch_latest()
        except Exception as e:
            log_exc("update check FAILED", e)
            self.error.emit(str(e))
            return

        # Always record the attempt so we don't hammer GitHub on app launch loops.
        set_setting("last_update_check", int(time.time()))

        if info is None:
            log("update check: no exe asset found in latest release", level="WARN")
            return

        current = _parse_version(APP_VERSION)
        latest = _parse_version(info.version)
        if latest > current:
            log(f"update available: {APP_VERSION} → {info.version}")
            self.update_available.emit(info)
        else:
            log(f"update check ok: {APP_VERSION} is current (latest={info.version})")

    def _is_due_for_check(self) -> bool:
        last = get_setting("last_update_check", 0)
        try:
            last_ts = int(last)
        except Exception:
            last_ts = 0
        return (time.time() - last_ts) >= CHECK_INTERVAL_SECONDS

    def _fetch_latest(self) -> Optional[UpdateInfo]:
        req = urllib.request.Request(
            UPDATE_FEED_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"KeyLessFlow/{APP_VERSION}",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        tag = (data.get("tag_name") or "").strip()
        if not tag:
            return None
        assets = data.get("assets") or []
        asset = _pick_exe_asset(assets)
        if not asset:
            return None
        # Companion checksum asset: "<exact exe name>.sha256" (case-insens).
        exe_name = (asset.get("name") or "").lower()
        sha_url = ""
        for a in assets:
            if (a.get("name") or "").lower() == exe_name + ".sha256":
                sha_url = a.get("browser_download_url", "")
                break
        return UpdateInfo(
            version=tag,
            download_url=asset.get("browser_download_url", ""),
            notes=data.get("body", "") or "",
            sha256_url=sha_url,
        )

    # ------------------------------------------------------- download + run
    def download_and_run_async(
        self,
        info: UpdateInfo,
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        """Background download → save to %TEMP% → exec the installer.

        on_done fires on the main thread after we exec the installer (you
        typically want to QApplication.quit() there so the installer can
        overwrite our binary cleanly).
        """
        threading.Thread(
            target=self._download_worker,
            args=(info, on_done),
            daemon=True,
            name="KFUpdateDownload",
        ).start()

    def _download_worker(
        self,
        info: UpdateInfo,
        on_done: Optional[Callable[[], None]],
    ) -> None:
        try:
            dest = self._download(info.download_url)
        except Exception as e:
            log_exc("update download FAILED", e)
            self.error.emit(f"Descarga fallida: {e}")
            return

        # Integrity gate: when the release publishes a .sha256 companion, the
        # download must match it or we refuse to execute. No checksum asset →
        # log loudly but proceed (older releases predate the checksum flow).
        if info.sha256_url:
            try:
                req = urllib.request.Request(
                    info.sha256_url, headers={"User-Agent": f"KeyLessFlow/{APP_VERSION}"},
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    expected = resp.read().decode("utf-8", "replace").split()[0].strip().lower()
                actual = _sha256_file(dest).lower()
                if expected != actual:
                    log(
                        f"update REJECTED: sha256 mismatch expected={expected[:12]}… "
                        f"actual={actual[:12]}…", level="ERROR",
                    )
                    try:
                        os.remove(dest)
                    except Exception:
                        pass
                    self.error.emit(
                        "La actualización descargada no pasó la verificación de "
                        "integridad y fue descartada. Intenta más tarde."
                    )
                    return
                log("update sha256 verified OK")
            except Exception as e:
                # Can't fetch/parse the checksum — fail CLOSED (don't run an
                # unverifiable binary when the release claims to have one).
                log_exc("update sha256 check failed", e)
                self.error.emit("No se pudo verificar la actualización. Intenta más tarde.")
                return
        else:
            log("update: release has no .sha256 asset — skipping verification", level="WARN")

        self.ready_to_install.emit(dest)
        try:
            self._exec_installer(dest)
        except Exception as e:
            log_exc("update exec FAILED", e)
            self.error.emit(f"No se pudo ejecutar el instalador: {e}")
            return

        if on_done is not None:
            try:
                on_done()
            except Exception:
                pass

    def _download(self, url: str) -> str:
        tmp_dir = tempfile.gettempdir()
        dest = os.path.join(tmp_dir, "KeyLessFlow-Setup.exe")
        log(f"updater: downloading {url} → {dest}")

        def reporthook(block_num, block_size, total_size):
            if total_size <= 0:
                return
            pct = min(100, int(block_num * block_size * 100 / total_size))
            self.download_progress.emit(pct)

        urllib.request.urlretrieve(url, dest, reporthook=reporthook)
        return dest

    def _exec_installer(self, path: str) -> None:
        """Run Inno Setup installer silently. CLOSEAPPLICATIONS lets the
        installer close us gracefully so it can overwrite the .exe.
        RESTARTAPPLICATIONS re-launches after install completes.
        """
        log(f"updater: launching installer {path}")
        # detach so the installer outlives our process
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        flags = 0
        if sys.platform == "win32":
            flags = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        subprocess.Popen(
            [
                path,
                "/SILENT",                 # progress bar, no questions
                "/CLOSEAPPLICATIONS",      # close us before overwriting
                "/RESTARTAPPLICATIONS",    # re-launch the new exe after
                "/NORESTART",              # don't reboot Windows
            ],
            creationflags=flags,
            close_fds=True,
        )
