"""Plug any Model Context Protocol server into JARVIS (filesystem, GitHub, Notion,
Home Assistant, databases …). Configure under `mcp_servers:` in config.yaml."""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

log = logging.getLogger("jarvis.mcp")


class MCPBridge:
    def __init__(self, servers: dict[str, dict]) -> None:
        self.servers = servers or {}
        self.stack = AsyncExitStack()
        self.sessions: dict[str, Any] = {}
        self.tools: list[dict] = []
        self.handlers: dict[str, Any] = {}

    async def start(self) -> int:
        if not self.servers:
            return 0
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            log.warning("mcp_servers configured but the `mcp` package isn't installed (pip install mcp)")
            return 0
        for name, spec in self.servers.items():
            try:
                params = StdioServerParameters(command=spec["command"], args=spec.get("args", []),
                                               env=spec.get("env"))
                read, write = await self.stack.enter_async_context(stdio_client(params))
                session = await self.stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = await session.list_tools()
                self.sessions[name] = session
                for t in listed.tools:
                    full = f"mcp__{name}__{t.name}"[:64]
                    self.tools.append({
                        "name": full,
                        "description": (t.description or f"{t.name} from {name}")[:1024],
                        "input_schema": t.inputSchema or {"type": "object", "properties": {}},
                    })
                    self.handlers[full] = self._make_handler(session, t.name)
                log.info("MCP server %s: %d tools", name, len(listed.tools))
            except Exception as exc:
                log.error("MCP server %s failed to start: %s", name, exc)
        return len(self.tools)

    def _make_handler(self, session, tool_name: str):
        async def handler(args: dict) -> Any:
            result = await session.call_tool(tool_name, args)
            parts = []
            for item in result.content or []:
                text = getattr(item, "text", None)
                parts.append(text if text is not None else str(item))
            out = "\n".join(parts) or "(no output)"
            if getattr(result, "isError", False):
                raise RuntimeError(out)
            return out[:20000]
        return handler

    async def close(self) -> None:
        await self.stack.aclose()
