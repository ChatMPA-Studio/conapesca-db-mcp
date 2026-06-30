"""
reporting — summary and aggregation tools.
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
    def landings_by_year(
        estado: str | None = None,
        tipo_aviso: str | None = None,
    ) -> str:
        """
        Annual summary: total landed weight (kg), estimated value (MXN),
        number of records, unique species (binomial nombre_cientifico) and
        recursos (no valid nombre_cientifico) per year.
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
            f"COUNT(DISTINCT CASE "
            f"  WHEN nombre_cientifico IS NOT NULL AND TRIM(nombre_cientifico) != '' "
            f"    AND UPPER(TRIM(nombre_cientifico)) != 'ND' "
            f"    AND nombre_cientifico LIKE '% %' "
            f"  THEN nombre_cientifico END)           AS n_especies, "
            f"COUNT(DISTINCT CASE "
            f"  WHEN nombre_cientifico IS NULL OR TRIM(nombre_cientifico) = '' "
            f"    OR UPPER(TRIM(nombre_cientifico)) = 'ND' "
            f"    OR nombre_cientifico NOT LIKE '% %' "
            f"  THEN nombre_especie END)              AS n_recursos "
            f"FROM conapesca_landings_historical {where} "
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
        Landings by state: weight, value, records, unique species (binomial
        nombre_cientifico) and recursos (no valid nombre_cientifico).
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
            f"COUNT(DISTINCT CASE "
            f"  WHEN nombre_cientifico IS NOT NULL AND TRIM(nombre_cientifico) != '' "
            f"    AND UPPER(TRIM(nombre_cientifico)) != 'ND' "
            f"    AND nombre_cientifico LIKE '% %' "
            f"  THEN nombre_cientifico END)         AS n_especies, "
            f"COUNT(DISTINCT CASE "
            f"  WHEN nombre_cientifico IS NULL OR TRIM(nombre_cientifico) = '' "
            f"    OR UPPER(TRIM(nombre_cientifico)) = 'ND' "
            f"    OR nombre_cientifico NOT LIKE '% %' "
            f"  THEN nombre_especie END)            AS n_recursos "
            f"FROM conapesca_landings_historical {where} "
            f"GROUP BY nombre_estado ORDER BY total_kg DESC",
            tuple(params) or None,
            max_rows=50,
        )
        return _json({
            "by_estado": [dict(r) for r in rows],
            "meta": {"filters": {"year": year, "tipo_aviso": tipo_aviso}},
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
            f"FROM conapesca_landings_historical {where} "
            f"GROUP BY tipo_aviso ORDER BY total_kg DESC",
            tuple(params) or None,
            max_rows=10,
        )
        return _json({
            "by_fleet_type": [dict(r) for r in rows],
            "meta": {"year": year},
        })

