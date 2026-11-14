#!/bin/bash
# Installs the latest verified JARVIS.app build into /Applications and opens it.
#   curl -fsSL https://raw.githubusercontent.com/BireshwarChakravarty/JarvisServer/claude/eager-newton-yifemd/scripts/install-app.sh | bash
set -euo pipefail
REPO="BireshwarChakravarty/JarvisServer"
URL="https://github.com/$REPO/releases/download/jarvis-latest/JARVIS-macOS-AppleSilicon.zip"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "JARVIS.app needs a Mac with Apple Silicon." >&2; exit 1
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "▸ Downloading JARVIS…"
curl -fL --progress-bar "$URL" -o "$TMP/JARVIS.zip"
echo "▸ Installing to /Applications…"
osascript -e 'tell application "JARVIS" to quit' >/dev/null 2>&1 || true
pkill -f "JARVIS.app/Contents/MacOS" >/dev/null 2>&1 || true
ditto -x -k "$TMP/JARVIS.zip" "$TMP/x"
rm -rf /Applications/JARVIS.app
ditto "$TMP/x/JARVIS.app" /Applications/JARVIS.app
xattr -dr com.apple.quarantine /Applications/JARVIS.app 2>/dev/null || true
echo "✓ Installed. Opening JARVIS…"
open /Applications/JARVIS.app
