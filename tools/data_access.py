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
            f"SELECT nombre_especie, nombre_cientifico, "
            f"ROUND(SUM(peso_desembarcado_kg), 1) AS total_kg, "
            f"ROUND(SUM(valor_pesos_estimado), 0) AS total_valor_mxn, "
            f"COUNT(*) AS n_records "
            f"FROM conapesca_landings_historical {where} "
            f"GROUP BY nombre_especie, nombre_cientifico "
            f"ORDER BY total_kg DESC",
            tuple(params) or None,
            max_rows=max_rows,
        )
        result = [{**dict(r), "tipo": _tipo(r.get("nombre_cientifico"))} for r in rows]
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
        List species (nombre_especie + nombre_cientifico) with total landed
        weight (kg), estimated value (MXN) and record count.
        Filters: year, estado, tipo_aviso (MAYORES/MENORES/COSECHA).
        top_n: if provided, return only the top N species by landed weight
        (max 500); if omitted, return all matching combinations.
        """
        return await asyncio.to_thread(_get_species_sync, year, estado, tipo_aviso, top_n)

    def _species_count_sync() -> str:
        # One row per unique nombre_cientifico with representative taxonomy
        identified_rows = execute_select(
            "SELECT nombre_cientifico, "
            "MAX(genus) AS genus, MAX(family) AS family, MAX(`order`) AS `order`, "
            "MAX(class) AS class, MAX(phylum) AS phylum, MAX(kingdom) AS kingdom "
            "FROM conapesca_landings_historical "
            "WHERE nombre_cientifico IS NOT NULL "
            "AND TRIM(nombre_cientifico) != '' "
            "AND UPPER(TRIM(nombre_cientifico)) != 'ND' "
            "GROUP BY nombre_cientifico",
            max_rows=5000,
        )

        levels: dict[str, list[str]] = {
            "species": [], "genus": [], "family": [],
            "order": [], "class": [], "phylum": [], "kingdom": [], "unclassified": [],
        }
        for row in identified_rows:
            nc = (row.get("nombre_cientifico") or "").strip()
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

        # Unidentified: ND or empty nombre_cientifico
        nd_rows = execute_select(
            "SELECT DISTINCT nombre_especie "
            "FROM conapesca_landings_historical "
            "WHERE nombre_cientifico IS NULL "
            "OR TRIM(nombre_cientifico) = '' "
            "OR UPPER(TRIM(nombre_cientifico)) = 'ND'",
            max_rows=5000,
        )
        nd_especies = sorted(r["nombre_especie"] for r in nd_rows if r.get("nombre_especie"))

        nd_record_rows = execute_select(
            "SELECT COUNT(*) AS n FROM conapesca_landings_historical "
            "WHERE nombre_cientifico IS NULL "
            "OR TRIM(nombre_cientifico) = '' "
            "OR UPPER(TRIM(nombre_cientifico)) = 'ND'"
        )
        nd_records = nd_record_rows[0]["n"] if nd_record_rows else 0

        total = sum(len(v) for v in levels.values())

        return _json({
            "summary": {
                "total_unique_nombre_cientifico": total,
                "by_taxonomic_level": {k: len(v) for k, v in levels.items() if v},
            },
            "by_level": {k: sorted(v) for k, v in levels.items() if v},
            "unidentified": {
                "note": (
                    "These nombre_especie values have nombre_cientifico = ND or empty "
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
        Count unique scientific names (nombre_cientifico) and classify them by
        taxonomic resolution level (species, genus, family, order, class, phylum).
        A name with two or more words is species-level; a single-word name is
        matched against the taxonomy columns (genus, family, order, class, phylum,
        kingdom) to determine its resolution.  Classification is done on unique
        values of nombre_cientifico, not on individual rows.
        Also reports which nombre_especie entries have no scientific name
        (nombre_cientifico = ND or empty) and how many records they represent.
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
        estado: str | None,
        especie: str | None,
        tipo_aviso: str | None,
        oficina: str | None,
        limit: int,
        group_by: str | None,
    ) -> str:
        conditions, params = [], []
        if year:
            conditions.append("anio_corte = ?")
            params.append(year)
        if estado:
            conditions.append("nombre_estado = ?")
            params.append(estado.upper())
        if especie:
            conditions.append("(nombre_especie LIKE ? OR nombre_cientifico LIKE ?)")
            params.extend([f"%{especie.upper()}%", f"%{especie.upper()}%"])
        if tipo_aviso:
            conditions.append("tipo_aviso = ?")
            params.append(tipo_aviso.upper())
        if oficina:
            conditions.append("nombre_oficina = ?")
            params.append(oficina.upper())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        p = tuple(params) or None

        agg_select = (
            "ROUND(SUM(peso_desembarcado_kg), 1) AS total_kg, "
            "ROUND(SUM(valor_pesos_estimado), 0) AS total_valor_mxn, "
            "COUNT(*) AS n_records "
        )

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
            # Fetch one row past the cap so "exactly DEFAULT_MAX_ROWS results
            # exist" can be told apart from "truncated at DEFAULT_MAX_ROWS".
            truncated = len(rows) > DEFAULT_MAX_ROWS
            rows = rows[:DEFAULT_MAX_ROWS]
            return _json({
                "by_folio": [dict(r) for r in rows],
                "meta": {
                    "filters": {"year": year, "estado": estado, "especie": especie,
                                "tipo_aviso": tipo_aviso, "oficina": oficina},
                    "folio_count": len(rows),
                    "truncated": truncated,
                    "note": (
                        "One row per trip. dias_efectivos is a trip-level field "
                        "identical across all species lines of the same folio. "
                        "Exclude records where flag_fecha_generica=1 or "
                        "flag_dias_efectivos_sospechoso=1 or dias_efectivos IS NULL "
                        "before computing CPUE."
                    ),
                },
            })

        if group_by == "year":
            rows = execute_select(
                f"SELECT anio_corte, {agg_select}"
                f"FROM conapesca_landings_historical {where} "
                f"GROUP BY anio_corte ORDER BY anio_corte",
                p, max_rows=101,
            )
            truncated = len(rows) > 100
            rows = rows[:100]
            return _json({
                "annual_trend": [dict(r) for r in rows],
                "meta": {
                    "filters": {"year": year, "estado": estado, "especie": especie,
                                "tipo_aviso": tipo_aviso, "oficina": oficina},
                    "year_count": len(rows),
                    "truncated": truncated,
                },
            })

        if group_by == "estado":
            rows = execute_select(
                f"SELECT nombre_estado, {agg_select}"
                f"FROM conapesca_landings_historical {where} "
                f"GROUP BY nombre_estado ORDER BY total_kg DESC",
                p, max_rows=51,
            )
            truncated = len(rows) > 50
            rows = rows[:50]
            return _json({
                "by_estado": [dict(r) for r in rows],
                "meta": {
                    "filters": {"year": year, "estado": estado, "especie": especie,
                                "tipo_aviso": tipo_aviso, "oficina": oficina},
                    "estado_count": len(rows),
                    "truncated": truncated,
                },
            })

        if group_by == "litoral":
            rows = execute_select(
                f"SELECT litoral, {agg_select}"
                f"FROM conapesca_landings_historical {where} "
                f"GROUP BY litoral ORDER BY total_kg DESC",
                p, max_rows=11,
            )
            truncated = len(rows) > 10
            rows = rows[:10]
            return _json({
                "by_litoral": [dict(r) for r in rows],
                "meta": {
                    "filters": {"year": year, "estado": estado, "especie": especie,
                                "tipo_aviso": tipo_aviso, "oficina": oficina},
                    "truncated": truncated,
                },
            })

        safe_limit = min(max(1, limit), 2000)
        rows = execute_select(
            f"SELECT anio_corte, fecha_aviso, tipo_aviso, folio_aviso, "
            f"nombre_estado, nombre_oficina, nombre_sitio_desembarque, "
            f"unidad_economica, nombre_especie, nombre_cientifico, "
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
            "landings": [{**dict(r), "tipo": _tipo(r.get("nombre_cientifico"))} for r in rows],
            "meta": {
                "filters": {"year": year, "estado": estado, "especie": especie,
                            "tipo_aviso": tipo_aviso, "oficina": oficina},
                "row_count": len(rows),
                "limit": safe_limit,
                "truncated": truncated,
            },
        })

    @mcp.tool()
    async def get_landings(
        year: int | None = None,
        estado: str | None = None,
        especie: str | None = None,
        tipo_aviso: str | None = None,
        oficina: str | None = None,
        limit: int = 500,
        group_by: str | None = None,
    ) -> str:
        """
        Return landing data filtered by any combination of year, estado,
        especie (partial match against nombre_especie OR nombre_cientifico),
        tipo_aviso, oficina (exact match — use get_offices() to find the exact
        nombre_oficina value).

        group_by=None (default): individual landing records (avisos de arribo),
        one row per species line per trip. Includes dias_efectivos and quality
        flags. Capped at `limit` rows (max 2000). meta.truncated is true if
        the cap was hit.

        group_by="folio": one row per trip (folio_aviso), aggregating
        peso_desembarcado_kg across all species lines of the same folio.
        Includes dias_efectivos, quality flags, and effort source. Use this
        for CPUE computation — it is the correct aggregation unit. Capped at
        5000 folios; meta.truncated is true if the cap was hit.

        group_by="year": annual aggregates — total kg, value, record count per
        year. Use for time-series / trend queries. Capped at 100 years;
        meta.truncated is true if the cap was hit.

        group_by="estado": aggregates by state — total kg, value, record count
        per estado, sorted by total_kg desc. Capped at 50 estados;
        meta.truncated is true if the cap was hit.

        group_by="litoral": aggregates by coast — total kg, value, record count
        per litoral. Capped at 10 litorales; meta.truncated is true if the cap
        was hit.
        """
        return await asyncio.to_thread(
            _get_landings_sync, year, estado, especie, tipo_aviso, oficina, limit, group_by,
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
        rows = execute_select(
            "SELECT DISTINCT nombre_especie, nombre_cientifico, "
            "kingdom, phylum, class, `order`, family, genus, worms_id, "
            "spec_code_fishbase, fishbase_database, "
            "k, loo, lmax, tmax, wmax, trophic_level, tipo_pesca_canonico "
            "FROM conapesca_landings_historical "
            "WHERE nombre_especie LIKE ? OR nombre_cientifico LIKE ? "
            "LIMIT 10",
            (f"%{especie.upper()}%", f"%{especie.upper()}%"),
        )
        return _json({
            "taxonomy": [dict(r) for r in rows],
            "meta": {"query": especie, "count": len(rows)},
        })

    @mcp.tool()
    async def get_taxonomy(especie: str) -> str:
        """
        Return the taxonomic classification for a species name
        (kingdom → genus) plus FishBase traits if available.
        """
        return await asyncio.to_thread(_get_taxonomy_sync, especie)
