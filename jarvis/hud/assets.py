"""Pages always load the scripts and stylesheets that match them.

Each page's links to /static/… (and /remote/static/…) get a fingerprint of the file's contents
(`reactor.js?v=3f2a91c0`). After an update the fingerprint changes, so the window fetches the new file
instead of running a copy it cached from the old version: without this, macOS's web view could keep an
old script for days and an update would arrive half-applied (a new page around an old core). Static
files are also served with "check before reusing", which costs a cheap 304 when nothing changed.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

_REF = re.compile(r'((?:src|href)=")((?:/remote)?/static/)([\w./-]+\.(?:js|css))(")')
_cache: dict[Path, tuple[float, str]] = {}


def fingerprint(path: Path) -> str:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return "0"
    got = _cache.get(path)
    if got and got[0] == mtime:
        return got[1]
    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:10]
    _cache[path] = (mtime, digest)
    return digest


def versioned(html: str, static_dir: Path) -> str:
    """Add ?v=<content hash> to every local script and stylesheet link."""
    return _REF.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}?v={fingerprint(static_dir / m.group(3))}"
                    f"{m.group(4)}", html)


def page(path: Path, static_dir: Path, headers: dict | None = None) -> HTMLResponse:
    return HTMLResponse(versioned(path.read_text(encoding="utf-8"), static_dir), headers=headers)


class RevalidatedStatic(StaticFiles):
    """Static files that the browser must check (ETag / Last-Modified) before reusing."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response
