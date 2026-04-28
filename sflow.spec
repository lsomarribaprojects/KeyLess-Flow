# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for SFlow — macOS menu bar voice-to-text app."""

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None

# --- Collect PyObjC frameworks ---
pyobjc_datas = []
pyobjc_binaries = []
pyobjc_hiddenimports = []

for pkg in ['AppKit', 'Foundation', 'Cocoa', 'Quartz', 'CoreFoundation',
            'objc', 'CoreText', 'ApplicationServices']:
    try:
        d, b, h = collect_all(pkg)
        pyobjc_datas += d
        pyobjc_binaries += b
        pyobjc_hiddenimports += h
    except Exception:
        pass

# --- Collect sounddevice portaudio binary ---
sounddevice_datas = collect_data_files('_sounddevice_data')

# --- Collect pynput backends ---
pynput_hidden = collect_submodules('pynput')

# --- Data files ---
datas = [
    ('logo_small.png', '.'),
    ('logo.png', '.'),
]
datas += sounddevice_datas
datas += pyobjc_datas

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=pyobjc_binaries,
    datas=datas,
    hiddenimports=[
        # PyObjC
        *pyobjc_hiddenimports,
        # pynput
        *pynput_hidden,
        'pynput.keyboard._darwin',
        'pynput.mouse._darwin',
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
        # dotenv
        'dotenv',
        # SFlow internals (explicit so PyInstaller picks them up)
        'core.transcriber',
        'core.transcriber_groq',
        'core.transcriber_local',
        'core.llm_cleanup',
        'core.context',
        'core.dictionary',
        'core.smart_commands',
        'core.command_mode',
        'core.hotkey',
        'core.recorder',
        'core.clipboard',
        'ui.pill_widget',
        'ui.audio_visualizer',
        'ui.settings_dialog',
        'ui.red_dot_indicator',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'unittest',
        'test',
        # Local transcription stack — opt-in via pip install mlx-whisper.
        # Excluded from bundle to keep it small. LocalTranscriber detects availability at runtime.
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
        'regex',
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
    name='SFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SFlow',
)

app = BUNDLE(
    coll,
    name='SFlow.app',
    icon='SFlow.icns',
    bundle_identifier='so.saasfactory.sflow',
    info_plist={
        'LSUIElement': True,
        'NSMicrophoneUsageDescription':
            'SFlow necesita acceso al microfono para transcribir voz.',
        'NSAppleEventsUsageDescription':
            'SFlow usa AppleScript para pegar texto en otras aplicaciones.',
        'CFBundleDisplayName': 'SFlow',
        'CFBundleName': 'SFlow',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'LSApplicationCategoryType': 'public.app-category.productivity',
        'NSHighResolutionCapable': True,
    },
)
