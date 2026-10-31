"""Crash-Resilient Web Scraping Agent with Playwright + LetItLoop.

Demonstrates durable execution for a multi-page web scraping pipeline using
Playwright's async API wrapped with @durable_async / async_step checkpoints.

Architecture:
  Scrape Page 1 -> Scrape Page 2 -> ... -> Scrape Page N -> Aggregate Results
  (step 1)         (step 2)                (step N)         (final step)

If the Python process is killed (SIGKILL, OOM, etc.) at any point, resuming
the same run-id instantly skips previously scraped pages via WAL lookup (<1ms)
and continues from where it left off — no wasted proxy bandwidth or tokens.

Usage:
  python examples/cookbooks/playwright_scraper.py --demo --run-id demo-one
  python examples/cookbooks/playwright_scraper.py --live --run-id news-one
  python examples/cookbooks/playwright_scraper.py --demo --run-id crash-test --crash-after 2
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import sys
import threading
import time
import urllib.request
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.decorators import async_step, durable_async  # noqa: E402

RECIPE_VERSION = 1

# Default demo pages: synthetic HTML served by a local HTTP server
DEMO_PAGES = [
    "/page/1",
    "/page/2",
    "/page/3",
    "/page/4",
    "/page/5",
]

# Live mode: public news feeds (read-only, no auth required)
LIVE_PAGES = [
    "https://news.ycombinator.com/",
    "https://www.reddit.com/r/worldnews/.rss",
    "https://feeds.bbci.co.uk/news/rss.xml",
]


def _normalize_demo_page(page: str) -> str:
    """Extract path for demo pages so ephemeral local server ports do not alter run identity."""
    if page.startswith("http://127.0.0.1:") or page.startswith("http://localhost:"):
        return urlparse(page).path
    return page


def run_directory(wal_root: str, run_id: str, pages: Sequence[str], mode: str) -> Path:
    """Keep changed inputs, live/demo data and separately requested runs apart."""
    if not run_id.strip():
        raise ValueError("run_id must name this scrape; reuse it only to resume")

    page_keys = [_normalize_demo_page(p) for p in pages] if mode == "demo" else list(pages)
    identity = json.dumps(
        {"recipe": RECIPE_VERSION, "run_id": run_id, "pages": page_keys, "mode": mode},
        sort_keys=True,
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return Path(wal_root) / digest


# ── Mock HTML Server for Demo Mode ──────────────────────────────────────────


class _MockPageHandler(BaseHTTPRequestHandler):
    """Serves synthetic HTML pages that look like a news listing."""

    hits: list[str] = []

    def do_GET(self) -> None:
        self.hits.append(self.path)
        if self.path.startswith("/page/"):
            page_num = self.path.rsplit("/", 1)[-1]
            body = f"""<!DOCTYPE html>
<html><head><title>News Page {page_num}</title></head>
<body>
  <h1 class="page-title">Synthetic News Feed — Page {page_num}</h1>
  <article class="story">
    <h2><a href="/article/{page_num}-a">Headline {page_num}A: Market Rallies</a></h2>
    <span class="score">42 points</span>
  </article>
  <article class="story">
    <h2><a href="/article/{page_num}-b">Headline {page_num}B: Tech Breakthrough</a></h2>
    <span class="score">17 points</span>
  </article>
  <article class="story">
    <h2><a href="/article/{page_num}-c">Headline {page_num}C: Weather Update</a></h2>
    <span class="score">8 points</span>
  </article>
