"""
Unit tests for mcp_server/db.py's MySQL connection-pool lazy singleton.

No real MySQL server is available in this environment, so pymysql and
dbutils.pooled_db are faked at the module boundary that mcp_server.db
imports them through (`_get_pool()` does `import pymysql` and
`from dbutils.pooled_db import PooledDB` lazily, inside the function body).
Replacing those entries in sys.modules lets the real `_get_pool()` /
`get_connection()` code run unmodified against fakes we can inspect.
"""

from __future__ import annotations

import sys
import threading
import types

import pytest

import mcp_server.config as config_module
import mcp_server.db as db_module


class _FakePooledDB:
    """Stand-in for dbutils.pooled_db.PooledDB: records constructor kwargs
    instead of opening any real connections, and hands back a shared fake
    'connection' object from .connection()."""

    instances: list["_FakePooledDB"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakePooledDB.instances.append(self)

    def connection(self):
        return object()


def _install_fake_pymysql_and_dbutils(monkeypatch):
    """Patch sys.modules so `import pymysql` / `from dbutils.pooled_db import
    PooledDB` (both executed lazily inside _get_pool) resolve to fakes,
    regardless of whether the real packages are installed."""
    fake_pymysql = types.SimpleNamespace(
        connect=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("pymysql.connect() should never be called directly; "
                            "PooledDB construction is faked entirely")
        ),
        cursors=types.SimpleNamespace(DictCursor=object()),
    )
    monkeypatch.setitem(sys.modules, "pymysql", fake_pymysql)

    fake_pooled_db_module = types.SimpleNamespace(PooledDB=_FakePooledDB)
    fake_dbutils_pkg = types.SimpleNamespace(pooled_db=fake_pooled_db_module)
    monkeypatch.setitem(sys.modules, "dbutils", fake_dbutils_pkg)
    monkeypatch.setitem(sys.modules, "dbutils.pooled_db", fake_pooled_db_module)


def _set_mysql_config(monkeypatch, *, pool_size=5, pool_overflow=5, connect_timeout=60, read_timeout=110):
    """mcp_server.config only defines DB_HOST/DB_POOL_SIZE/etc. when it was
    imported with USE_SQLITE=false; raising=False lets us inject them
    regardless of which mode the module happened to load under in this
    process (test files may run in either order)."""
    monkeypatch.setattr(db_module, "USE_SQLITE", False)
    monkeypatch.setattr(config_module, "DB_HOST", "fake-host", raising=False)
    monkeypatch.setattr(config_module, "DB_PORT", 3306, raising=False)
    monkeypatch.setattr(config_module, "DB_USER", "fake-user", raising=False)
    monkeypatch.setattr(config_module, "DB_PASSWORD", "fake-pass", raising=False)
    monkeypatch.setattr(config_module, "DB_NAME", "fake-db", raising=False)
    monkeypatch.setattr(config_module, "DB_POOL_SIZE", pool_size, raising=False)
    monkeypatch.setattr(config_module, "DB_POOL_MAX_OVERFLOW", pool_overflow, raising=False)
    monkeypatch.setattr(config_module, "DB_CONNECT_TIMEOUT_SECONDS", connect_timeout, raising=False)
    monkeypatch.setattr(config_module, "DB_READ_TIMEOUT_SECONDS", read_timeout, raising=False)


@pytest.fixture(autouse=True)
def _reset_pool_singleton(monkeypatch):
    """Every test starts from a cold (unbuilt) pool, and the mutation is
    undone by monkeypatch even though `_pool` is a plain module global."""
    monkeypatch.setattr(db_module, "_pool", None)
    _FakePooledDB.instances.clear()
    yield


def test_get_pool_is_a_singleton_not_rebuilt_per_call(monkeypatch):
    _set_mysql_config(monkeypatch)
    _install_fake_pymysql_and_dbutils(monkeypatch)

    pool1 = db_module._get_pool()
    pool2 = db_module._get_pool()
    pool3 = db_module._get_pool()

    assert pool1 is pool2 is pool3
    # The whole point of pooling: PooledDB is constructed once, not per query.
    assert len(_FakePooledDB.instances) == 1


def test_get_connection_mysql_mode_reuses_pool(monkeypatch):
    """get_connection() -> _mysql_connect() must borrow from the same pool
    on repeated calls instead of building a fresh PooledDB each time."""
    _set_mysql_config(monkeypatch)
    _install_fake_pymysql_and_dbutils(monkeypatch)

    db_module.get_connection()
    db_module.get_connection()
    db_module.get_connection()

    assert len(_FakePooledDB.instances) == 1


