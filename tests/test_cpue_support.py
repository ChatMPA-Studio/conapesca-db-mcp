"""
Tests for what the conapesca-cpue skill and the panel skills need from
get_landings / get_taxonomy: the resource-group and canonical-name filters, the
year range, the year_fleet and office_year_fleet modes, one row per trip in
group_by="folio", and an honest meta.truncated on every capped mode.

Run against the fixture DB built in tests/conftest.py (see ROWS there):

  A: 2020 SINALOA MENORES  folio F1  CAMARON        / Litopenaeus vannamei  kg=100 val=5000  OFICINA NORTE
  B: 2020 SINALOA MENORES  folio F1  JAIBA          / ND                    kg=50  val=1000  OFICINA NORTE
  C: 2021 SONORA  MAYORES  folio F2  ATUN           / Thunnus albacares     kg=200 val=20000 OFICINA SUR
  D: 2021 SONORA  MAYORES  folio F2  CAMARON ROSADO / Penaeidae             kg=20  val=800   OFICINA SUR
  E: 2021 SONORA  COSECHA  folio F3  MOJARRA        / Oreochromis           kg=30  val=900   OFICINA SUR
  F: 2020 SINALOA MENORES  folio F4  PULPO          / Mysteryus             kg=15  val=450   OFICINA NORTE

nombre_principal (resource group): A and D are CAMARON, B JAIBA, C ATUN,
E MOJARRA, F PULPO. nombre_cientifico_canonico equals nombre_cientifico.
"""

from __future__ import annotations
import contextlib
import os
import sqlite3


# ── filters --------------------------------------------------------------------

def test_nombre_principal_filter_matches_the_resource_group_across_states(call_tool):
    data = call_tool("get_landings", {"nombre_principal": "camaron"})  # input is upper-cased
    assert sorted(r["folio_aviso"] for r in data["landings"]) == ["F1", "F2"]
    assert {r["nombre_principal"] for r in data["landings"]} == {"CAMARON"}
    assert data["meta"]["filters"]["nombre_principal"] == "camaron"


def test_nombre_cientifico_canonico_filter_is_an_exact_match(call_tool):
    data = call_tool("get_landings", {"nombre_cientifico_canonico": "Thunnus albacares"})
    assert [r["folio_aviso"] for r in data["landings"]] == ["F2"]
    # exact, not partial: a fragment of the name finds nothing
    partial = call_tool("get_landings", {"nombre_cientifico_canonico": "Thunnus"})
    assert partial["landings"] == []


def test_year_range_filters_are_inclusive(call_tool):
    assert call_tool("get_landings", {"year_from": 2021})["meta"]["row_count"] == 3
    assert call_tool("get_landings", {"year_to": 2020})["meta"]["row_count"] == 3
    assert call_tool("get_landings", {"year_from": 2020, "year_to": 2021})["meta"]["row_count"] == 6
    assert call_tool("get_landings", {"year_from": 2021, "year_to": 2020})["meta"]["row_count"] == 0


def test_exact_year_takes_precedence_over_the_range(call_tool):
    data = call_tool("get_landings", {"year": 2020, "year_from": 2021, "year_to": 2021})
    assert data["meta"]["row_count"] == 3
    assert {r["anio_corte"] for r in data["landings"]} == {2020}


def test_default_records_include_litoral_and_the_species_columns(call_tool):
    row = call_tool("get_landings", {"year": 2021, "estado": "SONORA", "tipo_aviso": "COSECHA"})["landings"][0]
    assert row["litoral"] == "GOLFO"
    assert row["nombre_principal"] == "MOJARRA"
    assert row["nombre_cientifico_canonico"] == "Oreochromis"


# ── group_by="folio" -------------------------------------------------------------

def test_folio_stays_one_row_per_trip_when_filtering_by_resource_group(call_tool):
    # F1 has two species lines (A: CAMARON, B: JAIBA). The resource-group filter
    # works in the WHERE, so the trip stays ONE row and only counts the matching line.
    data = call_tool("get_landings", {"group_by": "folio", "nombre_principal": "CAMARON", "estado": "SINALOA"})
    assert [(r["folio_aviso"], r["peso_desembarcado_kg"]) for r in data["by_folio"]] == [("F1", 100.0)]
    # without the filter the trip carries both lines
    full = call_tool("get_landings", {"group_by": "folio", "estado": "SINALOA", "year": 2020})
    assert {r["folio_aviso"]: r["peso_desembarcado_kg"] for r in full["by_folio"]}["F1"] == 150.0


def test_folio_rows_do_not_carry_species_columns(call_tool):
    data = call_tool("get_landings", {"group_by": "folio", "nombre_cientifico_canonico": "Thunnus albacares"})
    assert "nombre_cientifico_canonico" not in data["by_folio"][0]
    assert "nombre_principal" not in data["by_folio"][0]


