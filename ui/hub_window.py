"""Hub window — Wispr Flow-style dashboard with sidebar + history.

Opened from the tray menu or Cmd+Shift+H. Non-activating — doesn't steal
focus from the app the user is working in (uses native NSPanel behavior
like the pill).
"""
from datetime import datetime, timezone
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QFrame, QStackedWidget, QListWidget, QListWidgetItem,
    QMenu, QPlainTextEdit, QGroupBox, QCheckBox, QComboBox, QDialog,
    QApplication, QMessageBox, QSizePolicy, QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QAction, QPixmap, QFont, QPainter, QColor, QPen
from db.database import TranscriptionDB
from db.snippets import SnippetsDB
from core.paste import paste_last_transcript
from core.relaunch import relaunch_app
from config import (
    LOGO_PATH, DICTIONARY_PATH, get_setting, set_setting,
)
import os
import subprocess


# ---------- Color palette ----------
class C:
    BG = "#0f0f0f"
    BG_ALT = "#1a1a1a"
    BG_HOVER = "#222222"
    BG_CARD = "#181818"
    BG_INPUT = "#202020"
    TEXT = "#e8e8e8"
    TEXT_DIM = "#8a8a8a"
    TEXT_FAINT = "#555555"
    ACCENT = "#5a9fff"
    DIVIDER = "#2a2a2a"
    OK = "#50d278"
    ERR = "#ff4646"


# ---------- Helpers ----------
def time_ago(iso_ts: str) -> str:
    try:
        if not iso_ts:
            return ""
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        s = int(delta.total_seconds())
        if s < 60:
            return "hace un momento"
        if s < 3600:
            return f"hace {s // 60}m"
        if s < 86400:
            return f"hace {s // 3600}h"
        if s < 604800:
            return f"hace {s // 86400}d"
        return dt.strftime("%d %b")
    except Exception:
        return iso_ts or ""


class EditTranscriptDialog(QDialog):
    """Edit a past transcription. After save, suggest new words (capitalized /
    hyphenated) to add to dictionary.txt for future Whisper boosting."""
    saved = pyqtSignal(int)  # row id

    def __init__(self, row_id: int, original_text: str):
        super().__init__()
        self.row_id = row_id
        self.original = original_text
        self.setWindowTitle("Editar transcripción")
        self.resize(580, 340)
        self.setStyleSheet(f"background: {C.BG}; color: {C.TEXT};")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        lbl = QLabel("Corrige el texto. SFlow sugiere automáticamente palabras nuevas (nombres, jerga) para el diccionario.")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 12px;")
        root.addWidget(lbl)

        self.editor = QPlainTextEdit()
        self.editor.setPlainText(original_text)
        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {C.BG_INPUT}; color: {C.TEXT};
                border: 1px solid {C.DIVIDER}; border-radius: 6px;
                padding: 10px; font-size: 13px;
            }}
            QPlainTextEdit:focus {{ border-color: {C.ACCENT}; }}
        """)
        root.addWidget(self.editor, 1)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{
                background: {C.BG_INPUT}; color: {C.TEXT};
                border: 1px solid {C.DIVIDER}; border-radius: 6px;
                padding: 8px 18px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {C.BG_HOVER}; }}
        """)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

        save = QPushButton("Guardar")
        save.setDefault(True)
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setStyleSheet(f"""
            QPushButton {{
                background: {C.ACCENT}; color: white;
                border: none; border-radius: 6px;
                padding: 8px 18px; font-weight: 500; font-size: 12px;
            }}
            QPushButton:hover {{ background: #4a8fef; }}
        """)
        save.clicked.connect(self._save)
        row.addWidget(save)
        root.addLayout(row)

    def _save(self):
        new_text = self.editor.toPlainText().strip()
        if not new_text or new_text == self.original:
            self.accept()
            return

        from core.dictionary_learner import diff_candidates, add_to_dictionary
        from db.database import TranscriptionDB

        TranscriptionDB().update_text(self.row_id, new_text)

        candidates = diff_candidates(self.original, new_text)
        if candidates:
            msg = QMessageBox(self)
            msg.setWindowTitle("Palabras detectadas")
            msg.setText("Detectamos estas palabras nuevas en tu corrección:\n\n"
                        + "\n".join(f"  • {c}" for c in candidates[:10])
                        + "\n\n¿Agregarlas al diccionario personal? (mejora reconocimiento futuro)")
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg.setDefaultButton(QMessageBox.StandardButton.Yes)
            if msg.exec() == QMessageBox.StandardButton.Yes:
                add_to_dictionary(candidates)

        self.saved.emit(self.row_id)
        self.accept()


