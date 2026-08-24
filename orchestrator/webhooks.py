"""Webhook delivery for orchestrator events — signed HTTP POSTs via urllib."""

import hashlib
import hmac
import json
import os
import sys
import threading
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .events import EVENT_TYPES, EventBus

ENV_WEBHOOKS_JSON = "LETITLOOP_WEBHOOKS_JSON"


def sign_payload(body: bytes, secret: str) -> str:
    """Return the HMAC-SHA256 hex digest of body keyed by secret."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@dataclass
class WebhookConfig:
    """A single webhook endpoint configuration."""

    url: str
    secret: Optional[str] = None
    events: Optional[List[str]] = None
    timeout: float = 5.0


class WebhookDispatcher:
    """Delivers event envelopes to configured webhook endpoints."""

    def __init__(self, webhooks: List[WebhookConfig]) -> None:
        self.webhooks = list(webhooks)

    def dispatch(self, event_type: str, envelope: Dict[str, Any]) -> None:
        """Fire matching webhook deliveries asynchronously; never raises or blocks."""
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        for config in self.webhooks:
            if config.events is not None and event_type not in config.events:
                continue
            thread = threading.Thread(
                target=self._post,
                args=(config, body, event_type),
                daemon=True,
            )
            thread.start()

    def _post(self, config: WebhookConfig, body: bytes, event_type: str) -> None:
        try:
            headers = {
                "Content-Type": "application/json",
                "X-LetItLoop-Event": event_type,
            }
            if config.secret:
                headers["X-LetItLoop-Signature"] = "sha256=" + sign_payload(body, config.secret)
            req = urllib.request.Request(config.url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=config.timeout) as resp:  # nosec B310
                resp.read()
        except Exception as exc:
            print(f"[webhooks] delivery to {config.url} failed: {exc}", file=sys.stderr)


def attach_webhooks(bus: EventBus, dispatcher: WebhookDispatcher) -> Callable[[], None]:
    """Subscribe dispatcher.dispatch on the bus for all known event types.

    Returns an unsubscribe callable that detaches every registration.
    """
    unsubs = []
    for et in EVENT_TYPES:

        def make_callback(event_type=et):
            def cb(envelope: Dict[str, Any]) -> None:
                dispatcher.dispatch(event_type, envelope)

            return cb

        unsubs.append(bus.subscribe(make_callback(), event_type=et))

    def detach() -> None:
        for unsub in unsubs:
            unsub()

    return detach


def load_webhook_configs(path: str) -> List[WebhookConfig]:
    """Load webhook configs from a JSON list file; missing file yields []."""
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"[webhooks] failed to read {path}: {exc}", file=sys.stderr)
        return []
    if not isinstance(raw, list):
        print(f"[webhooks] {path}: expected a JSON list", file=sys.stderr)
        return []
    configs: List[WebhookConfig] = []
    for entry in raw:
        config = _parse_config(entry)
        if config is None:
            print(f"[webhooks] skipping invalid webhook entry: {entry!r}", file=sys.stderr)
        else:
            configs.append(config)
    return configs


def _parse_config(entry: Any) -> Optional[WebhookConfig]:
    if not isinstance(entry, dict):
        return None
    url = entry.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    events = entry.get("events")
    if events is not None and not isinstance(events, list):
        return None
    secret = entry.get("secret")
    try:
        timeout = float(entry.get("timeout", 5.0))
    except (TypeError, ValueError):
        return None
    return WebhookConfig(
        url=url,
        secret=secret if isinstance(secret, str) and secret else None,
        events=[str(e) for e in events] if events is not None else None,
        timeout=timeout,
    )


def load_webhook_configs_from_env() -> List[WebhookConfig]:
    """Load webhook configs from the path named by LETITLOOP_WEBHOOKS_JSON, if set."""
    return load_webhook_configs(os.environ.get(ENV_WEBHOOKS_JSON, ""))
