"""
Tests for tools/data_access.py, run against the fixture DB built in
tests/conftest.py (see ROWS there for the raw data). All expected numbers
below are hand-derived from that fixture:

  A: 2020 SINALOA MENORES  folio F1  CAMARON        / Litopenaeus vannamei  kg=100 val=5000
  B: 2020 SINALOA MENORES  folio F1  JAIBA          / ND                    kg=50  val=1000
  C: 2021 SONORA  MAYORES  folio F2  ATUN           / Thunnus albacares     kg=200 val=20000
  D: 2021 SONORA  MAYORES  folio F2  CAMARON ROSADO / Penaeidae             kg=20  val=800
  E: 2021 SONORA  COSECHA  folio F3  MOJARRA        / Oreochromis           kg=30  val=900
  F: 2020 SINALOA MENORES  folio F4  PULPO          / Mysteryus             kg=15  val=450

Every tool is `async def` and offloads its DB call via asyncio.to_thread;
tests call through the real fastmcp in-memory Client (see call_tool fixture)
so they exercise that wiring, not just the private _xxx_sync functions.
"""

from __future__ import annotations
import threading


# ── _json / _tipo (pure helpers, no DB) --------------------------------------

def test_tipo_classifies_none_and_empty_as_recurso(mcp_app):
    from tools.data_access import _tipo
    assert _tipo(None) == "recurso"
    assert _tipo("") == "recurso"
    assert _tipo("   ") == "recurso"


def test_tipo_classifies_nd_case_insensitively_as_recurso(mcp_app):
    from tools.data_access import _tipo
    assert _tipo("ND") == "recurso"
    assert _tipo("nd") == "recurso"
    assert _tipo("  nD  ") == "recurso"


def test_tipo_classifies_single_word_non_nd_as_recurso(mcp_app):
    from tools.data_access import _tipo
    assert _tipo("Oreochromis") == "recurso"


def test_tipo_classifies_two_word_name_as_especie(mcp_app):
    from tools.data_access import _tipo
    assert _tipo("Thunnus albacares") == "especie"
    assert _tipo("  Thunnus albacares  ") == "especie"


def test_json_converts_decimal_to_float(mcp_app):
    import json
    from decimal import Decimal
    from tools.data_access import _json
    encoded = _json({"total_kg": Decimal("12.5"), "n": None})
    assert json.loads(encoded) == {"total_kg": 12.5, "n": None}


def test_json_raises_typeerror_for_unsupported_types(mcp_app):
    from tools.data_access import _json
    try:
        _json({"bad": {1, 2, 3}})
    except TypeError:
        pass
    else:
        raise AssertionError("_json should have raised TypeError for a set")


# ── get_estados ---------------------------------------------------------------

def test_get_estados_default_lists_all_states_sorted(call_tool):
    data = call_tool("get_estados")
    assert data["estados"] == ["SINALOA", "SONORA"]
    assert data["meta"] == {"count": 2, "year": None}


def test_get_estados_filtered_by_year(call_tool):
    data = call_tool("get_estados", {"year": 2020})
    assert data["estados"] == ["SINALOA"]
    assert data["meta"] == {"count": 1, "year": 2020}


def test_get_estados_year_with_no_matches(call_tool):
    data = call_tool("get_estados", {"year": 1999})
    assert data["estados"] == []
    assert data["meta"] == {"count": 0, "year": 1999}


def test_get_estados_different_years_are_not_confused_by_cache(call_tool):
    # get_estados is cache.get/set-wrapped, keyed on (tool_name, year) — this
    # guards against a cache key that forgets to include the year filter.
    data_2020 = call_tool("get_estados", {"year": 2020})
    data_2021 = call_tool("get_estados", {"year": 2021})
    assert data_2020["estados"] == ["SINALOA"]
    assert data_2021["estados"] == ["SONORA"]


# ── get_species -----------------------------------------------------------------

def test_get_species_default_returns_all_combinations_ordered_by_kg_desc(call_tool):
    data = call_tool("get_species")
    names = [(r["nombre_especie"], r["tipo"]) for r in data["species"]]
    assert names == [
        ("ATUN", "especie"),
        ("CAMARON", "especie"),
        ("JAIBA", "recurso"),
        ("MOJARRA", "recurso"),
        ("CAMARON ROSADO", "recurso"),
        ("PULPO", "recurso"),
    ]
    meta = data["meta"]
    assert meta["count"] == 6
    assert meta["n_especies"] == 2
    assert meta["n_recursos"] == 4
    assert meta["top_n"] is None
    assert meta["filters"] == {"year": None, "estado": None, "tipo_aviso": None}


