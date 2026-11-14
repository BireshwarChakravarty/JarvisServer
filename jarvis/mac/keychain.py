"""Store the Anthropic API key in the macOS login Keychain (via /usr/bin/security)."""
from __future__ import annotations

import subprocess
import sys

SERVICE = "JARVIS Anthropic API Key"
ACCOUNT = "jarvis"


def available() -> bool:
    return sys.platform == "darwin"


def get_api_key() -> str | None:
    if not available():
        return None
    proc = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"],
                          capture_output=True, text=True)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def set_api_key(key: str) -> None:
    if not available():
        raise RuntimeError("Keychain is only available on macOS")
    proc = subprocess.run(["/usr/bin/security", "add-generic-password", "-U", "-s", SERVICE, "-a", ACCOUNT,
                           "-l", SERVICE, "-w", key], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "couldn't save to Keychain")


def delete_api_key() -> None:
    if available():
        subprocess.run(["/usr/bin/security", "delete-generic-password", "-s", SERVICE, "-a", ACCOUNT],
                       capture_output=True)
