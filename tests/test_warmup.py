"""
Tests for mcp_server/warmup.py, run against the fixture DB built in
tests/conftest.py. The warm-up goes through the real tools via fastmcp's
in-memory Client, so these also check that the keys it fills in the cache
are the ones the tools themselves read.
"""

from __future__ import annotations
import asyncio
import logging

# Cache keys of the no-argument calls (see tools/data_access.py, mcp_server/server.py).
WARMED_KEYS = [
    ("get_version",),
    ("get_offices", None),
    ("get_estados", None),
    ("schema_snapshot",),
    ("species_count",),
]


def test_run_fills_cache_for_every_warm_call(mcp_app):
    from mcp_server import cache, warmup
    cache.clear()
    asyncio.run(warmup.run(mcp_app))
    for key in WARMED_KEYS:
        assert cache.get(key) is not None, f"{key} was not cached"


def test_warm_calls_cover_exactly_the_expected_keys():
    # Guards WARMED_KEYS above against drifting from WARM_CALLS.
    from mcp_server import warmup
    assert [name for name, _ in warmup.WARM_CALLS] == [k[0] for k in WARMED_KEYS]


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


def test_a_failing_call_is_logged_and_does_not_stop_the_rest(mcp_app, monkeypatch, caplog):
    from mcp_server import cache, warmup
    cache.clear()
    monkeypatch.setattr(warmup, "WARM_CALLS", [("no_such_tool", {}), ("get_estados", {})])
    with caplog.at_level(logging.WARNING, logger="conapesca_mcp.warmup"):
        asyncio.run(warmup.run(mcp_app))  # must not raise
    assert any("no_such_tool" in r.getMessage() for r in caplog.records)
    assert cache.get(("get_estados", None)) is not None


def test_start_runs_in_a_daemon_thread(mcp_app):
    from mcp_server import cache, warmup
    cache.clear()
    thread = warmup.start(mcp_app)
    assert thread is not None
    assert thread.daemon is True
    thread.join(timeout=30)
    assert not thread.is_alive()
    for key in WARMED_KEYS:
        assert cache.get(key) is not None


def test_start_is_a_noop_when_disabled(mcp_app, monkeypatch):
    from mcp_server import cache, config, warmup
    cache.clear()
    monkeypatch.setattr(config, "CACHE_WARMUP", False)
    assert warmup.start(mcp_app) is None
    assert cache.get(("get_estados", None)) is None
