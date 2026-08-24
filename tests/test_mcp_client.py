"""Unit tests for orchestrator.mcp_client (Bidirectional MCP Client Adapter)."""

import json
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orchestrator.contract import validate_contract
from orchestrator.mcp_client import McpClientError, McpClientManager, SseMcpClient, StdioMcpClient

# A minimal mock stdio MCP server script in Python
MOCK_SERVER_CODE = """
import sys, json

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        req_id = req.get("id")
        method = req.get("method")

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mock-server", "version": "1.0.0"}
                }
            }
            print(json.dumps(resp), flush=True)
        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo_tool",
                            "description": "Echoes back the input",
                            "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}}
                        }
                    ]
                }
            }
            print(json.dumps(resp), flush=True)
        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            if name == "echo_tool":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Echo: {args.get('msg', '')}"}]
                    }
                }
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool '{name}' not found"}
                }
            print(json.dumps(resp), flush=True)

if __name__ == "__main__":
    main()
"""


def test_stdio_mcp_client_lifecycle(tmp_path):
    """Test full MCP handshake, tool listing, and tool execution."""
    server_script = tmp_path / "mock_mcp_server.py"
    server_script.write_text(MOCK_SERVER_CODE, encoding="utf-8")

    cmd = [sys.executable, str(server_script)]

    with StdioMcpClient(command=cmd) as client:
        assert client._is_initialized

        # 1. List tools
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "echo_tool"
        assert "Echoes back" in tools[0]["description"]

        # 2. Call tool
        res = client.call_tool("echo_tool", {"msg": "hello letitloop"})
        assert "content" in res
        assert res["content"][0]["text"] == "Echo: hello letitloop"

        # 3. Call non-existent tool should raise McpClientError
        with pytest.raises(McpClientError) as exc_info:
            client.call_tool("unknown_tool", {})
        assert "Tool 'unknown_tool' not found" in str(exc_info.value)


def _write_mock_server_script(tmp_path):
    script = tmp_path / "mock_mcp_server.py"
    script.write_text(MOCK_SERVER_CODE, encoding="utf-8")
    return [sys.executable, str(script)]


def test_manager_lifecycle_two_stdio_servers(tmp_path):
    """Manager connects two stdio servers, namespaces tools, and tears down children."""
    cmd = _write_mock_server_script(tmp_path)
    manager = McpClientManager(
        {
            "alpha": {"transport": "stdio", "command": cmd},
            "beta": {"transport": "stdio", "command": cmd},
        }
    )
    procs = []
    try:
        manager.ensure_servers(["alpha", "beta"])
        assert manager.connected == {"alpha", "beta"}

        alpha_tools = manager.list_tools("alpha")
        beta_tools = manager.list_tools("beta")
        assert [t["name"] for t in alpha_tools] == ["echo_tool"]
        assert [t["name"] for t in beta_tools] == ["echo_tool"]

        res = manager.call_tool("beta", "echo_tool", {"msg": "hi"})
        assert res["content"][0]["text"] == "Echo: hi"

        defs = manager.as_worker_tool_defs(["alpha", "beta"])
        names = {d["name"] for d in defs}
        assert "alpha__echo_tool" in names
        assert "beta__echo_tool" in names
        schema = next(d["inputSchema"] for d in defs if d["name"] == "alpha__echo_tool")
        assert schema.get("type") == "object"

        procs = [manager.connect(name).process for name in ("alpha", "beta")]
    finally:
        manager.shutdown_all()

    for proc in procs:
        assert proc is not None
        assert proc.poll() is not None, "child MCP server process was not terminated"

    manager.shutdown_all()  # double shutdown must be safe


def test_manager_disconnect_removes_cleanly(tmp_path):
    cmd = _write_mock_server_script(tmp_path)
    manager = McpClientManager({"gamma": {"transport": "stdio", "command": cmd}})
    client = manager.connect("gamma")

    manager.disconnect("gamma")
    assert "gamma" not in manager._clients
    assert manager.connected == set()
    assert client.process is None

    fresh = manager.connect("gamma")  # reconnect spawns a new client
    assert fresh is not client
    manager.shutdown_all()


