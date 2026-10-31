# Interactive crash and WAL visualizer

This optional Streamlit app demonstrates LetItLoop's real durable-step cache and write-ahead log. It does not call an LLM or make network requests.

## Run locally

From the repository root, install LetItLoop and the optional demo UI dependency:

    python -m pip install -e .
    python -m pip install -r examples/interactive_demo/requirements.txt
    streamlit run examples/interactive_demo/app.py

Choose the step before which to pause, then click **Start Agent Workflow**. The worker completes and checkpoints every preceding step, announces that it is waiting before the selected operation, and pauses so you can click **Inject SIGKILL / Crash**. After the subprocess is killed, choose **Resume from WAL**. The existing workflow ID and WAL directory are reused, so completed steps are returned from durable storage rather than executed again.

## What the metrics mean

- **Resumption latency** is the measured sum of calls that returned previously checkpointed step results; it excludes steps that still need to execute.
- **Duplicate token waste** estimates synthetic costs for already-completed steps that run again. The selected crash point is before the operation starts, so the normal demonstration should show 0.0% duplicate waste.
- **WAL-restored steps** counts cache hits in the current resume attempt.
- **Synthetic tokens skipped** is the sample token cost avoided by reading cached results. These values are illustrative, not model usage.

The WAL viewer parses the repository's framed LILWAL02 records and shows the latest events. Each run gets its own temporary directory. The Resume button reuses that directory; starting a new run creates a fresh WAL.

The pause expires after three minutes if no crash is injected, to avoid leaving a demo worker waiting indefinitely.
