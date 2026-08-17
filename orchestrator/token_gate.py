"""token_gate.py — hard per-call token ceiling guard.

User directive: NO single LLM API call may exceed ``HARD_CAP_TOTAL`` tokens
(prompt + completion). Passing the cap is a HARD STOP:

  - pre-flight:   refuse to dispatch (exit code 3) BEFORE any bytes leave
  - in-flight:    abort the response stream the moment the estimate passes
  - authoritative: if the API reports actual usage.total_tokens > cap, the
                   call is recorded as a violation even after completion

Every hard stop appends a record to the violation ledger
(``scratch/token_gate_violations.jsonl`` by default; override with the
``TOKEN_GATE_LEDGER`` env var).

Shared by Python (import) and JS (CLI subcommand).

Usage:
  from orchestrator.token_gate import preflight, TokenGateError, record_violation
  python -m orchestrator.token_gate check --chars 400000 --max-tokens 200000 --caller foo
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HARD_CAP_TOTAL = 1_000_000  # tokens per single call (prompt + completion)
CHARS_PER_TOKEN = 3  # conservative estimate: fewer chars per token -> earlier refusal
EXIT_GATE = 3

DEFAULT_LEDGER = Path(
    os.environ.get("TOKEN_GATE_LEDGER", str(Path.cwd() / "scratch" / "token_gate_violations.jsonl"))
)
MODELS_CACHE = Path(
    os.environ.get("TOKEN_GATE_MODELS_CACHE", str(Path.cwd() / "scratch" / "llm_models.json"))
)
MODELS_CACHE_TTL_SEC = 24 * 3600


class TokenGateError(RuntimeError):
    """Raised pre-flight when a call would exceed the hard cap."""


def approx_tokens(chars: int) -> int:
    """Conservative token estimate: chars / CHARS_PER_TOKEN, minimum 1."""
    return max(1, math.ceil(chars / CHARS_PER_TOKEN))


def _lock_file() -> object:
    if sys.platform == "win32":
        import msvcrt

        return msvcrt
    import fcntl

    return fcntl


def record_violation(
    *,
    caller: str,
    kind: str,
    est_prompt_tokens: int,
    max_tokens: int | None = None,
    est_completion_tokens: int | None = None,
    actual_usage: dict | None = None,
    model: str | None = None,
    reason: str,
) -> None:
    """Append a JSONL violation record to the ledger (serialized via lock file)."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "caller": caller,
        "kind": kind,
        "reason": reason,
        "model": model,
        "est_prompt_tokens": est_prompt_tokens,
        "max_tokens": max_tokens,
        "est_completion_tokens": est_completion_tokens,
        "actual_usage": actual_usage,
        "cap": HARD_CAP_TOTAL,
    }
    try:
        DEFAULT_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        lock = DEFAULT_LEDGER.with_suffix(".lock")
        handle = open(lock, "a+")
        try:
            _lock_file().lockf(handle, _lock_file().LOCK_EX)
            with open(DEFAULT_LEDGER, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            _lock_file().lockf(handle, _lock_file().LOCK_UN)
        finally:
            handle.close()
    except (OSError, ImportError, ValueError, AttributeError):
        # Ledger write must never crash the caller; the gate itself already stopped the call.
        pass


def preflight(prompt_chars: int, max_tokens: int | None = None, caller: str = "unknown") -> None:
    """Refuse the dispatch if prompt + requested max_tokens exceed the cap.

    Raises TokenGateError (callers should exit EXIT_GATE). A violation record
    is written before raising.
    """
    est_prompt = approx_tokens(prompt_chars)
    total = est_prompt + (max_tokens or 0)
    if total > HARD_CAP_TOTAL:
        record_violation(
            caller=caller,
            kind="preflight_refused",
            est_prompt_tokens=est_prompt,
            max_tokens=max_tokens,
            reason=f"est_prompt({est_prompt}) + max_tokens({max_tokens}) = {total} > {HARD_CAP_TOTAL}",
        )
        raise TokenGateError(
            f"TOKEN GATE: pre-flight refusal. est_prompt={est_prompt} "
            f"+ max_tokens={max_tokens} = {total} > cap {HARD_CAP_TOTAL}. "
            f"Split the payload. (caller={caller})"
        )


class StreamGuard:
    """In-flight completion counter. Abort the stream once the cap is passed."""

    def __init__(self, prompt_chars: int, caller: str = "unknown", model: str | None = None):
        self.est_prompt = approx_tokens(prompt_chars)
        self.completion_chars = 0
        self.caller = caller
        self.model = model

    def add(self, text: str) -> None:
        self.completion_chars += len(text or "")

    @property
    def est_completion(self) -> int:
        return approx_tokens(self.completion_chars)

    @property
    def est_total(self) -> int:
        return self.est_prompt + self.est_completion

    @property
    def exceeded(self) -> bool:
        return self.est_total > HARD_CAP_TOTAL

    def report(self, actual_usage: dict | None = None) -> None:
        record_violation(
            caller=self.caller,
            kind="stream_aborted",
            est_prompt_tokens=self.est_prompt,
            est_completion_tokens=self.est_completion,
            actual_usage=actual_usage,
            model=self.model,
            reason=f"est_prompt({self.est_prompt}) + est_completion({self.est_completion}) "
                   f"= {self.est_total} > cap {HARD_CAP_TOTAL}",
        )


def check_usage_authoritative(
    usage: dict, est_prompt: int, caller: str = "unknown", model: str | None = None
) -> bool:
    """Post-check: the API's own reported total over the cap -> violation record.

    Returns True when the call is over the cap (caller should treat it as a hard stop).
    """
    total = usage.get("total_tokens")
    if total is not None and int(total) > HARD_CAP_TOTAL:
        record_violation(
            caller=caller,
            kind="usage_over_cap",
            est_prompt_tokens=est_prompt,
            actual_usage=usage,
            model=model,
            reason=f"provider-reported total_tokens={total} > cap {HARD_CAP_TOTAL}",
        )
        return True
    return False


def _cached_models(refresh: bool = False) -> list[str]:
    """Live model IDs from the provider, cached for MODELS_CACHE_TTL_SEC."""
    if not refresh and MODELS_CACHE.exists():
        try:
            stale = time.time() - MODELS_CACHE.stat().st_mtime > MODELS_CACHE_TTL_SEC
            if not stale:
                data = json.loads(MODELS_CACHE.read_text(encoding="utf-8"))
                return list(data)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    return []


def check_model_available(model: str) -> bool:
    """Capability preflight: refuse dispatch to a model the provider does not serve.

    Consults the cached /v1/models snapshot (``scratch/llm_models.json``), fetched
    by the JS dispatchers on a TTL. Unknown model -> False.
    """
    if not model:
        return False
    live = _cached_models()
    if not live:
        return True  # no cache: allow (dispatcher refreshes on next run); preflight is advisory
    return model in live


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print(__doc__)
        return 1
    if args[0] == "check":
        def _val(flag: str) -> int | None:
            for i, a in enumerate(args):
                if a == flag and i + 1 < len(args):
                    try:
                        return int(args[i + 1])
                    except ValueError:
                        return None
            return None

        chars = _val("--chars")
        max_tokens = _val("--max-tokens")
        caller = "cli"
        for i, a in enumerate(args):
            if a == "--caller" and i + 1 < len(args):
                caller = args[i + 1]
        if chars is None:
            print("check requires --chars N [--max-tokens M] [--caller NAME]", file=sys.stderr)
            return 1
        try:
            preflight(chars, max_tokens=max_tokens, caller=caller)
            est = approx_tokens(chars)
            total = est + (max_tokens or 0)
            print(f"TOKEN GATE: OK est_prompt={est} total={total} cap={HARD_CAP_TOTAL}")
            return 0
        except TokenGateError as e:
            print(str(e), file=sys.stderr)
            return EXIT_GATE
    print(f"unknown subcommand: {args[0]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