def test_from_config_and_ensure_servers_missing(tmp_path):
    cmd = _write_mock_server_script(tmp_path)
    cfg_path = tmp_path / "mcp.json"
    cfg_path.write_text(
        json.dumps({"servers": {"delta": {"transport": "stdio", "command": cmd}}}),
        encoding="utf-8",
    )

    manager = McpClientManager.from_config(path=str(cfg_path))
    manager.ensure_servers(["delta"])  # lazy connect succeeds
    assert "delta" in manager.connected

    with pytest.raises(McpClientError) as exc_info:
        manager.ensure_servers(["delta", "missing-server"])
    assert "missing-server" in str(exc_info.value)

    manager.shutdown_all()


BASE_CONTRACT = {
    "task_id": "mcp-contract-001",
    "title": "Contract with required MCP servers",
    "status": "drafted",
    "risk_tier": "auto",
    "workspace_scope": {"allow": [], "deny": []},
    "objective": "Exercise required_mcp_servers validation",
    "worker": {"model": "mock:model", "max_attempts": 1},
    "outputs": [],
    "acceptance_checks": [],
    "qc": {"required": False, "lens": "code_correctness"},
}


def test_contract_required_mcp_servers_valid():
    raw = dict(BASE_CONTRACT, required_mcp_servers=["fs", "web-search"])
    errors = validate_contract(raw)
    assert errors == [], f"Expected no errors, got: {errors}"


def test_contract_required_mcp_servers_rejected():
    invalid_values = [
        ["dup", "dup"],  # duplicates
        ["ok", 123],  # non-string entry
        [""],  # empty string entry
        ["   "],  # whitespace-only entry
        "fs",  # wrong container type
    ]
    for bad in invalid_values:
        raw = dict(BASE_CONTRACT, required_mcp_servers=bad)
        errors = validate_contract(raw)
        assert errors, f"expected rejection for required_mcp_servers={bad!r}"
        assert any("required_mcp_servers" in e for e in errors), errors


def test_contract_absent_required_mcp_servers_unchanged():
    # Absent field: unchanged behavior.
    errors = validate_contract(dict(BASE_CONTRACT))
    assert errors == []

    # Unknown-key rejection still enforced for keys outside the optional set.
    raw = dict(BASE_CONTRACT, bogus_key=1)
    errors = validate_contract(raw)
    assert any("bogus_key" in e for e in errors), errors


class MockSseMcpServer:
    """Minimal stdlib HTTP server emulating the MCP-over-SSE transport."""

    def __init__(self):
        self._responses = queue.Queue()
        self._running = True
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(b"event: endpoint\r\ndata: /messages\r\n\r\n")
                    self.wfile.flush()
                except OSError:
                    return
                while outer._running:
                    try:
                        msg = outer._responses.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    try:
                        self.wfile.write(("data: " + json.dumps(msg) + "\n\n").encode("utf-8"))
                        self.wfile.flush()
                    except OSError:
                        return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length > 0 else b""
                try:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except OSError:
                    return
                try:
                    req = json.loads(body.decode("utf-8"))
                except Exception:
                    return
                resp = outer._handle(req)
                if resp is not None:
                    outer._responses.put(resp)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def _handle(self, req):
        method = req.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-sse-server", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "sse_echo",
                        "description": "Echoes over SSE",
                        "inputSchema": {"type": "object"},
                    }
                ]
            }
        elif method == "tools/call":
            args = req.get("params", {}).get("arguments", {})
            result = {"content": [{"type": "text", "text": f"SSE Echo: {args.get('msg', '')}"}]}
        else:
            return None
        return {"jsonrpc": "2.0", "id": req.get("id"), "result": result}

    def stop(self):
        self._running = False
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


def test_sse_mcp_client_handshake_and_list_tools():
    server = MockSseMcpServer()
    try:
        client = SseMcpClient(url=f"http://127.0.0.1:{server.port}/sse", timeout=5.0)
        client.start()  # SSE handshake + initialize over the discovered POST endpoint
        assert client._is_initialized

        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "sse_echo"

        res = client.call_tool("sse_echo", {"msg": "over-sse"})
        assert res["content"][0]["text"] == "SSE Echo: over-sse"

        client.close()  # closes the SSE connection; idempotent
        client.close()
        assert client.endpoint is None
        assert client._is_initialized is False
    finally:
        server.stop()