def test_get_species_filtered_by_estado_lowercase_input(call_tool):
    # estado is upper()'d for matching but echoed back verbatim in meta.filters
    data = call_tool("get_species", {"estado": "sinaloa"})
    names = {r["nombre_especie"] for r in data["species"]}
    assert names == {"CAMARON", "JAIBA", "PULPO"}
    assert data["meta"]["filters"]["estado"] == "sinaloa"
    assert data["meta"]["n_especies"] == 1
    assert data["meta"]["n_recursos"] == 2


def test_get_species_combined_filters(call_tool):
    data = call_tool("get_species", {"estado": "SONORA", "tipo_aviso": "MAYORES"})
    names = {r["nombre_especie"] for r in data["species"]}
    assert names == {"ATUN", "CAMARON ROSADO"}
    assert data["meta"]["count"] == 2


def test_get_species_year_filter(call_tool):
    data = call_tool("get_species", {"year": 2021})
    names = [r["nombre_especie"] for r in data["species"]]
    assert names == ["ATUN", "MOJARRA", "CAMARON ROSADO"]


def test_get_species_no_matches(call_tool):
    data = call_tool("get_species", {"estado": "OAXACA"})
    assert data["species"] == []
    assert data["meta"]["count"] == 0
    assert data["meta"]["n_especies"] == 0
    assert data["meta"]["n_recursos"] == 0


def test_get_species_top_n_caps_row_count(call_tool):
    data = call_tool("get_species", {"top_n": 1})
    assert len(data["species"]) == 1
    assert data["species"][0]["nombre_especie"] == "ATUN"
    assert data["meta"]["top_n"] == 1


def test_get_species_top_n_zero_clamps_to_one_row(call_tool):
    data = call_tool("get_species", {"top_n": 0})
    assert len(data["species"]) == 1


def test_get_species_offloads_db_call_to_worker_thread(call_tool, mcp_app, monkeypatch):
    import tools.data_access as data_access
    main_thread_id = threading.get_ident()
    seen_thread_ids = []
    original = data_access.execute_select

    def spy(*args, **kwargs):
        seen_thread_ids.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(data_access, "execute_select", spy)
    call_tool("get_species")

    assert seen_thread_ids, "execute_select was never called"
    assert seen_thread_ids[0] != main_thread_id


# ── species_count -----------------------------------------------------------------

def test_species_count_classifies_every_taxonomic_level(call_tool):
    data = call_tool("species_count")

    assert data["by_level"]["species"] == ["Litopenaeus vannamei", "Thunnus albacares"]
    assert data["by_level"]["genus"] == ["Oreochromis"]
    assert data["by_level"]["family"] == ["Penaeidae"]
    assert data["by_level"]["unclassified"] == ["Mysteryus"]
    assert "order" not in data["by_level"]  # no fixture row lands there

    assert data["summary"]["total_unique_nombre_cientifico_canonico"] == 5
    assert data["summary"]["by_taxonomic_level"] == {
        "species": 2, "genus": 1, "family": 1, "unclassified": 1,
    }

    assert data["unidentified"]["n_unique_nombre_especie"] == 1
    assert data["unidentified"]["n_records"] == 1
    assert data["unidentified"]["nombre_especie_values"] == ["JAIBA"]


def test_species_count_is_stable_across_repeated_calls(call_tool):
    # species_count is cached; a second call must return the same content.
    first = call_tool("species_count")
    second = call_tool("species_count")
    assert first == second


# ── get_landings: default (row-level) branch -----------------------------------

def test_get_landings_default_returns_all_rows_ordered_by_fecha_desc(call_tool):
    data = call_tool("get_landings")
    folios = [r["folio_aviso"] for r in data["landings"]]
    assert folios == ["F3", "F2", "F2", "F1", "F1", "F4"]
    meta = data["meta"]
    assert meta["row_count"] == 6
    assert meta["limit"] == 500
    assert meta["truncated"] is False
    assert meta["filters"] == {
        "year": None, "year_from": None, "year_to": None,
        "estado": None, "especie": None,
        "nombre_principal": None, "nombre_cientifico_canonico": None,
        "tipo_aviso": None, "oficina": None,
    }
    # row-level records get a computed "tipo" field, keyed off nombre_cientifico_canonico
    tipos = {r["folio_aviso"]: r["tipo"] for r in data["landings"]}
    assert tipos["F3"] == "recurso"  # Oreochromis (E)


