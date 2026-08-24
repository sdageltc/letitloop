"""Server-Sent Events HTTP server streaming orchestrator events to local clients."""

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .events import EventBus, get_bus


class SSEServer:
    """Minimal SSE server exposing GET /events and GET /health.

    A single bus subscription (created in start()) fans each event envelope out
    into a per-connection queue; every connection thread writes SSE frames and
    keepalive comments independently.
    """

    HEARTBEAT_SECONDS = 15.0

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        bus: Optional[EventBus] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.bus = bus if bus is not None else get_bus()
        self.bound_port: Optional[int] = None
        self._clients: Dict[int, "queue.Queue[Dict[str, Any]]"] = {}
        self._lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._unsubscribe: Optional[Any] = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                try:
                    if self.path == "/health":
                        body = b"ok"
                        self.send_response(200)
                        self.send_header("Content-Type", "text/plain")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    elif self.path == "/events":
                        outer._stream_events(self)
                    else:
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                except (BrokenPipeError, ConnectionAbortedError, OSError):
                    pass

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._handler_cls = Handler

    def start(self) -> None:
        """Bind the server, subscribe to the bus, and serve in a daemon thread."""
        if self._thread is not None:
            return
        self._server = ThreadingHTTPServer((self.host, self.port), self._handler_cls)
        self.bound_port = int(self._server.server_address[1])
        fan_out = self._client_queues
        self._unsubscribe = self.bus.subscribe(lambda env: fan_out(env))
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.2},
            daemon=True,
        )
        self._thread.start()

    @property
    def client_count(self) -> int:
        """Number of currently connected /events streams."""
        with self._lock:
            return len(self._clients)

    def _client_queues(self, envelope: Dict[str, Any]) -> None:
        with self._lock:
            queues = list(self._clients.values())
        for q in queues:
            q.put(envelope)

    def shutdown(self) -> None:
        """Unsubscribe from the bus and stop serving; safe to call once after start."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            if thread is not None:
                thread.join(timeout=5.0)
            server.server_close()

    def _register_client(self) -> "queue.Queue[Dict[str, Any]]":
        q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        with self._lock:
            self._clients[id(q)] = q
        return q

    def _unregister_client(self, q: "queue.Queue[Dict[str, Any]]") -> None:
        with self._lock:
            self._clients.pop(id(q), None)

    def _stream_events(self, handler: BaseHTTPRequestHandler) -> None:
        q = self._register_client()
        try:
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Connection", "close")
            handler.end_headers()
            while True:
                try:
                    envelope = q.get(timeout=self.HEARTBEAT_SECONDS)
                    frame = "event: {0}\ndata: {1}\n\n".format(
                        envelope.get("event", ""),
                        json.dumps(envelope, ensure_ascii=False),
                    )
                except queue.Empty:
                    frame = ": keepalive\n\n"
                handler.wfile.write(frame.encode("utf-8"))
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, OSError):
            pass
        finally:
            self._unregister_client(q)


def start_sse_server(
    port: int = 0,
    bus: Optional[EventBus] = None,
) -> SSEServer:
    """Convenience helper: construct and start an SSE server on an ephemeral port."""
    server = SSEServer(port=port, bus=bus)
    server.start()
    return server
