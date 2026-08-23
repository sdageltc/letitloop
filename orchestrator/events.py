"""Thread-safe event bus for goal lifecycle events — async pub/sub with no blocking."""

import sys
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

EVENT_TYPES: Tuple[str, ...] = (
    "goal.started",
    "contract.working",
    "contract.verified",
    "contract.qc_passed",
    "contract.failed",
    "impossibility.generated",
    "goal.completed",
)

Subscriber = Callable[[Dict[str, Any]], None]


class EventBus:
    """Publish/subscribe bus delivering each event to matching subscribers.

    Every subscriber invocation runs in its own daemon thread so a slow or
    broken consumer never blocks the publisher.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: List[Tuple[Optional[str], Subscriber]] = []

    def subscribe(
        self,
        callback: Subscriber,
        event_type: Optional[str] = None,
    ) -> Callable[[], None]:
        """Register callback; event_type=None matches all events. Returns unsubscribe."""
        entry = (event_type, callback)
        with self._lock:
            self._subscribers.append(entry)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(entry)
                except ValueError:
                    pass

        return unsubscribe

    def publish(
        self,
        event_type: str,
        goal_id: Optional[str] = None,
        task_id: Optional[str] = None,
        **payload: Any,
    ) -> Dict[str, Any]:
        """Build an envelope and deliver it to all matching subscribers asynchronously."""
        envelope: Dict[str, Any] = {
            "event": event_type,
            "goal_id": goal_id,
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": dict(payload),
        }
        with self._lock:
            targets = [
                cb
                for et, cb in self._subscribers
                if et is None or et == event_type
            ]
        for cb in targets:
            thread = threading.Thread(
                target=self._invoke,
                args=(cb, envelope),
                daemon=True,
            )
            thread.start()
        return envelope

    def clear(self) -> None:
        """Drop all subscriptions (test helper)."""
        with self._lock:
            self._subscribers = []

    @staticmethod
    def _invoke(callback: Subscriber, envelope: Dict[str, Any]) -> None:
        try:
            callback(envelope)
        except Exception as exc:
            print(f"[events] subscriber error: {exc}", file=sys.stderr)


_bus = EventBus()


def get_bus() -> EventBus:
    """Return the process-wide singleton bus."""
    return _bus
