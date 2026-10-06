"""
Tests for mcp_server/warmup.py, run against the fixture DB built in
tests/conftest.py. The warm-up goes through the real tools via fastmcp's
in-memory Client, so these also check that the keys it fills in the cache
are the ones the tools themselves read.
"""

from __future__ import annotations
import asyncio
import logging
import threading
import time

# Cache keys of the no-argument calls (see tools/data_access.py, mcp_server/server.py).
WARMED_KEYS = [
    ("get_version",),
    ("get_offices", None),
    ("get_estados", None),
    ("schema_snapshot",),
    ("species_count",),
]

ESTADOS_CALL = ("get_estados", {}, ("get_estados", None))


def _counting_db(monkeypatch, fail_first: int = 0, on_call=None):
    """Wrap tools.data_access.execute_select: count calls, optionally raise on
    the first `fail_first` of them, optionally run `on_call()` before each."""
    import tools.data_access as data_access
    original = data_access.execute_select
    state = {"calls": 0}

    def wrapper(*args, **kwargs):
        state["calls"] += 1
        if on_call:
            on_call()
        if state["calls"] <= fail_first:
            raise RuntimeError("simulated DB failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(data_access, "execute_select", wrapper)
    return state


def _drive_loop(mcp, seconds: float) -> None:
    """Run warmup.loop() for `seconds`, then cancel it."""
    from mcp_server import warmup

    async def _main():
        task = asyncio.create_task(warmup.loop(mcp))
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_main())


# ── one pass ------------------------------------------------------------------

def test_run_fills_cache_for_every_warm_call(mcp_app):
    from mcp_server import cache, warmup
    cache.clear()
    assert asyncio.run(warmup.run(mcp_app)) == []
    for key in WARMED_KEYS:
        assert cache.get(key) is not None, f"{key} was not cached"


def test_warm_calls_cover_exactly_the_expected_keys():
    # Guards WARMED_KEYS above against drifting from WARM_CALLS.
    from mcp_server import warmup
    assert [key for _, _, key in warmup.WARM_CALLS] == WARMED_KEYS


def test_warmed_result_is_served_from_cache(call_tool, mcp_app, monkeypatch):
    # The call_tool fixture has already cleared the cache by the time the test
    # body runs, so this warm-up is what fills it.
    from mcp_server import warmup
    import tools.data_access as data_access
    asyncio.run(warmup.run(mcp_app))

    def _db_must_not_be_hit(*args, **kwargs):
        raise AssertionError("DB was hit; expected a cache hit")
    monkeypatch.setattr(data_access, "execute_select", _db_must_not_be_hit)

    result = call_tool("get_estados")
    assert result["meta"]["count"] == 2  # SINALOA, SONORA


def test_a_failing_call_is_logged_reported_and_does_not_stop_the_rest(mcp_app, monkeypatch, caplog):
    from mcp_server import cache, warmup
    cache.clear()
    bad = ("no_such_tool", {}, ("no_such_tool",))
    monkeypatch.setattr(warmup, "WARM_CALLS", [bad, ESTADOS_CALL])
    with caplog.at_level(logging.WARNING, logger="conapesca_mcp.warmup"):
        failed = asyncio.run(warmup.run(mcp_app))  # must not raise
    assert failed == [bad]
    assert any("no_such_tool" in r.getMessage() for r in caplog.records)
    assert cache.get(("get_estados", None)) is not None


# ── refresh without a cold gap ---------------------------------------------------

def test_refresh_recomputes_and_extends_the_entry(mcp_app, monkeypatch):
    from mcp_server import cache, warmup
    cache.clear()
    asyncio.run(warmup.run(mcp_app, [ESTADOS_CALL]))
    first_deadline = cache.expires_at(("get_estados", None))
    state = _counting_db(monkeypatch)
    time.sleep(0.01)

    assert asyncio.run(warmup.run(mcp_app, [ESTADOS_CALL])) == []
    assert state["calls"] == 1  # went to the DB again instead of returning the cached value
    assert cache.expires_at(("get_estados", None)) > first_deadline