def test_get_landings_especie_matches_nombre_cientifico_specifically(call_tool):
    # "vannamei" only appears in nombre_cientifico ("Litopenaeus vannamei"),
    # not in nombre_especie ("CAMARON") — this only passes if the LIKE
    # condition really checks nombre_cientifico too, not just nombre_especie.
    data = call_tool("get_landings", {"especie": "vannamei"})
    assert data["meta"]["row_count"] == 1
    assert data["landings"][0]["nombre_especie"] == "CAMARON"


def test_get_landings_especie_matches_nombre_especie_substring(call_tool):
    # "camaron" matches both CAMARON and CAMARON ROSADO via nombre_especie
    data = call_tool("get_landings", {"especie": "camaron"})
    especies = {r["nombre_especie"] for r in data["landings"]}
    assert especies == {"CAMARON", "CAMARON ROSADO"}


def test_get_landings_combined_filters(call_tool):
    data = call_tool("get_landings", {
        "estado": "SINALOA", "tipo_aviso": "MENORES", "year": 2020,
    })
    folios = [r["folio_aviso"] for r in data["landings"]]
    assert folios == ["F1", "F1", "F4"]


def test_get_landings_oficina_filter(call_tool):
    data = call_tool("get_landings", {"oficina": "OFICINA SUR"})
    assert data["meta"]["row_count"] == 3
    assert {r["nombre_estado"] for r in data["landings"]} == {"SONORA"}


def test_get_landings_no_matches(call_tool):
    data = call_tool("get_landings", {"especie": "nonexistent-xyz"})
    assert data["landings"] == []
    assert data["meta"]["row_count"] == 0


def test_get_landings_limit_clamps_zero_up_to_one(call_tool):
    data = call_tool("get_landings", {"limit": 0})
    assert data["meta"]["limit"] == 1
    assert len(data["landings"]) == 1


def test_get_landings_limit_clamps_above_max_down_to_2000(call_tool):
    data = call_tool("get_landings", {"limit": 5000})
    assert data["meta"]["limit"] == 2000
    assert data["meta"]["row_count"] == 6  # fixture only has 6 rows
    assert data["meta"]["truncated"] is False


# ── get_landings: group_by="folio" ---------------------------------------------

def test_get_landings_group_by_folio_aggregates_species_lines(call_tool):
    data = call_tool("get_landings", {"group_by": "folio"})
    by_folio = {r["folio_aviso"]: r for r in data["by_folio"]}

    assert set(by_folio) == {"F1", "F2", "F3", "F4"}
    assert by_folio["F1"]["peso_desembarcado_kg"] == 150.0  # A(100) + B(50)
    assert by_folio["F1"]["dias_efectivos"] == 5
    assert by_folio["F2"]["peso_desembarcado_kg"] == 220.0  # C(200) + D(20)
    assert by_folio["F2"]["dias_efectivos"] == 10
    assert by_folio["F3"]["peso_desembarcado_kg"] == 30.0
    assert by_folio["F4"]["peso_desembarcado_kg"] == 15.0

    meta = data["meta"]
    assert meta["folio_count"] == 4
    assert meta["truncated"] is False
    assert "note" in meta
    # order: ORDER BY anio_corte, folio_aviso
    assert [r["folio_aviso"] for r in data["by_folio"]] == ["F1", "F4", "F2", "F3"]


def test_get_landings_group_by_folio_ignores_the_limit_argument(call_tool):
    # group_by="folio" is capped at db.py's DEFAULT_MAX_ROWS (5000), reported in
    # meta.truncated, and has no `limit` argument in its query path.
    data = call_tool("get_landings", {"group_by": "folio", "limit": 1})
    assert data["meta"]["folio_count"] == 4  # `limit` is ignored for this branch


# ── get_landings: group_by="year" ------------------------------------------------

def test_get_landings_group_by_year(call_tool):
    data = call_tool("get_landings", {"group_by": "year"})
    by_year = {r["anio_corte"]: r for r in data["annual_trend"]}
    assert by_year[2020]["total_kg"] == 165.0
    assert by_year[2020]["total_valor_mxn"] == 6450.0
    assert by_year[2020]["n_records"] == 3
    assert by_year[2021]["total_kg"] == 250.0
    assert by_year[2021]["n_records"] == 3
    assert data["meta"]["year_count"] == 2
    assert data["meta"]["truncated"] is False


# ── get_landings: group_by="estado" ----------------------------------------------

def test_get_landings_group_by_estado_orders_by_kg_desc(call_tool):
    data = call_tool("get_landings", {"group_by": "estado"})
    assert [r["nombre_estado"] for r in data["by_estado"]] == ["SONORA", "SINALOA"]
    assert data["by_estado"][0]["total_kg"] == 250.0
    assert data["by_estado"][1]["total_kg"] == 165.0
    assert data["meta"]["estado_count"] == 2


