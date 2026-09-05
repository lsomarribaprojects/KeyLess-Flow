"""Reliability + retention + usage regression tests (2026-08 goal).

Covers the four things that made recordings "end in errors" or pile up:
  1. Error classification -> friendly message + retryable flag.
  2. Transcriber retry with backoff on transient errors ONLY.
  3. Failed transcriptions become DB rows (visible + retryable in the Hub)
     and heal to status='ok' on a successful retry.
  4. Audio retention policy (age, failed-kept-longer, orphans, size cap).
  5. Usage meter math (month/today/remaining/pct).

Runs without pytest:  venv\\Scripts\\python.exe tests\\test_reliability.py
"""
import io
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ------------------------------------------------------------ 1. classify
def test_classify_kinds():
    from core.errors import classify, KIND_OFFLINE, KIND_RATE, KIND_PROVIDER, KIND_AUTH, KIND_QUOTA

    class APIConnectionError(Exception):
        pass

    class RateLimitError(Exception):
        status_code = 429

    class AuthenticationError(Exception):
        status_code = 401

    k, msg, r = classify(APIConnectionError("boom"))
    assert k == KIND_OFFLINE and r and "conexión" in msg.lower()
    k, _, r = classify(RateLimitError("429"))
    assert k == KIND_RATE and r
    k, _, r = classify(RuntimeError("Backend error 502: bad_gateway"))
    assert k == KIND_PROVIDER and r
    k, _, r = classify(AuthenticationError("invalid api key"))
    assert k == KIND_AUTH and not r
    k, _, r = classify(RuntimeError("Tu suscripción no está activa. Renueva en tu cuenta."))
    assert k == KIND_QUOTA and not r
    k, _, r = classify(ConnectionResetError())
    assert k == KIND_OFFLINE and r


# --------------------------------------------------------------- 2. retry
class _Flaky:
    """Backend that fails N times with `exc` then succeeds."""
    model_id = "fake"

    def __init__(self, fails, exc):
        self.fails, self.exc, self.calls = fails, exc, 0

    def transcribe(self, buf, vocabulary_prompt=""):
        self.calls += 1
        if self.calls <= self.fails:
            raise self.exc
        return "hola mundo"


def _router(backend):
    from core.transcriber import Transcriber
    t = Transcriber.__new__(Transcriber)  # skip __init__ (no network clients)
    t._sleep = lambda s: None
    return t


def test_retry_recovers_from_transient():
    class APITimeoutError(Exception):
        pass
    b = _Flaky(2, APITimeoutError("timed out"))
    t = _router(b)
    out = t._call_with_retry(b, io.BytesIO(b"x" * 200), "")
    assert out == "hola mundo" and b.calls == 3


def test_retry_gives_up_after_max():
    from core.transcriber import RETRY_ATTEMPTS

    class APIConnectionError(Exception):
        pass
    b = _Flaky(99, APIConnectionError("net down"))
    t = _router(b)
    try:
        t._call_with_retry(b, io.BytesIO(b"x" * 200), "")
        assert False, "should raise"
    except APIConnectionError:
        pass
    assert b.calls == RETRY_ATTEMPTS


def test_no_retry_on_auth():
    class AuthenticationError(Exception):
        status_code = 401
    b = _Flaky(99, AuthenticationError("invalid api key"))
    t = _router(b)
    try:
        t._call_with_retry(b, io.BytesIO(b"x" * 200), "")
        assert False
    except AuthenticationError:
        pass
    assert b.calls == 1, f"retried an auth error {b.calls}x"


# ------------------------------------------------------ 3. failed rows
def _tmp_db():
    from db.database import TranscriptionDB
    d = tempfile.mkdtemp()
    return TranscriptionDB(os.path.join(d, "t.db")), d


def test_failed_row_visible_and_heals():
    db, d = _tmp_db()
    wav = os.path.join(d, "a.wav")
    open(wav, "wb").write(b"RIFF")
    rid = db.insert(text="", duration_seconds=12, model="failed",
                    audio_path=wav, status="failed", error_message="offline")
    rows = db.get_recent()
    assert rows[0]["status"] == "failed" and rows[0]["id"] == rid
    assert db.last_failed()["id"] == rid
    assert db.usage_seconds("2000-01-01 00:00:00") == 0, "failed rows must not count as usage"
    db.update_text(rid, "texto recuperado")
    row = db.get(rid)
    assert row["status"] == "ok" and row["text"] == "texto recuperado"
    assert db.last_failed() is None
    assert db.usage_seconds("2000-01-01 00:00:00") == 12


