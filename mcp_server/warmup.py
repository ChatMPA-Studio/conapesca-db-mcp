"""
Cache warm-up and refresh-ahead for mcp_server.cache.

Pre-fills the cache with the no-argument calls of the near-static tools at
startup (species_count alone takes ~80s on the full table), and then renews
those entries before they expire, so a client never pays for a cold call.

start() is called once from mcp_server/__main__.py. It runs loop() in a
background daemon thread with its own event loop, so it never delays the
server accepting requests (the container health check keeps passing).

It calls the real tools through fastmcp's in-memory Client, so what it caches
is exactly what a client call would cache (same keys, same payloads) — there
are no copies of the queries here to keep in sync with tools/*.py.

How a renewal avoids a cold gap: each pass runs inside cache.refreshing(), so
the tool recomputes instead of returning its cached value, and cache.set()
then replaces the entry with a fresh TTL. Until that replacement, other
threads (the ones serving clients) keep reading the previous entry.

Schedule: a full pass at startup, then another REFRESH_FRACTION of the TTL
after the previous one started. Calls run one after another, cheapest first,
to keep the load on the DB gentle while a previous task may still be serving
traffic during a rolling deploy. A call that fails keeps its old entry and is
retried on its own with a growing wait (RETRY_SECONDS doubling up to
MAX_RETRY_SECONDS), never later than the next full pass.
"""

from __future__ import annotations
import asyncio
import logging
import threading
import time

from mcp_server import cache, config

logger = logging.getLogger("conapesca_mcp.warmup")

# (tool, arguments, cache key the tool stores its result under).
# Only the no-argument shapes: variants keyed by arguments
# (get_estados(year=...), get_offices(estado=...)) aren't worth pre-computing.
WARM_CALLS: list[tuple[str, dict, tuple]] = [
    ("get_version", {}, ("get_version",)),
    ("get_offices", {}, ("get_offices", None)),
    ("get_estados", {}, ("get_estados", None)),
    ("schema_snapshot", {}, ("schema_snapshot",)),
    ("species_count", {}, ("species_count",)),
]

REFRESH_FRACTION = 0.8      # renew when this share of the TTL has elapsed
RETRY_SECONDS = 60          # first wait before retrying a failed call
MAX_RETRY_SECONDS = 600     # cap of the doubling wait
MIN_DELAY = 1.0             # never spin: minimum sleep between passes


def _budget() -> float:
    """Seconds between the starts of two full passes."""
    return config.CACHE_TTL_SECONDS * REFRESH_FRACTION


async def _warm_one(client, name: str, arguments: dict, key: tuple) -> bool:
    """Call one tool; True if it stored a fresher entry under `key`."""
    t0 = time.monotonic()
    before = cache.expires_at(key)
    try:
        result = await client.call_tool(name, arguments, raise_on_error=False)
    except Exception as e:
        logger.warning("Cache warm-up %s failed: %s", name, e)
        return False
    renewed = (not result.is_error) and (cache.expires_at(key) or 0) > (before or 0)
    if renewed:
        logger.info("Cache warm-up %s done in %.1fs", name, time.monotonic() - t0)
    else:
        logger.warning("Cache warm-up %s did not store a fresh entry", name)
    return renewed


async def run(mcp, calls: list[tuple[str, dict, tuple]] | None = None) -> list[tuple[str, dict, tuple]]:
    """One pass over `calls` (default WARM_CALLS) in refresh mode. Returns the
    calls that did not renew their entry (empty list = all good)."""
    from fastmcp import Client

    calls = WARM_CALLS if calls is None else calls
    t0 = time.monotonic()
    failed: list[tuple[str, dict, tuple]] = []
    try:
        with cache.refreshing():
            async with Client(mcp) as client:
                for call in calls:
                    if not await _warm_one(client, *call):
                        failed.append(call)
    except Exception as e:
        logger.warning("Cache warm-up pass aborted: %s", e)
        failed = list(calls)
    logger.info("Cache warm-up pass finished in %.1fs (%d of %d failed)",
                time.monotonic() - t0, len(failed), len(calls))
    return failed


async def loop(mcp) -> None:
    """Warm at startup, then keep renewing for as long as the process lives."""
    calls = WARM_CALLS
    attempt = 0
    full_started = time.monotonic()
    while True:
        pass_started = time.monotonic()
        was_full = calls is WARM_CALLS
        failed = await run(mcp, calls)
        now = time.monotonic()

        if was_full and now - pass_started > _budget():
            logger.warning(
                "A full cache pass took %.0fs, longer than %.0f%% of CACHE_TTL_SECONDS "
                "(%.0fs): entries can expire before they are renewed. Raise CACHE_TTL_SECONDS.",
                now - pass_started, REFRESH_FRACTION * 100, config.CACHE_TTL_SECONDS,
            )

        delay = max(full_started + _budget() - now, MIN_DELAY)   # until the next full pass
        calls, next_is_full = WARM_CALLS, True
        if failed:
            attempt += 1
            retry_delay = min(RETRY_SECONDS * 2 ** (attempt - 1), MAX_RETRY_SECONDS)
            if retry_delay < delay:
                delay, calls, next_is_full = retry_delay, failed, False
                logger.warning("Retrying %d failed cache call(s) in %.0fs", len(failed), delay)
        if next_is_full:
            attempt = 0
            full_started = now + delay
            logger.info("Next full cache refresh in %.0fs", delay)
        await asyncio.sleep(delay)


def _thread_main(mcp) -> None:
    try:
        asyncio.run(loop(mcp))
    except Exception as e:
        logger.warning("Cache warm-up thread stopped: %s", e)


def start(mcp) -> threading.Thread | None:
    """Start warm-up + refresh-ahead in a daemon thread. Returns the thread,
    or None if CACHE_WARMUP is disabled."""
    if not config.CACHE_WARMUP:
        logger.info("Cache warm-up disabled (CACHE_WARMUP=false)")
        return None
    thread = threading.Thread(target=_thread_main, args=(mcp,), name="cache-warmup", daemon=True)
    thread.start()
    return thread
