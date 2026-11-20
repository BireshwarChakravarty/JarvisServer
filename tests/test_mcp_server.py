"""The tools endpoint Claude Code / Codex use: secret path, JSON-RPC, tools, and bad input handled."""
from fastapi.testclient import TestClient

from jarvis.tools.context import ToolContext
from jarvis.tools.mcp_server import MCPServer
from jarvis.tools.registry import ToolRegistry


def make(cfg):
    reg = ToolRegistry()

    @reg.tool("Say hello.", {"name": {"type": "string"}}, ["name"])
    def hello(ctx, name):
        return f"Hello, {name}."
    srv = MCPServer(reg, ToolContext(cfg=cfg))
    return srv, TestClient(srv.app())


def test_tools_list_and_call(cfg):
    srv, c = make(cfg)
    url = f"/mcp/{srv.secret}"
    r = c.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.json()["result"]["serverInfo"]["name"] and "Mcp-Session-Id" in r.headers
    tools = c.post(url, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["hello"]
    out = c.post(url, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "hello", "arguments": {"name": "Tony"}}}).json()["result"]
    assert out["content"][0]["text"] == "Hello, Tony." and not out["isError"]
    bad = c.post(url, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                            "params": {"name": "hello", "arguments": {}}}).json()["result"]
    assert bad["isError"]


def test_wrong_secret_and_malformed_requests(cfg):
    srv, c = make(cfg)
    assert c.post("/mcp/guess", json={"id": 1, "method": "ping"}).status_code == 404
    url = f"/mcp/{srv.secret}"
    assert c.post(url, json="just a string").json()["error"]["code"] == -32600
    assert c.post(url, json={"id": 5, "method": "tools/call", "params": [1, 2]}).json()["error"]["code"] == -32602
    assert c.post(url, json=[{"id": 6, "method": "ping"}, 7]).status_code == 200
    assert c.post(url, json={"method": "notifications/initialized"}).status_code == 202
