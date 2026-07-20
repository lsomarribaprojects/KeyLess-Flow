"""Library store — saved "Redactor" outputs (idea → generated text).

Lets the user keep a searchable history of ideas they've turned into
ready-to-send text via core/redactor.py, so they can reload or re-copy a
past result without re-generating it.
"""
import sqlite3
from config import DB_PATH


class LibraryDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init()

    def _init(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS library_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    idea TEXT NOT NULL,
                    result TEXT NOT NULL,
                    language TEXT,
                    tone TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_library_created ON library_items(created_at)")

    def add(self, title: str, idea: str, result: str, language: str = None, tone: str = None) -> int:
        title = (title or "").strip()
        idea = idea or ""
        result = result or ""
        if not title or not idea or not result:
            raise ValueError("title, idea y result son requeridos")
        with sqlite3.connect(self.db_path) as conn:
            c = conn.execute(
                "INSERT INTO library_items (title, idea, result, language, tone) VALUES (?, ?, ?, ?, ?)",
                (title, idea, result, language, tone),
            )
            return c.lastrowid

    def list_all(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM library_items ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def search(self, q: str) -> list[dict]:
        q = (q or "").strip()
        if not q:
            return self.list_all()
        like = f"%{q}%"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM library_items
                WHERE title LIKE ? OR idea LIKE ? OR result LIKE ?
                ORDER BY created_at DESC
                """,
                (like, like, like),
            ).fetchall()
            return [dict(r) for r in rows]

    def delete(self, item_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM library_items WHERE id = ?", (item_id,))

    def get(self, item_id: int) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM library_items WHERE id = ?", (item_id,)
            ).fetchone()
            return dict(row) if row else None
