"""
Tests for mcp_server/cache.py: the refresh mode and expires_at() that the
cache warm-up (mcp_server/warmup.py) relies on.
"""

from __future__ import annotations
import threading
import time


def _get_in_another_thread(key):
    from mcp_server import cache
    out: list = []
    t = threading.Thread(target=lambda: out.append(cache.get(key)))
    t.start()
    t.join()
    return out[0]


def test_refreshing_misses_only_in_the_current_thread():
    from mcp_server import cache
    cache.clear()
    cache.set(("k",), "old")
    with cache.refreshing():
        assert cache.get(("k",)) is None                  # this thread recomputes
        assert _get_in_another_thread(("k",)) == "old"    # other threads still hit
    assert cache.get(("k",)) == "old"                     # back to normal afterwards


def test_refreshing_is_cleared_even_if_the_block_raises():
    from mcp_server import cache
    cache.clear()
    cache.set(("k",), "v")
    try:
        with cache.refreshing():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert cache.get(("k",)) == "v"


def test_set_inside_refreshing_replaces_the_value_with_a_new_deadline():
    from mcp_server import cache
    cache.clear()
    cache.set(("k",), "old", ttl=10)
    first = cache.expires_at(("k",))
    time.sleep(0.01)
    with cache.refreshing():
        cache.set(("k",), "new", ttl=10)
    assert cache.get(("k",)) == "new"
    assert cache.expires_at(("k",)) > first


def test_expires_at_is_none_for_missing_and_expired_entries():
    from mcp_server import cache
    cache.clear()
    assert cache.expires_at(("missing",)) is None
    cache.set(("short",), "v", ttl=0.01)
    time.sleep(0.03)
    assert cache.expires_at(("short",)) is None
