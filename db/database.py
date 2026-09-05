import sqlite3
from config import DB_PATH


class TranscriptionDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transcriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    language TEXT,
                    duration_seconds REAL,
                    model TEXT DEFAULT 'whisper-large-v3-turbo',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_transcriptions_created_at
                ON transcriptions(created_at)
            """)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(transcriptions)").fetchall()]
            # Migration: audio_path (retry from history)
            if "audio_path" not in cols:
                conn.execute("ALTER TABLE transcriptions ADD COLUMN audio_path TEXT")
            # Migration (2026-07): failed transcriptions now get a row too, so
            # they show in the Hub with a "Re-transcribir" action instead of
            # leaving an orphan WAV nobody can find.
            if "status" not in cols:
                conn.execute("ALTER TABLE transcriptions ADD COLUMN status TEXT DEFAULT 'ok'")
            if "error_message" not in cols:
                conn.execute("ALTER TABLE transcriptions ADD COLUMN error_message TEXT")

    def insert(self, text: str, language: str = None, duration_seconds: float = None,
               model: str = "whisper-large-v3-turbo", audio_path: str = None,
               status: str = "ok", error_message: str = None) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO transcriptions (text, language, duration_seconds, model, "
                "audio_path, status, error_message) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (text, language, duration_seconds, model, audio_path, status, error_message),
            )
            return cursor.lastrowid

    def update_text(self, row_id: int, new_text: str):
        """Set the text and mark the row successful (used by retry + edits)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE transcriptions SET text = ?, status = 'ok', error_message = NULL "
                "WHERE id = ?",
                (new_text, row_id),
            )

    def get_recent(self, limit: int = 20) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM transcriptions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get(self, row_id: int) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT * FROM transcriptions WHERE id = ?", (row_id,)).fetchone()
            return dict(r) if r else None

    def search(self, query: str, limit: int = 20) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM transcriptions WHERE text LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM transcriptions").fetchone()[0]

    # ------------------------------------------------------------ usage
    def usage_seconds(self, since: str) -> int:
        """Sum of recorded audio (successful rows) since a UTC timestamp
        'YYYY-MM-DD HH:MM:SS' (sqlite CURRENT_TIMESTAMP format)."""
        with sqlite3.connect(self.db_path) as conn:
            r = conn.execute(
                "SELECT COALESCE(SUM(duration_seconds), 0) FROM transcriptions "
                "WHERE created_at >= ? AND COALESCE(status, 'ok') = 'ok'",
                (since,),
            ).fetchone()
            return int(r[0] or 0)

    # -------------------------------------------------------- retention
    def rows_with_audio(self) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, audio_path, created_at, COALESCE(status, 'ok') AS status "
                "FROM transcriptions WHERE audio_path IS NOT NULL"
            ).fetchall()
            return [dict(r) for r in rows]

    def clear_audio_paths(self, ids: list[int]):
        if not ids:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "UPDATE transcriptions SET audio_path = NULL WHERE id = ?",
                [(i,) for i in ids],
            )

    def last_failed(self) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT * FROM transcriptions WHERE status = 'failed' AND audio_path IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(r) if r else None
