"""Read-only durability behavior, including a real process death and restart."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from examples.cookbooks.browser_use_durable import run_digest, run_directory

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.fast
def test_completed_reads_are_reused(tmp_path):
    calls = []

    async def reader(url):
        calls.append(url)
        return {"url": url, "title": "Recorded title", "mode": "fixture"}

    kwargs = dict(wal_root=str(tmp_path), run_id="digest-one", urls=["one", "two"], reader=reader)
    first = asyncio.run(run_digest(**kwargs))
    assert asyncio.run(run_digest(**kwargs)) == first
    assert calls == ["one", "two"]


@pytest.mark.fast
def test_failed_read_is_not_checkpointed(tmp_path):
    calls = []

    async def reader(url):
        calls.append(url)
        if calls == ["one", "two"]:
            raise RuntimeError("read interrupted")
        return {"url": url, "title": url}

    kwargs = dict(wal_root=str(tmp_path), run_id="failure", urls=["one", "two"], reader=reader)
    with pytest.raises(RuntimeError, match="read interrupted"):
        asyncio.run(run_digest(**kwargs))
    assert asyncio.run(run_digest(**kwargs)) == [{"url": "one", "title": "one"}, {"url": "two", "title": "two"}]
    assert calls == ["one", "two", "two"]


@pytest.mark.fast
def test_new_inputs_and_new_run_do_not_reuse_old_results(tmp_path):
    calls = []

    async def reader(url):
        calls.append(url)
        return {"url": url}

    for run_id, urls, mode in [
        ("first", ["one"], "demo"),
        ("second", ["one"], "demo"),
        ("first", ["two"], "demo"),
        ("first", ["one"], "live"),
    ]:
        asyncio.run(run_digest(wal_root=str(tmp_path), run_id=run_id, urls=urls, mode=mode, reader=reader))
    assert calls == ["one", "one", "two", "one"]
    with pytest.raises(ValueError, match="run_id"):
        run_directory(str(tmp_path), "", ["one"], "demo")


@pytest.mark.integration
def test_sigkill_then_fresh_process_skips_first_read(tmp_path):
    command = [
        sys.executable,
        "-m",
        "examples.cookbooks.browser_use_durable",
        "--demo",
        "--run-id",
        "hard-crash",
        "--wal-dir",
        str(tmp_path),
    ]
    killed = subprocess.run(command + ["--crash-after", "1"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert killed.returncode in (-9, 137)
    assert killed.stdout.count("READ demo") == 1
    resumed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.count("READ demo") == 2
    assert "READ demo https://pypi.org/project/browser-use/" not in resumed.stdout
    third = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert third.returncode == 0, third.stderr
    assert "READ demo" not in third.stdout


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("LIL_TEST_BROWSER") != "1", reason="Opt-in: launches task-owned local Chrome")
def test_real_browser_metadata_is_cached(tmp_path):
    """A local synthetic page and real Browser Use, without a model or cloud key."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/release":
                hits.append(self.path)
                page = b"<html><h1 class='project-header__name'>Fixture package 1.2.3</h1></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(page)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/release"
    try:
        kwargs = dict(wal_root=str(tmp_path), run_id="real-browser", urls=[url], mode="live")
        first = asyncio.run(run_digest(**kwargs))
        second = asyncio.run(run_digest(**kwargs))
        assert first == second == [{"url": url, "title": "Fixture package 1.2.3", "mode": "live"}]
        assert hits == ["/release"]
        json.dumps(first)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
