"""
Shared test fixtures.

USE_SQLITE / SQLITE_PATH are set (and the fixture DB built) at MODULE level,
below — i.e. as soon as pytest imports this conftest.py, which happens
before any test module in this directory is collected. This has to happen
that early: mcp_server.config reads these env vars at import time, and other
test modules in this directory (e.g. one importing mcp_server.config at its
own module level) get collected — meaning imported — before any fixture
(even a session-scoped one) would get a chance to run. A fixture is too late
for that; only conftest.py's own module-level code is guaranteed to run
first.

Given that, no test module here may import mcp_server.* / tools.* at module
level itself before this file's setup has run — which, for anything living
in tests/, is automatically true since conftest.py is always imported first.
Still, prefer importing them locally inside test functions/fixtures (as this
file and tests/test_data_access.py / tests/test_reporting.py do) rather than
relying on that ordering guarantee.

tests/test_data_access.py and tests/test_reporting.py are plain sync
functions that call `asyncio.run(...)` themselves (via the `call_tool`
fixture below) rather than using pytest-asyncio.
"""

from __future__ import annotations
import asyncio
import json
import os
import sqlite3
import tempfile

import pytest

# Column order mirrors the real conapesca_landings_historical table (the
# subset of columns the tools under test actually SELECT/GROUP BY on).
COLUMNS = [
    "anio_corte", "fecha_aviso", "tipo_aviso", "folio_aviso",
    "nombre_estado", "nombre_oficina", "nombre_sitio_desembarque", "unidad_economica",
    "nombre_especie", "nombre_cientifico",
    "peso_desembarcado_kg", "valor_pesos_estimado", "tipo_pesca_canonico", "litoral",
    "dias_efectivos", "dias_efectivos_fuente",
    "flag_fecha_generica", "flag_dias_efectivos_sospechoso", "flag_periodo_futuro",
    "genus", "family", "order", "class", "phylum", "kingdom",
    "worms_id", "spec_code_fishbase", "fishbase_database",
    "k", "loo", "lmax", "tmax", "wmax", "trophic_level",
]

# Six rows, deliberately covering (see tests/*.py docstrings for the exact
# arithmetic derived from this fixture):
#   - 2 years (2020, 2021), 2 estados (SINALOA, SONORA), 3 tipo_aviso
#     (MENORES, MAYORES, COSECHA), 2 oficinas, 2 litorales
#   - a folio with >1 species line: F1 (rows A, B) and F2 (rows C, D)
#   - nombre_cientifico = 'ND' (row B)
#   - two-word (species-level) nombre_cientifico (rows A, C)
#   - a genus-level, family-level and unclassified single-word
#     nombre_cientifico (rows E, D, F respectively) for species_count()
ROWS = [
    # A: SINALOA/MENORES/2020, folio F1, species-level
    (2020, "2020-03-15", "MENORES", "F1", "SINALOA", "OFICINA NORTE", "SITIO A", "COOP1",
     "CAMARON", "Litopenaeus vannamei", 100.0, 5000.0, "ARTESANAL", "PACIFICO",
     5, "bitacora", 0, 0, 0,
     "Litopenaeus", "Penaeidae", "Decapoda", "Malacostraca", "Arthropoda", "Animalia",
     1, 1, "fb", 0.1, 20, 25, 5, 500, 3.2),
    # B: same folio F1 as A (2nd species line), nombre_cientifico = ND
    (2020, "2020-03-16", "MENORES", "F1", "SINALOA", "OFICINA NORTE", "SITIO A", "COOP1",
     "JAIBA", "ND", 50.0, 1000.0, "ARTESANAL", "PACIFICO",
     5, "bitacora", 0, 0, 0,
     None, None, None, None, None, None,
     None, None, None, None, None, None, None, None, None),
    # C: SONORA/MAYORES/2021, folio F2, species-level
    (2021, "2021-07-01", "MAYORES", "F2", "SONORA", "OFICINA SUR", "SITIO B", "COOP2",
     "ATUN", "Thunnus albacares", 200.0, 20000.0, "INDUSTRIAL", "PACIFICO",
     10, "bitacora", 0, 0, 0,
     "Thunnus", "Scombridae", "Perciformes", "Actinopterygii", "Chordata", "Animalia",
     2, 2, "fb", 0.2, 150, 200, 10, 50000, 4.0),
    # D: same folio F2 as C (2nd species line), family-level nombre_cientifico
    # (genus column is None so classification falls through to family)
    (2021, "2021-07-02", "MAYORES", "F2", "SONORA", "OFICINA SUR", "SITIO B", "COOP2",
     "CAMARON ROSADO", "Penaeidae", 20.0, 800.0, "INDUSTRIAL", "PACIFICO",
     10, "bitacora", 0, 0, 0,
     None, "Penaeidae", "Decapoda", "Malacostraca", "Arthropoda", "Animalia",
     3, 3, "fb", 0.12, 18, 22, 4, 400, 3.0),
    # E: SONORA/COSECHA/2021, folio F3, genus-level nombre_cientifico
    (2021, "2021-08-01", "COSECHA", "F3", "SONORA", "OFICINA SUR", "SITIO C", "COOP3",
     "MOJARRA", "Oreochromis", 30.0, 900.0, "ACUACULTURA", "GOLFO",
     None, None, 1, 0, 0,
     "Oreochromis", "Cichlidae", "Cichliformes", "Actinopterygii", "Chordata", "Animalia",
     4, 4, "fb", 0.15, 30, 35, 6, 800, 2.9),
    # F: SINALOA/MENORES/2020, folio F4 (single-line trip), unclassified
    # nombre_cientifico (doesn't match any of its own taxonomy columns)
    (2020, "2020-01-10", "MENORES", "F4", "SINALOA", "OFICINA NORTE", "SITIO A", "COOP1",
     "PULPO", "Mysteryus", 15.0, 450.0, "ARTESANAL", "PACIFICO",
     2, "bitacora", 0, 0, 0,
     "Octopus", "Octopodidae", "Octopoda", "Cephalopoda", "Mollusca", "Animalia",
     5, 5, "fb", 0.05, 10, 12, 3, 100, 2.5),
]


