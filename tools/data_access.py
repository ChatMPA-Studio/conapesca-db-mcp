"""
data_access — basic query and extraction tools.
No aggregations, no statistics: just filtered raw data.
"""

from __future__ import annotations
import json
from decimal import Decimal
from mcp_server.db import execute_select


def _json(obj) -> str:
    """Serialize to JSON, converting Decimal and None gracefully."""
    def _default(v):
        if isinstance(v, Decimal):
            return float(v)
        raise TypeError(f"Not serializable: {type(v)}")
    return json.dumps(obj, default=_default, ensure_ascii=False)


def register(mcp) -> None:

    @mcp.tool()
    def get_years() -> str:
        """List all available landing years in the database."""
        rows = execute_select(
            "SELECT DISTINCT anio_corte FROM conapesca_landings "
            "WHERE anio_corte IS NOT NULL ORDER BY anio_corte"
        )
        years = [r["anio_corte"] for r in rows]
        return _json({"years": years, "meta": {"count": len(years)}})

    @mcp.tool()
    def get_estados(year: int | None = None) -> str:
        """
        List all Mexican states (nombre_estado) present in the landings.
        Optionally filter by year.
        """
        conditions, params = [], []
        if year:
            conditions.append("anio_corte = ?")
            params.append(year)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = execute_select(
            f"SELECT DISTINCT nombre_estado FROM conapesca_landings "
            f"{where} ORDER BY nombre_estado",
            tuple(params) or None,
        )
        estados = [r["nombre_estado"] for r in rows if r["nombre_estado"]]
        return _json({"estados": estados, "meta": {"count": len(estados), "year": year}})

    @mcp.tool()
    def get_fleet_types() -> str:
        """
        Return the fleet types (tipo_aviso): MAYORES (industrial),
        MENORES (artisanal), COSECHA (aquaculture).
        """
        rows = execute_select(
            "SELECT tipo_aviso, COUNT(*) AS n_records "
            "FROM conapesca_landings "
            "WHERE tipo_aviso IS NOT NULL "
            "GROUP BY tipo_aviso ORDER BY n_records DESC"
        )
        return _json({"fleet_types": [dict(r) for r in rows]})

    @mcp.tool()
    def get_species(
        year: int | None = None,
        estado: str | None = None,
        tipo_aviso: str | None = None,
        top_n: int = 50,
    ) -> str:
        """
        List species (nombre_especie + nombre_cientifico) present in the
        landings, with their total landed weight.
        Filters: year, estado, tipo_aviso (MAYORES/MENORES/COSECHA).
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
        safe_n = min(max(1, top_n), 500)
        rows = execute_select(
            f"SELECT nombre_especie, nombre_cientifico, "
            f"SUM(peso_desembarcado_kg) AS total_kg, COUNT(*) AS n_records "
            f"FROM conapesca_landings {where} "
            f"GROUP BY nombre_especie, nombre_cientifico "
            f"ORDER BY total_kg DESC",
            tuple(params) or None,
            max_rows=safe_n,
        )
        return _json({
            "species": [dict(r) for r in rows],
            "meta": {
                "filters": {"year": year, "estado": estado, "tipo_aviso": tipo_aviso},
                "count": len(rows),
            },
        })

    @mcp.tool()
    def get_landings(
        year: int | None = None,
        estado: str | None = None,
        especie: str | None = None,
        tipo_aviso: str | None = None,
        oficina: str | None = None,
        limit: int = 500,
    ) -> str:
        """
        Return individual landing records (avisos de arribo).
        Each row is one species line within one fishing trip report.
        Filters: year, estado, especie (nombre_especie), tipo_aviso, oficina.
        """
        conditions, params = [], []
        if year:
            conditions.append("anio_corte = ?")
            params.append(year)
        if estado:
            conditions.append("nombre_estado = ?")
            params.append(estado.upper())
        if especie:
            conditions.append("nombre_especie LIKE ?")
            params.append(f"%{especie.upper()}%")
        if tipo_aviso:
            conditions.append("tipo_aviso = ?")
            params.append(tipo_aviso.upper())
        if oficina:
            conditions.append("nombre_oficina_canonico LIKE ?")
            params.append(f"%{oficina.upper()}%")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        safe_limit = min(max(1, limit), 2000)
        rows = execute_select(
            f"SELECT anio_corte, fecha_aviso, tipo_aviso, folio_aviso, "
            f"nombre_estado, nombre_oficina_canonico, nombre_sitio_desembarque_canonico, "
            f"unidad_economica, nombre_especie, nombre_cientifico, "
            f"peso_desembarcado_kg, valor_pesos_estimado, tipo_pesca_canonico "
            f"FROM conapesca_landings {where} "
            f"ORDER BY fecha_aviso DESC",
            tuple(params) or None,
            max_rows=safe_limit,
        )
        return _json({
            "landings": [dict(r) for r in rows],
            "meta": {
                "filters": {
                    "year": year, "estado": estado, "especie": especie,
                    "tipo_aviso": tipo_aviso, "oficina": oficina,
                },
                "row_count": len(rows),
                "limit": safe_limit,
            },
        })

    @mcp.tool()
    def get_offices(estado: str | None = None) -> str:
        """
        List fishing offices (oficinas CONAPESCA) with their state and
        number of landing records.
        """
        conditions, params = [], []
        if estado:
            conditions.append("nombre_estado = ?")
            params.append(estado.upper())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = execute_select(
            f"SELECT nombre_oficina_canonico, nombre_estado, "
            f"COUNT(*) AS n_records "
            f"FROM conapesca_landings {where} "
            f"GROUP BY nombre_oficina_canonico, nombre_estado "
            f"ORDER BY nombre_estado, nombre_oficina_canonico",
            tuple(params) or None,
        )
        return _json({
            "offices": [dict(r) for r in rows],
            "meta": {"estado": estado, "count": len(rows)},
        })

    @mcp.tool()
    def get_taxonomy(especie: str) -> str:
        """
        Return the taxonomic classification for a species name
        (kingdom → genus) plus FishBase traits if available.
        """
        rows = execute_select(
            "SELECT DISTINCT nombre_especie, nombre_cientifico, "
            "kingdom, phylum, class, `order`, family, genus, worms_id, "
            "spec_code_fishbase, fishbase_database, "
            "k, loo, lmax, tmax, wmax, trophic_level, tipo_pesca_canonico, manglar "
            "FROM conapesca_landings "
            "WHERE nombre_especie LIKE ? OR nombre_cientifico LIKE ? "
            "LIMIT 10",
            (f"%{especie.upper()}%", f"%{especie.upper()}%"),
        )
        return _json({
            "taxonomy": [dict(r) for r in rows],
            "meta": {"query": especie, "count": len(rows)},
        })
