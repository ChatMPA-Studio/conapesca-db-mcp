"""
Cache warm-up: pre-fills mcp_server.cache with the no-argument calls of the
near-static tools, so the first client after a deploy doesn't pay for them
(species_count alone takes ~80s on the full table).

start() is called once from mcp_server/__main__.py. It runs in a background
thread with its own event loop, so it never delays the server accepting
requests (the container health check keeps passing while it works).

It calls the real tools through fastmcp's in-memory Client, so what it caches
is exactly what a client call would cache (same keys, same payloads) — there
are no copies of the queries here to keep in sync with tools/*.py.

The calls run one after another, cheapest first, to keep the load on the DB
gentle while the previous task is still serving traffic during a rolling
deploy. A failure in one call is logged and does not stop the others.
"""

from __future__ import annotations
import asyncio
import logging
import threading
import time

logger = logging.getLogger("conapesca_mcp.warmup")

# Only the no-argument shapes. Variants keyed by arguments
# (get_estados(year=...), get_offices(estado=...)) aren't worth pre-computing.
WARM_CALLS: list[tuple[str, dict]] = [
    ("get_version", {}),
    ("get_offices", {}),
    ("get_estados", {}),
    ("schema_snapshot", {}),
    ("species_count", {}),
]


async def _warm_one(client, name: str, arguments: dict) -> None:
    t0 = time.monotonic()
    try:
        result = await client.call_tool(name, arguments, raise_on_error=False)
    except Exception as e:
        logger.warning("Cache warm-up %s failed: %s", name, e)
        return
    if result.is_error:
        logger.warning("Cache warm-up %s returned an error", name)
    else:
        logger.info("Cache warm-up %s done in %.1fs", name, time.monotonic() - t0)


async def run(mcp) -> None:
    """Warm every call in WARM_CALLS against `mcp`, in order."""
    from fastmcp import Client

    t0 = time.monotonic()
    async with Client(mcp) as client:
        for name, arguments in WARM_CALLS:
            await _warm_one(client, name, arguments)
    logger.info("Cache warm-up finished in %.1fs", time.monotonic() - t0)


def _thread_main(mcp) -> None:
    try:
        asyncio.run(run(mcp))
    except Exception as e:
        logger.warning("Cache warm-up aborted: %s", e)


def start(mcp) -> threading.Thread | None:
    """Start the warm-up in a daemon thread. Returns the thread, or None if
    CACHE_WARMUP is disabled."""
    from mcp_server import config

    if not config.CACHE_WARMUP:
        logger.info("Cache warm-up disabled (CACHE_WARMUP=false)")
        return None
    thread = threading.Thread(target=_thread_main, args=(mcp,), name="cache-warmup", daemon=True)
    thread.start()
    return thread
