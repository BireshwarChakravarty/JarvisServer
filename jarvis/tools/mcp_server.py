"""JARVIS's abilities as a Model Context Protocol server (Streamable HTTP, JSON responses).

The subscription brains (the official `claude` CLI for Claude Pro/Max, `codex` for ChatGPT
plans) run as separate processes; they reach JARVIS's tools — Mac control, camera, memory,
spoken confirmations — through this endpoint. It listens on 127.0.0.1 only, behind a random
secret path, so nothing else on the machine can call the tools.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

log = logging.getLogger("jarvis.mcp_server")

SERVER_NAME = "jarvis"
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def _rpc_result(id_: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _rpc_error(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def to_mcp_content(content: Any) -> list[dict]:
    """ToolResult.content (str or Claude-style blocks) -> MCP content items."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out = []
    for block in content or []:
        if block.get("type") == "image":
            src = block.get("source", {})
            out.append({"type": "image", "data": src.get("data", ""), "mimeType": src.get("media_type", "image/jpeg")})
        elif block.get("type") == "text":
            out.append({"type": "text", "text": block.get("text", "")})
    return out or [{"type": "text", "text": "Done."}]


class MCPServer:
    def __init__(self, registry, ctx, extra_tools: list[dict] | None = None, extra_handlers: dict | None = None) -> None:
        self.registry = registry
        self.ctx = ctx
        self.extra_tools = extra_tools or []
        self.extra_handlers = extra_handlers or {}
        self.secret = secrets.token_urlsafe(18)
        self.port = 0
        self._server = None
        self._task: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp/{self.secret}"

    def tools(self) -> list[dict]:
        out = [{"name": t.name, "description": t.description, "inputSchema": t.input_schema}
               for t in self.registry.active()]
        out += [{"name": t["name"], "description": t.get("description", ""),
                 "inputSchema": t.get("input_schema") or {"type": "object", "properties": {}}}
                for t in self.extra_tools]
        return out

    async def handle(self, msg: dict) -> dict | None:
        method, id_ = msg.get("method"), msg.get("id")
        if id_ is None:  # notification
            return None
        params = msg.get("params") or {}
        if method == "initialize":
            want = params.get("protocolVersion")
            version = want if want in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            return _rpc_result(id_, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": "J.A.R.V.I.S.", "version": "1.2.0"},
                "instructions": "JARVIS's abilities on the user's Mac. Tools that change things outside the Mac "
                                "(messages, shell, AppleScript) ask the user out loud before running.",
            })
        if method == "ping":
            return _rpc_result(id_, {})
        if method == "tools/list":
            return _rpc_result(id_, {"tools": self.tools()})
        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            if name in self.extra_handlers:
                try:
                    value = await self.extra_handlers[name](args)
                    return _rpc_result(id_, {"content": [{"type": "text", "text": str(value)}], "isError": False})
                except Exception as exc:
                    return _rpc_result(id_, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
            if name not in self.registry:
                return _rpc_result(id_, {"content": [{"type": "text", "text": f"Unknown tool {name}"}], "isError": True})
            result = await self.registry.call(name, args, self.ctx)
            return _rpc_result(id_, {"content": to_mcp_content(result.content), "isError": bool(result.is_error)})
        if method in ("resources/list", "prompts/list"):
            return _rpc_result(id_, {method.split("/")[0]: []})
        return _rpc_error(id_, -32601, f"Method not found: {method}")

    def app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.post("/mcp/{secret}")
        async def post(secret: str, request: Request):
            if not secrets.compare_digest(secret, self.secret):
                return Response(status_code=404)
            try:
                body = await request.json()
            except json.JSONDecodeError:
                return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
            if isinstance(body, list):
                replies = [r for r in [await self.handle(m) for m in body] if r is not None]
                return JSONResponse(replies) if replies else Response(status_code=202)
            reply = await self.handle(body)
            if reply is None:
                return Response(status_code=202)
            headers = {"Mcp-Session-Id": self.secret[:12]} if body.get("method") == "initialize" else {}
            return JSONResponse(reply, headers=headers)

        @app.get("/mcp/{secret}")
        async def get(secret: str):
            return Response(status_code=405)  # no server-initiated stream

        @app.delete("/mcp/{secret}")
        async def delete(secret: str):
            return Response(status_code=200)

        return app

    async def start(self) -> str:
        """Serve on a free localhost port inside the current event loop."""
        import uvicorn

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        config = uvicorn.Config(self.app(), host="127.0.0.1", port=self.port, log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None
        self._task = asyncio.ensure_future(self._server.serve())
        for _ in range(100):
            if self._server.started:
                break
            await asyncio.sleep(0.05)
        log.info("MCP tools endpoint on 127.0.0.1:%d", self.port)
        return self.url

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
