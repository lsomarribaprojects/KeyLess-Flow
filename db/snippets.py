"""Snippets store — voice triggers → text expansions.

Example: trigger "my email" → expansion "you@example.com".
After transcription, any trigger matching (case-insensitive, word-boundary)
is replaced inline by its expansion.
"""
import sqlite3
from config import DB_PATH


class SnippetsDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init()

    def _init(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snippets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger TEXT NOT NULL,
                    expansion TEXT NOT NULL,
                    usage_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snippets_trigger ON snippets(trigger)")
            # Seed defaults on very first run (empty table)
            cur = conn.execute("SELECT COUNT(*) FROM snippets").fetchone()
            if cur[0] == 0:
                defaults = [
                    ("mi correo", "tu@email.com"),
                    ("mi firma", "Saludos,\n[Tu nombre]"),
                    ("firma larga", "Saludos cordiales,\n[Tu nombre]\n[Tu empresa]"),
                ]
                conn.executemany("INSERT INTO snippets (trigger, expansion) VALUES (?, ?)", defaults)

    def list_all(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM snippets ORDER BY usage_count DESC, trigger"
            ).fetchall()
            return [dict(r) for r in rows]

    def add(self, trigger: str, expansion: str) -> int:
        trigger = (trigger or "").strip().lower()
        expansion = expansion or ""
        if not trigger or not expansion:
            raise ValueError("trigger y expansion son requeridos")
        with sqlite3.connect(self.db_path) as conn:
            c = conn.execute(
                "INSERT INTO snippets (trigger, expansion) VALUES (?, ?)",
                (trigger, expansion),
            )
            return c.lastrowid

    def update(self, snippet_id: int, trigger: str, expansion: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE snippets SET trigger = ?, expansion = ? WHERE id = ?",
                (trigger.strip().lower(), expansion, snippet_id),
            )

    def delete(self, snippet_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))

    def increment_usage(self, snippet_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE snippets SET usage_count = usage_count + 1 WHERE id = ?",
                (snippet_id,),
            )