# ── group_by="year_fleet" / "office_year_fleet" ------------------------------------

def test_year_fleet_totals_per_year_and_fleet(call_tool):
    data = call_tool("get_landings", {"group_by": "year_fleet"})
    got = [(r["anio_corte"], r["tipo_aviso"], r["total_kg"], r["total_valor_mxn"], r["n_records"])
           for r in data["by_year_fleet"]]
    assert got == [
        (2020, "MENORES", 165.0, 6450.0, 3),
        (2021, "COSECHA", 30.0, 900.0, 1),
        (2021, "MAYORES", 220.0, 20800.0, 2),
    ]
    assert data["meta"]["row_count"] == 3
    assert data["meta"]["truncated"] is False


def test_year_fleet_honours_the_filters(call_tool):
    data = call_tool("get_landings", {"group_by": "year_fleet", "estado": "SONORA", "year_from": 2021})
    assert [(r["tipo_aviso"], r["n_records"]) for r in data["by_year_fleet"]] == [("COSECHA", 1), ("MAYORES", 2)]


def test_office_year_fleet_totals_per_office_year_and_fleet(call_tool):
    data = call_tool("get_landings", {"group_by": "office_year_fleet"})
    got = [(r["nombre_oficina"], r["nombre_estado"], r["anio_corte"], r["tipo_aviso"], r["total_kg"], r["n_records"])
           for r in data["by_office_year_fleet"]]
    assert got == [
        ("OFICINA NORTE", "SINALOA", 2020, "MENORES", 165.0, 3),
        ("OFICINA SUR", "SONORA", 2021, "COSECHA", 30.0, 1),
        ("OFICINA SUR", "SONORA", 2021, "MAYORES", 220.0, 2),
    ]
    assert data["meta"]["office_count"] == 2
    assert data["meta"]["truncated"] is False


# ── meta.truncated on every capped mode -------------------------------------------

def _cap_at(monkeypatch, n: int) -> None:
    import tools.data_access as data_access
    monkeypatch.setattr(data_access, "DEFAULT_MAX_ROWS", n)


def test_folio_reports_truncation_and_not_exactly_at_the_cap(call_tool, monkeypatch):
    _cap_at(monkeypatch, 3)  # the fixture has 4 trips
    cut = call_tool("get_landings", {"group_by": "folio"})
    assert cut["meta"]["truncated"] is True and cut["meta"]["folio_count"] == 3
    _cap_at(monkeypatch, 4)
    exact = call_tool("get_landings", {"group_by": "folio"})
    assert exact["meta"]["truncated"] is False and exact["meta"]["folio_count"] == 4


# ── group_by="folio" pages with `offset` -----------------------------------------------

def _all_folio_pages(call_tool) -> list[dict]:
    """Follow meta.next_offset until the last page, the way a client past the cap does."""
    pages, offset = [], 0
    while True:
        args = {"group_by": "folio", "offset": offset} if offset else {"group_by": "folio"}
        page = call_tool("get_landings", args)
        pages.append(page)
        if not page["meta"]["truncated"]:
            return pages
        offset = page["meta"]["next_offset"]


def test_folio_pages_cover_every_trip_exactly_once_and_in_order(call_tool, monkeypatch):
    _cap_at(monkeypatch, 3)  # the fixture has 4 trips -> 3 + 1
    pages = _all_folio_pages(call_tool)
    assert [p["meta"]["truncated"] for p in pages] == [True, False]
    assert [p["meta"]["offset"] for p in pages] == [0, 3]
    assert [p["meta"]["next_offset"] for p in pages] == [3, None]
    assert [r["folio_aviso"] for p in pages for r in p["by_folio"]] == ["F1", "F4", "F2", "F3"]

    _cap_at(monkeypatch, 1)  # one trip per page
    pages = _all_folio_pages(call_tool)
    assert [r["folio_aviso"] for p in pages for r in p["by_folio"]] == ["F1", "F4", "F2", "F3"]
    assert len(pages) == 4


def test_folio_paged_rows_equal_the_uncapped_rows(call_tool, monkeypatch):
    whole = call_tool("get_landings", {"group_by": "folio"})["by_folio"]
    _cap_at(monkeypatch, 2)
    paged = [r for p in _all_folio_pages(call_tool) for r in p["by_folio"]]
    assert paged == whole


def test_folio_page_at_the_exact_cap_is_the_last_one(call_tool, monkeypatch):
    _cap_at(monkeypatch, 2)
    second = call_tool("get_landings", {"group_by": "folio", "offset": 2})
    assert second["meta"]["truncated"] is False and second["meta"]["next_offset"] is None
    assert second["meta"]["folio_count"] == 2


