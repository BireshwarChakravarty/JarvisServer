#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  J.A.R.V.I.S. installer for macOS
#  Usage:  ./install.sh            (Apple Silicon or Intel, macOS 13+)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

C="\033[96m"; G="\033[92m"; A="\033[93m"; R="\033[91m"; D="\033[2m"; N="\033[0m"
say()  { printf "${C}▸${N} %s\n" "$*"; }
ok()   { printf "${G}✓${N} %s\n" "$*"; }
warn() { printf "${A}!${N} %s\n" "$*"; }
die()  { printf "${R}✗ %s${N}\n" "$*"; exit 1; }

printf "${C}\n   J . A . R . V . I . S .   —   installer\n${N}${D}   Just A Rather Very Intelligent System${N}\n\n"

[[ "$(uname -s)" == "Darwin" ]] || warn "Not macOS — installing the cross-platform core only (Mac control features will be disabled)."
ARCH="$(uname -m)"
say "Architecture: $ARCH"

# ── Python 3.12 (best wheel coverage for mediapipe / onnxruntime / mlx) ─────────
PY=""
if command -v uv >/dev/null 2>&1; then
  say "Using uv to create a Python 3.12 environment"
  uv venv --python 3.12 .venv >/dev/null
  PY=".venv/bin/python"
  PIP="uv pip install --python $PY"
else
  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)'; then
        PY_SYS="$candidate"; break
      fi
    fi
  done
  if [[ -z "${PY_SYS:-}" ]]; then
    if command -v brew >/dev/null 2>&1; then
      say "Installing Python 3.12 with Homebrew"
      brew install python@3.12
      PY_SYS="$(brew --prefix)/bin/python3.12"
    else
      die "Need Python 3.10–3.12. Install uv (https://docs.astral.sh/uv/) or Homebrew, then re-run."
    fi
  fi
  say "Creating virtualenv with $PY_SYS"
  "$PY_SYS" -m venv .venv
  PY=".venv/bin/python"
  "$PY" -m pip install --quiet --upgrade pip wheel
  PIP="$PY -m pip install --quiet"
fi
ok "Python: $($PY --version)"

# ── Dependencies ───────────────────────────────────────────────────────────────
EXTRAS="voice,vision,ui,mcp"
if [[ "$(uname -s)" == "Darwin" ]]; then
  EXTRAS="$EXTRAS,apple"
  [[ "$ARCH" == "arm64" ]] || EXTRAS="$EXTRAS,tts-onnx"   # Intel Macs: Kokoro on CPU
fi
say "Installing JARVIS with extras [$EXTRAS] — this takes a few minutes the first time"
$PIP -e ".[$EXTRAS]"
ok "Packages installed"

# Optional helpers
if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
  command -v brightness >/dev/null 2>&1 || { say "Installing 'brightness' CLI (display brightness control)"; brew install brightness >/dev/null 2>&1 || warn "brightness CLI skipped"; }
fi

# ── Put `jarvis` on PATH ───────────────────────────────────────────────────────
mkdir -p "$HOME/.local/bin"
ln -sf "$(pwd)/.venv/bin/jarvis" "$HOME/.local/bin/jarvis"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *)
  warn "Add ~/.local/bin to your PATH:  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc";;
esac
ok "Command installed: jarvis"

# ── Setup wizard ───────────────────────────────────────────────────────────────
printf "\n"
.venv/bin/jarvis setup

printf "\n${D}Next: grant permissions when macOS asks (Microphone, Camera, Automation, Calendars, Reminders).\n"
printf "Accessibility (for push-to-talk and typing) is under System Settings › Privacy & Security › Accessibility.${N}\n"
printf "\n${G}Launch:${N}  jarvis        ${D}or${N}  open ~/Applications/JARVIS.app\n\n"