# ── get_landings: group_by="litoral" ---------------------------------------------

def test_get_landings_group_by_litoral_has_no_count_key(call_tool):
    data = call_tool("get_landings", {"group_by": "litoral"})
    assert [r["litoral"] for r in data["by_litoral"]] == ["PACIFICO", "GOLFO"]
    assert data["by_litoral"][0]["total_kg"] == 385.0  # A+B+C+D+F
    assert data["by_litoral"][1]["total_kg"] == 30.0   # E
    # unlike its siblings (folio/year/estado), the litoral meta has no
    # "*_count" key at all
    assert "truncated" in data["meta"]
    assert not any(k.endswith("_count") for k in data["meta"])


def test_get_landings_offloads_db_call_to_worker_thread(call_tool, mcp_app, monkeypatch):
    import tools.data_access as data_access
    main_thread_id = threading.get_ident()
    seen_thread_ids = []
    original = data_access.execute_select

    def spy(*args, **kwargs):
        seen_thread_ids.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(data_access, "execute_select", spy)
    call_tool("get_landings", {"group_by": "year"})

    assert seen_thread_ids
    assert seen_thread_ids[0] != main_thread_id


# ── record_count ------------------------------------------------------------------

def test_record_count(call_tool):
    data = call_tool("record_count")
    assert data == {"total_records": 6, "first_year": 2020, "last_year": 2021}


# ── get_offices ---------------------------------------------------------------

def test_get_offices_default(call_tool):
    data = call_tool("get_offices")
    offices = {(r["nombre_oficina"], r["nombre_estado"]): r["n_records"] for r in data["offices"]}
    assert offices == {
        ("OFICINA NORTE", "SINALOA"): 3,
        ("OFICINA SUR", "SONORA"): 3,
    }
    assert data["meta"] == {"estado": None, "count": 2}


def test_get_offices_filtered_by_estado(call_tool):
    data = call_tool("get_offices", {"estado": "SONORA"})
    assert len(data["offices"]) == 1
    assert data["offices"][0]["nombre_oficina"] == "OFICINA SUR"
    assert data["meta"] == {"estado": "SONORA", "count": 1}


def test_get_offices_no_matches(call_tool):
    data = call_tool("get_offices", {"estado": "NOPLACE"})
    assert data["offices"] == []
    assert data["meta"]["count"] == 0


# ── get_taxonomy ---------------------------------------------------------------

def test_get_taxonomy_falls_back_to_nombre_especie(call_tool):
    # "atun" is a common name: no canonical scientific name contains it, so the
    # canonical search comes back empty and the nombre_especie fallback answers.
    data = call_tool("get_taxonomy", {"especie": "atun"})
    assert data["meta"]["count"] == 1
    assert data["meta"]["search_field"] == "nombre_especie"
    row = data["taxonomy"][0]
    assert row["nombres_especie_conapesca"] == "ATUN"
    assert row["nombre_cientifico_canonico"] == "Thunnus albacares"
    assert row["genus"] == "Thunnus"
    assert row["tipo_pesca_canonico"] == "INDUSTRIAL"


def test_get_taxonomy_searches_the_canonical_name_first(call_tool):
    # "vannamei" is only present in the scientific name: found on the first
    # (canonical) search, with no fallback.
    data = call_tool("get_taxonomy", {"especie": "vannamei"})
    assert data["meta"]["count"] == 1
    assert data["meta"]["search_field"] == "nombre_cientifico_canonico"
    row = data["taxonomy"][0]
    assert row["nombre_cientifico_canonico"] == "Litopenaeus vannamei"
    assert row["nombres_especie_conapesca"] == "CAMARON"


def test_get_taxonomy_no_matches(call_tool):
    data = call_tool("get_taxonomy", {"especie": "nonexistent-xyz"})
    assert data["taxonomy"] == []
    # nothing found on either search: the fallback was tried, so it is the reported field
    assert data["meta"] == {
        "query": "nonexistent-xyz", "count": 0, "search_field": "nombre_especie",
    }


def test_get_taxonomy_offloads_db_call_to_worker_thread(call_tool, mcp_app, monkeypatch):
    import tools.data_access as data_access
    main_thread_id = threading.get_ident()
    seen_thread_ids = []
    original = data_access.execute_select

    def spy(*args, **kwargs):
        seen_thread_ids.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(data_access, "execute_select", spy)
    call_tool("get_taxonomy", {"especie": "atun"})

    assert seen_thread_ids
    assert seen_thread_ids[0] != main_thread_id
