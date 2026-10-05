"""
Tests for tools/reporting.py, against the same fixture DB as
tests/test_data_access.py (see tests/conftest.py:ROWS). Recap:

  A: 2020 SINALOA MENORES  CAMARON        / Litopenaeus vannamei  kg=100 val=5000
  B: 2020 SINALOA MENORES  JAIBA          / ND                    kg=50  val=1000
  C: 2021 SONORA  MAYORES  ATUN           / Thunnus albacares     kg=200 val=20000
  D: 2021 SONORA  MAYORES  CAMARON ROSADO / Penaeidae             kg=20  val=800
  E: 2021 SONORA  COSECHA  MOJARRA        / Oreochromis           kg=30  val=900
  F: 2020 SINALOA MENORES  PULPO          / Mysteryus             kg=15  val=450

Each tool's n_especies / n_recursos come from an inline SQL CASE that
re-implements the "two-word, non-ND/empty nombre_cientifico" test
(tools/data_access._tipo does the same classification in Python, but these
tools do NOT call it — the SQL is a separate, duplicated implementation).
Per fixture: A and C are species-level (n_especies); B, D and F are
recursos (ND, family-level Penaeidae, and unclassified Mysteryus are all
single-word-or-ND, so all count as recursos); E (Oreochromis, genus-level,
single word) is also a recurso by this same rule.
"""

from __future__ import annotations
import threading


# ── _json (duplicated helper, identical to data_access._json) -----------------

def test_json_converts_decimal_to_float(mcp_app):
    import json
    from decimal import Decimal
    from tools.reporting import _json
    encoded = _json({"total_kg": Decimal("42.0")})
    assert json.loads(encoded) == {"total_kg": 42.0}


def test_json_raises_typeerror_for_unsupported_types(mcp_app):
    from tools.reporting import _json
    try:
        _json({"bad": object()})
    except TypeError:
        pass
    else:
        raise AssertionError("_json should have raised TypeError")


# ── landings_by_year ------------------------------------------------------------

def test_landings_by_year_default(call_tool):
    data = call_tool("landings_by_year")
    by_year = {r["anio_corte"]: r for r in data["annual_landings"]}

    assert by_year[2020]["total_kg"] == 165.0       # A(100)+B(50)+F(15)
    assert by_year[2020]["total_valor_mxn"] == 6450.0
    assert by_year[2020]["n_records"] == 3
    assert by_year[2020]["n_especies"] == 1          # Litopenaeus vannamei
    assert by_year[2020]["n_recursos"] == 2           # JAIBA, PULPO

    assert by_year[2021]["total_kg"] == 250.0        # C(200)+D(20)+E(30)
    assert by_year[2021]["n_especies"] == 1           # Thunnus albacares
    assert by_year[2021]["n_recursos"] == 2           # CAMARON ROSADO, MOJARRA

    assert data["meta"]["filters"] == {"estado": None, "tipo_aviso": None}


def test_landings_by_year_filtered_by_estado(call_tool):
    data = call_tool("landings_by_year", {"estado": "sinaloa"})
    assert len(data["annual_landings"]) == 1
    row = data["annual_landings"][0]
    assert row["anio_corte"] == 2020
    assert row["total_kg"] == 165.0
    assert data["meta"]["filters"]["estado"] == "sinaloa"  # echoed verbatim, not upper()'d


def test_landings_by_year_filtered_by_tipo_aviso(call_tool):
    data = call_tool("landings_by_year", {"tipo_aviso": "MAYORES"})
    assert len(data["annual_landings"]) == 1
    row = data["annual_landings"][0]
    assert row["anio_corte"] == 2021
    assert row["total_kg"] == 220.0  # C(200)+D(20)
    assert row["n_especies"] == 1
    assert row["n_recursos"] == 1


def test_landings_by_year_combined_filters_no_match(call_tool):
    data = call_tool("landings_by_year", {"estado": "SINALOA", "tipo_aviso": "COSECHA"})
    assert data["annual_landings"] == []


def test_landings_by_year_offloads_db_call_to_worker_thread(call_tool, mcp_app, monkeypatch):
    import tools.reporting as reporting
    main_thread_id = threading.get_ident()
    seen_thread_ids = []
    original = reporting.execute_select

    def spy(*args, **kwargs):
        seen_thread_ids.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(reporting, "execute_select", spy)
    call_tool("landings_by_year")

    assert seen_thread_ids, "execute_select was never called"
    assert seen_thread_ids[0] != main_thread_id


# ── landings_by_estado -----------------------------------------------------------

def test_landings_by_estado_default_orders_by_kg_desc(call_tool):
    data = call_tool("landings_by_estado")
    assert [r["nombre_estado"] for r in data["by_estado"]] == ["SONORA", "SINALOA"]
    sonora, sinaloa = data["by_estado"]
    assert sonora["total_kg"] == 250.0
    assert sonora["n_especies"] == 1
    assert sonora["n_recursos"] == 2
    assert sinaloa["total_kg"] == 165.0
    assert sinaloa["n_especies"] == 1
    assert sinaloa["n_recursos"] == 2
    assert data["meta"]["filters"] == {"year": None, "tipo_aviso": None}


def test_landings_by_estado_filtered_by_year(call_tool):
    data = call_tool("landings_by_estado", {"year": 2021})
    assert len(data["by_estado"]) == 1
    assert data["by_estado"][0]["nombre_estado"] == "SONORA"
    assert data["by_estado"][0]["total_kg"] == 250.0


def test_landings_by_estado_filtered_by_tipo_aviso(call_tool):
    data = call_tool("landings_by_estado", {"tipo_aviso": "MENORES"})
    assert len(data["by_estado"]) == 1
    assert data["by_estado"][0]["nombre_estado"] == "SINALOA"
    assert data["by_estado"][0]["total_kg"] == 165.0


def test_landings_by_estado_no_matches(call_tool):
    data = call_tool("landings_by_estado", {"year": 1999})
    assert data["by_estado"] == []


# ── landings_by_fleet_type --------------------------------------------------------

def test_landings_by_fleet_type_default_orders_by_kg_desc(call_tool):
    data = call_tool("landings_by_fleet_type")
    fleet = {r["tipo_aviso"]: r for r in data["by_fleet_type"]}
    assert [r["tipo_aviso"] for r in data["by_fleet_type"]] == ["MAYORES", "MENORES", "COSECHA"]
    assert fleet["MAYORES"]["total_kg"] == 220.0
    assert fleet["MENORES"]["total_kg"] == 165.0
    assert fleet["COSECHA"]["total_kg"] == 30.0
    assert data["meta"] == {"year": None}


def test_landings_by_fleet_type_filtered_by_year(call_tool):
    data = call_tool("landings_by_fleet_type", {"year": 2020})
    assert len(data["by_fleet_type"]) == 1
    assert data["by_fleet_type"][0]["tipo_aviso"] == "MENORES"
    assert data["meta"] == {"year": 2020}


def test_landings_by_fleet_type_no_matches(call_tool):
    data = call_tool("landings_by_fleet_type", {"year": 1999})
    assert data["by_fleet_type"] == []


def test_landings_by_fleet_type_offloads_db_call_to_worker_thread(call_tool, mcp_app, monkeypatch):
    import tools.reporting as reporting
    main_thread_id = threading.get_ident()
    seen_thread_ids = []
    original = reporting.execute_select

    def spy(*args, **kwargs):
        seen_thread_ids.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(reporting, "execute_select", spy)
    call_tool("landings_by_fleet_type")

    assert seen_thread_ids
    assert seen_thread_ids[0] != main_thread_id
