"""Keep the local server private to JARVIS's own windows.

JARVIS listens on 127.0.0.1, but every web page you open can also reach 127.0.0.1. Without these
checks a malicious site could open the WebSocket and send JARVIS commands (cross-site WebSocket
hijacking), fire state-changing POSTs, or, via DNS rebinding, read the API. Defences (OWASP):

  • Host allowlist: only 127.0.0.1 / localhost / [::1] (defeats DNS rebinding)
  • Origin check on the WebSocket and on every non-GET request: it must be JARVIS's own origin,
    or absent (native clients such as the menu bar and the mini panel's helpers)
  • Fetch Metadata on everything: a browser marks requests from other sites "cross-site";
    those are refused, so other pages can't even embed the camera stream
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("jarvis.guard")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1", "testserver"}


def _headers(scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


def _host_ok(host: str) -> bool:
    if not host:
        return True
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return name.lower() in LOCAL_HOSTS


def _split(netloc: str) -> tuple[str, str]:
    if netloc.startswith("["):
        name, _, port = netloc[1:].partition("]")
        return f"[{name}]", port.lstrip(":")
    name, _, port = netloc.partition(":")
    return name, port


def _origin_ok(origin: str | None, host: str) -> bool:
    """Same-origin only: the page must have been served by this very server."""
    if origin is None:
        return True                       # not a browser page (curl, httpx, native helpers)
    scheme, sep, netloc = origin.partition("://")
    if not sep or scheme not in ("http", "https") or not _host_ok(netloc):
        return False
    if not host:
        return True
    (o_name, o_port), (h_name, h_port) = _split(netloc), _split(host)
    return o_port == h_port               # 127.0.0.1 vs localhost on the same port is still us


def verdict(scope) -> str | None:
    """None if allowed, else a reason."""
    h = _headers(scope)
    host = h.get("host", "")
    if not _host_ok(host):
        return f"host {host!r} not allowed"
    if h.get("sec-fetch-site") in ("cross-site", "same-site"):
        return "cross-site request"
    method = scope.get("method", "GET")
    if scope["type"] == "websocket" or method not in ("GET", "HEAD", "OPTIONS"):
        if not _origin_ok(h.get("origin"), host):
            return f"origin {h.get('origin')!r} not allowed"
    return None


class LocalOnly:
    """Pure ASGI middleware (covers HTTP and WebSocket)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            reason = verdict(scope)
            if reason:
                log.warning("blocked %s %s: %s", scope["type"], scope.get("path"), reason)
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4403})
                    return
                body = json.dumps({"ok": False, "error": "forbidden"}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