def test_pool_constructed_with_configured_size_and_overflow(monkeypatch):
    _set_mysql_config(monkeypatch, pool_size=7, pool_overflow=3)
    _install_fake_pymysql_and_dbutils(monkeypatch)

    pool = db_module._get_pool()

    assert pool.kwargs["maxcached"] == 7
    assert pool.kwargs["maxconnections"] == 7 + 3
    assert pool.kwargs["mincached"] == 1
    assert pool.kwargs["blocking"] is True
    assert pool.kwargs["host"] == "fake-host"
    assert pool.kwargs["database"] == "fake-db"


def test_pool_sizing_changes_when_config_changes(monkeypatch):
    """Different DB_POOL_SIZE/DB_POOL_MAX_OVERFLOW values must actually flow
    through to the PooledDB kwargs (i.e. they aren't hardcoded)."""
    _set_mysql_config(monkeypatch, pool_size=2, pool_overflow=8)
    _install_fake_pymysql_and_dbutils(monkeypatch)

    pool = db_module._get_pool()

    assert pool.kwargs["maxcached"] == 2
    assert pool.kwargs["maxconnections"] == 10


def test_pool_uses_the_configured_read_and_connect_timeouts(monkeypatch):
    """The read timeout is what cut species_count's first query at 60s: it must
    come from config, and the connect timeout must stay a separate setting."""
    _set_mysql_config(monkeypatch, connect_timeout=7, read_timeout=99)
    _install_fake_pymysql_and_dbutils(monkeypatch)

    pool = db_module._get_pool()

    assert pool.kwargs["read_timeout"] == 99
    assert pool.kwargs["connect_timeout"] == 7


def _config_values(env_extra: dict) -> list[float]:
    """Import mcp_server.config in a clean process (MySQL mode, no .env override)
    and print the two timeouts."""
    import os
    import subprocess
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("DB_", "CONAPESCA_DB_", "DATABASE_URL"))}
    env.update({"USE_SQLITE": "false", "CONAPESCA_DB_HOST": "h", "CONAPESCA_DB_USER": "u",
                "CONAPESCA_DB_PASSWORD": "p", "CONAPESCA_DB_NAME": "n"})
    env.update(env_extra)
    out = subprocess.run(
        [sys.executable, "-c",
         "from mcp_server import config as c; print(c.DB_CONNECT_TIMEOUT_SECONDS, c.DB_READ_TIMEOUT_SECONDS)"],
        capture_output=True, text=True, env=env, check=True,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
    ).stdout.split()
    return [float(x) for x in out]


def test_default_timeouts_are_60s_to_connect_and_110s_to_read():
    assert _config_values({}) == [60.0, 110.0]


def test_timeouts_can_be_overridden_by_environment():
    assert _config_values({"DB_READ_TIMEOUT_SECONDS": "45", "DB_CONNECT_TIMEOUT_SECONDS": "5"}) == [5.0, 45.0]


def test_concurrent_first_calls_build_pool_exactly_once(monkeypatch):
    """Tool calls now run inside asyncio.to_thread worker threads, so
    concurrent *first* calls genuinely race on _get_pool(). The
    double-checked lock must still only construct one PooledDB and hand
    every thread the same instance."""
    _set_mysql_config(monkeypatch)
    _install_fake_pymysql_and_dbutils(monkeypatch)

    n_threads = 16
    barrier = threading.Barrier(n_threads)
    results: list = [None] * n_threads

    def worker(i):
        barrier.wait()  # maximize the race at the exact _get_pool() call
        results[i] = db_module._get_pool()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(_FakePooledDB.instances) == 1
    assert all(r is results[0] for r in results)


def test_sqlite_mode_never_touches_pool_machinery(monkeypatch, tmp_path):
    """USE_SQLITE=true must take the sqlite3.connect() path unconditionally
    and never build (or even attempt to build) the MySQL pool."""
    monkeypatch.setattr(db_module, "USE_SQLITE", True)
    monkeypatch.setattr(db_module, "SQLITE_PATH", str(tmp_path / "dev.sqlite"))

    def _boom():
        raise AssertionError("_get_pool() must not be called in SQLite mode")

    monkeypatch.setattr(db_module, "_get_pool", _boom)

    conn = db_module.get_connection()
    try:
        assert db_module._pool is None
    finally:
        conn.close()


def test_sqlite_mode_leaves_pool_none_across_repeated_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(db_module, "USE_SQLITE", True)
    monkeypatch.setattr(db_module, "SQLITE_PATH", str(tmp_path / "dev.sqlite"))

    for _ in range(3):
        conn = db_module.get_connection()
        conn.close()

    assert db_module._pool is None
