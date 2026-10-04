# Resume a Browser Use release digest

Collect the current release headings for Browser Use, LetItLoop and Pydantic. LetItLoop stores each completed read as JSON. Restarting the same digest reads only unfinished pages.

This uses Browser Use's browser API directly. No LLM, Cloud browser, model key or personal Chrome profile is required. It does not restore an autonomous agent's memory or a live browser session.

## Try it

From this repository:

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -e . "browser-use==0.13.10"
.venv/bin/python -m examples.cookbooks.browser_use_durable --demo --run-id demo-one
```

The demo is synthetic. To read the three public PyPI pages using a fresh local headless Chrome:

```sh
ANONYMIZED_TELEMETRY=false .venv/bin/python -m examples.cookbooks.browser_use_durable --live --run-id releases-one
```

Use an installed Chrome/Chromium compatible with Browser Use. A missing browser is an error, not a silent fallback to demo data. The example disables Cloud and default extensions and closes each browser it creates.

## Prove a restart

The first command deliberately kills only its own Python process after one completed checkpoint:

```sh
.venv/bin/python -m examples.cookbooks.browser_use_durable --demo --run-id restart-one --crash-after 1
.venv/bin/python -m examples.cookbooks.browser_use_durable --demo --run-id restart-one
```

The resumed command prints two READ lines. The first result comes from the WAL. A third execution with the same run ID prints no READ lines.

```text
First process                   Durable storage        Second process
read page 1 -> close browser --> page 1 JSON ----------> reuse page 1 JSON
checkpoint complete
process dies                                           read page 2 -> save JSON
                                                       read page 3 -> save JSON
```

A new run ID requests fresh data. The input URL list, demo/live mode and recipe version are part of the cache identity, so changed inputs never silently reuse another digest. Reuse the same durable storage directory across a process restart; losing that storage also loses the checkpoints.

## Limits

- A read interrupted before checkpoint completion can run again. This is appropriate only for repeatable, read-only operations.
- The WAL stores returned page metadata on disk in plaintext. Use public content. Do not store credentials, private page contents, cookies, tokens, browser objects or CDP URLs.
- A checkpoint is a saved result, not proof the page is still current. Start a new run ID when you want fresh releases.
- Login state, cookies, tab state and arbitrary agent trajectories are not restored.
- Do not put payments, checkout, form submission or other external mutations inside this example. A local atomic marker cannot prove that a remote action succeeded: a crash can occur between writing the marker and completing that action. Those workflows need provider-supported idempotency and reconciliation, plus the user's approval.
- This is a single-host, retained-storage example, not a distributed workflow engine or a claim of exactly-once browser actions.

## Test

```sh
uv pip install --python .venv/bin/python pytest pytest-asyncio pytest-xdist
.venv/bin/python -m pytest tests/test_browser_use_cookbook.py -n 0 -q
ANONYMIZED_TELEMETRY=false LIL_TEST_BROWSER=1 .venv/bin/python -m pytest tests/test_browser_use_cookbook.py -n 0 -q
```

The opt-in browser check serves a local synthetic release page, reads it through the installed Browser Use package and headless Chrome, resumes, and asserts that the server received only one read.

