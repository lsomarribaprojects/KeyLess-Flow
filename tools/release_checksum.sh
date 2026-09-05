#!/usr/bin/env bash
# Publish a VERIFIED SHA256 companion for a GitHub release's Windows installer.
#
#   tools/release_checksum.sh v1.3.0
#
# Why this exists: a flaky download once produced an EMPTY hash that got
# uploaded, and the auto-updater (which fails closed on purpose) would have
# refused every update. This script refuses to upload anything it hasn't
# re-downloaded and matched byte-for-byte.
set -euo pipefail
TAG="${1:?usage: release_checksum.sh vX.Y.Z}"
REPO="lsomarribaprojects/KeyLess-Flow"
ASSET="KeyLessFlow-Setup.exe"
WORK="$(mktemp -d)"
cd "$WORK"

dl() {  # dl <pattern> [-O out] — up to 4 tries (TLS timeouts happen)
  for i in 1 2 3 4; do
    gh release download "$TAG" --repo "$REPO" --pattern "$1" ${2:+-O "$2"} --clobber && return 0
    echo "download retry $i…" >&2; sleep 5
  done
  return 1
}

dl "$ASSET"
SIZE=$(stat -c %s "$ASSET")
[ "$SIZE" -gt 10000000 ] || { echo "ABORT: $ASSET too small ($SIZE bytes) — partial download?"; exit 1; }
HASH=$(sha256sum "$ASSET" | awk '{print $1}')
[ ${#HASH} -eq 64 ] || { echo "ABORT: bad hash '$HASH'"; exit 1; }
printf '%s  %s\n' "$HASH" "$ASSET" > "$ASSET.sha256"

gh release upload "$TAG" "$ASSET.sha256" --repo "$REPO" --clobber

# Round-trip verification: what GitHub now serves must match the binary.
dl "$ASSET.sha256" verify.sha256
REMOTE=$(awk '{print $1}' verify.sha256)
[ "$REMOTE" = "$HASH" ] || { echo "ABORT: uploaded checksum mismatch (remote=$REMOTE local=$HASH)"; exit 1; }
echo "OK $TAG: $HASH  ($SIZE bytes) — checksum published and verified"
