#!/bin/bash
# Installs (or updates) JARVIS.app in /Applications and opens it.
#   curl -fsSL https://raw.githubusercontent.com/BireshwarChakravarty/JarvisServer/claude/eager-newton-yifemd/scripts/install-app.sh | bash
#
# Start completely fresh (removes JARVIS, its settings, data, models and permissions first):
#   curl -fsSL …/install-app.sh | bash -s -- --fresh
#
# Why it re-signs the app: macOS ties every privacy permission (camera, microphone, Accessibility,
# Screen Recording…) to the app's code-signing identity. Downloaded builds are ad-hoc signed, so
# each update looks like a brand-new app and your permissions silently stop applying. Here we
# create a private "JARVIS Local" signing identity on THIS Mac (the key is generated here and never
# leaves it) and sign every installed version with it, so macOS keeps recognising JARVIS and your
# permissions survive updates.
set -euo pipefail
REPO="BireshwarChakravarty/JarvisServer"
URL="https://github.com/$REPO/releases/download/jarvis-latest/JARVIS-macOS-AppleSilicon.zip"
APP="/Applications/JARVIS.app"
BUNDLE_ID="com.jarvisassistant.jarvis"
SIGN_DIR="$HOME/Library/Application Support/JARVIS/signing"
KC="$SIGN_DIR/jarvis-signing.keychain-db"
NAME="JARVIS Local Code Signing"
OPENSSL=/usr/bin/openssl   # macOS's LibreSSL: its PKCS#12 output is what `security import` reads

FRESH=0
for arg in "$@"; do [[ "$arg" == "--fresh" ]] && FRESH=1; done

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "JARVIS.app needs a Mac with Apple Silicon." >&2; exit 1
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# download first: if it fails, nothing on this Mac has been touched yet
echo "▸ Downloading JARVIS…"
curl -fL --progress-bar "$URL" -o "$TMP/JARVIS.zip"
ditto -x -k "$TMP/JARVIS.zip" "$TMP/x"
[[ -d "$TMP/x/JARVIS.app" ]] || { echo "The download didn't contain JARVIS.app; nothing was changed." >&2; exit 1; }

if [[ $FRESH == 1 ]]; then
  echo "▸ Fresh install: removing JARVIS and everything it stored on this Mac…"
  # turn the login item off while the app can still do it
  curl -s -m 3 -X POST -H 'Content-Type: application/json' -d '{"enabled":false}' \
    http://127.0.0.1:7777/api/setup/login-item >/dev/null 2>&1 || true
  osascript -e 'tell application "JARVIS" to quit' >/dev/null 2>&1 || true
  sleep 1; pkill -f "JARVIS.app/Contents/MacOS" >/dev/null 2>&1 || true
  pkill -f "JARVIS-brain-workspace" >/dev/null 2>&1 || true             # its Claude Code / Codex helper, if left running
  rm -rf "$APP" "$HOME/Applications/JARVIS.app"
  rm -rf "$HOME/Library/Application Support/JARVIS" "$HOME/.jarvis"          # settings, faces, memory, models, logs
  for id in com.jarvisassistant.jarvis com.jarvis.assistant; do
    rm -rf "$HOME/Library/Caches/$id" "$HOME/Library/WebKit/$id" "$HOME/Library/HTTPStorages/$id" \
           "$HOME/Library/HTTPStorages/$id.binarycookies" "$HOME/Library/Saved Application State/$id.savedState"
    defaults delete "$id" >/dev/null 2>&1 || true
    rm -f "$HOME/Library/Preferences/$id.plist"
    tccutil reset All "$id" >/dev/null 2>&1 || true                    # every permission, asked afresh
  done
  for id in com.jarvisassistant.jarvis com.jarvis.assistant JARVIS; do
    rm -rf "$HOME/Library/Logs/$id"                                    # logs, if any were written there
  done
  launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.jarvis.assistant.plist" >/dev/null 2>&1 || true
  rm -f "$HOME/Library/LaunchAgents/com.jarvis.assistant.plist"
  osascript -e 'tell application "System Events" to delete (every login item whose name is "JARVIS")' \
    >/dev/null 2>&1 || true                                            # a login item left from an older build
  while security delete-generic-password -s "JARVIS Anthropic API Key" >/dev/null 2>&1; do :; done
  # JARVIS's conversations as Claude Code recorded them (its project folder for JARVIS's workspace).
  # Your Claude sign-in and every other project of yours are left exactly as they are.
  find "$HOME/.claude/projects" -maxdepth 1 -type d -name "*JARVIS-brain-workspace*" -exec rm -rf {} + 2>/dev/null || true
  # speech models fetched through the Hugging Face cache (Apple Silicon voice and transcription)
  for repo in Kokoro-82M-bf16 whisper-large-v3-turbo whisper-large-v3-mlx whisper-medium.en-mlx \
              whisper-small.en-mlx whisper-base.en-mlx whisper-tiny.en-mlx; do
    rm -rf "$HOME/.cache/huggingface/hub/models--mlx-community--$repo"
  done
  rm -rf "$HOME/.cache/huggingface/hub/"models--mlx-community--Qwen3-TTS-* 2>/dev/null || true   # natural voice
  rm -rf "${TMPDIR:-/tmp}"/jarvis-* 2>/dev/null || true
  lsregister=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
  [[ -x "$lsregister" ]] && "$lsregister" -u "$APP" >/dev/null 2>&1 || true
  echo "  ✓ removed: the app, settings, memory, voiceprint, face data, models, logs, paired devices,"
  echo "    signing identity, permissions, saved key and JARVIS's Claude Code history."
  echo "    (Claude Code itself and your Claude sign-in are separate and were left alone. On a paired"
  echo "    iPhone or iPad, remove the JARVIS profile in Settings › General › VPN & Device Management.)"
