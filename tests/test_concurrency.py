"""
Regression test for the production bug this pass fixed: a slow query used to
run synchronously on the single asyncio event loop and freeze every other
concurrent MCP tool call for its whole duration. `async def` + `await
asyncio.to_thread(...)` moves the blocking DB work to a worker thread so the
loop stays free.

This test proves that concretely: one tool call is made artificially slow,
a second unrelated tool call is issued at the same time, and each call's own
completion time is measured — the fast call must finish quickly on its own,
regardless of how long the slow call takes, rather than waiting behind it.

Uses the shared session-scoped `mcp_app` fixture from conftest.py (the same
one test_data_access.py / test_reporting.py use) instead of building a
separate server instance — a previous version of this file force-reimported
mcp_server.server/.config/.db per test via sys.modules eviction, which left
those modules permanently missing from sys.modules after teardown and broke
tests/test_db_pool.py's own module-level imports when both files ran in the
same pytest process.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastmcp import Client

SLOW_DELAY = 0.3
# How long the FAST call is allowed to take even while the slow call is
# still in flight. Nowhere near SLOW_DELAY: if the event loop were blocked
# by the slow call, the fast call couldn't start until the slow one
# finishes, so it would take close to SLOW_DELAY too instead of ~0ms.
FAST_BUDGET = 0.15
# Two slow calls, offloaded to separate to_thread workers, should overlap
# and finish in ~1x SLOW_DELAY; serialized on the event loop they'd cost
# ~2x. This budget sits well below the 2x mark.
CONCURRENT_BUDGET = SLOW_DELAY + 0.25


@pytest.fixture(autouse=True)
def _disable_cache(monkeypatch):
    """get_estados (used as the 'slow' call below) is cache-wrapped
    (mcp_server/cache.py). A cache hit would let a call return without ever
    reaching the monkeypatched, artificially-slow execute_select — silently
    masking a regression back to blocking synchronous tool execution (the
    first of two gathered calls would run to completion, including
    cache.set(), before the scheduler ever lets the second one start, so it
    would find a warm cache and return instantly regardless of whether
    asyncio.to_thread is actually being used). Force every call in this file
    to miss the cache so it always re-enters the real (patched) query path."""
    from mcp_server import cache
    monkeypatch.setattr(cache, "get", lambda key: None)


async def _timed(coro):
    start = time.perf_counter()
    result = await coro
    return result, time.perf_counter() - start


@pytest.mark.asyncio
async def test_slow_tool_does_not_block_concurrent_fast_tool(mcp_app, monkeypatch):
    """get_estados() is made slow via tools.data_access.execute_select;
    health_check() is unrelated (mcp_server.db.test_connection ->
    execute_raw, a different function) and stays fast. Both are issued via
    asyncio.gather; each call's own elapsed time is measured separately so
    a slow call dominating the *combined* wall time can't hide the fast
    call actually being stuck behind it."""
    import tools.data_access as data_access_module

    def slow_execute_select(sql, params=None, max_rows=5000):
        time.sleep(SLOW_DELAY)  # real, synchronous, blocking sleep
        return [{"nombre_estado": "SONORA"}]

    monkeypatch.setattr(data_access_module, "execute_select", slow_execute_select)

    async with Client(mcp_app) as client:
        (slow_result, slow_elapsed), (fast_result, fast_elapsed) = await asyncio.gather(
            _timed(client.call_tool("get_estados", {})),
            _timed(client.call_tool("health_check", {})),
        )

    assert not slow_result.is_error
    assert not fast_result.is_error
    assert slow_elapsed >= SLOW_DELAY, (
        f"sanity check failed: the 'slow' call only took {slow_elapsed:.3f}s, "
        f"expected >= {SLOW_DELAY:.1f}s — the monkeypatch may not be active."
    )
    assert fast_elapsed < FAST_BUDGET, (
        f"health_check() took {fast_elapsed:.3f}s to complete on its own while "
        f"get_estados() (slow, {slow_elapsed:.3f}s) was still in flight; expected "
        f"under {FAST_BUDGET:.2f}s. A regression here means the slow tool's DB "
        "call is once again blocking the event loop instead of running in a "
        "worker thread — the fast call had to wait its turn instead of running "
        "concurrently."
    )


@pytest.mark.asyncio
async def test_two_slow_calls_run_concurrently_not_serially(mcp_app, monkeypatch):
    """Same idea with two identically-slow calls to the same tool: if they
    ran on the event loop directly they would serialize to ~2x SLOW_DELAY;
    offloaded to separate to_thread workers they overlap and finish in
    ~1x SLOW_DELAY."""
    import tools.data_access as data_access_module

    def slow_execute_select(sql, params=None, max_rows=5000):
        time.sleep(SLOW_DELAY)
        return [{"nombre_estado": "SONORA"}]

    monkeypatch.setattr(data_access_module, "execute_select", slow_execute_select)

    async with Client(mcp_app) as client:
        start = time.perf_counter()
        results = await asyncio.gather(
            client.call_tool("get_estados", {}),
            client.call_tool("get_estados", {}),
        )
        elapsed = time.perf_counter() - start

    assert all(not r.is_error for r in results)
    assert elapsed < CONCURRENT_BUDGET, (
        f"two concurrent get_estados() calls took {elapsed:.3f}s; expected "
        f"close to {SLOW_DELAY:.1f}s (overlapping worker threads), not "
        f"~{SLOW_DELAY * 2:.1f}s (serialized on the event loop)."
    )
