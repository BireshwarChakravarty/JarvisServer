"""Paired iPhones and iPads.

Pairing: the Mac shows a QR code holding a short-lived pairing code (10 minutes, usable by several
devices so you can pair your phone and both iPads from one code). A device that presents it gets
its own random key. Only a SHA-256 of each key is stored, so this file can't be used to sign in.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

PAIR_TTL = 600.0


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class DeviceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._code: str | None = None
        self._code_expires = 0.0
        self.data = {"devices": {}}
        if path.exists():
            try:
                self.data = json.loads(path.read_text()) or {"devices": {}}
            except Exception:
                self.data = {"devices": {}}
        self.data.setdefault("devices", {})

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    # ── pairing codes ────────────────────────────────────────────────────
    def new_code(self) -> tuple[str, float]:
        with self._lock:
            self._code = secrets.token_urlsafe(18)
            self._code_expires = time.time() + PAIR_TTL
            return self._code, self._code_expires

    def current_code(self) -> tuple[str, float]:
        with self._lock:
            if self._code and time.time() < self._code_expires - 60:
                return self._code, self._code_expires
        return self.new_code()

    def pair(self, code: str, name: str) -> tuple[str, str] | None:
        """Exchange a valid pairing code for (device_id, key)."""
        with self._lock:
            ok = (self._code is not None and time.time() < self._code_expires
                  and secrets.compare_digest(str(code), self._code))
            if not ok:
                return None
            device_id, token = secrets.token_hex(6), secrets.token_urlsafe(32)
            self.data["devices"][device_id] = {
                "name": (name or "Device")[:60], "key": _hash(token),
                "added": int(time.time()), "last_seen": int(time.time()),
            }
            self._save()
            return device_id, token

    # ── keys ─────────────────────────────────────────────────────────────
    def check(self, token: str | None) -> str | None:
        if not token:
            return None
        h = _hash(token)
        with self._lock:
            for device_id, d in self.data["devices"].items():
                if secrets.compare_digest(d.get("key", ""), h):
                    if time.time() - d.get("last_seen", 0) > 60:
                        d["last_seen"] = int(time.time())
                        self._save()
                    return device_id
        return None

    def forget(self, device_id: str | None = None) -> int:
        with self._lock:
            if device_id is None:
                n = len(self.data["devices"])
                self.data["devices"] = {}
            else:
                n = 1 if self.data["devices"].pop(device_id, None) is not None else 0
            self._code = None                    # any QR on screen stops working too
            self._save()
            return n

    def list(self) -> list[dict]:
        with self._lock:
            return [{"id": k, "name": v["name"], "added": v["added"], "last_seen": v["last_seen"]}
                    for k, v in sorted(self.data["devices"].items(), key=lambda kv: -kv[1].get("last_seen", 0))]
