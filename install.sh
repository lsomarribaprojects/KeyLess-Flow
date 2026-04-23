#!/bin/bash
# install.sh — Build, install, kill old proc, launch new, open Accessibility.
# Use this instead of `build.sh` when you actually want the running SFlow
# to update. `build.sh` only produces the bundle; this completes the dance.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

bash build.sh

echo ""
echo "=== INSTALL ==="
ditto dist/SFlow.app /Applications/SFlow.app
xattr -cr /Applications/SFlow.app
echo "  → /Applications/SFlow.app"

echo ""
echo "=== KILL OLD INSTANCE ==="
# Why: a running SFlow process keeps using the OLD binary in memory; macOS
# also revokes Accessibility for the new ad-hoc-signed binary, so paste
# breaks silently. Killing forces the user into a clean re-permission flow.
OLD_PID=$(pgrep -f "/Applications/SFlow.app/Contents/MacOS/SFlow" || true)
if [ -n "$OLD_PID" ]; then
    echo "  Matando PID $OLD_PID..."
    kill -TERM $OLD_PID 2>/dev/null || true
    sleep 0.4
    # SIGKILL fallback if still alive
    kill -KILL $OLD_PID 2>/dev/null || true
    echo "  Listo."
else
    echo "  No hay instancia corriendo."
fi

echo ""
echo "=== LAUNCH NEW INSTANCE ==="
open -n /Applications/SFlow.app
sleep 0.6
echo "  Lanzada."

echo ""
echo "=== ACCESSIBILITY PANEL ==="
# After every ad-hoc rebuild, macOS revokes Accessibility for the new binary
# hash. Open the panel so the user can re-add SFlow with one click.
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" || true
echo "  Panel abierto. Quita SFlow (-) y vuelvelo a agregar (+)"
echo "  apuntando a /Applications/SFlow.app"
echo ""
echo "  Luego repite en: Privacy & Security → Input Monitoring"
echo ""
echo "=== INSTALL COMPLETO ==="