class SidebarButton(QPushButton):
    def __init__(self, icon_text: str, label: str):
        super().__init__(f"{icon_text}   {label}")
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setFont(QFont("Helvetica Neue", 13))
        self.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                padding: 6px 12px;
                border: none;
                border-radius: 8px;
                color: {C.TEXT_DIM};
                background: transparent;
            }}
            QPushButton:hover {{
                background: {C.BG_HOVER};
                color: {C.TEXT};
            }}
            QPushButton:checked {{
                background: {C.BG_CARD};
                color: {C.TEXT};
                font-weight: 500;
            }}
        """)


class TranscriptionCard(QFrame):
    delete_requested = pyqtSignal(int)
    copy_requested = pyqtSignal(str)
    repaste_requested = pyqtSignal(str)
    retry_requested = pyqtSignal(int)

    def __init__(self, row: dict):
        super().__init__()
        self._row = row
        self._id = row.get("id")
        self._text = row.get("text", "") or ""
        self._model = row.get("model", "") or ""
        self._duration = row.get("duration_seconds") or 0.0
        self._created = row.get("created_at", "") or ""
        self._audio_path = row.get("audio_path") or ""
        self._expanded = False

        self.setStyleSheet(f"""
            TranscriptionCard {{
                background: {C.BG_CARD};
                border-radius: 10px;
                border: 1px solid {C.DIVIDER};
            }}
            TranscriptionCard:hover {{
                border-color: {C.BG_HOVER};
            }}
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        root = QVBoxLayout()
        root.setContentsMargins(14, 10, 14, 12)
        root.setSpacing(6)

        # Header: time · model · duration
        head = QHBoxLayout()
        head.setSpacing(6)
        meta = QLabel(self._meta_line())
        meta.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: 11px;")
        head.addWidget(meta)
        head.addStretch()

        menu_btn = QPushButton("⋮")
        menu_btn.setFixedSize(22, 22)
        menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        menu_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: none; font-size: 16px; font-weight: bold;
                border-radius: 4px;
            }}
            QPushButton:hover {{ background: {C.BG_HOVER}; color: {C.TEXT}; }}
        """)
        menu_btn.clicked.connect(self._show_menu)
        head.addWidget(menu_btn)
        root.addLayout(head)

        # Body text
        self._text_label = QLabel(self._preview())
        self._text_label.setWordWrap(True)
        self._text_label.setStyleSheet(f"color: {C.TEXT}; font-size: 13px; line-height: 1.45;")
        self._text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._text_label)

        self.setLayout(root)

    def _meta_line(self) -> str:
        parts = [time_ago(self._created)]
        if self._duration:
            parts.append(f"{self._duration:.1f}s")
        if self._model:
            # shorten long model ids
            m = self._model
            if "/" in m:
                m = m.split("/")[-1]
            parts.append(m)
        return " · ".join(p for p in parts if p)

    def _preview(self) -> str:
        t = self._text
        if self._expanded or len(t) <= 220:
            return t
        return t[:220].rstrip() + "…"

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._expanded = not self._expanded
            self._text_label.setText(self._preview())

    def _show_menu(self):
        m = QMenu(self)
        m.setStyleSheet(f"""
            QMenu {{
                background: {C.BG_CARD}; color: {C.TEXT};
                border: 1px solid {C.DIVIDER}; border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {C.BG_HOVER}; }}
        """)
        a_copy = QAction("Copiar al portapapeles", m)
        a_copy.triggered.connect(lambda: self.copy_requested.emit(self._text))
        m.addAction(a_copy)

        a_repaste = QAction("Re-pegar en cursor activo", m)
        a_repaste.triggered.connect(lambda: self.repaste_requested.emit(self._text))
        m.addAction(a_repaste)

        a_edit = QAction("Editar y aprender al diccionario", m)
        a_edit.triggered.connect(self._open_edit)
        m.addAction(a_edit)

        import os
        if self._audio_path and os.path.exists(self._audio_path):
            a_retry = QAction("Re-transcribir audio", m)
            a_retry.triggered.connect(lambda: self.retry_requested.emit(self._id))
            m.addAction(a_retry)

        m.addSeparator()
        a_del = QAction("Eliminar", m)
        a_del.triggered.connect(lambda: self.delete_requested.emit(self._id))
        m.addAction(a_del)
        m.exec(self.mapToGlobal(self.rect().topRight()))

    def _open_edit(self):
        dlg = EditTranscriptDialog(self._id, self._text)
        dlg.saved.connect(self.retry_requested.emit)  # piggyback reload
        dlg.exec()


class HistoryPage(QWidget):
    def __init__(self, db: TranscriptionDB):
        super().__init__()
        self.db = db
        self._all_rows: list[dict] = []

        root = QVBoxLayout()
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        title = QLabel("Historial")
        title.setStyleSheet(f"color: {C.TEXT}; font-size: 22px; font-weight: 600;")
        root.addWidget(title)

        sub = QLabel("Tus transcripciones recientes. Click en una para expandir, ⋮ para acciones.")
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 12px;")
        root.addWidget(sub)

        # Search + refresh
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍  Buscar transcripciones…")
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(f"""
            QLineEdit {{
                background: {C.BG_INPUT}; color: {C.TEXT};
                border: 1px solid {C.DIVIDER}; border-radius: 8px;
                padding: 8px 12px; font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {C.ACCENT}; }}
        """)
        self.search.textChanged.connect(self._filter)
        row.addWidget(self.search)

        refresh = QPushButton("↻")
        refresh.setFixedSize(36, 36)
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.setStyleSheet(f"""
            QPushButton {{
                background: {C.BG_INPUT}; color: {C.TEXT_DIM};
                border: 1px solid {C.DIVIDER}; border-radius: 8px;
                font-size: 16px;
            }}
            QPushButton:hover {{ background: {C.BG_HOVER}; color: {C.TEXT}; }}
        """)
        refresh.clicked.connect(self.reload)
        row.addWidget(refresh)

        root.addLayout(row)

        # Scroll area with cards
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setSpacing(8)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(self._list_container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: transparent; width: 8px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {C.DIVIDER}; border-radius: 4px; min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {C.BG_HOVER}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        root.addWidget(scroll, 1)

        self.setLayout(root)
        self.reload()

    def reload(self):
        self._all_rows = self.db.get_recent(limit=500)
        self._filter(self.search.text())

    def _filter(self, query: str):
        # Clear existing cards (keep stretch at end)
        while self._list_layout.count() > 1:
            w = self._list_layout.itemAt(0).widget()
            if w is None:
                break
            self._list_layout.removeWidget(w)
            w.deleteLater()

        q = (query or "").lower().strip()
        rows = self._all_rows
        if q:
            rows = [r for r in rows if q in (r.get("text") or "").lower()]

        if not rows:
            empty = QLabel("Nada por aquí. Dicta algo presionando Ctrl+Alt.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {C.TEXT_FAINT}; padding: 40px; font-size: 13px;")
            self._list_layout.insertWidget(0, empty)
            return

        for row in rows[:200]:
            card = TranscriptionCard(row)
            card.copy_requested.connect(self._copy_to_clipboard)
            card.repaste_requested.connect(self._repaste)
            card.delete_requested.connect(self._delete)
            card.retry_requested.connect(self._retry)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)

    def _copy_to_clipboard(self, text: str):
        QApplication.clipboard().setText(text)

    def _repaste(self, text: str):
        # Hide the hub so focus returns to the app underneath
        top = self.window()
        top.hide()
        QTimer.singleShot(180, lambda: paste_last_transcript(text))

    def _delete(self, row_id: int):
        import sqlite3, os
        from config import DB_PATH
        with sqlite3.connect(DB_PATH) as conn:
            r = conn.execute("SELECT audio_path FROM transcriptions WHERE id = ?", (row_id,)).fetchone()
            conn.execute("DELETE FROM transcriptions WHERE id = ?", (row_id,))
        if r and r[0] and os.path.exists(r[0]):
            try:
                os.unlink(r[0])
            except OSError:
                pass
        self.reload()

    def _retry(self, row_id: int):
        """Re-transcribe a past recording using the current backend/settings."""
        import os, io, threading
        from PyQt6.QtCore import QMetaObject, Qt as _Qt
        row = self.db.get(row_id)
        if not row or not row.get("audio_path") or not os.path.exists(row["audio_path"]):
            QMessageBox.warning(self, "Sin audio", "Este dictado no tiene WAV asociado.")
            return

        from core.transcriber import Transcriber
        transcriber = Transcriber()
        path = row["audio_path"]

        # Show feedback toast-style label at top of list
        busy = QLabel("Re-transcribiendo…")
        busy.setStyleSheet(f"color: {C.ACCENT}; padding: 8px; font-size: 12px;")
        self._list_layout.insertWidget(0, busy)

        def worker():
            try:
                with open(path, "rb") as f:
                    buf = io.BytesIO(f.read())
                new_text, model_id = transcriber.transcribe(buf)
                if new_text:
                    self.db.update_text(row_id, new_text)
            except Exception as e:
                print(f"retry failed: {e}")
            # Reload on main thread
            QTimer.singleShot(0, lambda: (busy.deleteLater() if busy else None, self.reload()))

        threading.Thread(target=worker, daemon=True).start()


class DictionaryPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout()
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(12)

        title = QLabel("Diccionario personal")
        title.setStyleSheet(f"color: {C.TEXT}; font-size: 22px; font-weight: 600;")
        root.addWidget(title)

        sub = QLabel("Una palabra o frase por línea. Se usa como pista de vocabulario para Whisper — mejora reconocimiento de nombres propios, términos técnicos, jerga.")
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 12px;")
        sub.setWordWrap(True)
        root.addWidget(sub)

        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {C.BG_INPUT}; color: {C.TEXT};
                border: 1px solid {C.DIVIDER}; border-radius: 8px;
                padding: 10px; font-family: 'SF Mono', Menlo, monospace;
                font-size: 13px;
            }}
        """)
        self._load()
        root.addWidget(self.editor, 1)

        row = QHBoxLayout()
        row.addStretch()
        save = QPushButton("Guardar")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setStyleSheet(self._btn_primary_style())
        save.clicked.connect(self._save)
        row.addWidget(save)
        root.addLayout(row)

        self.setLayout(root)

    def _btn_primary_style(self):
        return f"""
            QPushButton {{
                background: {C.ACCENT}; color: white;
                border: none; border-radius: 8px;
                padding: 8px 20px; font-weight: 500; font-size: 13px;
            }}
            QPushButton:hover {{ background: #4a8fef; }}
        """

    def _load(self):
        try:
            if os.path.exists(DICTIONARY_PATH):
                with open(DICTIONARY_PATH) as f:
                    self.editor.setPlainText(f.read())
            else:
                from core.dictionary import _ensure_file
                _ensure_file()
                with open(DICTIONARY_PATH) as f:
                    self.editor.setPlainText(f.read())
        except Exception as e:
            self.editor.setPlainText(f"# Error loading: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(DICTIONARY_PATH), exist_ok=True)
            with open(DICTIONARY_PATH, "w") as f:
                f.write(self.editor.toPlainText())
        except Exception as e:
            QMessageBox.warning(self, "Error", f"No se pudo guardar: {e}")


class SnippetsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.db = SnippetsDB()
        root = QVBoxLayout()
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        title = QLabel("Snippets")
        title.setStyleSheet(f"color: {C.TEXT}; font-size: 22px; font-weight: 600;")
        root.addWidget(title)

        sub = QLabel(
            'Atajos de voz. Cuando dictes el trigger, SFlow lo reemplaza por la expansión. '
            'Ejemplo: di "mi correo" y se pega tu email.'
        )
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 12px;")
        sub.setWordWrap(True)
        root.addWidget(sub)

        # New snippet form
        form = QFrame()
        form.setStyleSheet(f"background: {C.BG_CARD}; border: 1px solid {C.DIVIDER}; border-radius: 10px;")
        fl = QVBoxLayout(form)
        fl.setContentsMargins(14, 12, 14, 12)
        fl.setSpacing(8)

        lbl = QLabel("Agregar snippet")
        lbl.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px; font-weight: 500;")
        fl.addWidget(lbl)

        self.trigger_input = QLineEdit()
        self.trigger_input.setPlaceholderText("trigger (ej: mi correo)")
        self.trigger_input.setStyleSheet(self._input_style())
        fl.addWidget(self.trigger_input)

        self.expansion_input = QPlainTextEdit()
        self.expansion_input.setPlaceholderText("expansión (lo que se pega cuando digas el trigger)")
        self.expansion_input.setMaximumHeight(80)
        self.expansion_input.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {C.BG_INPUT}; color: {C.TEXT};
                border: 1px solid {C.DIVIDER}; border-radius: 6px;
                padding: 8px; font-size: 13px;
            }}
            QPlainTextEdit:focus {{ border-color: {C.ACCENT}; }}
        """)
        fl.addWidget(self.expansion_input)

        row = QHBoxLayout()
        row.addStretch()
        add_btn = QPushButton("Agregar")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(self._btn_style())
        add_btn.clicked.connect(self._add)
        row.addWidget(add_btn)
        fl.addLayout(row)
        root.addWidget(form)

        # List of existing snippets
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setSpacing(6)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(self._list_container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}")
        root.addWidget(scroll, 1)

        self.setLayout(root)
        self.reload()

    def _input_style(self):
        return f"""
            QLineEdit {{
                background: {C.BG_INPUT}; color: {C.TEXT};
                border: 1px solid {C.DIVIDER}; border-radius: 6px;
                padding: 8px 10px; font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {C.ACCENT}; }}
        """

    def _btn_style(self):
        return f"""
            QPushButton {{
                background: {C.ACCENT}; color: white;
                border: none; border-radius: 6px;
                padding: 7px 18px; font-weight: 500; font-size: 12px;
            }}
            QPushButton:hover {{ background: #4a8fef; }}
        """

    def _add(self):
        t = self.trigger_input.text().strip()
        e = self.expansion_input.toPlainText().strip()
        if not t or not e:
            QMessageBox.warning(self, "Campos vacíos", "Trigger y expansión son requeridos.")
            return
        try:
            self.db.add(t, e)
            self.trigger_input.clear()
            self.expansion_input.clear()
            self.reload()
        except Exception as err:
            QMessageBox.warning(self, "Error", str(err))

    def reload(self):
        while self._list_layout.count() > 1:
            w = self._list_layout.itemAt(0).widget()
            if w is None:
                break
            self._list_layout.removeWidget(w)
            w.deleteLater()

        rows = self.db.list_all()
        if not rows:
            empty = QLabel("No tienes snippets. Agrega uno arriba.")
            empty.setStyleSheet(f"color: {C.TEXT_FAINT}; padding: 20px; text-align: center;")
            self._list_layout.insertWidget(0, empty)
            return

        for s in rows:
            card = self._snippet_card(s)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)

    def _snippet_card(self, s: dict) -> QFrame:
        f = QFrame()
        f.setStyleSheet(f"""
            QFrame {{
                background: {C.BG_CARD}; border: 1px solid {C.DIVIDER};
                border-radius: 8px;
            }}
        """)
        lay = QVBoxLayout(f)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)

        head = QHBoxLayout()
        trig = QLabel(f'"{s["trigger"]}"')
        trig.setStyleSheet(f"color: {C.ACCENT}; font-size: 13px; font-weight: 600;")
        head.addWidget(trig)

        if s.get("usage_count", 0) > 0:
            usage = QLabel(f"· usado {s['usage_count']}×")
            usage.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: 11px;")
            head.addWidget(usage)

        head.addStretch()
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(22, 22)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_FAINT};
                border: none; border-radius: 4px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {C.BG_HOVER}; color: {C.ERR}; }}
        """)
        sid = s["id"]
        del_btn.clicked.connect(lambda: self._delete(sid))
        head.addWidget(del_btn)
        lay.addLayout(head)

        # Show expansion (multi-line safe)
        exp = QLabel(s["expansion"])
        exp.setWordWrap(True)
        exp.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; line-height: 1.4;")
        lay.addWidget(exp)

        return f

    def _delete(self, sid: int):
        self.db.delete(sid)
        self.reload()


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout()
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(16)

        title = QLabel("Ajustes")
        title.setStyleSheet(f"color: {C.TEXT}; font-size: 22px; font-weight: 600;")
        root.addWidget(title)

        def group(name: str) -> QVBoxLayout:
            g = QGroupBox(name)
            g.setStyleSheet(f"""
                QGroupBox {{
                    color: {C.TEXT}; font-size: 13px; font-weight: 500;
                    border: 1px solid {C.DIVIDER}; border-radius: 10px;
                    padding-top: 20px; padding-bottom: 8px;
                    margin-top: 8px; background: {C.BG_CARD};
                }}
                QGroupBox::title {{
                    left: 14px; top: 0px; padding: 0 6px;
                }}
                QCheckBox {{ color: {C.TEXT}; font-size: 13px; padding: 6px 0; }}
                QCheckBox::indicator {{
                    width: 16px; height: 16px;
                    background: {C.BG_INPUT};
                    border: 1px solid {C.DIVIDER}; border-radius: 4px;
                }}
                QCheckBox::indicator:checked {{
                    background: {C.ACCENT}; border-color: {C.ACCENT};
                }}
                QLabel {{ color: {C.TEXT_DIM}; font-size: 12px; }}
                QComboBox {{
                    background: {C.BG_INPUT}; color: {C.TEXT};
                    border: 1px solid {C.DIVIDER}; border-radius: 6px;
                    padding: 6px 10px; min-width: 200px; font-size: 13px;
                }}
                QComboBox QAbstractItemView {{
                    background: {C.BG_CARD}; color: {C.TEXT};
                    selection-background-color: {C.BG_HOVER};
                    border: 1px solid {C.DIVIDER};
                }}
            """)
            inner = QVBoxLayout()
            inner.setContentsMargins(14, 4, 14, 10)
            inner.setSpacing(2)
            g.setLayout(inner)
            root.addWidget(g)
            return inner

        # --- Transcription ---
        tl = group("Motor de transcripción")
        self.backend = QComboBox()
        self.backend.addItem("Groq Whisper Turbo — cloud, 500-800ms E2E, requiere internet", "groq")
        self.backend.addItem("mlx-whisper (small) — offline, ~1s por 10s audio, `pip install mlx-whisper`", "local")
        self.backend.setCurrentIndex(0 if get_setting("transcribe_backend", "groq") == "groq" else 1)
        tl.addWidget(self.backend)

        # --- Paste ---
        pl = group("Inserción de texto")
        self.paste_combo = QComboBox()
        self.paste_combo.addItem("Keystroke injection — NO toca tu portapapeles (default)", "keystroke")
        self.paste_combo.addItem("Clipboard + Cmd+V — legacy, sobrescribe tu portapapeles temporalmente", "clipboard")
        self.paste_combo.setCurrentIndex(0 if get_setting("paste_backend", "keystroke") == "keystroke" else 1)
        pl.addWidget(self.paste_combo)

        self.streaming = QCheckBox("Streaming paste (efecto typing palabra-por-palabra)")
        self.streaming.setChecked(get_setting("streaming_paste_enabled", False))
        pl.addWidget(self.streaming)

        # --- AI cleanup ---
        al = group("Procesamiento con LLM")
        self.llm = QCheckBox("Limpiar con Llama (remueve muletillas, puntúa)")
        self.llm.setChecked(get_setting("llm_cleanup_enabled", True))
        al.addWidget(self.llm)

        self.context = QCheckBox("Adaptar tono según app activa (Slack casual, Gmail formal, etc.)")
        self.context.setChecked(get_setting("context_aware_tone", True))
        al.addWidget(self.context)

        self.commands = QCheckBox("Comandos de voz (\"nueva línea\", \"punto y aparte\", \"coma\")")
        self.commands.setChecked(get_setting("smart_commands_enabled", True))
        al.addWidget(self.commands)

        self.dict_toggle = QCheckBox("Usar diccionario personal como vocabulario")
        self.dict_toggle.setChecked(get_setting("personal_dictionary_enabled", True))
        al.addWidget(self.dict_toggle)

        # --- UX ---
        ul = group("UX")
        self.glass = QCheckBox("Liquid Glass en la pill (experimental — macOS 26+)")
        self.glass.setChecked(get_setting("liquid_glass_enabled", False))
        ul.addWidget(self.glass)

        self.command_mode = QCheckBox("Command Mode (Ctrl+Shift hold → transforma selección con voz)")
        self.command_mode.setChecked(get_setting("command_mode_enabled", True))
        ul.addWidget(self.command_mode)

        # --- Mouse hotkey ---
        hl = group("Hotkey de mouse (opcional)")
        self.mouse = QComboBox()
        self.mouse.addItem("Ninguno", "")
        self.mouse.addItem("Click medio (rueda)", "middle")
        self.mouse.addItem("Botón lateral 1 (Mouse4)", "x1")
        self.mouse.addItem("Botón lateral 2 (Mouse5)", "x2")
        cur = get_setting("mouse_button_hotkey") or ""
        self.mouse.setCurrentIndex(max(0, self.mouse.findData(cur)))
        hl.addWidget(self.mouse)

        root.addStretch()

        # Save bar
        bar = QHBoxLayout()
        bar.addStretch()

        relaunch_btn = QPushButton("Reiniciar SFlow")
        relaunch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        relaunch_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.BG_INPUT}; color: {C.TEXT};
                border: 1px solid {C.DIVIDER}; border-radius: 8px;
                padding: 10px 18px; font-weight: 500; font-size: 13px;
            }}
            QPushButton:hover {{ background: {C.BG_HOVER}; }}
        """)
        relaunch_btn.clicked.connect(self._relaunch)
        bar.addWidget(relaunch_btn)

        save = QPushButton("Guardar ajustes")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setStyleSheet(f"""
            QPushButton {{
                background: {C.ACCENT}; color: white;
                border: none; border-radius: 8px;
                padding: 10px 24px; font-weight: 500; font-size: 13px;
            }}
            QPushButton:hover {{ background: #4a8fef; }}
        """)
        save.clicked.connect(self._save)
        bar.addWidget(save)
        root.addLayout(bar)

        # Snapshot of relaunch-sensitive settings — used by _save() to decide
        # whether to offer an immediate restart.
        self._snapshot = self._restart_snapshot()

        self.setLayout(root)

    def _restart_snapshot(self) -> tuple:
        """Settings that only take effect after restart. If any change → offer relaunch."""
        return (
            get_setting("mouse_button_hotkey"),
            get_setting("liquid_glass_enabled", False),
            get_setting("command_mode_enabled", True),
            get_setting("transcribe_backend", "groq"),
        )

    def _relaunch(self):
        confirm = QMessageBox.question(
            self, "Reiniciar SFlow",
            "Se cerrará y abrirá una nueva instancia. ¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            relaunch_app()

    def _save(self):
        set_setting("transcribe_backend", self.backend.currentData())
        set_setting("paste_backend", self.paste_combo.currentData())
        set_setting("streaming_paste_enabled", self.streaming.isChecked())
        set_setting("llm_cleanup_enabled", self.llm.isChecked())
        set_setting("context_aware_tone", self.context.isChecked())
        set_setting("smart_commands_enabled", self.commands.isChecked())
        set_setting("personal_dictionary_enabled", self.dict_toggle.isChecked())
        set_setting("liquid_glass_enabled", self.glass.isChecked())
        set_setting("command_mode_enabled", self.command_mode.isChecked())
        mb = self.mouse.currentData()
        set_setting("mouse_button_hotkey", mb if mb else None)

        new_snapshot = self._restart_snapshot()
        needs_restart = new_snapshot != self._snapshot
        self._snapshot = new_snapshot

        if needs_restart:
            box = QMessageBox(self)
            box.setWindowTitle("Guardado")
            box.setText("Algunos cambios requieren reiniciar SFlow para aplicarse.")
            restart_btn = box.addButton("Reiniciar ahora", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Después", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is restart_btn:
                relaunch_app()
        else:
            QMessageBox.information(self, "Guardado", "Ajustes guardados.")


class HomePage(QWidget):
    def __init__(self, db: TranscriptionDB):
        super().__init__()
        self.db = db
        root = QVBoxLayout()
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(18)

        hour = datetime.now().hour
        greet = "Buenos días" if hour < 13 else ("Buenas tardes" if hour < 20 else "Buenas noches")
        title = QLabel(f"{greet} 👋")
        title.setStyleSheet(f"color: {C.TEXT}; font-size: 28px; font-weight: 600;")
        root.addWidget(title)

        shortcuts = QLabel(
            "  <b>Ctrl+Alt</b> hold  ·  dictado normal<br>"
            "  <b>Ctrl+Shift</b> hold  ·  Command Mode (transforma selección)<br>"
            "  Doble-tap <b>Ctrl</b>  ·  hands-free (otra vez para parar)<br>"
            "  <b>Cmd+Shift+H</b>  ·  abre este Hub"
        )
        shortcuts.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 13px; line-height: 1.7;")
        shortcuts.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(shortcuts)

        # Stats row
        self._stats_row = QHBoxLayout()
        self._stats_row.setSpacing(10)
        root.addLayout(self._stats_row)
        self._refresh_stats()

        # Latest card
        recent_lbl = QLabel("Último dictado")
        recent_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 12px; margin-top: 8px;")
        root.addWidget(recent_lbl)

        self._latest_container = QWidget()
        lv = QVBoxLayout(self._latest_container)
        lv.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._latest_container)
        self._refresh_latest()

        root.addStretch()
        self.setLayout(root)

    def _stat_card(self, value: str, label: str) -> QFrame:
        f = QFrame()
        f.setStyleSheet(f"""
            QFrame {{
                background: {C.BG_CARD}; border: 1px solid {C.DIVIDER};
                border-radius: 12px;
            }}
        """)
        lay = QVBoxLayout(f)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(4)
        v = QLabel(value)
        v.setStyleSheet(f"color: {C.TEXT}; font-size: 26px; font-weight: 600;")
        l = QLabel(label)
        l.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(v)
        lay.addWidget(l)
        return f

    def _refresh_stats(self):
        while self._stats_row.count():
            item = self._stats_row.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        rows = self.db.get_recent(limit=500)
        total = len(rows)
        words = sum(len((r.get("text") or "").split()) for r in rows)
        today = 0
        today_prefix = datetime.now().strftime("%Y-%m-%d")
        for r in rows:
            ts = r.get("created_at") or ""
            if ts.startswith(today_prefix):
                today += 1
        self._stats_row.addWidget(self._stat_card(str(total), "transcripciones totales"))
        self._stats_row.addWidget(self._stat_card(str(words), "palabras dictadas"))
        self._stats_row.addWidget(self._stat_card(str(today), "hoy"))

    def _refresh_latest(self):
        lay = self._latest_container.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        rows = self.db.get_recent(limit=1)
        if not rows:
            e = QLabel("Aún no has dictado nada. Prueba Ctrl+Alt hold.")
            e.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: 13px; padding: 20px; background: {C.BG_CARD}; border-radius: 10px; border: 1px solid {C.DIVIDER};")
            lay.addWidget(e)
            return
        card = TranscriptionCard(rows[0])
        lay.addWidget(card)

    def reload(self):
        self._refresh_stats()
        self._refresh_latest()


class HubWindow(QWidget):
    def __init__(self, db: TranscriptionDB):
        super().__init__()
        self.db = db
        self.setWindowTitle("SFlow")
        self.resize(880, 620)
        self.setStyleSheet(f"background: {C.BG}; color: {C.TEXT};")

        # --- Layout ---
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        side = QFrame()
        side.setFixedWidth(190)
        side.setStyleSheet(f"background: {C.BG_ALT}; border-right: 1px solid {C.DIVIDER};")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 18, 14, 14)
        sl.setSpacing(4)

        # Logo + brand
        brand_row = QHBoxLayout()
        brand_row.setSpacing(8)
        logo = QLabel()
        pm = QPixmap(LOGO_PATH)
        if not pm.isNull():
            logo.setPixmap(pm.scaled(22, 22, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand_row.addWidget(logo)
        name = QLabel("SFlow")
        name.setStyleSheet(f"color: {C.TEXT}; font-size: 15px; font-weight: 600;")
        brand_row.addWidget(name)
        brand_row.addStretch()
        w = QWidget()
        w.setLayout(brand_row)
        sl.addWidget(w)
        sl.addSpacing(18)

        self.btn_home = SidebarButton("🏠", "Home")
        self.btn_hist = SidebarButton("🕐", "Historial")
        self.btn_dict = SidebarButton("📖", "Diccionario")
        self.btn_snip = SidebarButton("✨", "Snippets")
        self.btn_set = SidebarButton("⚙️", "Ajustes")
        for b in (self.btn_home, self.btn_hist, self.btn_dict, self.btn_snip, self.btn_set):
            sl.addWidget(b)
        sl.addStretch()
        self.btn_home.setChecked(True)
        root.addWidget(side)

        # Pages
        self.pages = QStackedWidget()
        self.home_page = HomePage(db)
        self.history_page = HistoryPage(db)
        self.dict_page = DictionaryPage()
        self.snippets_page = SnippetsPage()
        self.settings_page = SettingsPage()
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.history_page)
        self.pages.addWidget(self.dict_page)
        self.pages.addWidget(self.snippets_page)
        self.pages.addWidget(self.settings_page)
        root.addWidget(self.pages, 1)

        self.btn_home.clicked.connect(lambda: self._go(0))
        self.btn_hist.clicked.connect(lambda: self._go(1))
        self.btn_dict.clicked.connect(lambda: self._go(2))
        self.btn_snip.clicked.connect(lambda: self._go(3))
        self.btn_set.clicked.connect(lambda: self._go(4))

    def _go(self, idx: int):
        self.pages.setCurrentIndex(idx)
        if idx == 0:
            self.home_page.reload()
        elif idx == 1:
            self.history_page.reload()
        elif idx == 3:
            self.snippets_page.reload()

    def showEvent(self, event):
        super().showEvent(event)
        self.home_page.reload()
        self.history_page.reload()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)
