"""Unit tests for orchestrator.mcp_client (Bidirectional MCP Client Adapter)."""

import sys

import pytest

from orchestrator.mcp_client import McpClientError, StdioMcpClient

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
