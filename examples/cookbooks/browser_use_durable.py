"""Checkpoint a read-only release digest with Browser Use and LetItLoop.

Run --demo without Chrome; use --live for real, isolated headless Chrome.
Each page read owns and closes its browser before its result is checkpointed.
A resumed run reuses completed JSON results, not a browser, login or DOM state.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import sys
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.decorators import async_step, durable_async

RECIPE_VERSION = 1
RELEASE_PAGES = (
    "https://pypi.org/project/browser-use/",
    "https://pypi.org/project/letitloop/",
    "https://pypi.org/project/pydantic/",
)
Reader = Callable[[str], Awaitable[dict[str, Any]]]


def run_directory(wal_root: str, run_id: str, urls: Sequence[str], mode: str) -> Path:
    """Keep changed inputs, live/demo data and separately requested runs apart."""
    if not run_id.strip():
        raise ValueError("run_id must name this digest; reuse it only to resume")
    identity = json.dumps(
        {"recipe": RECIPE_VERSION, "run_id": run_id, "urls": list(urls), "mode": mode},
        sort_keys=True,
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return Path(wal_root) / digest


async def demo_reader(url: str) -> dict[str, Any]:
    """Return explicitly synthetic data, without claiming a real page was read."""
    print(f"READ demo {url}", flush=True)
    return {"url": url, "title": url.split("/")[-2] + " (synthetic)", "mode": "demo"}


async def browser_reader(url: str) -> dict[str, Any]:
    """Read public page metadata using Browser Use's real browser API."""
    from browser_use import BrowserSession

    print(f"READ live {url}", flush=True)
    # A fresh, task-owned profile prevents reuse of personal logins or browser state.
    with tempfile.TemporaryDirectory(prefix="lil-browser-read-") as profile:
        browser = BrowserSession(
            headless=True,
            user_data_dir=profile,
            use_cloud=False,
            keep_alive=False,
            enable_default_extensions=False,
            captcha_solver=False,
        )
        try:
            await browser.start()
            page = await browser.new_page()
            await page.goto(url)
            # Wait for parsed page content, without treating network-idle as readiness.
            raw = await page.evaluate(
                """() => new Promise((resolve, reject) => {
                    const deadline = Date.now() + 15000;
                    const read = () => {
                        const heading = document.querySelector('h1.project-header__name');
                        if (document.readyState !== 'loading' && heading?.innerText.trim()) {
                            resolve(JSON.stringify({
                                url: location.href,
                                title: heading.innerText.trim()
                            }));
                        } else if (Date.now() > deadline) {
                            reject(new Error('No PyPI package heading became readable'));
                        } else {
                            setTimeout(read, 100);
                        }
                    };
                    read();
                })"""
            )
            result = json.loads(raw)
            if not result.get("title"):
                raise RuntimeError("The browser returned no page heading")
            return {**result, "mode": "live"}
        finally:
            await browser.close()


async def run_digest(
    *,
    wal_root: str,
    run_id: str,
    urls: Sequence[str] = RELEASE_PAGES,
    mode: str = "demo",
    reader: Reader | None = None,
    crash_after: int | None = None,
) -> list[dict[str, Any]]:
    """Persist completed read results; unfinished reads can repeat after a crash."""
    if mode not in {"demo", "live"}:
        raise ValueError("mode must be demo or live")
    if not urls:
        raise ValueError("at least one page is required")
    run_dir = run_directory(wal_root, run_id, urls, mode)
    read = reader or (demo_reader if mode == "demo" else browser_reader)

    @durable_async(goal_id="browser-use-release-digest", wal_dir=str(run_dir))
    async def workflow() -> list[dict[str, Any]]:
        output = []
        for index, url in enumerate(urls, 1):
            output.append(await async_step(f"page-{index}", read, url))
            print(f"CHECKPOINT {index}", flush=True)
            if crash_after == index:
                # Fault injection is opt-in and affects only this process.
                if hasattr(signal, "SIGKILL"):
                    os.kill(os.getpid(), signal.SIGKILL)
                os._exit(137)
        return output

    return await workflow()


def main() -> None:
    """Run a new or resumed digest, with explicit mode and identity."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--demo", action="store_true")
    modes.add_argument("--live", action="store_true")
    parser.add_argument("--run-id", required=True, help="Reuse to resume; use a new ID for fresh page data.")
    parser.add_argument("--wal-dir", default=".bench_wal/cookbooks/browser_use_durable")
    parser.add_argument("--crash-after", type=int, choices=range(1, len(RELEASE_PAGES) + 1))
    args = parser.parse_args()
    results = asyncio.run(
        run_digest(
            wal_root=args.wal_dir,
            run_id=args.run_id,
            mode="live" if args.live else "demo",
            crash_after=args.crash_after,
        )
    )
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
