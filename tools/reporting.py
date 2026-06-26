"""
reporting — summary and numeralia tools.
Aggregations without statistical modelling.
"""

from __future__ import annotations
import json
from decimal import Decimal
from mcp_server.db import execute_select


def _json(obj) -> str:
    def _default(v):
        if isinstance(v, Decimal):
            return float(v)
        raise TypeError(f"Not serializable: {type(v)}")
    return json.dumps(obj, default=_default, ensure_ascii=False)


def register(mcp) -> None:

    @mcp.tool()
    def numeralia() -> str:
        """
        Overall database statistics: total records, unique species,
        states, offices, year range, and total landed weight.
        """
        rows = execute_select(
            "SELECT "
            "COUNT(*)                          AS total_records, "
            "COUNT(DISTINCT nombre_especie)    AS unique_species, "
            "COUNT(DISTINCT nombre_estado)     AS unique_estados, "
            "COUNT(DISTINCT nombre_oficina_canonico) AS unique_offices, "
            "COUNT(DISTINCT folio_aviso)       AS unique_folios, "
            "MIN(anio_corte)                   AS year_min, "
            "MAX(anio_corte)                   AS year_max, "
            "ROUND(SUM(peso_desembarcado_kg) / 1000.0, 1) AS total_tonnes "
            "FROM conapesca_landings"
        )
        return _json({"numeralia": dict(rows[0]) if rows else {}})

    @mcp.tool()
    def landings_by_year(
        estado: str | None = None,
        tipo_aviso: str | None = None,
    ) -> str:
        """
        Annual summary: total landed weight (kg), estimated value (MXN),
        number of records and unique species per year.
        Filters: estado, tipo_aviso.
        """
        conditions, params = [], []
        if estado:
            conditions.append("nombre_estado = ?")
            params.append(estado.upper())
        if tipo_aviso:
            conditions.append("tipo_aviso = ?")
            params.append(tipo_aviso.upper())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = execute_select(
            f"SELECT anio_corte, "
            f"ROUND(SUM(peso_desembarcado_kg), 1)    AS total_kg, "
            f"ROUND(SUM(valor_pesos_estimado), 0)     AS total_valor_mxn, "
            f"COUNT(*)                                AS n_records, "
            f"COUNT(DISTINCT nombre_especie)          AS n_species "
            f"FROM conapesca_landings {where} "
            f"GROUP BY anio_corte ORDER BY anio_corte",
            tuple(params) or None,
            max_rows=100,
        )
        return _json({
            "annual_landings": [dict(r) for r in rows],
            "meta": {"filters": {"estado": estado, "tipo_aviso": tipo_aviso}},
        })

    @mcp.tool()
    def landings_by_estado(
        year: int | None = None,
        tipo_aviso: str | None = None,
    ) -> str:
        """
        Landings by state: weight, value, records, species count.
        Filters: year, tipo_aviso.
        """
        conditions, params = [], []
        if year:
            conditions.append("anio_corte = ?")
            params.append(year)
        if tipo_aviso:
            conditions.append("tipo_aviso = ?")
            params.append(tipo_aviso.upper())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = execute_select(
            f"SELECT nombre_estado, "
            f"ROUND(SUM(peso_desembarcado_kg), 1)  AS total_kg, "
            f"ROUND(SUM(valor_pesos_estimado), 0)   AS total_valor_mxn, "
            f"COUNT(*)                              AS n_records, "
            f"COUNT(DISTINCT nombre_especie)        AS n_species "
            f"FROM conapesca_landings {where} "
            f"GROUP BY nombre_estado ORDER BY total_kg DESC",
            tuple(params) or None,
            max_rows=50,
        )
        return _json({
            "by_estado": [dict(r) for r in rows],
            "meta": {"filters": {"year": year, "tipo_aviso": tipo_aviso}},
        })

    @mcp.tool()
    def landings_by_species(
        year: int | None = None,
        estado: str | None = None,
        tipo_aviso: str | None = None,
        top_n: int = 20,
    ) -> str:
        """
        Top species by landed weight. Returns nombre_especie,
        nombre_cientifico, total_kg, total_valor_mxn, n_records.
        """
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
        safe_n = min(max(1, top_n), 200)
        rows = execute_select(
            f"SELECT nombre_especie, nombre_cientifico, "
            f"ROUND(SUM(peso_desembarcado_kg), 1) AS total_kg, "
            f"ROUND(SUM(valor_pesos_estimado), 0)  AS total_valor_mxn, "
            f"COUNT(*) AS n_records "
            f"FROM conapesca_landings {where} "
            f"GROUP BY nombre_especie, nombre_cientifico "
            f"ORDER BY total_kg DESC",
            tuple(params) or None,
            max_rows=safe_n,
        )
        return _json({
            "top_species": [dict(r) for r in rows],
            "meta": {
                "filters": {"year": year, "estado": estado, "tipo_aviso": tipo_aviso},
                "top_n": safe_n,
            },
        })

    @mcp.tool()
    def landings_by_fleet_type(year: int | None = None) -> str:
        """
        Landings split by fleet type (MAYORES / MENORES / COSECHA):
        weight, value, record count per type.
        """
        conditions, params = [], []
        if year:
            conditions.append("anio_corte = ?")
            params.append(year)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = execute_select(
            f"SELECT tipo_aviso, "
            f"ROUND(SUM(peso_desembarcado_kg), 1) AS total_kg, "
            f"ROUND(SUM(valor_pesos_estimado), 0)  AS total_valor_mxn, "
            f"COUNT(*) AS n_records "
            f"FROM conapesca_landings {where} "
            f"GROUP BY tipo_aviso ORDER BY total_kg DESC",
            tuple(params) or None,
            max_rows=10,
        )
        return _json({
            "by_fleet_type": [dict(r) for r in rows],
            "meta": {"year": year},
        })

    @mcp.tool()
    def landings_by_fishing_type(
        year: int | None = None,
        estado: str | None = None,
    ) -> str:
        """
        Landings by fishing type (tipo_pesca_canonico):
        ARTESANAL, INDUSTRIAL, ALTURA. Weight and value per type.
        """
        conditions, params = [], []
        if year:
            conditions.append("anio_corte = ?")
            params.append(year)
        if estado:
            conditions.append("nombre_estado = ?")
            params.append(estado.upper())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = execute_select(
            f"SELECT tipo_pesca_canonico, "
            f"ROUND(SUM(peso_desembarcado_kg), 1) AS total_kg, "
            f"ROUND(SUM(valor_pesos_estimado), 0)  AS total_valor_mxn, "
            f"COUNT(*) AS n_records "
            f"FROM conapesca_landings {where} "
            f"GROUP BY tipo_pesca_canonico ORDER BY total_kg DESC",
            tuple(params) or None,
            max_rows=10,
        )
        return _json({
            "by_fishing_type": [dict(r) for r in rows],
            "meta": {"filters": {"year": year, "estado": estado}},
        })
