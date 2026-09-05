"""Audio retention — keep recordings long enough to recover, never long enough
to fill the disk.

Before this module the WAV checkpoints (%LOCALAPPDATA%\\KeyLessFlow\\audio)
grew forever: prune_old_audio_paths() existed in the DB layer but nothing
ever called it. Policy now (same shape as Wispr Flow / superwhisper: rolling
window + size cap, failed items kept longer):

  1. Successful transcriptions: WAV deleted after `retention_days` (7).
  2. FAILED transcriptions (kept for retry): WAV kept `failed_retention_days` (30).
  3. Orphan WAVs (no DB row — crash leftovers, sub-threshold taps): deleted
     after 1 day.
  4. If the folder still exceeds `max_mb` (500), delete the OLDEST successful
     WAVs first until under the cap. Failed ones are never evicted by the cap.

Runs 30 s after launch and every 24 h. Pure function core + tiny I/O so it's
unit-testable with a temp dir and a temp DB.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from core.logger import log


def _parse_ts(ts: str) -> float:
    """sqlite CURRENT_TIMESTAMP is UTC 'YYYY-MM-DD HH:MM:SS' → epoch."""
    try:
        dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return time.time()


def prune(
    db,
    audio_dir: str,
    retention_days: int = 7,
    failed_retention_days: int = 30,
    max_mb: int = 500,
    now: float | None = None,
) -> dict:
    now = now or time.time()
    deleted = 0
    freed = 0
    if not os.path.isdir(audio_dir):
        return {"deleted": 0, "freed_mb": 0.0, "kept": 0, "total_mb": 0.0}

    def _unlink(path: str) -> int:
        try:
            size = os.path.getsize(path)
            os.remove(path)
            return size
        except OSError:
            return 0

    # --- 1 & 2: age-based, driven by DB rows -----------------------------
    rows = db.rows_with_audio()
    referenced: set[str] = set()
    expired_ids: list[int] = []
    for r in rows:
        path = r["audio_path"]
        referenced.add(os.path.normcase(os.path.abspath(path)))
        age_days = (now - _parse_ts(r["created_at"])) / 86400.0
        limit = failed_retention_days if r.get("status") == "failed" else retention_days
        if age_days > limit:
            if os.path.exists(path):
                freed += _unlink(path)
                deleted += 1
            expired_ids.append(r["id"])
    if expired_ids:
        db.clear_audio_paths(expired_ids)

    # --- 3: orphans --------------------------------------------------------
    for name in os.listdir(audio_dir):
        if not name.lower().endswith(".wav"):
            continue
        path = os.path.join(audio_dir, name)
        key = os.path.normcase(os.path.abspath(path))
        if key in referenced:
            continue
        try:
            age_days = (now - os.path.getmtime(path)) / 86400.0
        except OSError:
            continue
        if age_days > 1.0:
            freed += _unlink(path)
            deleted += 1

    # --- 4: size cap — evict oldest SUCCESSFUL first ---------------------
    def _total() -> int:
        t = 0
        for name in os.listdir(audio_dir):
            p = os.path.join(audio_dir, name)
            try:
                t += os.path.getsize(p)
            except OSError:
                pass
        return t

    cap = max_mb * 1024 * 1024
    if _total() > cap:
        candidates = [
            r for r in db.rows_with_audio()
            if r.get("status") != "failed" and os.path.exists(r["audio_path"])
        ]
        candidates.sort(key=lambda r: _parse_ts(r["created_at"]))  # oldest first
        evicted: list[int] = []
        for r in candidates:
            if _total() <= cap:
                break
            freed += _unlink(r["audio_path"])
            deleted += 1
            evicted.append(r["id"])
        if evicted:
            db.clear_audio_paths(evicted)

    total_mb = _total() / (1024 * 1024)
    result = {
        "deleted": deleted,
        "freed_mb": round(freed / (1024 * 1024), 1),
        "kept": len([n for n in os.listdir(audio_dir) if n.lower().endswith(".wav")]),
        "total_mb": round(total_mb, 1),
    }
    log(f"retention: {result}")
    return result
