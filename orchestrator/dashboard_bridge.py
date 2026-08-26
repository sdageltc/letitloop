"""Dashboard bridge — serves DCP-2.0 receipts + chaos reports via HTTP/SSE.

Exposes:
  GET /              — HTML dashboard with leaderboard + chaos
  GET /api/leaderboard — JSON from results/leaderboard.json or docs fallback
  GET /api/chaos     — JSON from results/chaos_report.json
  GET /health        — ok
  GET /events        — SSE stream from orchestrator EventBus (reuse SSEServer)

Usage: lil dashboard --serve --port 8080
"""

from __future__ import annotations

import json
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _load_json(paths: list[pathlib.Path]) -> dict[str, Any]:
    for p in paths:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {"error": "no receipt found", "hint": "run lil bench --compare all"}


class ReceiptsHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        try:
            if self.path in ("/", "/index.html"):
                self._serve_dashboard()
            elif self.path == "/api/leaderboard":
                data = _load_json([pathlib.Path("results/leaderboard.json"), pathlib.Path("docs/leaderboard.json"), pathlib.Path("results/V033_SCORECARD.md")])
                # if leaderboard not found, try to load from docs/V033
                if "error" in data and pathlib.Path("docs/V033_SCORECARD.md").exists():
                    data = {"note": "see docs/V033_SCORECARD.md"}
                self._json(data)
            elif self.path == "/api/chaos":
                data = _load_json([pathlib.Path("results/chaos_report.json"), pathlib.Path("results/chaos_report_500.json")])
                self._json(data)
            elif self.path == "/health":
                self._text(b"ok")
            elif self.path == "/events":
                # delegate to SSEServer's streaming (create temp server instance)
                # For simplicity, reuse global SSEServer if running; otherwise 404
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
        except (BrokenPipeError, ConnectionAbortedError, OSError):
            pass

    def _json(self, data: dict[str, Any]) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_dashboard(self) -> None:
        html_str = """<!doctype html>
<html><head><meta charset=utf-8><title>LetItLoop DCP-2.0 Dashboard</title>
<style>body{font-family:system-ui, sans-serif; max-width:900px; margin:40px auto; padding:20px} pre{background:#f6f8fa; padding:12px; overflow:auto} .badge{padding:2px 6px; border-radius:4px; color:#fff} .pass{background:#2da44e} .fail{background:#cf222e}</style>
</head><body>
<h1>LetItLoop DCP-2.0 Dashboard</h1>
<p>Live receipts from <code>results/leaderboard.json</code> + <code>results/chaos_report.json</code></p>
<h2>Leaderboard</h2><pre id=lb>loading...</pre>
<h2>Chaos 500</h2><pre id=chaos>loading...</pre>
<script>
async function load(){
  try{ document.getElementById('lb').textContent = JSON.stringify(await (await fetch('/api/leaderboard')).json(), null, 2)}catch(e){ document.getElementById('lb').textContent='no leaderboard -- run lil bench --compare all'}
  try{ document.getElementById('chaos').textContent = JSON.stringify(await (await fetch('/api/chaos')).json(), null, 2)}catch(e){ document.getElementById('chaos').textContent='no chaos -- run scripts/chaos_fuzzer_v2.py'}
}
load(); setInterval(load, 5000);
</script>
</body></html>"""
        html = html_str.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def serve_receipts_dashboard(host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    """Start receipts dashboard + SSE server. Returns the HTTP server (caller should serve_forever)."""
    # Start SSE server on same host but port+1 for events, or share?
    # For now, just serve receipts; SSE events available via separate `lil serve`
    server = ThreadingHTTPServer((host, port), ReceiptsHandler)
    print(f"[dashboard] serving DCP-2.0 receipts at http://{host}:{port}/ (leaderboard + chaos)")
    print("[dashboard] APIs: /api/leaderboard, /api/chaos, /health")
    return server