</body></html>""".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args: Any) -> None:
        pass


def _start_mock_server() -> tuple[ThreadingHTTPServer, threading.Thread, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, server.server_port


# ── Extraction Logic ────────────────────────────────────────────────────────


async def _extract_stories_demo(url: str) -> dict[str, Any]:
    """Extract story data from a demo page using Playwright async API with fallback."""
    print(f"  SCRAPE {url}", flush=True)
    t0 = time.perf_counter()

    try:
        from playwright.async_api import async_playwright

        has_playwright = True
    except ImportError:
        has_playwright = False

    if has_playwright:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                stories = await page.evaluate(
                    """() => {
                        const items = [];
                        document.querySelectorAll('article.story').forEach(el => {
                            const link = el.querySelector('h2 a');
                            const score = el.querySelector('.score');
                            items.push({
                                title: link ? link.innerText.trim() : '',
                                href: link ? link.href : '',
                                score: score ? parseInt(score.innerText) || 0 : 0,
                            });
                        });
                        const title = document.querySelector('.page-title');
                        return {
                            page_title: title ? title.innerText.trim() : '',
                            stories: items,
                        };
                    }"""
                )
            finally:
                await browser.close()
    else:
        # Standard library fallback for environments without Playwright browser binaries
        with urllib.request.urlopen(url, timeout=10) as resp:
            html = resp.read().decode("utf-8")
        title_match = re.search(r'<h1 class="page-title">([^<]+)</h1>', html)
        page_title = title_match.group(1) if title_match else "Synthetic News Feed"
        items = []
        for m in re.finditer(
            r'<article class="story">\s*<h2><a href="([^"]+)">([^<]+)</a></h2>\s*<span class="score">([^<]+)</span>',
            html,
        ):
            href, title, score_str = m.groups()
            score_num = int(re.search(r"\d+", score_str).group(0)) if re.search(r"\d+", score_str) else 0
            items.append({"title": title, "href": href, "score": score_num})
        stories = {"page_title": page_title, "stories": items}

    elapsed_ms = (time.perf_counter() - t0) * 1000
    return {
        "url": url,
        "page_title": stories["page_title"],
        "stories": stories["stories"],
        "story_count": len(stories["stories"]),
        "scrape_duration_ms": round(elapsed_ms, 1),
        "mode": "demo",
    }


async def _extract_stories_live(url: str) -> dict[str, Any]:
    """Extract story data from a live page using Playwright async API."""
    from playwright.async_api import async_playwright

    print(f"  SCRAPE {url}", flush=True)
    t0 = time.perf_counter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            stories = await page.evaluate(
                """() => {
                    const items = [];
                    document.querySelectorAll('a').forEach(el => {
                        const text = el.innerText.trim();
                        if (text.length > 10 && el.href) {
                            items.push({title: text, href: el.href});
                        }
                    });
                    return {
                        page_title: document.title || '',
                        stories: items.slice(0, 20),
                    };
                }"""
            )
        finally:
            await browser.close()

    elapsed_ms = (time.perf_counter() - t0) * 1000
    return {
        "url": url,
        "page_title": stories["page_title"],
        "stories": stories["stories"],
        "story_count": len(stories["stories"]),
        "scrape_duration_ms": round(elapsed_ms, 1),
        "mode": "live",
    }


# ── Durable Workflow ────────────────────────────────────────────────────────


async def run_scraper(
    *,
    wal_root: str,
    run_id: str,
    pages: Sequence[str],
    mode: str = "demo",
    crash_after: int | None = None,
    extract_fn: Optional[Callable[..., Any]] = None,
) -> list[dict[str, Any]]:
    """Persist completed scrape results; unfinished pages can repeat after a crash."""
    if mode not in {"demo", "live"}:
        raise ValueError("mode must be demo or live")
    if not pages:
        raise ValueError("at least one page is required")

    run_dir = run_directory(wal_root, run_id, pages, mode)
    if extract_fn is not None:
        extract = extract_fn
    else:
        extract = _extract_stories_demo if mode == "demo" else _extract_stories_live

    @durable_async(goal_id="playwright-scraper", wal_dir=str(run_dir))
    async def workflow() -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for index, url in enumerate(pages, 1):
            result = await async_step(f"page-{index}", extract, url)
            results.append(result)
            print(f"  CHECKPOINT {index}/{len(pages)} — {result['story_count']} stories", flush=True)

            if crash_after == index:
                # Fault injection: simulate OOM kill / SIGKILL
                print(f"  *** SIMULATED CRASH after page {index} ***", flush=True)
                if hasattr(signal, "SIGKILL"):
                    try:
                        os.kill(os.getpid(), signal.SIGKILL)
                    except Exception:
                        pass
                os._exit(137)

        return results

    return await workflow()


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--demo", action="store_true", help="Scrape synthetic local pages")
    modes.add_argument("--live", action="store_true", help="Scrape real public news feeds")
    parser.add_argument("--run-id", required=True, help="Reuse to resume; new ID for fresh scrape")
    parser.add_argument("--wal-dir", default=".bench_wal/cookbooks/playwright_scraper")
    parser.add_argument("--crash-after", type=int, choices=range(1, len(DEMO_PAGES) + 1))
    args = parser.parse_args()

    pages = LIVE_PAGES if args.live else DEMO_PAGES

    # Start mock server for demo mode
    server = None
    if args.demo:
        server, thread, port = _start_mock_server()
        pages = [f"http://127.0.0.1:{port}{p}" for p in DEMO_PAGES]
        print(f"Mock server started on port {port}", flush=True)

    try:
        print(f"\n{'=' * 60}", flush=True)
        print(" Playwright + LetItLoop Crash-Resilient Scraper", flush=True)
        print(f" Mode: {'live' if args.live else 'demo'}  |  Pages: {len(pages)}  |  Run: {args.run_id}", flush=True)
        print(f"{'=' * 60}\n", flush=True)

        t0 = time.perf_counter()
        results = asyncio.run(
            run_scraper(
                wal_root=args.wal_dir,
                run_id=args.run_id,
                pages=pages,
                mode="live" if args.live else "demo",
                crash_after=args.crash_after,
            )
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        total_stories = sum(r["story_count"] for r in results)
        print(f"\n{'=' * 60}", flush=True)
        print(f" COMPLETE — {len(results)} pages, {total_stories} stories in {elapsed_ms:.1f}ms", flush=True)
        print(f"{'=' * 60}\n", flush=True)

        for r in results:
            print(f"  {r['page_title']}: {r['story_count']} stories ({r['scrape_duration_ms']}ms)", flush=True)

        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))

    finally:
        if server:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
