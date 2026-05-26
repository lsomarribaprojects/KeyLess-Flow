# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for KeyLess Flow on Windows.

Produces dist\\KeyLessFlow\\KeyLessFlow.exe (onedir) — windowed (no console).
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None


# --- sounddevice ships its own portaudio DLLs in _sounddevice_data ---
sounddevice_datas = collect_data_files('_sounddevice_data')

# --- pynput needs its Windows backend bundled explicitly ---
pynput_hidden = collect_submodules('pynput')


datas = [
    ('logo_small.png', '.'),
    ('logo.png', '.'),
]
datas += sounddevice_datas


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # pynput
        *pynput_hidden,
        'pynput.keyboard._win32',
        'pynput.mouse._win32',
        # PyQt6
        'PyQt6.QtWidgets',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        # Flask
        'flask',
        'jinja2',
        'markupsafe',
        'werkzeug',
        # sounddevice
        '_sounddevice',
        'sounddevice',
        '_cffi_backend',
        # groq + httpx
        'groq',
        'httpx',
        'httpcore',
        'h11',
        'anyio',
        'sniffio',
        'certifi',
        'idna',
        # numpy
        'numpy',
        # dotenv + platformdirs
        'dotenv',
        'platformdirs',
        # Windows-specific
        'win32gui',
        'win32process',
        'win32con',
        'win32api',
        'winreg',
        'psutil',
        'pyperclip',
        # KeyLess Flow internals (explicit so PyInstaller picks them up)
        'core.transcriber',
        'core.transcriber_groq',
        'core.transcriber_local',
        'core.llm_cleanup',
        'core.context',
        'core.dictionary',
        'core.dictionary_learner',
        'core.smart_commands',
        'core.snippets_matcher',
        'core.command_mode',
        'core.hotkey',
        'core.recorder',
        'core.clipboard',
        'core.paste',
        'core.relaunch',
        'core.logger',
        'core.transform',
        'core.focus_mode',
        'core.dictation_actions',
        'core.platform',
        'core.platform._windows',
        'ui.pill_widget',
        'ui.audio_visualizer',
        'ui.settings_dialog',
        'ui.red_dot_indicator',
        'ui.hub_window',
        'db.database',
        'db.snippets',
        'web.server',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'unittest',
        'test',
        # Mac-only PyObjC frameworks
        'AppKit',
        'objc',
        'Foundation',
        'Cocoa',
        'Quartz',
        'CoreFoundation',
        'CoreText',
        'ApplicationServices',
        # Local transcription stack — Apple Silicon only, opt-in on Mac
        'mlx_whisper',
        'parakeet_mlx',
        'mlx',
        'mlx.core',
        'mlx_metal',
        'librosa',
        'numba',
        'llvmlite',
        'scipy',
        'sklearn',
        'scikit-learn',
        'soundfile',
        'soxr',
        'pooch',
        'audioread',
        'pandas',
        'matplotlib',
        'torch',
        'torchaudio',
        'tiktoken',
        'sympy',
        'networkx',
        'moonshine',
        'moonshine_voice',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KeyLessFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # windowed app — no terminal pop-up
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='keylessflow.ico',
    version_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='KeyLessFlow',
)
