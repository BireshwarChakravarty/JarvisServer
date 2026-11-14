"""Builds ~/Applications/JARVIS.app — a tiny wrapper that launches JARVIS from this
virtualenv. Running as an app means macOS asks for Microphone / Camera / Automation
permission *for JARVIS* (with proper explanations), and it can live in Login Items so
JARVIS greets you the moment you log in."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "JARVIS"
BUNDLE_ID = "com.jarvis.assistant"


def _icon_png(path: Path, size: int = 1024) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        return False
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    d.rounded_rectangle([0, 0, size, size], radius=size * 0.22, fill=(4, 12, 22, 255))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([c - size * 0.3, c - size * 0.3, c + size * 0.3, c + size * 0.3], fill=(25, 230, 255, 140))
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.08))
    img.alpha_composite(glow)
    for r, w, a in ((0.38, 10, 200), (0.31, 26, 255), (0.2, 8, 220)):
        d.ellipse([c - size * r, c - size * r, c + size * r, c + size * r], outline=(25, 230, 255, a), width=int(w))
    for i in range(10):  # coil segments
        a0 = i * 36 + 4
        d.arc([c - size * 0.27, c - size * 0.27, c + size * 0.27, c + size * 0.27], a0, a0 + 28,
              fill=(150, 245, 255, 255), width=int(size * 0.05))
    d.ellipse([c - size * 0.11, c - size * 0.11, c + size * 0.11, c + size * 0.11], fill=(230, 255, 255, 255))
    img.save(path)
    return True


def build_app(dest_dir: Path | None = None) -> Path:
    dest_dir = dest_dir or Path.home() / "Applications"
    dest_dir.mkdir(parents=True, exist_ok=True)
    app = dest_dir / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    res = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)

    python = sys.executable
    launcher = macos / APP_NAME
    launcher.write_text(f"""#!/bin/bash
# Launches J.A.R.V.I.S. from the virtualenv it was installed in.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1
export JARVIS_APP=1
mkdir -p "$HOME/.jarvis"
cd "$HOME"
exec "{python}" -m jarvis run >> "$HOME/.jarvis/jarvis.log" 2>&1
""")
    launcher.chmod(0o755)

    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": "J.A.R.V.I.S.",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleExecutable": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleIconFile": "jarvis",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "JARVIS listens for “Hey Jarvis” and your voice commands.",
        "NSCameraUsageDescription": "JARVIS recognises you, greets you when you sit down, and reads your gestures.",
        "NSAppleEventsUsageDescription": "JARVIS controls apps like Music, Calendar, Messages and System Events on your behalf.",
        "NSCalendarsFullAccessUsageDescription": "JARVIS reads and creates calendar events for your briefings.",
        "NSRemindersFullAccessUsageDescription": "JARVIS reads and creates reminders for you.",
        "NSContactsUsageDescription": "JARVIS looks up contacts when you ask him to message someone.",
    }
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump(info, fh)

    png = res / "jarvis.png"
    if _icon_png(png) and shutil.which("iconutil") and shutil.which("sips"):
        iconset = res / "jarvis.iconset"
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f"icon_{s}x{s}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(s * scale), str(s * scale), str(png), "--out", str(iconset / name)],
                               capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(res / "jarvis.icns")], capture_output=True)
        shutil.rmtree(iconset, ignore_errors=True)
    png.unlink(missing_ok=True)
    return app


def add_login_item(app: Path) -> None:
    from jarvis.mac.system import osascript, q

    osascript(
        'tell application "System Events"\n'
        f'  if exists login item "{APP_NAME}" then delete login item "{APP_NAME}"\n'
        f"  make login item at end with properties {{path:{q(str(app))}, hidden:false}}\n"
        "end tell"
    )


def remove_login_item() -> None:
    from jarvis.mac.system import osascript

    osascript(f'tell application "System Events" to if exists login item "{APP_NAME}" then delete login item "{APP_NAME}"')


LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def install_launch_agent() -> Path:
    LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
    home = Path.home() / ".jarvis"
    plist = {
        "Label": BUNDLE_ID,
        "ProgramArguments": [sys.executable, "-m", "jarvis", "run"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        "WorkingDirectory": str(Path.home()),
        "StandardOutPath": str(home / "jarvis.log"),
        "StandardErrorPath": str(home / "jarvis.log"),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "PYTHONUNBUFFERED": "1"},
    }
    with open(LAUNCH_AGENT, "wb") as fh:
        plistlib.dump(plist, fh)
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(LAUNCH_AGENT)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(LAUNCH_AGENT)], capture_output=True)
    return LAUNCH_AGENT


def uninstall_launch_agent() -> None:
    if LAUNCH_AGENT.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(LAUNCH_AGENT)], capture_output=True)
        LAUNCH_AGENT.unlink()
