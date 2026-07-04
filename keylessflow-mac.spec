# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for KeyLess by Sinsajo on macOS.

Produces dist/KeyLess by Sinsajo.app (native macOS app bundle).
Wrap the resulting .app in a .dmg via `create-dmg` — see .github/workflows/release.yml.

Note: v1 ships UNSIGNED. macOS Gatekeeper will require the user to
right-click → Open on first launch. A signed + notarized build needs an
Apple Developer Program membership ($99/yr) — added in a future pass.
"""
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None


# ------------------------------------------------------------ PyObjC bundle
# PyObjC's dynamic import scheme is opaque to PyInstaller; collect_all
# eagerly gathers all data, binaries, and hiddenimports so the .app can
# actually initialise AppKit / Quartz at runtime.
pyobjc_datas, pyobjc_binaries, pyobjc_hiddenimports = [], [], []
for pkg in [
    'AppKit', 'Foundation', 'Cocoa', 'Quartz', 'CoreFoundation',
    'objc', 'CoreText', 'ApplicationServices',
]:
    try:
        d, b, h = collect_all(pkg)
        pyobjc_datas += d
        pyobjc_binaries += b
        pyobjc_hiddenimports += h
    except Exception:
        pass


sounddevice_datas = collect_data_files('_sounddevice_data')
pynput_hidden = collect_submodules('pynput')


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
        # PyObjC (Mac-specific)
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
        # SSL via OS cert store (macOS keychain in this case)
        'truststore',
        'truststore._api',
        'truststore._macos',
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
        # System-audio (loopback) capture — soundcard uses coreaudio bindings
        # on macOS; needs BlackHole (or Loopback) installed by the user.
        'soundcard',
        'soundcard.coreaudio',
        'cffi',
        'psutil',
        # KeyLess by Sinsajo internals
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
        # Pro tier: backend auth + transcribe proxy
        'core.auth',
        'core.transcriber_pro',
        # Auto-updater
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
        # Windows-only bits
        'win32gui',
        'win32process',
        'win32con',
        'win32api',
        'winreg',
        'pyperclip',
        # Local transcription stack — opt-in on Mac (mlx-whisper). Excluded
        # from the ship-bundle to keep it small; LocalTranscriber detects at
        # runtime if the packages happen to be installed.
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
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KeyLess by Sinsajo',
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
    name='KeyLess by Sinsajo',
)

# ------------------------------------------------------------ .app bundle
app = BUNDLE(
    coll,
    name='KeyLess by Sinsajo.app',
    icon='keylessflow.icns' if __import__('os').path.exists('keylessflow.icns') else None,
    bundle_identifier='com.sinsajocreators.keylessflow',
    info_plist={
        'LSUIElement': True,  # menu-bar app, no Dock icon
        'NSMicrophoneUsageDescription':
            'KeyLess by Sinsajo necesita acceso al microfono para transcribir voz.',
        'NSAppleEventsUsageDescription':
            'KeyLess by Sinsajo usa AppleScript para pegar texto en otras aplicaciones.',
        'CFBundleDisplayName': 'KeyLess by Sinsajo',
        'CFBundleName': 'KeyLess by Sinsajo',
        'CFBundleVersion': '1.1.1',
        'CFBundleShortVersionString': '1.1.1',
        'LSApplicationCategoryType': 'public.app-category.productivity',
        'NSHighResolutionCapable': True,
    },
)