def test_clients_keep_reading_the_old_entry_while_it_is_recomputed(mcp_app, monkeypatch):
    from mcp_server import cache, warmup
    cache.clear()
    asyncio.run(warmup.run(mcp_app, [ESTADOS_CALL]))
    old_value = cache.get(("get_estados", None))
    assert old_value is not None

    seen: list = []

    def _read_from_another_thread_mid_refresh():
        t = threading.Thread(target=lambda: seen.append(cache.get(("get_estados", None))))
        t.start()
        t.join()

    _counting_db(monkeypatch, on_call=_read_from_another_thread_mid_refresh)
    asyncio.run(warmup.run(mcp_app, [ESTADOS_CALL]))
    assert seen == [old_value]  # no cold gap: a client thread still got a hit during the recompute


def test_a_failed_refresh_keeps_the_old_entry_and_is_reported(mcp_app, monkeypatch):
    from mcp_server import cache, warmup
    cache.clear()
    asyncio.run(warmup.run(mcp_app, [ESTADOS_CALL]))
    old_value = cache.get(("get_estados", None))
    _counting_db(monkeypatch, fail_first=1)

    assert asyncio.run(warmup.run(mcp_app, [ESTADOS_CALL])) == [ESTADOS_CALL]
    assert cache.get(("get_estados", None)) == old_value


# ── the loop -------------------------------------------------------------------

def test_loop_renews_the_entries_periodically(mcp_app, monkeypatch):
    from mcp_server import cache, warmup
    cache.clear()
    monkeypatch.setattr(warmup, "WARM_CALLS", [ESTADOS_CALL])
    monkeypatch.setattr(warmup, "_budget", lambda: 0.2)
    monkeypatch.setattr(warmup, "MIN_DELAY", 0.01)
    state = _counting_db(monkeypatch)

    _drive_loop(mcp_app, 1.5)
    assert state["calls"] >= 3  # startup pass + at least two renewals
    assert cache.get(("get_estados", None)) is not None


def test_loop_retries_only_what_failed_and_backs_off(mcp_app, monkeypatch):
    from mcp_server import cache, warmup
    cache.clear()
    monkeypatch.setattr(warmup, "WARM_CALLS", [ESTADOS_CALL])
    monkeypatch.setattr(warmup, "_budget", lambda: 100.0)  # no second full pass during the test
    monkeypatch.setattr(warmup, "RETRY_SECONDS", 0.05)
    monkeypatch.setattr(warmup, "MAX_RETRY_SECONDS", 0.2)
    state = _counting_db(monkeypatch, fail_first=2)

    _drive_loop(mcp_app, 1.5)
    assert state["calls"] == 3  # two failures, then the retry that stored the entry; nothing more
    assert cache.get(("get_estados", None)) is not None


def test_loop_never_retries_later_than_the_next_full_pass(mcp_app, monkeypatch):
    from mcp_server import warmup
    monkeypatch.setattr(warmup, "WARM_CALLS", [ESTADOS_CALL])
    monkeypatch.setattr(warmup, "_budget", lambda: 0.1)
    monkeypatch.setattr(warmup, "MIN_DELAY", 0.01)
    monkeypatch.setattr(warmup, "RETRY_SECONDS", 50.0)  # would be far later than the full pass
    state = _counting_db(monkeypatch, fail_first=1)

    _drive_loop(mcp_app, 1.0)
    assert state["calls"] >= 3  # the full passes (every 0.1s) kept happening despite the 50s retry wait


# ── start ----------------------------------------------------------------------

def test_start_runs_in_a_daemon_thread_and_fills_the_cache(mcp_app, monkeypatch):
    from mcp_server import cache, warmup
    cache.clear()
    monkeypatch.setattr(warmup, "_budget", lambda: 3600.0)  # the thread idles after its first pass
    thread = warmup.start(mcp_app)
    assert thread is not None
    assert thread.daemon is True

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and any(cache.get(k) is None for k in WARMED_KEYS):
        time.sleep(0.05)
    for key in WARMED_KEYS:
        assert cache.get(key) is not None, f"{key} was not cached"


def test_start_is_a_noop_when_disabled(mcp_app, monkeypatch):
    from mcp_server import cache, config, warmup
    cache.clear()
    monkeypatch.setattr(config, "CACHE_WARMUP", False)
    assert warmup.start(mcp_app) is None
    assert cache.get(("get_estados", None)) is None
