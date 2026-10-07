"""
data_access — basic query and extraction tools.
No aggregations, no statistics: just filtered raw data.
"""

from __future__ import annotations
import asyncio
import json
from decimal import Decimal
from mcp_server import cache
from mcp_server.db import execute_select, DEFAULT_MAX_ROWS


def _json(obj) -> str:
    """Serialize to JSON, converting Decimal and None gracefully."""
    def _default(v):
        if isinstance(v, Decimal):
            return float(v)
        raise TypeError(f"Not serializable: {type(v)}")
    return json.dumps(obj, default=_default, ensure_ascii=False)


def _tipo(nc: str | None) -> str:
    """Classify a nombre_cientifico as 'especie' (binomial) or 'recurso' (ND/genus/empty)."""
    nc = (nc or "").strip()
    return "especie" if (nc and nc.upper() != "ND" and " " in nc) else "recurso"


# Every tool below is declared `async def` and immediately hands its blocking
# DB work off to a worker thread via `asyncio.to_thread`. FastMCP only awaits
# a tool's return value if it is already awaitable — a plain sync function
# would run its pymysql call directly on the event loop, freezing the whole
# server (including unrelated concurrent calls) for the query's duration.

def register(mcp) -> None:

    def _get_estados_sync(year: int | None) -> str:
        conditions, params = [], []
        if year:
            conditions.append("anio_corte = ?")
            params.append(year)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = execute_select(
            f"SELECT DISTINCT nombre_estado FROM conapesca_landings_historical "
            f"{where} ORDER BY nombre_estado",
            tuple(params) or None,
        )
        estados = [r["nombre_estado"] for r in rows if r["nombre_estado"]]
        return _json({"estados": estados, "meta": {"count": len(estados), "year": year}})

    @mcp.tool()
    async def get_estados(year: int | None = None) -> str:
        """
        List all Mexican states (nombre_estado) present in the landings.
        Optionally filter by year.
        """
        key = ("get_estados", year)
        hit = cache.get(key)
        if hit is not None:
            return hit
        result = await asyncio.to_thread(_get_estados_sync, year)
        cache.set(key, result)
        return result

    def _get_species_sync(
        year: int | None,
        estado: str | None,
        tipo_aviso: str | None,
        top_n: int | None,
    ) -> str:
        conditions, params = [], []
        if year:
            conditions.append("anio_corte = ?")
            params.append(year)
        if estado:
            conditions.append("nombre_estado = ?")
            params.append(estado.upper())
        if tipo_aviso:
            conditions.append("tipo_aviso = ?")
            params.append(tipo_aviso.upper())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        max_rows = min(max(1, top_n), 500) if top_n is not None else 5000
        rows = execute_select(
            f"SELECT nombre_especie, nombre_cientifico_canonico, "
            f"ROUND(SUM(peso_desembarcado_kg), 1) AS total_kg, "
            f"ROUND(SUM(valor_pesos_estimado), 0) AS total_valor_mxn, "
            f"COUNT(*) AS n_records "
            f"FROM conapesca_landings_historical {where} "
            f"GROUP BY nombre_especie, nombre_cientifico_canonico "
            f"ORDER BY total_kg DESC",
            tuple(params) or None,
            max_rows=max_rows,
        )
        result = [{**dict(r), "tipo": _tipo(r.get("nombre_cientifico_canonico"))} for r in rows]
        return _json({
            "species": result,
            "meta": {
                "filters": {"year": year, "estado": estado, "tipo_aviso": tipo_aviso},
                "top_n": top_n,
                "count": len(result),
                "n_especies": sum(1 for r in result if r["tipo"] == "especie"),
                "n_recursos": sum(1 for r in result if r["tipo"] == "recurso"),
            },
        })

    @mcp.tool()
    async def get_species(
        year: int | None = None,
        estado: str | None = None,
        tipo_aviso: str | None = None,
        top_n: int | None = None,
    ) -> str:
        """
        List species (nombre_especie + nombre_cientifico_canonico) with total landed
        weight (kg), estimated value (MXN) and record count.
        Filters: year, estado, tipo_aviso (MAYORES/MENORES/COSECHA).
        top_n: if provided, return only the top N species by landed weight
        (max 500); if omitted, return all matching combinations.

        IMPORTANT: Always provide at least one filter (year, estado, or tipo_aviso).
        Queries without any filter scan 12+ million rows and will likely time out.
        If the user does not specify a year or state, ask them to provide one before
        calling this tool.
        """
        return await asyncio.to_thread(_get_species_sync, year, estado, tipo_aviso, top_n)

    def _species_count_sync() -> str:
        # One row per unique nombre_cientifico_canonico with representative taxonomy
        identified_rows = execute_select(
            "SELECT nombre_cientifico_canonico, "
            "MAX(genus) AS genus, MAX(family) AS family, MAX(`order`) AS `order`, "
            "MAX(class) AS class, MAX(phylum) AS phylum, MAX(kingdom) AS kingdom "
            "FROM conapesca_landings_historical "
            "WHERE nombre_cientifico_canonico IS NOT NULL "
            "AND TRIM(nombre_cientifico_canonico) != '' "
            "AND UPPER(TRIM(nombre_cientifico_canonico)) != 'ND' "
            "GROUP BY nombre_cientifico_canonico",
            max_rows=5000,
        )

        levels: dict[str, list[str]] = {
            "species": [], "genus": [], "family": [],
            "order": [], "class": [], "phylum": [], "kingdom": [], "unclassified": [],
        }
        for row in identified_rows:
            nc = (row.get("nombre_cientifico_canonico") or "").strip()
            if not nc:
                continue
            if " " in nc:
                levels["species"].append(nc)
                continue
            nc_up = nc.upper()
            matched = False
            for level, col in [
                ("genus",   "genus"),
                ("family",  "family"),
                ("order",   "order"),
                ("class",   "class"),
                ("phylum",  "phylum"),
                ("kingdom", "kingdom"),
            ]:
                val = (row.get(col) or "").strip().upper()
                if val and nc_up == val:
                    levels[level].append(nc)
                    matched = True
                    break
            if not matched:
                levels["unclassified"].append(nc)

        # Unidentified: ND or empty nombre_cientifico_canonico
        nd_rows = execute_select(
            "SELECT DISTINCT nombre_especie "
            "FROM conapesca_landings_historical "
            "WHERE nombre_cientifico_canonico IS NULL "
            "OR TRIM(nombre_cientifico_canonico) = '' "
            "OR UPPER(TRIM(nombre_cientifico_canonico)) = 'ND'",
            max_rows=5000,
        )
        nd_especies = sorted(r["nombre_especie"] for r in nd_rows if r.get("nombre_especie"))

        nd_record_rows = execute_select(
            "SELECT COUNT(*) AS n FROM conapesca_landings_historical "
            "WHERE nombre_cientifico_canonico IS NULL "
            "OR TRIM(nombre_cientifico_canonico) = '' "
            "OR UPPER(TRIM(nombre_cientifico_canonico)) = 'ND'"
        )
        nd_records = nd_record_rows[0]["n"] if nd_record_rows else 0

        total = sum(len(v) for v in levels.values())

        return _json({
            "summary": {
                "total_unique_nombre_cientifico_canonico": total,
                "by_taxonomic_level": {k: len(v) for k, v in levels.items() if v},
            },
            "by_level": {k: sorted(v) for k, v in levels.items() if v},
            "unidentified": {
                "note": (
                    "These nombre_especie values have nombre_cientifico_canonico = ND or empty "
                    "and are not yet taxonomically identified."
                ),
                "n_unique_nombre_especie": len(nd_especies),
                "n_records": nd_records,
                "nombre_especie_values": nd_especies,
            },
        })

    @mcp.tool()
    async def species_count() -> str:
        """
        Count unique canonical scientific names (nombre_cientifico_canonico) and
        classify them by taxonomic resolution level (species, genus, family, order,
        class, phylum). A name with two or more words is species-level; a single-word
        name is matched against the taxonomy columns (genus, family, order, class,
        phylum, kingdom) to determine its resolution.  Classification is done on
        unique values of nombre_cientifico_canonico, not on individual rows.
        Also reports which nombre_especie entries have no scientific name
        (nombre_cientifico_canonico = ND or empty) and how many records they represent.
        Use this tool to answer any question about species diversity or richness.
        """
        key = ("species_count",)
        hit = cache.get(key)
        if hit is not None:
            return hit
        result = await asyncio.to_thread(_species_count_sync)
        cache.set(key, result)
        return result

    def _get_landings_sync(
        year: int | None,
        year_from: int | None,
        year_to: int | None,
        estado: str | None,
        especie: str | None,
        nombre_principal: str | None,
        nombre_cientifico_canonico: str | None,
        tipo_aviso: str | None,
        oficina: str | None,
        limit: int,
        group_by: str | None,
    ) -> str:
        conditions, params = [], []

        # Year filters — exact OR range (not both)
        if year:
            conditions.append("anio_corte = ?")
            params.append(year)
        else:
            if year_from:
                conditions.append("anio_corte >= ?")
                params.append(year_from)
            if year_to:
                conditions.append("anio_corte <= ?")
                params.append(year_to)

        if estado:
            conditions.append("nombre_estado = ?")
            params.append(estado.upper())
        if especie:
            conditions.append("(nombre_especie LIKE ? OR nombre_cientifico LIKE ?)")
            params.extend([f"%{especie.upper()}%", f"%{especie.upper()}%"])
        if nombre_principal:
            conditions.append("nombre_principal = ?")
            params.append(nombre_principal.upper())
        if nombre_cientifico_canonico:
            conditions.append("nombre_cientifico_canonico = ?")
            params.append(nombre_cientifico_canonico)
        if tipo_aviso:
            conditions.append("tipo_aviso = ?")
            params.append(tipo_aviso.upper())
        if oficina:
            conditions.append("nombre_oficina = ?")
            params.append(oficina.upper())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        p = tuple(params) or None

        # Shared metrics used by all aggregated modes
        agg_metrics = (
            "ROUND(SUM(peso_desembarcado_kg), 1) AS total_kg, "
            "ROUND(SUM(valor_pesos_estimado), 0) AS total_valor_mxn, "
            "COUNT(*) AS n_records"
        )

        # Active filters dict — included in all meta blocks
        active_filters = {
            "year": year, "year_from": year_from, "year_to": year_to,
            "estado": estado, "especie": especie,
            "nombre_principal": nombre_principal,
            "nombre_cientifico_canonico": nombre_cientifico_canonico,
            "tipo_aviso": tipo_aviso, "oficina": oficina,
        }

        # Every mode below fetches ONE row past its cap (max_rows = cap + 1) to tell
        # "exactly at the cap" apart from "more rows exist beyond it", and reports it
        # in meta.truncated.

        # ── group_by = "folio" ────────────────────────────────────────────────
        if group_by == "folio":
            rows = execute_select(
                f"SELECT folio_aviso, anio_corte, tipo_aviso, "
                f"nombre_estado, nombre_oficina, "
                f"MAX(dias_efectivos) AS dias_efectivos, "
                f"MAX(dias_efectivos_fuente) AS dias_efectivos_fuente, "
                f"MAX(flag_fecha_generica) AS flag_fecha_generica, "
                f"MAX(flag_dias_efectivos_sospechoso) AS flag_dias_efectivos_sospechoso, "
                f"MAX(flag_periodo_futuro) AS flag_periodo_futuro, "
                f"ROUND(SUM(peso_desembarcado_kg), 3) AS peso_desembarcado_kg "
                f"FROM conapesca_landings_historical {where} "
                f"GROUP BY folio_aviso, anio_corte, tipo_aviso, "
                f"nombre_estado, nombre_oficina "
                f"ORDER BY anio_corte, folio_aviso",
                p, max_rows=DEFAULT_MAX_ROWS + 1,
            )
            truncated = len(rows) > DEFAULT_MAX_ROWS
            rows = rows[:DEFAULT_MAX_ROWS]
            return _json({
                "by_folio": [dict(r) for r in rows],
                "meta": {
                    "filters": active_filters,
                    "folio_count": len(rows),
                    "truncated": truncated,
                    "note": (
                        "One row per trip. dias_efectivos is trip-level — do NOT sum "
                        "across rows of the same folio. Exclude flag_fecha_generica=1 "
                        "or flag_dias_efectivos_sospechoso=1 or dias_efectivos IS NULL "
                        "before computing CPUE."
                    ),
                },
            })

        # ── group_by = "year" ─────────────────────────────────────────────────
        if group_by == "year":
            rows = execute_select(
                f"SELECT anio_corte, {agg_metrics} "
                f"FROM conapesca_landings_historical {where} "
                f"GROUP BY anio_corte ORDER BY anio_corte",
                p, max_rows=101,
            )
            truncated = len(rows) > 100
            rows = rows[:100]
            return _json({
                "annual_trend": [dict(r) for r in rows],
                "meta": {
                    "filters": active_filters,
                    "year_count": len(rows),
                    "truncated": truncated,
                },
            })

        # ── group_by = "year_fleet" ───────────────────────────────────────────
        if group_by == "year_fleet":
            rows = execute_select(
                f"SELECT anio_corte, tipo_aviso, {agg_metrics} "
                f"FROM conapesca_landings_historical {where} "
                f"GROUP BY anio_corte, tipo_aviso "
                f"ORDER BY anio_corte, tipo_aviso",
                p, max_rows=DEFAULT_MAX_ROWS + 1,
            )
            truncated = len(rows) > DEFAULT_MAX_ROWS
            rows = rows[:DEFAULT_MAX_ROWS]
            return _json({
                "by_year_fleet": [dict(r) for r in rows],
                "meta": {
                    "filters": active_filters,
                    "row_count": len(rows),
                    "truncated": truncated,
                    "note": (
                        "Annual totals disaggregated by fleet type (tipo_aviso). "
                        "Use for timeseries and comparative-summary skills."
                    ),
                },
            })

        # ── group_by = "office_year_fleet" ────────────────────────────────────
        if group_by == "office_year_fleet":
            rows = execute_select(
                f"SELECT nombre_oficina, nombre_estado, anio_corte, tipo_aviso, "
                f"{agg_metrics} "
                f"FROM conapesca_landings_historical {where} "
                f"GROUP BY nombre_oficina, nombre_estado, anio_corte, tipo_aviso "
                f"ORDER BY anio_corte, nombre_estado, nombre_oficina, tipo_aviso",
                p, max_rows=DEFAULT_MAX_ROWS + 1,
            )
            truncated = len(rows) > DEFAULT_MAX_ROWS
            rows = rows[:DEFAULT_MAX_ROWS]
            return _json({
                "by_office_year_fleet": [dict(r) for r in rows],
                "meta": {
                    "filters": active_filters,
                    "row_count": len(rows),
                    "office_count": len({r["nombre_oficina"] for r in rows}),
                    "truncated": truncated,
                    "note": (
                        "Annual totals per office × fleet type. "
                        "Use for national ranking — includes all offices matching "
                        "the filters so the target office can be compared against "
                        "the full national universe."
                    ),
                },
            })

        # ── group_by = "estado" ───────────────────────────────────────────────
        if group_by == "estado":
            rows = execute_select(
                f"SELECT nombre_estado, {agg_metrics} "
                f"FROM conapesca_landings_historical {where} "
                f"GROUP BY nombre_estado ORDER BY total_kg DESC",
                p, max_rows=51,
            )
            truncated = len(rows) > 50
            rows = rows[:50]
            return _json({
                "by_estado": [dict(r) for r in rows],
                "meta": {
                    "filters": active_filters,
                    "estado_count": len(rows),
                    "truncated": truncated,
                },
            })

        # ── group_by = "litoral" ──────────────────────────────────────────────
        if group_by == "litoral":
            rows = execute_select(
                f"SELECT litoral, {agg_metrics} "
                f"FROM conapesca_landings_historical {where} "
                f"GROUP BY litoral ORDER BY total_kg DESC",
                p, max_rows=11,
            )
            truncated = len(rows) > 10
            rows = rows[:10]
            return _json({
                "by_litoral": [dict(r) for r in rows],
                "meta": {
                    "filters": active_filters,
                    "truncated": truncated,
                },
            })

        # ── default: individual records (capped) ──────────────────────────────
        safe_limit = min(max(1, limit), 2000)
        rows = execute_select(
            f"SELECT anio_corte, fecha_aviso, tipo_aviso, folio_aviso, "
            f"litoral, nombre_estado, nombre_oficina, nombre_sitio_desembarque, "
            f"unidad_economica, nombre_principal, nombre_especie, "
            f"nombre_cientifico, nombre_cientifico_canonico, "
            f"peso_desembarcado_kg, valor_pesos_estimado, tipo_pesca_canonico, "
            f"dias_efectivos, dias_efectivos_fuente, "
            f"flag_fecha_generica, flag_dias_efectivos_sospechoso, flag_periodo_futuro "
            f"FROM conapesca_landings_historical {where} "
            f"ORDER BY fecha_aviso DESC",
            p, max_rows=safe_limit + 1,
        )
        truncated = len(rows) > safe_limit
        rows = rows[:safe_limit]
        return _json({
            "landings": [{**dict(r), "tipo": _tipo(r.get("nombre_cientifico_canonico"))} for r in rows],
            "meta": {
                "filters": active_filters,
                "row_count": len(rows),
                "limit": safe_limit,
                "truncated": truncated,
            },
        })

    @mcp.tool()
    async def get_landings(
        year: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        estado: str | None = None,
        especie: str | None = None,
        nombre_principal: str | None = None,
        nombre_cientifico_canonico: str | None = None,
        tipo_aviso: str | None = None,
        oficina: str | None = None,
        limit: int = 500,
        group_by: str | None = None,
    ) -> str:
        """
        Return landing data filtered by any combination of year/range, estado,
        especie, nombre_principal, nombre_cientifico_canonico, tipo_aviso, oficina.

        ── FILTERS ──────────────────────────────────────────────────────────────
        year             : exact year match (use this OR year_from/year_to, not both)
        year_from/year_to: inclusive year range (e.g. year_from=2015, year_to=2024)
        estado           : exact match on nombre_estado (uppercase)
        oficina          : exact match on nombre_oficina (uppercase) — use
                           get_offices() to find the exact nombre_oficina value.
        tipo_aviso       : exact match — MAYORES | MENORES | COSECHA
        especie          : partial match on nombre_especie OR nombre_cientifico
                           (legacy; use nombre_principal or
                           nombre_cientifico_canonico instead)
        nombre_principal : exact match on nombre_principal — resource group level
                           (e.g. "JUREL", "CAMARON", "OSTION")
        nombre_cientifico_canonico: exact match on nombre_cientifico_canonico —
                           species level (e.g. "Seriola lalandi").
                           Mutually exclusive with nombre_principal.

        ── ROW CAPS ─────────────────────────────────────────────────────────────
        Every mode is capped (see each one below) and the cap is NOT silent:
        meta.truncated is true when more rows exist than were returned. When it
        is true the result is incomplete — narrow the filters (year, estado,
        oficina) instead of using it as if it were the full answer.

        ── GROUP_BY MODES ───────────────────────────────────────────────────────
        None (default)   : individual records, capped at `limit` rows (max 2000).
                           Includes quality flags and effort fields.

        "folio"          : one row per fishing trip (folio_aviso), summing
                           peso_desembarcado_kg across species lines. Includes
                           dias_efectivos and quality flags. Capped at 5000 folios.
                           → Use for CPUE computation. A whole state-year usually
                           exceeds the cap: filter by oficina (and year).

        "year"           : annual totals — total_kg, total_valor_mxn, n_records
                           per year. Capped at 100 years.

        "year_fleet"     : annual totals per year × tipo_aviso (MAYORES/MENORES/
                           COSECHA). Capped at 5000 rows.
                           → Use for timeseries and comparative-summary skills.

        "office_year_fleet": annual totals per oficina × year × tipo_aviso for
                           ALL offices matching the filters. Capped at 5000 rows
                           (all offices over all years can exceed it: narrow it).
                           → Use for national ranking computation (compare one
                           office against the full national universe).

        "estado"         : totals per estado. Capped at 50 estados.
        "litoral"        : totals per litoral. Capped at 10 litorales.
        """
        return await asyncio.to_thread(
            _get_landings_sync, year, year_from, year_to, estado, especie,
            nombre_principal, nombre_cientifico_canonico, tipo_aviso, oficina,
            limit, group_by,
        )

    def _record_count_sync() -> str:
        rows = execute_select(
            "SELECT COUNT(*) AS total_records, "
            "MIN(anio_corte) AS first_year, MAX(anio_corte) AS last_year "
            "FROM conapesca_landings_historical",
        )
        r = dict(rows[0]) if rows else {}
        return _json(r)

    @mcp.tool()
    async def record_count() -> str:
        """
        Return total record count and year range in the CONAPESCA landings
        database. Fast single-row query — use this instead of get_landings
        when only the total size or date range is needed.
        """
        return await asyncio.to_thread(_record_count_sync)

    def _get_offices_sync(estado: str | None) -> str:
        conditions, params = [], []
        if estado:
            conditions.append("nombre_estado = ?")
            params.append(estado.upper())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = execute_select(
            f"SELECT nombre_oficina, nombre_estado, "
            f"COUNT(*) AS n_records "
            f"FROM conapesca_landings_historical {where} "
            f"GROUP BY nombre_oficina, nombre_estado "
            f"ORDER BY nombre_estado, nombre_oficina",
            tuple(params) or None,
        )
        return _json({
            "offices": [dict(r) for r in rows],
            "meta": {"estado": estado, "count": len(rows)},
        })

    @mcp.tool()
    async def get_offices(estado: str | None = None) -> str:
        """
        List fishing offices (oficinas CONAPESCA) with their state and
        number of landing records.
        """
        key = ("get_offices", estado)
        hit = cache.get(key)
        if hit is not None:
            return hit
        result = await asyncio.to_thread(_get_offices_sync, estado)
        cache.set(key, result)
        return result

    def _get_taxonomy_sync(especie: str) -> str:
        _q = f"%{especie.upper()}%"
        _base = (
            "SELECT nombre_cientifico_canonico, "
            "GROUP_CONCAT(DISTINCT nombre_especie) AS nombres_especie_conapesca, "
            "MAX(kingdom) AS kingdom, MAX(phylum) AS phylum, "
            "MAX(class) AS class, MAX(`order`) AS `order`, "
            "MAX(family) AS family, MAX(genus) AS genus, "
            "MAX(worms_id) AS worms_id, "
            "MAX(spec_code_fishbase) AS spec_code_fishbase, "
            "MAX(fishbase_database) AS fishbase_database, "
            "MAX(k) AS k, MAX(loo) AS loo, MAX(lmax) AS lmax, "
            "MAX(tmax) AS tmax, MAX(wmax) AS wmax, "
            "MAX(trophic_level) AS trophic_level, "
            "MAX(tipo_pesca_canonico) AS tipo_pesca_canonico "
            "FROM conapesca_landings_historical "
            "WHERE {where} "
            "GROUP BY nombre_cientifico_canonico "
            "ORDER BY nombre_cientifico_canonico "
            "LIMIT 10"
        )
        rows = execute_select(
            _base.format(where="nombre_cientifico_canonico LIKE ?"), (_q,)
        )
        fallback_used = False
        if not rows:
            rows = execute_select(
                _base.format(where="nombre_especie LIKE ?"), (_q,)
            )
            fallback_used = True
        return _json({
            "taxonomy": [dict(r) for r in rows],
            "meta": {
                "query": especie,
                "count": len(rows),
                "search_field": "nombre_especie" if fallback_used else "nombre_cientifico_canonico",
            },
        })

    @mcp.tool()
    async def get_taxonomy(especie: str) -> str:
        """
        Return the taxonomic classification for a species name
        (kingdom → genus) plus FishBase traits if available.
        Searches nombre_cientifico_canonico first; falls back to nombre_especie
        if no results are found.
        """
        return await asyncio.to_thread(_get_taxonomy_sync, especie)