def test_migration_adds_status_to_old_db():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "old.db")
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE transcriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "text TEXT NOT NULL, language TEXT, duration_seconds REAL, model TEXT, "
                  "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("INSERT INTO transcriptions (text, duration_seconds) VALUES ('viejo', 5)")
    from db.database import TranscriptionDB
    db = TranscriptionDB(p)
    r = db.get_recent()[0]
    assert r["status"] in (None, "ok") and "audio_path" in r
    assert db.usage_seconds("2000-01-01 00:00:00") == 5  # NULL status counts as ok


# ------------------------------------------------------- 4. retention
def _row_at(db, audio, days_ago, status="ok"):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - days_ago * 86400))
    with sqlite3.connect(db.db_path) as c:
        cur = c.execute(
            "INSERT INTO transcriptions (text, duration_seconds, audio_path, status, created_at) "
            "VALUES ('t', 1, ?, ?, ?)", (audio, status, ts))
        return cur.lastrowid


def _wav(d, name, size=1024, mtime_days_ago=0):
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(b"\0" * size)
    t = time.time() - mtime_days_ago * 86400
    os.utime(p, (t, t))
    return p


def test_retention_policy():
    from core.retention import prune
    db, d = _tmp_db()
    audio_dir = os.path.join(d, "audio")
    os.makedirs(audio_dir)
    fresh_ok = _wav(audio_dir, "fresh_ok.wav")
    old_ok = _wav(audio_dir, "old_ok.wav")
    old_failed = _wav(audio_dir, "old_failed.wav")
    ancient_failed = _wav(audio_dir, "ancient_failed.wav")
    orphan_new = _wav(audio_dir, "orphan_new.wav", mtime_days_ago=0)
    orphan_old = _wav(audio_dir, "orphan_old.wav", mtime_days_ago=3)
    _row_at(db, fresh_ok, 1)
    r_old_ok = _row_at(db, old_ok, 10)
    _row_at(db, old_failed, 10, status="failed")       # < 30d -> KEEP
    _row_at(db, ancient_failed, 40, status="failed")   # > 30d -> delete

    res = prune(db, audio_dir, retention_days=7, failed_retention_days=30, max_mb=500)
    left = set(os.listdir(audio_dir))
    assert "fresh_ok.wav" in left, "deleted a fresh successful recording"
    assert "old_ok.wav" not in left, "did not expire a 10-day-old ok recording"
    assert "old_failed.wav" in left, "deleted a failed recording still inside its 30d window"
    assert "ancient_failed.wav" not in left
    assert "orphan_new.wav" in left, "deleted a <1 day orphan (could be an in-flight recording)"
    assert "orphan_old.wav" not in left
    assert db.get(r_old_ok)["audio_path"] is None, "DB still points at a deleted WAV"
    assert res["deleted"] == 3


def test_retention_size_cap_evicts_oldest_ok_only():
    from core.retention import prune
    db, d = _tmp_db()
    audio_dir = os.path.join(d, "audio")
    os.makedirs(audio_dir)
    big_old = _wav(audio_dir, "big_old.wav", size=600 * 1024)
    big_new = _wav(audio_dir, "big_new.wav", size=600 * 1024)
    failed = _wav(audio_dir, "failed.wav", size=600 * 1024)
    _row_at(db, big_old, 2)
    _row_at(db, big_new, 1)
    _row_at(db, failed, 1, status="failed")
    prune(db, audio_dir, retention_days=7, failed_retention_days=30, max_mb=1)  # cap 1 MB
    left = set(os.listdir(audio_dir))
    assert "big_old.wav" not in left, "oldest ok should be evicted first"
    assert "failed.wav" in left, "size cap must never evict failed (retryable) audio"


# ------------------------------------------------------------ 5. usage
def test_usage_summary_math():
    from core import usage
    db, _ = _tmp_db()
    db.insert(text="a", duration_seconds=3600)      # 1h now (this month, today)
    db.insert(text="b", duration_seconds=1800)      # +30m
    db.insert(text="f", duration_seconds=9999, status="failed")  # ignored
    old = getattr(usage, "budget_hours")
    usage.budget_hours = lambda: 8.0
    try:
        s = usage.summary(db)
    finally:
        usage.budget_hours = old
    assert s["month_seconds"] == 5400 and s["today_seconds"] == 5400
    assert s["budget_seconds"] == 8 * 3600
    assert s["remaining_seconds"] == 8 * 3600 - 5400
    assert abs(s["pct"] - 18.8) < 0.2
    assert usage.fmt_hms(5400) == "1h 30m"
    assert usage.fmt_hms(75) == "1m 15s"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