def _build_sqlite_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        col_defs = ", ".join(f'"{c}"' for c in COLUMNS)
        conn.execute(f"CREATE TABLE conapesca_landings_historical ({col_defs})")
        placeholders = ", ".join("?" for _ in COLUMNS)
        conn.executemany(
            f"INSERT INTO conapesca_landings_historical ({col_defs}) VALUES ({placeholders})",
            ROWS,
        )
        conn.commit()
    finally:
        conn.close()


# ── Module-level setup (runs once, at conftest import time — see the module
# docstring for why this can't be deferred into a fixture) ------------------

_TEST_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="conapesca_test_"), "test.sqlite")
_build_sqlite_db(_TEST_DB_PATH)

os.environ["USE_SQLITE"] = "true"
os.environ["SQLITE_PATH"] = _TEST_DB_PATH


@pytest.fixture(scope="session")
def mcp_app():
    """The real FastMCP server object, built against the fixture DB above.
    Session-scoped: mcp_server.server (and everything it imports) is only
    ever imported once per process, since config.py/db.py freeze USE_SQLITE
    / SQLITE_PATH at import time — re-importing later with different env
    vars would have no effect anyway.
    """
    import mcp_server.server as server_module
    return server_module.mcp


async def _invoke(mcp_app, name: str, arguments: dict | None):
    from fastmcp import Client
    async with Client(mcp_app) as client:
        result = await client.call_tool(name, arguments or {})
        return json.loads(result.data)


@pytest.fixture()
def call_tool(mcp_app):
    """Sync helper: call_tool("get_estados", {"year": 2020}) -> dict.

    Runs the async fastmcp Client call via asyncio.run(), so tests stay
    plain `def test_...():` functions (no pytest-asyncio dependency).

    Also clears mcp_server.cache (a process-global in-memory store keyed by
    (tool_name, *args)) before each test that uses this fixture, so a cached
    result from one test can't be mistaken for fresh behaviour in another —
    safe to clear unconditionally since the fixture DB never changes across
    the session. Scoped to `call_tool` (rather than an autouse fixture) so it
    only affects tests in this file/tests/test_reporting.py, not unrelated
    test modules elsewhere in tests/.
    """
    from mcp_server import cache
    cache.clear()

    def _call(name: str, arguments: dict | None = None):
        return asyncio.run(_invoke(mcp_app, name, arguments))
    return _call
