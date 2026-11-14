"""Runs the brain against the *real* Anthropic SDK, pointed at a local mock of the
Messages API that replies with server-sent events. Proves our request parameters are
accepted by the SDK and that we consume its real stream event types correctly."""
import asyncio
import json
import socket
import threading
import time

import pytest

pytest.importorskip("anthropic")
uvicorn = pytest.importorskip("uvicorn")

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

CAPTURED: list[dict] = []


def sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def turn(blocks, stop_reason):
    events = [{"type": "message_start", "message": {
        "id": f"msg_{len(CAPTURED)}", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 1, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}}}]
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            events.append({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
            for k in range(0, len(b["text"]), 9):
                events.append({"type": "content_block_delta", "index": i,
                               "delta": {"type": "text_delta", "text": b["text"][k:k + 9]}})
        else:
            events.append({"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
            events.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"])}})
        events.append({"type": "content_block_stop", "index": i})
    events.append({"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                   "usage": {"output_tokens": 30}})
    events.append({"type": "message_stop"})
    return sse(events)


app = FastAPI()


@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    CAPTURED.append({"body": body, "beta": request.headers.get("anthropic-beta", "")})
    if len(CAPTURED) == 1:
        payload = turn([{"type": "text", "text": "One moment, sir. "},
                        {"type": "tool_use", "id": "toolu_01", "name": "get_weather", "input": {"city": "London"}}],
                       "tool_use")
    else:
        payload = turn([{"type": "text", "text": "Fourteen degrees and clear. Splendid evening for it."}], "end_turn")
    return StreamingResponse(iter([payload]), media_type="text/event-stream")


@pytest.fixture(scope="module")
def server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


def test_brain_with_real_sdk(server, cfg, tmp_path):
    import anthropic

    from jarvis.core.brain import Brain
    from jarvis.core.memory import Memory
    from jarvis.tools.context import ToolContext
    from jarvis.tools.registry import ToolRegistry

    reg = ToolRegistry()

    @reg.tool("Weather", {"city": {"type": "string"}}, ["city"])
    def get_weather(ctx, city):
        return {"temp": 14, "city": city}

    client = anthropic.AsyncAnthropic(api_key="test", base_url=server, max_retries=0)
    mem = Memory(tmp_path / "c.db")
    brain = Brain(cfg, reg, mem, ToolContext(cfg=cfg, memory=mem), client=client)
    spoken = []

    async def on_sentence(s):
        spoken.append(s)

    reply = asyncio.run(brain.respond("weather?", on_sentence=on_sentence))
    assert spoken == ["One moment, sir.", "Fourteen degrees and clear.", "Splendid evening for it."]
    assert reply.endswith("Splendid evening for it.")

    first, second = CAPTURED[0], CAPTURED[1]
    body = first["body"]
    assert "server-side-fallback-2026-07-01" in first["beta"]
    assert body["fallbacks"] == "default" and body["stream"] is True
    assert body["output_config"] == {"effort": "low"} and body["cache_control"] == {"type": "ephemeral"}
    tools = {t["name"]: t for t in body["tools"]}
    assert tools["web_search"]["type"] == "web_search_20260209"
    assert tools["get_weather"]["eager_input_streaming"] is True
    # assistant turn (with tool_use) is echoed back verbatim, then our tool_result
    msgs = second["body"]["messages"]
    assert msgs[1]["role"] == "assistant" and msgs[1]["content"][1]["type"] == "tool_use"
    assert msgs[1]["content"][1]["input"] == {"city": "London"}
    assert msgs[2]["content"][0]["tool_use_id"] == "toolu_01"
