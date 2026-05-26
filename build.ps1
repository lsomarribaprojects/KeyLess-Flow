# build.ps1 — Build KeyLess Flow Windows bundle from source (one shot).
#
# Output: dist\KeyLessFlow\KeyLessFlow.exe (onedir)
# Equivalent of the macOS build.sh.

$ErrorActionPreference = "Stop"

# Always run from the script's own directory
Set-Location -Path $PSScriptRoot

Write-Host ""
Write-Host "=== KeyLess Flow Build (Windows) ===" -ForegroundColor Cyan
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
& .\venv\Scripts\python.exe -m pip install --quiet pyinstaller
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
Write-Host "[4/5] Limpiando builds anteriores..." -ForegroundColor Yellow
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
Write-Host "  Listo."

# --- Step 5: Build ---
Write-Host "[5/5] Construyendo .exe (esto toma ~1-2 min)..." -ForegroundColor Yellow
& .\venv\Scripts\python.exe -m PyInstaller keylessflow.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host "  PyInstaller fallo (exit $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
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
