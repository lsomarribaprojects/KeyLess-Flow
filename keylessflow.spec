# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for KeyLess by Sinsajo on Windows.

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
        # SSL via OS cert store — needed behind corporate TLS-intercepting proxies.
        # Listing the submodules explicitly because PyInstaller's basic
        # 'truststore' hidden import does NOT pull the platform backends
        # (truststore._windows.py is loaded dynamically by inject_into_ssl()).
        'truststore',
        'truststore._api',
        'truststore._windows',
        'truststore._openssl',
        'truststore._ssl_constants',
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
        # MP3 encoder (used inside get_mp3_buffer, PyInstaller can miss it)
        'lameenc',
        # System-audio (WASAPI loopback) capture — soundcard uses cffi
        # bindings that PyInstaller doesn't auto-detect.
        'soundcard',
        'soundcard.mediafoundation',
        'cffi',
        # Windows-specific
        'win32gui',
        'win32process',
        'win32con',
        'win32api',
        'winreg',
        'psutil',
        'pyperclip',
        # KeyLess by Sinsajo internals (explicit so PyInstaller picks them up)
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
        # Pro tier: backend auth + transcribe proxy
        'core.auth',
        'core.transcriber_pro',
        # Auto-updater (polls GitHub Releases, runs installer)
        'core.updater',
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
    version='version_info.txt',
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
