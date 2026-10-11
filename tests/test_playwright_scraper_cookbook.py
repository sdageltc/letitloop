"""Read-only durability behavior for the Playwright scraper cookbook."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from examples.cookbooks.playwright_scraper import run_scraper, run_directory

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.fast
def test_completed_pages_are_reused(tmp_path):
    calls = []

    async def mock_extract(url):
        calls.append(url)
        return {"url": url, "page_title": f"Title {url}", "stories": [], "story_count": 0, "mode": "demo"}

    kwargs = dict(wal_root=str(tmp_path), run_id="scrape-one", pages=["a", "b"], mode="demo")
    first = asyncio.run(run_scraper(**kwargs))
    assert asyncio.run(run_scraper(**kwargs)) == first
    assert calls == ["a", "b"]


@pytest.mark.fast
def test_failed_page_is_not_checkpointed(tmp_path):
    calls = []

    async def mock_extract(url):
        calls.append(url)
        if calls == ["a", "b"]:
            raise RuntimeError("extraction interrupted")
        return {"url": url, "page_title": url, "stories": [], "story_count": 0, "mode": "demo"}

    kwargs = dict(wal_root=str(tmp_path), run_id="failure", pages=["a", "b"], mode="demo")
    with pytest.raises(RuntimeError, match="extraction interrupted"):
        asyncio.run(run_scraper(**kwargs))
    assert asyncio.run(run_scraper(**kwargs)) == [
        {"url": "a", "page_title": "a", "stories": [], "story_count": 0, "mode": "demo"},
        {"url": "b", "page_title": "b", "stories": [], "story_count": 0, "mode": "demo"},
    ]
    assert calls == ["a", "b", "b"]


@pytest.mark.fast
def test_new_inputs_and_new_run_do_not_reuse_old_results(tmp_path):
    calls = []

    async def mock_extract(url):
        calls.append(url)
        return {"url": url, "page_title": url, "stories": [], "story_count": 0, "mode": "demo"}

    for run_id, pages, mode in [
        ("first", ["a"], "demo"),
        ("second", ["a"], "demo"),
        ("first", ["b"], "demo"),
        ("first", ["a"], "live"),
    ]:
        asyncio.run(run_scraper(wal_root=str(tmp_path), run_id=run_id, pages=pages, mode=mode))
    assert calls == ["a", "a", "b", "a"]
    with pytest.raises(ValueError, match="run_id"):
        run_directory(str(tmp_path), "", ["a"], "demo")


@pytest.mark.integration
def test_sigkill_then_fresh_process_skips_first_page(tmp_path):
    command = [
        sys.executable,
        "-m",
        "examples.cookbooks.playwright_scraper",
        "--demo",
        "--run-id",
        "hard-crash",
        "--wal-dir",
        str(tmp_path),
    ]
    killed = subprocess.run(command + ["--crash-after", "1"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert killed.returncode in (-9, 137)
    assert killed.stdout.count("SCRAPE") == 1
    resumed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.count("SCRAPE") == 4  # pages 2-5 only (page 1 from WAL)
    third = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert third.returncode == 0, third.stderr
    assert "SCRAPE" not in third.stdout


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("LIL_TEST_BROWSER") != "1", reason="Opt-in: launches local Chromium")
def test_real_playwright_extraction_is_cached(tmp_path):
    """A local synthetic page and real Playwright, without external network."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/news":
                hits.append(self.path)
                page = b"""<html><head><title>Test News</title></head>
<body><h1 class="page-title">Test News Page</h1>
<article class="story"><h2><a href="/article/1">Story One</a></h2><span class="score">10</span></article>
<article class="story"><h2><a href="/article/2">Story Two</a></h2><span class="score">20</span></article>
</body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
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
    url = f"http://127.0.0.1:{server.server_port}/news"
    try:
        kwargs = dict(wal_root=str(tmp_path), run_id="real-browser", pages=[url], mode="demo")
        first = asyncio.run(run_scraper(**kwargs))
        second = asyncio.run(run_scraper(**kwargs))
        assert first == second
        assert first[0]["page_title"] == "Test News Page"
        assert first[0]["story_count"] == 2
        assert hits == ["/news"]  # only one actual HTTP request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)