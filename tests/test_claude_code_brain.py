"""End-to-end: JARVIS's ClaudeCodeBrain drives the *real* `claude` CLI, which calls JARVIS's tools
over the local MCP endpoint. The CLI talks to a local mock of the Messages API (fake key,
external traffic black-holed), so no account or plan is ever used.

Runs when the Claude Code CLI is available: set CLAUDE_BIN, or `npm i @anthropic-ai/claude-code`.
"""
import asyncio
import json
import os
import shutil
import socket
import threading
import time

import pytest

CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
pytestmark = pytest.mark.skipif(not CLAUDE_BIN, reason="claude CLI not installed")

uvicorn = pytest.importorskip("uvicorn")
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

SEEN: list[dict] = []


def sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def turn(blocks, stop_reason, model):
    ev = [{"type": "message_start", "message": {"id": f"msg_{len(SEEN)}", "type": "message", "role": "assistant",
                                                  "model": model, "content": [], "stop_reason": None,
                                                  "stop_sequence": None,
                                                  "usage": {"input_tokens": 5, "output_tokens": 1}}}]
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            ev.append({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
            for k in range(0, len(b["text"]), 7):
                ev.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "text_delta", "text": b["text"][k:k + 7]}})
        else:
            ev.append({"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
            ev.append({"type": "content_block_delta", "index": i,
                       "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"])}})
        ev.append({"type": "content_block_stop", "index": i})
    ev += [{"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 9}}, {"type": "message_stop"}]
    return sse(ev)


mock = FastAPI()


@mock.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    SEEN.append(body)
    model = body.get("model", "claude")
    tools = [t.get("name") for t in body.get("tools", [])]
    msgs = body.get("messages", [])
    def blocks(m):
        c = m.get("content")
        return c if isinstance(c, list) else [{"type": "text", "text": c or ""}]

    def is_human(m):  # a real user turn: has text that isn't a system reminder, and no tool results
        bs = blocks(m)
        return m.get("role") == "user" and not any(b.get("type") == "tool_result" for b in bs) and any(
            b.get("type") == "text" and not b.get("text", "").lstrip().startswith("<system-reminder>") for b in bs)

    last_human = max((i for i, m in enumerate(msgs) if is_human(m)), default=-1)
    has_tool_result = any(b.get("type") == "tool_result" for m in msgs[last_human + 1:] for b in blocks(m))
    if not body.get("stream"):
        return JSONResponse({"id": "msg_x", "type": "message", "role": "assistant", "model": model,
                             "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn",
                             "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
    if "mcp__jarvis__get_weather" in tools and not has_tool_result:
        payload = turn([{"type": "text", "text": "Checking the weather. "},
                        {"type": "tool_use", "id": f"toolu_w{len(SEEN)}", "name": "mcp__jarvis__get_weather",
                         "input": {"city": "London"}}], "tool_use", model)
    elif has_tool_result:
        payload = turn([{"type": "text", "text": "It's fourteen degrees in London, sir. Rather pleasant."}],
                       "end_turn", model)
    else:
        payload = turn([{"type": "text", "text": "OK"}], "end_turn", model)
    return StreamingResponse(iter([payload]), media_type="text/event-stream")


@pytest.fixture(scope="module")
def api():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(mock, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


def test_claude_code_cli_end_to_end(api, cfg, tmp_path, monkeypatch):
    from jarvis.core.cli_brains import ClaudeCodeBrain
    from jarvis.core.memory import Memory
    from jarvis.tools.context import ToolContext
    from jarvis.tools.registry import ToolRegistry

    # A clean environment: no host credentials of any kind reach the CLI.
    for k in list(os.environ):
        if k.startswith(("CLAUDE", "ANTHROPIC")) or k in ("CLAUDECODE", "AI_AGENT"):
            monkeypatch.delenv(k, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cfg.data["brain"]["provider"] = "claude-code"
    cfg.data["brain"]["claude_path"] = CLAUDE_BIN
    cfg.data["brain"]["cli_env"] = {
        "ANTHROPIC_API_KEY": "sk-ant-test-not-real", "ANTHROPIC_BASE_URL": api,
        "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        "DISABLE_TELEMETRY": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "HOME": str(home),
    }
    reg = ToolRegistry()
    calls = []

    @reg.tool("Get the weather", {"city": {"type": "string"}}, ["city"])
    def get_weather(ctx, city):
        calls.append(city)
        return {"city": city, "temperature": 14}

    mem = Memory(tmp_path / "m.db")
    brain = ClaudeCodeBrain(cfg, reg, mem, ToolContext(cfg=cfg, memory=mem))
    spoken = []

    async def on_sentence(s):
        spoken.append(s)

    async def run():
        try:
            r1 = await asyncio.wait_for(brain.respond("What's the weather?", on_sentence=on_sentence), 120)
            pid = brain.proc.pid
            r2 = await asyncio.wait_for(brain.respond("And tomorrow?", on_sentence=on_sentence), 120)
            assert brain.proc is not None and brain.proc.pid == pid, "CLI process was not kept warm"
            return r1, r2
        finally:
            await brain._stop()
            await brain.mcp.stop()

    r1, r2 = asyncio.run(run())
    assert calls and calls[0] == "London", f"tool never called; requests seen: {len(SEEN)}"
    assert "Checking the weather." in spoken and "It's fourteen degrees in London, sir." in spoken
    assert r1.endswith("Rather pleasant.")
    assert brain.turns == 2 and len(calls) == 2
    assert r2.endswith("Rather pleasant.")
    main = [b for b in SEEN if any(t.get("name") == "mcp__jarvis__get_weather" for t in b.get("tools", []))]
    assert main, "JARVIS tools were not offered to the model"
    sys_prompt = json.dumps(main[0].get("system"))
    assert "J.A.R.V.I.S." in sys_prompt
    tool_names = {t.get("name") for t in main[0]["tools"]}
    assert not (tool_names & {"Bash", "Edit", "Write"}), "coding tools must not be available"