def test_folio_offset_past_the_end_is_an_empty_last_page(call_tool):
    data = call_tool("get_landings", {"group_by": "folio", "offset": 50})
    assert data["by_folio"] == []
    assert data["meta"]["truncated"] is False and data["meta"]["next_offset"] is None


def test_folio_pages_keep_the_filters(call_tool, monkeypatch):
    _cap_at(monkeypatch, 1)
    pages, offset = [], 0
    while True:
        page = call_tool("get_landings", {"group_by": "folio", "estado": "SINALOA", "offset": offset})
        pages.append(page)
        if not page["meta"]["truncated"]:
            break
        offset = page["meta"]["next_offset"]
    assert [r["folio_aviso"] for p in pages for r in p["by_folio"]] == ["F1", "F4"]


def test_folio_without_offset_is_the_first_page(call_tool, monkeypatch):
    _cap_at(monkeypatch, 3)
    default = call_tool("get_landings", {"group_by": "folio"})
    explicit = call_tool("get_landings", {"group_by": "folio", "offset": 0})
    assert default["by_folio"] == explicit["by_folio"]
    assert default["meta"]["offset"] == 0


def test_offset_is_rejected_where_it_does_not_apply(call_tool):
    import pytest
    for args in ({"offset": 5}, {"group_by": "year_fleet", "offset": 5}):
        with pytest.raises(Exception, match="offset only applies to group_by='folio'"):
            call_tool("get_landings", args)
    with pytest.raises(Exception, match="offset must be >= 0"):
        call_tool("get_landings", {"group_by": "folio", "offset": -1})


def test_year_fleet_reports_truncation(call_tool, monkeypatch):
    _cap_at(monkeypatch, 2)  # 3 rows exist
    data = call_tool("get_landings", {"group_by": "year_fleet"})
    assert data["meta"]["truncated"] is True
    assert len(data["by_year_fleet"]) == 2


def test_office_year_fleet_reports_truncation(call_tool, monkeypatch):
    _cap_at(monkeypatch, 2)  # 3 rows exist
    data = call_tool("get_landings", {"group_by": "office_year_fleet"})
    assert data["meta"]["truncated"] is True
    assert len(data["by_office_year_fleet"]) == 2


# ── the canonical name is what finds a species whose raw name is empty --------------

@contextlib.contextmanager
def _raw_scientific_name_blanked():
    """Blank nombre_cientifico on row A (what most real rows look like) and restore it."""
    conn = sqlite3.connect(os.environ["SQLITE_PATH"])
    try:
        conn.execute("UPDATE conapesca_landings_historical SET nombre_cientifico = NULL "
                     "WHERE nombre_cientifico_canonico = 'Litopenaeus vannamei'")
        conn.commit()
        yield
    finally:
        conn.execute("UPDATE conapesca_landings_historical SET nombre_cientifico = 'Litopenaeus vannamei' "
                     "WHERE nombre_cientifico_canonico = 'Litopenaeus vannamei'")
        conn.commit()
        conn.close()


def test_canonical_filter_finds_the_species_when_the_raw_name_is_empty(call_tool):
    with _raw_scientific_name_blanked():
        by_raw = call_tool("get_landings", {"especie": "vannamei"})
        by_canonical = call_tool("get_landings", {"nombre_cientifico_canonico": "Litopenaeus vannamei"})
    assert by_raw["landings"] == []                                   # the legacy filter misses it
    assert [r["folio_aviso"] for r in by_canonical["landings"]] == ["F1"]


# ── get_taxonomy -----------------------------------------------------------------

def test_taxonomy_groups_by_canonical_name_and_lists_the_conapesca_names(call_tool):
    # "camaron" is a common name: found through the nombre_especie fallback, one
    # row per canonical name with the CONAPESCA names that map to it.
    data = call_tool("get_taxonomy", {"especie": "camaron"})
    assert data["meta"]["search_field"] == "nombre_especie"
    got = {r["nombre_cientifico_canonico"]: r["nombres_especie_conapesca"] for r in data["taxonomy"]}
    assert got == {"Litopenaeus vannamei": "CAMARON", "Penaeidae": "CAMARON ROSADO"}


# ── get_species / species_count -----------------------------------------------------

def test_get_species_rows_use_the_canonical_name_field(call_tool):
    data = call_tool("get_species", {"year": 2021, "estado": "SONORA"})
    row = next(r for r in data["species"] if r["nombre_especie"] == "ATUN")
    assert row["nombre_cientifico_canonico"] == "Thunnus albacares"
    assert "nombre_cientifico" not in row
    assert row["tipo"] == "especie"
