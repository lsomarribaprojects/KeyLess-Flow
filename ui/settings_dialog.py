"""Settings dialog — all user-toggleable features in one place."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QComboBox,
    QPushButton, QGroupBox, QFrame, QLineEdit, QMessageBox,
)
from PyQt6.QtCore import Qt
from config import get_setting, set_setting, DICTIONARY_PATH
import os
import subprocess


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("KeyLess Flow — Ajustes")
        self.setMinimumWidth(460)

        root = QVBoxLayout()
        root.setSpacing(12)

        # --- Backend ---
        backend_group = QGroupBox("Motor de transcripción")
        bl = QVBoxLayout()
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("Groq (cloud, rápido, requiere internet)", "groq")
        self.backend_combo.addItem("Local (parakeet-mlx, offline, requiere instalación)", "local")
        current = get_setting("transcribe_backend", "groq")
        self.backend_combo.setCurrentIndex(0 if current == "groq" else 1)
        bl.addWidget(self.backend_combo)
        backend_group.setLayout(bl)
        root.addWidget(backend_group)

        # --- AI post-processing ---
        ai_group = QGroupBox("Procesamiento con LLM")
        al = QVBoxLayout()
        self.cb_llm = QCheckBox("Limpiar transcripción con Llama (remueve muletillas, puntúa)")
        self.cb_llm.setChecked(get_setting("llm_cleanup_enabled", True))
        al.addWidget(self.cb_llm)

        self.cb_context = QCheckBox("Adaptar tono según la app activa (Slack casual, Gmail formal, código, etc.)")
        self.cb_context.setChecked(get_setting("context_aware_tone", True))
        al.addWidget(self.cb_context)

        self.cb_commands = QCheckBox("Comandos de voz (\"nueva línea\", \"punto y aparte\", etc.)")
        self.cb_commands.setChecked(get_setting("smart_commands_enabled", True))
        al.addWidget(self.cb_commands)

        self.cb_dict = QCheckBox("Usar diccionario personal como vocabulario")
        self.cb_dict.setChecked(get_setting("personal_dictionary_enabled", True))
        al.addWidget(self.cb_dict)

        dict_row = QHBoxLayout()
        dict_row.addWidget(QLabel(f"Archivo: {DICTIONARY_PATH}"))
        edit_btn = QPushButton("Editar")
        edit_btn.clicked.connect(self._edit_dictionary)
        dict_row.addWidget(edit_btn)
        al.addLayout(dict_row)
        ai_group.setLayout(al)
        root.addWidget(ai_group)

        # --- UX ---
        ux_group = QGroupBox("UX")
        ul = QVBoxLayout()
        self.cb_glass = QCheckBox("Liquid Glass en la pill (macOS 26+)")
        self.cb_glass.setChecked(get_setting("liquid_glass_enabled", True))
        ul.addWidget(self.cb_glass)

        self.cb_stream = QCheckBox("Paste en streaming (efecto typing word-by-word)")
        self.cb_stream.setChecked(get_setting("streaming_paste_enabled", False))
        ul.addWidget(self.cb_stream)

        self.cb_command = QCheckBox("Command Mode (Ctrl+Shift hold → transforma selección con voz)")
        self.cb_command.setChecked(get_setting("command_mode_enabled", True))
        ul.addWidget(self.cb_command)
        ux_group.setLayout(ul)
        root.addWidget(ux_group)

        # --- Hotkeys ---
        hk_group = QGroupBox("Hotkey de mouse (opcional)")
        hl = QVBoxLayout()
        self.mouse_combo = QComboBox()
        self.mouse_combo.addItem("Ninguno", "")
        self.mouse_combo.addItem("Click medio (rueda)", "middle")
        self.mouse_combo.addItem("Botón lateral 1 (Mouse4)", "x1")
        self.mouse_combo.addItem("Botón lateral 2 (Mouse5)", "x2")
        cur_mb = get_setting("mouse_button_hotkey") or ""
        idx = max(0, self.mouse_combo.findData(cur_mb))
        self.mouse_combo.setCurrentIndex(idx)
        hl.addWidget(self.mouse_combo)
        hk_group.setLayout(hl)
        root.addWidget(hk_group)

        # --- Save ---
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(line)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = QPushButton("Guardar")
        save.setDefault(True)
        save.clicked.connect(self._save)
        btn_row.addWidget(save)
        root.addLayout(btn_row)

        self.setLayout(root)

    def _edit_dictionary(self):
        os.makedirs(os.path.dirname(DICTIONARY_PATH), exist_ok=True)
        if not os.path.exists(DICTIONARY_PATH):
            from core.dictionary import _ensure_file
            _ensure_file()
        subprocess.run(["open", "-a", "TextEdit", DICTIONARY_PATH], capture_output=True)

    def _save(self):
        set_setting("transcribe_backend", self.backend_combo.currentData())
        set_setting("llm_cleanup_enabled", self.cb_llm.isChecked())
        set_setting("context_aware_tone", self.cb_context.isChecked())
        set_setting("smart_commands_enabled", self.cb_commands.isChecked())
        set_setting("personal_dictionary_enabled", self.cb_dict.isChecked())
        set_setting("liquid_glass_enabled", self.cb_glass.isChecked())
        set_setting("streaming_paste_enabled", self.cb_stream.isChecked())
        set_setting("command_mode_enabled", self.cb_command.isChecked())
        mb = self.mouse_combo.currentData()
        set_setting("mouse_button_hotkey", mb if mb else None)

        QMessageBox.information(
            self, "Guardado",
            "Ajustes guardados. Algunos cambios (hotkey de mouse, Liquid Glass) requieren reiniciar KeyLess Flow.",
        )
        self.accept()
