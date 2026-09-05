# build.ps1 — Build KeyLess by Sinsajo for Windows.
#
# Usage:
#   .\build.ps1                # → dist\KeyLessFlow\KeyLessFlow.exe (onedir bundle)
#   .\build.ps1 -Installer     # → ↑ AND dist\KeyLessFlow-Setup.exe (single-file installer)
#
# The installer flag requires Inno Setup 6 (winget install JRSoftware.InnoSetup).
# Equivalent of the macOS build.sh.

param(
    [switch]$Installer
)

$ErrorActionPreference = "Stop"

# Always run from the script's own directory
Set-Location -Path $PSScriptRoot

Write-Host ""
Write-Host "=== KeyLess by Sinsajo Build (Windows) ===" -ForegroundColor Cyan
Write-Host ""

# --- Step 1: venv check ---
Write-Host "[1/5] Verificando venv..." -ForegroundColor Yellow
if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Host "  No existe venv. Crealo con: python -m venv venv" -ForegroundColor Red
    exit 1
}
Write-Host "  venv OK."

# --- Step 2: PyInstaller ---
Write-Host "[2/5] PyInstaller..." -ForegroundColor Yellow
# pip writes progress/warnings to stderr; under ErrorActionPreference=Stop,
# PowerShell 5.1 turns that into a fatal NativeCommandError even on success.
# Relax around the call and judge by the REAL exit code instead.
$ErrorActionPreference = "Continue"
& .\venv\Scripts\python.exe -m pip install --quiet pyinstaller 2>$null
$pipExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($pipExit -ne 0) {
    Write-Host "  pip install pyinstaller fallo (exit $pipExit)." -ForegroundColor Red
    exit $pipExit
}
Write-Host "  PyInstaller listo."

# --- Step 3: .ico ---
Write-Host "[3/5] Icono..." -ForegroundColor Yellow
if (-not (Test-Path "keylessflow.ico")) {
    & .\venv\Scripts\python.exe -c @"
from PIL import Image
img = Image.open('logo.png').convert('RGBA')
img.save('keylessflow.ico', format='ICO', sizes=[(16,16),(20,20),(24,24),(32,32),(40,40),(48,48),(64,64),(96,96),(128,128),(256,256)])
"@
    Write-Host "  keylessflow.ico generado."
} else {
    Write-Host "  keylessflow.ico ya existe."
}

# --- Step 4: Clean ---
# The project lives under OneDrive, which can hold a handle on files from the
# previous build for a few seconds ("Acceso denegado" on base_library.zip).
# Retry the delete, and if it still fails just continue: PyInstaller
# --noconfirm overwrites whatever is left.
Write-Host "[4/5] Limpiando builds anteriores..." -ForegroundColor Yellow
function Remove-Retry($path) {
    if (-not (Test-Path $path)) { return }
    for ($i = 1; $i -le 5; $i++) {
        try {
            Remove-Item -Recurse -Force $path -ErrorAction Stop
            return
        } catch {
            Write-Host "  $path bloqueado (intento $i/5), reintentando..." -ForegroundColor DarkYellow
            Start-Sleep -Seconds 3
        }
    }
    Write-Host "  No se pudo borrar $path por completo - continuando (PyInstaller sobreescribe)." -ForegroundColor DarkYellow
}
Remove-Retry "build"
Remove-Retry "dist"
Write-Host "  Listo."

# --- Step 5: Build ---
Write-Host "[5/5] Construyendo .exe (esto toma ~1-2 min)..." -ForegroundColor Yellow
# PyInstaller loguea TODO a stderr; misma trampa de PS 5.1 que pip (ver Step 2).
$ErrorActionPreference = "Continue"
& .\venv\Scripts\python.exe -m PyInstaller keylessflow.spec --noconfirm 2>&1 |
    ForEach-Object { "$_" } | Select-Object -Last 5
$pyiExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($pyiExit -ne 0) {
    Write-Host "  PyInstaller fallo (exit $pyiExit)." -ForegroundColor Red
    exit $pyiExit
}

Write-Host ""
Write-Host "=== BUILD COMPLETO ===" -ForegroundColor Green
Write-Host ""
$exePath = Join-Path (Get-Location) "dist\KeyLessFlow\KeyLessFlow.exe"
if (Test-Path $exePath) {
    $size = (Get-ChildItem -Recurse "dist\KeyLessFlow" | Measure-Object -Property Length -Sum).Sum
    $sizeMB = [math]::Round($size / 1MB, 1)
    Write-Host "  Archivo:   $exePath"
    Write-Host "  Bundle:    $sizeMB MB"
    Write-Host ""
    Write-Host "  Para correrlo:" -ForegroundColor Cyan
    Write-Host "    & '$exePath'"
    Write-Host ""
    Write-Host "  El .env se lee de %LOCALAPPDATA%\KeyLessFlow\.env"
    Write-Host "  Si no existe, la app pedira tu Groq API key en el primer launch."
    Write-Host ""
} else {
    Write-Host "  El .exe no se genero. Revisa el output arriba." -ForegroundColor Red
    exit 1
}

# --- Optional Step 6: Installer (Inno Setup) ---
if ($Installer) {
    Write-Host ""
    Write-Host "=== INSTALLER (Inno Setup) ===" -ForegroundColor Cyan
    $isccCandidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"  # per-user winget install
    )
    $iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        Write-Host "  Inno Setup no esta instalado." -ForegroundColor Yellow
        Write-Host "  Instalalo con:  winget install JRSoftware.InnoSetup"
        Write-Host "  Despues vuelve a correr:  .\build.ps1 -Installer"
        exit 1
    }
    Write-Host "  Usando: $iscc"
    $ErrorActionPreference = "Continue"
    & $iscc installer.iss 2>&1 | ForEach-Object { "$_" } | Select-Object -Last 4
    $isccExit = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($isccExit -ne 0) {
        Write-Host "  Inno Setup fallo (exit $isccExit)." -ForegroundColor Red
        exit $isccExit
    }
    $setupPath = Join-Path (Get-Location) "dist\KeyLessFlow-Setup.exe"
    if (Test-Path $setupPath) {
        $setupSize = [math]::Round((Get-Item $setupPath).Length / 1MB, 1)
        # SHA256 companion — upload BOTH files to the GitHub release. The
        # auto-updater verifies the download against this and refuses to run
        # a mismatching binary.
        $hash = (Get-FileHash $setupPath -Algorithm SHA256).Hash.ToLower()
        "$hash  KeyLessFlow-Setup.exe" | Out-File -Encoding ascii "$setupPath.sha256"
        Write-Host ""
        Write-Host "=== INSTALLER LISTO ===" -ForegroundColor Green
        Write-Host "  Archivo:   $setupPath"
        Write-Host "  Tamano:    $setupSize MB"
        Write-Host "  SHA256:    $hash"
        Write-Host "  Checksum:  $setupPath.sha256  (subir AMBOS al release)"
        Write-Host ""
        Write-Host "  Comparte ese unico .exe - los usuarios lo doble-clickean y se instala." -ForegroundColor Cyan
        Write-Host ""
    }
}
