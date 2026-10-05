"""
Minimal in-memory TTL cache for near-static tool results (schema snapshot,
estados/offices lists, species_count, db version log). These only change
when the underlying table is reloaded, not per-request, so short-lived
caching avoids redundant round trips against a ~12.75M-row table.

Not for get_landings or anything keyed by rich filter arguments over the
full dataset — those results are effectively unbounded in cardinality and
would just grow the cache without much hit rate.

Process-local and unbounded by design: entries self-expire after their TTL,
and the set of cached keys here is small and fixed (one per near-static
tool call shape), so no eviction policy is needed.
"""

from __future__ import annotations
import threading
import time
from typing import Any

from mcp_server.config import CACHE_TTL_SECONDS

_store: dict[tuple, tuple[float, Any]] = {}
_lock = threading.Lock()


def get(key: tuple) -> Any | None:
    """Return the cached value for `key`, or None if missing/expired."""
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del _store[key]
            return None
        return value


def set(key: tuple, value: Any, ttl: float = CACHE_TTL_SECONDS) -> None:
    with _lock:
        _store[key] = (time.monotonic() + ttl, value)


def clear() -> None:
    """Drop all cached entries."""
    with _lock:
        _store.clear()
