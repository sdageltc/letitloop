"""Thread-safe event bus for goal lifecycle events — async pub/sub with no blocking.

Delivery is bounded: a shared semaphore caps concurrent subscriber invocations so
a slow consumer cannot turn event bursts into unbounded thread pileups. When the
cap is saturated, delivery is skipped (counted in ``dropped_count``) rather than
blocking the control loop - telemetry may drop; execution must not stall.
"""

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

DEFAULT_MAX_CONCURRENT_DELIVERIES = 32


class EventBus:
    """Publish/subscribe bus delivering each event to matching subscribers.

    Subscriber invocations run asynchronously in daemon threads, bounded by
    ``max_concurrent_deliveries``. When the bound is saturated the delivery is
    dropped and counted instead of queueing or blocking the publisher.
    """

    def __init__(self, max_concurrent_deliveries: int = DEFAULT_MAX_CONCURRENT_DELIVERIES) -> None:
        self._lock = threading.Lock()
        self._subscribers: List[Tuple[Optional[str], Subscriber]] = []
        cap = max(1, int(max_concurrent_deliveries))
        self._capacity = cap
        self._slots = threading.BoundedSemaphore(cap)
        self.dropped_count = 0

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
            targets = [cb for et, cb in self._subscribers if et is None or et == event_type]
        for cb in targets:
            if not self._slots.acquire(blocking=False):
                with self._lock:
                    self.dropped_count += 1
                print(
                    f"[events] delivery saturated - dropping event {event_type} (dropped_total={self.dropped_count})",
                    file=sys.stderr,
                )
                continue
            thread = threading.Thread(
                target=self._deliver,
                args=(cb, envelope),
                daemon=True,
            )
            thread.start()
        return envelope

    def clear(self) -> None:
        """Drop all subscriptions (test helper)."""
        with self._lock:
            self._subscribers = []

    def _deliver(self, callback: Subscriber, envelope: Dict[str, Any]) -> None:
        try:
            callback(envelope)
        except Exception as exc:
            print(f"[events] subscriber error: {exc}", file=sys.stderr)
        finally:
            try:
                self._slots.release()
            except ValueError:
                pass


_bus = EventBus()


def get_bus() -> EventBus:
    """Return the process-wide singleton bus."""
    return _bus
