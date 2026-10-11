# Crash-Resilient Web Scraping with Playwright + LetItLoop

Build a multi-page web scraping agent that survives crashes. Each page extraction is wrapped with `@durable_async` / `async_step`, so completed pages are checkpointed to a Write-Ahead Log (WAL). If the process is killed (SIGKILL, OOM, etc.), resuming the same run skips already-scraped pages via a <1ms WAL lookup and continues from where it left off.

## Architecture

```
Scrape Page 1 -> Scrape Page 2 -> ... -> Scrape Page N -> Aggregate Results
  (step 1)         (step 2)                (step N)         (final return)
     │                 │                       │
     ▼                 ▼                       ▼
  WAL entry 1      WAL entry 2             WAL entry N
```

If a SIGKILL strikes after step N, steps 1-N are recovered from the WAL on resume. No proxy bandwidth or tokens are wasted on already-completed pages.

## Try it

From this repository:

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -e . "playwright==1.52.0"
.venv/bin/python -m playwright install chromium
.venv/bin/python -m examples.cookbooks.playwright_scraper --demo --run-id scrape-one
```

The demo mode starts a local HTTP server serving synthetic news pages, then scrapes each page using Playwright's headless Chromium. No external network access or API key is required.

To scrape real public news feeds:

```sh
.venv/bin/python -m examples.cookbooks.playwright_scraper --live --run-id news-one
```

## Prove a restart

The first command deliberately kills only its own Python process after scraping one page:

```sh
.venv/bin/python -m examples.cookbooks.playwright_scraper --demo --run-id crash-demo --crash-after 1
.venv/bin/python -m examples.cookbooks.playwright_scraper --demo --run-id crash-demo
```

The resumed command prints SCRAPE lines only for the unfinished pages. The first page's result comes from the WAL. A third execution with the same run ID prints no SCRAPE lines at all.

```text
First process                   Durable storage        Second process
scrape page 1 -> extract --> page 1 JSON ----------> reuse page 1 JSON
checkpoint complete
process dies                                           scrape page 2 -> save JSON
                                                       scrape page 3 -> save JSON
                                                       ...
                                                       aggregate results
```

## How it works

1. **`@durable_async`** wraps the workflow function, creating a `DurableAsyncContext` that manages the WAL directory and completed-step cache.
2. **`async_step(page-N, extract_fn, url)`** executes a page extraction. On the first run, it calls the extraction function and appends the result to the WAL. On resume, it checks the in-memory `completed_steps` dictionary (populated from the WAL on initialization) and returns the cached result in <1ms without invoking the extraction function.
3. **Crash injection** (`--crash-after N`) sends SIGKILL to the current process after N pages, simulating an OOM kill or unexpected termination.
4. **Resume** uses the same `--run-id` and `--wal-dir`, so the WAL identity matches and completed steps are fast-forwarded.

## Cache identity

The run directory is derived from a SHA-256 hash of:
- Recipe version
- Run ID
- Page URL list
- Mode (demo/live)

Changing any of these creates a new run directory, so stale results are never accidentally reused.

## Limits

- A page extraction interrupted before checkpoint completion can run again. This is appropriate only for read-only scraping operations.
- The WAL stores extracted page data on disk in plaintext. Use public content only. Do not store credentials, cookies, or authentication tokens.
- A checkpoint is a saved result, not proof the page hasn't changed. Start a new run ID when you want fresh data.
- Login state, cookies, browser sessions, and arbitrary DOM state are not restored. The example starts a fresh browser for each page extraction.
- Do not put form submissions, payments, or other external mutations inside this example. A local atomic marker cannot prove a remote action succeeded; a crash can occur between writing the marker and completing that action. Those workflows need provider-supported idempotency and reconciliation.
- This is a single-host, retained-storage example, not a distributed workflow engine or a claim of exactly-once browser actions.

## Test

```sh
uv pip install --python .venv/bin/python pytest pytest-asyncio pytest-xdist
.venv/bin/python -m pytest tests/test_playwright_scraper_cookbook.py -n 0 -q
```

The test verifies:
- Completed pages are reused (no duplicate extraction)
- A failed page is not checkpointed
- New inputs or run IDs do not reuse old results
- SIGKILL followed by a fresh process skips the first page