fi

echo "▸ Installing to /Applications…"
osascript -e 'tell application "JARVIS" to quit' >/dev/null 2>&1 || true
pkill -f "JARVIS.app/Contents/MacOS" >/dev/null 2>&1 || true
rm -rf "$APP"
ditto "$TMP/x/JARVIS.app" "$APP"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

# ── a stable signing identity, created once on this Mac ───────────────────────────────────
make_identity() {
  mkdir -p "$SIGN_DIR"; chmod 700 "$SIGN_DIR"
  local pw; pw="$($OPENSSL rand -hex 24)"
  printf '%s' "$pw" > "$SIGN_DIR/keychain-password"; chmod 600 "$SIGN_DIR/keychain-password"
  cat > "$TMP/cert.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
EOF
  $OPENSSL req -x509 -newkey rsa:2048 -nodes -days 7300 -config "$TMP/cert.cnf" \
    -keyout "$TMP/key.pem" -out "$SIGN_DIR/cert.pem" 2>/dev/null
  $OPENSSL pkcs12 -export -inkey "$TMP/key.pem" -in "$SIGN_DIR/cert.pem" -name "$NAME" \
    -out "$TMP/id.p12" -passout "pass:$pw" 2>/dev/null
  rm -f "$KC"
  security create-keychain -p "$pw" "$KC"
  security set-keychain-settings "$KC"                       # never auto-locks
  security unlock-keychain -p "$pw" "$KC"
  security import "$TMP/id.p12" -k "$KC" -P "$pw" -T /usr/bin/codesign >/dev/null
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$pw" "$KC" >/dev/null
  rm -f "$TMP/key.pem" "$TMP/id.p12"
}

sign_app() {
  [[ -f "$KC" && -f "$SIGN_DIR/keychain-password" && -f "$SIGN_DIR/cert.pem" ]] || make_identity
  local pw sha old
  pw="$(cat "$SIGN_DIR/keychain-password")"
  security unlock-keychain -p "$pw" "$KC"
  sha="$($OPENSSL x509 -in "$SIGN_DIR/cert.pem" -noout -fingerprint -sha1 | sed 's/.*=//; s/://g')"
  # codesign looks identities up through the keychain search list: add ours just for this
  old="$(security list-keychains -d user | tr -d '"' | xargs)"
  # shellcheck disable=SC2086
  security list-keychains -d user -s "$KC" $old
  local ok=0
  codesign --force --deep --preserve-metadata=entitlements,flags --sign "$sha" "$APP" >"$TMP/sign.log" 2>&1 && ok=1
  # shellcheck disable=SC2086
  security list-keychains -d user -s $old
  [[ $ok == 1 ]] && codesign --verify --deep "$APP" >/dev/null 2>&1 || return 1
  echo "$sha"
}

echo "▸ Signing with this Mac's private JARVIS identity (keeps your permissions across updates)…"
if SHA="$(sign_app)"; then
  LAST="$(cat "$SIGN_DIR/installed-identity" 2>/dev/null || true)"
  if [[ "$LAST" != "$SHA" ]]; then
    # First install with this identity: clear grants recorded for the old, ad-hoc builds so
    # macOS asks afresh (otherwise System Settings shows them ON while they no longer apply).
    tccutil reset All "$BUNDLE_ID" >/dev/null 2>&1 || true
    echo "  JARVIS will ask for each permission once more. After that they stick across updates."
  fi
  printf '%s' "$SHA" > "$SIGN_DIR/installed-identity"
  echo "  ✓ signed"
else
  echo "  (couldn't sign locally, keeping the downloaded signature; see $TMP/sign.log)" >&2
  cat "$TMP/sign.log" >&2 2>/dev/null || true
  xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
fi

VER="$(find "$APP/Contents" -path '*/jarvis/_version.txt' -print -quit 2>/dev/null | xargs cat 2>/dev/null || true)"
echo "✓ Installed JARVIS ${VER:-(version unknown)}. Opening it…"
echo "  (check it any time: the JARVIS menu-bar icon shows the version at the top)"
open "$APP"
