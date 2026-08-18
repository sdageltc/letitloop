"""Bidirectional Model Context Protocol (MCP) Client Adapter for letitloop.

Enables letitloop workers to dynamically discover and execute tools exposed
by local or remote stdio MCP servers (e.g. database query tools, browser automation,
filesystem extensions).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class McpClientError(Exception):
    """Base error for MCP Client operations."""


class StdioMcpClient:
    """JSON-RPC 2.0 client for interacting with stdio MCP servers."""

    def __init__(
        self,
        command: List[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.command = command
        self.env = env or os.environ.copy()
        self.cwd = cwd
        self.timeout = timeout
        self.process: Optional[subprocess.Popen] = None
        self._msg_id = 0
        self._lock = threading.Lock()
        self._is_initialized = False

    def start(self) -> None:
        """Start the stdio subprocess and perform the initialize handshake."""
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                cwd=self.cwd,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            raise McpClientError(f"Failed to start MCP server subprocess: {e}") from e

        # Handshake: initialize
        init_res = self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "letitloop-mcp-client", "version": "0.1.1"},
            },
        )
        if "error" in init_res:
            raise McpClientError(f"MCP initialization error: {init_res['error']}")

        # Send initialized notification
        self._send_notification("notifications/initialized", {})
        self._is_initialized = True

    def _next_id(self) -> int:
        with self._lock:
            self._msg_id += 1
            return self._msg_id

    def _send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a synchronous JSON-RPC request and wait for the response."""
        if not self.process or not self.process.stdin or not self.process.stdout:
            raise McpClientError("MCP process is not running.")

        req_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        line = json.dumps(msg) + "\n"

        try:
            self.process.stdin.write(line)
            self.process.stdin.flush()
        except OSError as e:
            raise McpClientError(f"Error writing to MCP server stdin: {e}") from e

        # Read line from stdout
        try:
            resp_line = self.process.stdout.readline()
            if not resp_line:
                raise McpClientError("MCP server process closed connection (empty line read).")
            return json.loads(resp_line)
        except (json.JSONDecodeError, OSError) as e:
            raise McpClientError(f"Failed to read/parse response from MCP server: {e}") from e

    def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Send a one-way JSON-RPC notification (no response expected)."""
        if not self.process or not self.process.stdin:
            return
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        try:
            self.process.stdin.write(json.dumps(msg) + "\n")
            self.process.stdin.flush()
        except OSError:
            pass

    def list_tools(self) -> List[Dict[str, Any]]:
        """List all available tools provided by the MCP server."""
        resp = self._send_request("tools/list", {})
        if "error" in resp:
            raise McpClientError(f"Failed to list tools: {resp['error']}")
        return resp.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a specific tool on the MCP server and return its content payload."""
        resp = self._send_request("tools/call", {"name": name, "arguments": arguments})
        if "error" in resp:
            raise McpClientError(f"Tool '{name}' returned error: {resp['error']}")
        return resp.get("result", {})

    def close(self) -> None:
        """Terminate the MCP server process gracefully."""
        if self.process:
            try:
                if self.process.stdin:
                    self.process.stdin.close()
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
            self.process = None
            self._is_initialized = False

    def __enter__(self) -> StdioMcpClient:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
