"""Bidirectional Model Context Protocol (MCP) Client Adapter for letitloop.

Enables letitloop workers to dynamically discover and execute tools exposed
by local or remote MCP servers (e.g. database query tools, browser automation,
filesystem extensions) over the ``stdio`` and ``sse`` (HTTP+SSE) transports,
and exposes :class:`McpClientManager` to manage a fleet of configured servers.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"


class McpClientError(Exception):
    """Base error for MCP Client operations."""


class BaseMcpClient(ABC):
    """Common client surface shared by every MCP transport.

    Concrete transports only implement the low-level JSON-RPC plumbing
    (``_send_request`` / ``_send_notification``); the protocol-level surface
    (``initialize``, ``list_tools``, ``call_tool``, ``close``) lives here.
    """

    PROTOCOL_VERSION = MCP_PROTOCOL_VERSION

    def __init__(self):
        # RLock so transports that serialize whole requests under the lock
        # (e.g. SseMcpClient._send_request) can still call _next_id().
        self._msg_id = 0
        self._lock = threading.RLock()
        self._is_initialized = False

    @abstractmethod
    def _send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a synchronous JSON-RPC request and wait for the response."""

    @abstractmethod
    def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Send a one-way JSON-RPC notification (no response expected)."""

    @abstractmethod
    def close(self) -> None:
        """Release the transport resources."""

    def _next_id(self) -> int:
        with self._lock:
            self._msg_id += 1
            return self._msg_id

    def initialize(self) -> Dict[str, Any]:
        """Perform the MCP initialize handshake and mark the client ready."""
        init_res = self._send_request(
            "initialize",
            {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "letitloop-mcp-client", "version": "0.1.1"},
            },
        )
        if "error" in init_res:
            raise McpClientError(f"MCP initialization error: {init_res['error']}")

        # Send initialized notification
        self._send_notification("notifications/initialized", {})
        self._is_initialized = True
        return init_res.get("result", {})

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


class StdioMcpClient(BaseMcpClient):
    """JSON-RPC 2.0 client for interacting with stdio MCP servers."""

    def __init__(
        self,
        command: List[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
    ):
        super().__init__()
        self.command = command
        self.env = env or os.environ.copy()
        self.cwd = cwd
        self.timeout = timeout
        self.process: Optional[subprocess.Popen] = None

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

        # Handshake: initialize + notifications/initialized
        self.initialize()

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


class SseMcpClient(BaseMcpClient):
    """JSON-RPC 2.0 client for MCP servers exposed over HTTP+SSE.

    Implements the MCP HTTP+SSE transport: the client opens a long-lived
    ``GET text/event-stream`` connection, discovers the POST message endpoint
    from an ``event: endpoint`` frame, posts JSON-RPC messages there, and
    reads responses back off the SSE stream.
    """

    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ):
        super().__init__()
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.endpoint: Optional[str] = None
        self._stream: Any = None

    def start(self) -> None:
        """Open the SSE stream, discover the message endpoint, and handshake."""
        if self._stream is not None:
            return
        self._open_stream()
        deadline = time.monotonic() + self.timeout
        while True:
            if time.monotonic() > deadline:
                self.close()
                raise McpClientError(f"Timed out waiting for endpoint event from MCP server at {self.url}")
            frame = self._read_sse_frame()
            if frame is None:
                self.close()
                raise McpClientError(f"SSE stream closed before endpoint event from MCP server at {self.url}")
            event_name, data_lines = frame
            if event_name == "endpoint":
                uri = "\n".join(data_lines).strip()
                if uri:
                    self.endpoint = urljoin(self.url, uri)
                    break
        self.initialize()

    def _open_stream(self) -> None:
        req = urllib.request.Request(
            self.url,
            headers={"Accept": "text/event-stream", **self.headers},
            method="GET",
        )
        try:
            self._stream = urllib.request.urlopen(req, timeout=self.timeout)
        except (urllib.error.URLError, OSError) as e:
            raise McpClientError(f"Failed to open SSE stream to MCP server at {self.url}: {e}") from e

    def _read_sse_frame(self) -> Optional[tuple]:
        """Read one line-based SSE frame.

        Returns ``(event_name, data_lines)`` or ``None`` on EOF/closed stream.
        Handles LF/CRLF line endings, ``field: value`` syntax with optional
        space, and ``:`` comment lines per the SSE specification.
        """
        stream = self._stream
        if stream is None:
            return None
        event_name: Optional[str] = None
        data_lines: List[str] = []
        while True:
            raw = stream.readline()
            if not raw:
                return None
            if isinstance(raw, bytes):
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            else:
                line = str(raw).rstrip("\r\n")
            if line == "":
                if event_name is None and not data_lines:
                    continue
                return (event_name, data_lines)
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value
            elif field == "data":
                data_lines.append(value)

    def _post_message(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """POST a JSON-RPC message to the discovered endpoint.

        Returns an inline JSON response body only when the server replies
        synchronously with application/json carrying an id; otherwise None
        (the response will arrive on the SSE stream).
        """
        if self.endpoint is None:
            raise McpClientError("MCP SSE client has no message endpoint; call start() first.")
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                **self.headers,
            },
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except (urllib.error.URLError, OSError) as e:
            raise McpClientError(f"Failed to POST message to MCP endpoint {self.endpoint}: {e}") from e
        try:
            status = getattr(resp, "status", None) or resp.getcode()
            if status >= 400:
                raise McpClientError(f"MCP endpoint {self.endpoint} returned HTTP {status}")
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "application/json" in content_type:
                raw = resp.read()
                if raw.strip():
                    try:
                        parsed = json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        logger.debug("Ignoring non-JSON body from MCP endpoint %s", self.endpoint)
                        return None
                    if isinstance(parsed, dict) and "id" in parsed:
                        return parsed
            return None
        finally:
            resp.close()

    def _wait_for_response(self, req_id: int, deadline: float) -> Dict[str, Any]:
        """Consume SSE frames until the JSON-RPC response with ``req_id`` arrives."""
        while True:
            if time.monotonic() > deadline:
                raise McpClientError(f"Timed out waiting for MCP response to request id {req_id}.")
            frame = self._read_sse_frame()
            if frame is None:
                raise McpClientError("MCP SSE stream closed before a response was received.")
            _, data_lines = frame
            payload = "\n".join(data_lines).strip()
            if not payload:
                continue
            try:
                msg = json.loads(payload)
            except json.JSONDecodeError:
                logger.debug("Ignoring malformed SSE data frame: %.200s", payload)
                continue
            if isinstance(msg, dict) and msg.get("id") == req_id:
                return msg

    def _send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if self.endpoint is None or self._stream is None:
                raise McpClientError("MCP SSE client is not connected; call start() first.")
            req_id = self._next_id()
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }
            self._post_message(msg)
            return self._wait_for_response(req_id, time.monotonic() + self.timeout)

    def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        if self.endpoint is None:
            return
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        try:
            self._post_message(msg)
        except McpClientError:
            pass

    def close(self) -> None:
        """Close the SSE connection (idempotent)."""
        stream = self._stream
        self._stream = None
        self.endpoint = None
        self._is_initialized = False
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    def __enter__(self) -> SseMcpClient:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


class McpClientManager:
    """Lazy multi-server MCP client registry.

    Loads configuration shaped like::

        {"servers": {
            "local-fs": {"transport": "stdio", "command": ["python", "-m", "fs_server"], "env": {}},
            "remote-tools": {"transport": "sse", "url": "https://host/sse", "headers": {}}
        }}

    Clients are spawned lazily on first use and cached until
    :meth:`disconnect` / :meth:`shutdown_all`.
    """

    DEFAULT_CONFIG_REL_PATH = os.path.join(".letitloop", "mcp.json")

    def __init__(self, servers_config: Optional[Dict[str, Dict[str, Any]]] = None):
        self._configs: Dict[str, Dict[str, Any]] = dict(servers_config or {})
        self._clients: Dict[str, BaseMcpClient] = {}
        self._connected: set = set()
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, path: Any = None, workspace: Optional[str] = None) -> McpClientManager:
        """Build a manager from ``<workspace>/.letitloop/mcp.json``.

        ``path`` may be an explicit config file path, an already-parsed config
        dict, or omitted to resolve against ``workspace`` (defaulting to the
        current working directory).
        """
        source = path if isinstance(path, str) else "<config>"
        if isinstance(path, dict):
            data = path
        else:
            if path is None:
                root = workspace if workspace is not None else os.getcwd()
                path = os.path.join(root, cls.DEFAULT_CONFIG_REL_PATH)
                source = path
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                raise McpClientError(f"Failed to load MCP config from {source}: {e}") from e

        if not isinstance(data, dict):
            raise McpClientError(f"MCP config {source} must be a JSON object.")
        servers = data.get("servers", data)
        if not isinstance(servers, dict):
            raise McpClientError(f"MCP config {source}: 'servers' must map names to settings objects.")
        return cls(servers_config=servers)

    @property
    def connected(self) -> set:
        """Names of currently connected servers (copy)."""
        return set(self._connected)

    def connect(self, name: str) -> BaseMcpClient:
        """Lazily spawn, cache, and return the client for ``name``."""
        with self._lock:
            existing = self._clients.get(name)
            if existing is not None:
                return existing
            cfg = self._configs.get(name)
            if cfg is None:
                raise McpClientError(f"Unknown MCP server '{name}'. Configured servers: {sorted(self._configs)}")
            client = self._build_client(name, cfg)
            try:
                client.start()
            except Exception as exc:
                try:
                    client.close()
                except Exception:
                    pass
                raise McpClientError(f"Failed to connect to MCP server '{name}': {exc}") from exc
            self._clients[name] = client
            self._connected.add(name)
            return client

    def _build_client(self, name: str, cfg: Dict[str, Any]) -> BaseMcpClient:
        if not isinstance(cfg, dict):
            raise McpClientError(f"MCP server '{name}': settings must be an object.")
        transport = cfg.get("transport") or ("sse" if cfg.get("url") else "stdio")

        if transport == "stdio":
            command = cfg.get("command")
            if not command or not isinstance(command, list):
                raise McpClientError(f"MCP server '{name}': stdio transport requires a 'command' list.")
            extra_env = cfg.get("env") or {}
            if not isinstance(extra_env, dict):
                raise McpClientError(f"MCP server '{name}': 'env' must be an object.")
            env = os.environ.copy()
            env.update({str(k): str(v) for k, v in extra_env.items()})
            return StdioMcpClient(command=[str(part) for part in command], env=env)

        if transport == "sse":
            url = cfg.get("url")
            if not url or not isinstance(url, str):
                raise McpClientError(f"MCP server '{name}': sse transport requires a 'url'.")
            headers = cfg.get("headers") or {}
            if not isinstance(headers, dict):
                raise McpClientError(f"MCP server '{name}': 'headers' must be an object.")
            return SseMcpClient(url=url, headers={str(k): str(v) for k, v in headers.items()})

        raise McpClientError(f"MCP server '{name}': unsupported transport {transport!r}.")

    def ensure_servers(self, required: List[str]) -> None:
        """Connect every required server or raise listing what failed."""
        missing = [name for name in required if name not in self._configs]
        if missing:
            raise McpClientError(f"MCP servers not configured: {missing}. Available: {sorted(self._configs)}")
        failures: List[str] = []
        for name in required:
            try:
                self.connect(name)
            except McpClientError as exc:
                failures.append(str(exc))
        if failures:
            raise McpClientError("Unreachable MCP servers: " + "; ".join(failures))

    def list_tools(self, server: str) -> List[Dict[str, Any]]:
        """List tools provided by ``server`` (connecting lazily)."""
        return self.connect(server).list_tools()

    def call_tool(self, server: str, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call ``<name>`` on ``server`` with ``arguments`` (connecting lazily)."""
        return self.connect(server).call_tool(name, arguments)

    def as_worker_tool_defs(self, servers: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Translate discovered MCP tools into namespaced worker tool defs.

        Each def is ``{"name": "<server>__<tool>", "description": ..., "inputSchema": ...}``.
        With ``servers=None`` every configured server is discovered.
        """
        if servers is None:
            targets = list(self._configs.keys())
        else:
            unknown = [srv for srv in servers if srv not in self._configs]
            if unknown:
                raise McpClientError(f"Unknown MCP servers requested: {unknown}. Available: {sorted(self._configs)}")
            targets = list(dict.fromkeys(servers))
        defs: List[Dict[str, Any]] = []
        for srv in targets:
            for tool in self.list_tools(srv):
                defs.append(
                    {
                        "name": f"{srv}__{tool.get('name', '')}",
                        "description": tool.get("description", ""),
                        "inputSchema": tool.get("inputSchema", {"type": "object"}),
                    }
                )
        return defs

    def disconnect(self, server: str) -> None:
        """Close and forget the client for ``server`` (idempotent)."""
        with self._lock:
            client = self._clients.pop(server, None)
            self._connected.discard(server)
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                logger.warning("Error closing MCP client '%s': %s", server, exc)

    def shutdown_all(self) -> None:
        """Disconnect every live client (idempotent)."""
        for name in list(self._clients.keys()):
            self.disconnect(name)

    def __enter__(self) -> McpClientManager:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.shutdown_all()
