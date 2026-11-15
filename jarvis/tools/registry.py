"""Tool registry: declare a Python function once, get a Claude tool schema,
input validation, risk gating (spoken confirmation) and HUD telemetry for free."""
from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("jarvis.tools")

JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass
class ImageResult:
    """Return this (or a list containing it) from a tool to hand Claude an image."""
    data: bytes
    media_type: str = "image/jpeg"
    caption: str = ""


@dataclass
class ToolResult:
    content: str | list[dict]
    is_error: bool = False

    def to_block(self, tool_use_id: str) -> dict:
        block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": self.content}
        if self.is_error:
            block["is_error"] = True
        return block

    @property
    def summary(self) -> str:
        if isinstance(self.content, str):
            return self.content[:300]
        texts = [b.get("text", "") for b in self.content if b.get("type") == "text"]
        imgs = sum(1 for b in self.content if b.get("type") == "image")
        return (" ".join(texts)[:300] + (f" [+{imgs} image]" if imgs else "")).strip()


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    func: Callable[..., Any]
    confirm: bool = False
    confirm_message: str | Callable[[dict], str] | None = None
    category: str = "general"
    available: Callable[[], bool] | None = None

    def schema(self, eager: bool = True) -> dict:
        out = {"name": self.name, "description": self.description, "input_schema": self.input_schema}
        if eager:
            out["eager_input_streaming"] = True
        return out

    def describe_action(self, args: dict) -> str:
        if callable(self.confirm_message):
            try:
                return self.confirm_message(args)
            except Exception:  # pragma: no cover
                pass
        if isinstance(self.confirm_message, str):
            try:
                return self.confirm_message.format(**args)
            except (KeyError, IndexError):
                return self.confirm_message
        return f"run {self.name.replace('_', ' ')}"


def validate(schema: dict, args: Any, path: str = "input") -> list[str]:
    """Small JSON-schema subset validator (type/required/enum/items/properties).
    Needed because eager input streaming hands us unvalidated tool input."""
    errors: list[str] = []
    expected = schema.get("type")
    if expected:
        py = JSON_TYPES.get(expected)
        ok = isinstance(args, py) if py else True
        if expected in ("integer", "number") and isinstance(args, bool):
            ok = False
        if expected == "integer" and isinstance(args, float) and args.is_integer():
            ok = True
        if not ok:
            return [f"{path}: expected {expected}, got {type(args).__name__}"]
    if "enum" in schema and args not in schema["enum"]:
        errors.append(f"{path}: {args!r} not one of {schema['enum']}")
    if isinstance(args, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in args:
                errors.append(f"{path}.{req}: required")
        for key, value in args.items():
            if key in props:
                errors.extend(validate(props[key], value, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: unexpected property")
    if isinstance(args, list) and "items" in schema:
        for i, item in enumerate(args):
            errors.extend(validate(schema["items"], item, f"{path}[{i}]"))
    return errors


def _normalise(value: Any) -> str | list[dict]:
    """Turn whatever a tool returned into tool_result content."""
    if isinstance(value, ImageResult):
        value = [value]
    if isinstance(value, list) and any(isinstance(v, ImageResult) for v in value):
        blocks: list[dict] = []
        for item in value:
            if isinstance(item, ImageResult):
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": item.media_type,
                        "data": base64.standard_b64encode(item.data).decode("ascii"),
                    },
                })
                if item.caption:
                    blocks.append({"type": "text", "text": item.caption})
            else:
                blocks.append({"type": "text", "text": item if isinstance(item, str) else json.dumps(item, default=str)})
        return blocks
    if value is None:
        return "Done."
    if isinstance(value, str):
        return value or "Done."
    return json.dumps(value, default=str, ensure_ascii=False)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ── declaration ────────────────────────────────────────────────────────
    def register(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def tool(
        self,
        description: str,
        params: dict[str, dict] | None = None,
        required: list[str] | None = None,
        name: str | None = None,
        confirm: bool = False,
        confirm_message: str | Callable[[dict], str] | None = None,
        category: str = "general",
        available: Callable[[], bool] | None = None,
    ) -> Callable:
        def decorator(func: Callable) -> Callable:
            schema = {
                "type": "object",
                "properties": params or {},
                "required": list(required or []),
                "additionalProperties": False,
            }
            self.register(Tool(
                name=name or func.__name__,
                description=description.strip(),
                input_schema=schema,
                func=func,
                confirm=confirm,
                confirm_message=confirm_message,
                category=category,
                available=available,
            ))
            return func
        return decorator

    # ── introspection ──────────────────────────────────────────────────────
    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def active(self) -> list[Tool]:
        out = []
        for name in sorted(self._tools):  # deterministic order keeps the prompt cache warm
            t = self._tools[name]
            try:
                if t.available is None or t.available():
                    out.append(t)
            except Exception:  # pragma: no cover
                continue
        return out

    def schemas(self, eager: bool = True) -> list[dict]:
        return [t.schema(eager) for t in self.active()]

    # ── execution ──────────────────────────────────────────────────────────
    async def call(self, name: str, args: Any, ctx: Any) -> ToolResult:
        from jarvis.events import bus

        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"Unknown tool: {name}", is_error=True)
        errors = validate(tool.input_schema, args)
        if errors:
            return ToolResult(
                json.dumps({"INVALID_INPUT": errors, "received": args}, default=str),
                is_error=True,
            )

        if tool.confirm and getattr(ctx, "confirm", None) is not None and ctx.require_confirmation:
            action = tool.describe_action(args)
            approved = await ctx.confirm(action)
            if not approved:
                bus.emit("tool", name=name, status="declined", args=args)
                return ToolResult(f"The user declined: {action}. Do not retry unless asked.", is_error=True)

        bus.emit("tool", name=name, status="start", args=args, category=tool.category)
        started = time.perf_counter()
        try:
            if inspect.iscoroutinefunction(tool.func):
                value = await tool.func(ctx, **args)
            else:
                value = await asyncio.to_thread(tool.func, ctx, **args)
            result = ToolResult(_normalise(value))
        except Exception as exc:  # tools must never crash the brain
            log.exception("tool %s failed", name)
            result = ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
        elapsed = time.perf_counter() - started
        bus.emit(
            "tool", name=name, status="error" if result.is_error else "done",
            ms=int(elapsed * 1000), summary=result.summary,
        )
        return result


registry = ToolRegistry()
