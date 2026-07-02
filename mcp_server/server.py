"""
CONAPESCA MCP Server — entry point for FastMCP.
Auto-discovers tool modules from tools/.
"""

from __future__ import annotations
import importlib
import json
import pkgutil
import logging

from fastmcp import FastMCP
from mcp_server.db import test_connection, get_db_version_log
from mcp_server.schema import build_schema_snapshot
from mcp_server.config import MCP_VERSION, TESTED_DB_VERSION

logger = logging.getLogger("conapesca_mcp.server")

mcp = FastMCP("CONAPESCA Landings")


# ── Core tools (defined inline) ---------------------------------------------

@mcp.tool()
def health_check() -> str:
    """Check database connectivity and return server status."""
    try:
        info = test_connection()
        return json.dumps({"status": "ok", "db": info})
    except Exception as e:
        return json.dumps({"status": "error", "detail": str(e)})


@mcp.tool()
def get_version() -> str:
    """Return MCP server version and DB version history from db_version_log."""
    try:
        rows = get_db_version_log()
        # Latest entry per table
        latest_per_table: dict = {}
        for row in rows:
            tbl = row.get("table_name")
            if tbl not in latest_per_table:
                latest_per_table[tbl] = row
        return json.dumps({
            "mcp_version": MCP_VERSION,
            "tested_db_version": TESTED_DB_VERSION,
            "db": latest_per_table,
        }, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def schema_snapshot() -> str:
    """Return the full schema of conapesca_landings_historical: columns, types, row count."""
    try:
        snap = build_schema_snapshot()
        return json.dumps(snap, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Static resources --------------------------------------------------------

@mcp.resource("conapesca://data-dictionary")
def data_dictionary() -> str:
    """Column descriptions for the conapesca_landings_historical table."""
    return """
# CONAPESCA Landings — Data Dictionary

## Provenance
- source_dataset : Internal provenance tag (data engineering use only)
- source_file    : Internal source filename (data engineering use only)
- litoral        : Coast (PACIFICO / GOLFO)

## Temporal
- anio_corte     : Landing year (authoritative)
- mes_corte      : Month (CSV sources only)
- fecha_aviso    : Landing date
- periodo_inicio / periodo_fin : Trip start/end dates
- duracion       : Trip duration (days)
- dias_efectivos : Effective fishing days

## Fleet
- tipo_aviso     : MAYORES (industrial) / MENORES (artisanal) / COSECHA (aquaculture)
- folio_aviso    : Unique landing report ID
- rnp_activo     : Vessel registration number
- nombre_activo  : Vessel name
- rnpa_unidad_economica : Economic unit (company/cooperative) ID
- unidad_economica      : Company/cooperative name
- numero_permiso : Fishing permit number

## Geography
- nombre_estado                    : Mexican state
- clave_oficina / nombre_oficina_canonico : CONAPESCA office
- clave_sitio_desembarque / nombre_sitio_desembarque_canonico : Landing site
- nombre_lugar_captura             : Capture area

## Species
- nombre_principal   : Common name as reported
- clave_especie      : CONAPESCA 8-char species+presentation key
- nombre_especie     : Standardized presentation name
- nombre_cientifico  : Canonical scientific name (WoRMS-validated)
- kingdom / phylum / class / order / family / genus / worms_id : WoRMS taxonomy

## FishBase traits
- k, loo, t_linfinity : von Bertalanffy growth parameters
- lmax, type_lmax     : Maximum observed length (cm) and measurement type
- tmax, wmax          : Maximum age (yr) and weight (g)
- trophic_level       : Trophic level (FishBase median)

## Catches
- peso_desembarcado_kg  : Landed weight (kg)
- peso_vivo_kg          : Live weight equivalent (kg)
- precio_pesos          : Price per kg (MXN)
- valor_pesos           : Reported total value (MXN)
- valor_pesos_estimado  : Estimated value (MXN) — uses valor_pesos if available,
                          else peso_desembarcado_kg × precio_pesos

## Enrichment flags
- manglar            : Mangrove-associated species (SI/NO)
- tipo_pesca_canonico: ARTESANAL / INDUSTRIAL / ALTURA
"""


@mcp.resource("conapesca://coverage")
def coverage_info() -> str:
    """Geographic and temporal coverage of the database."""
    return """
# CONAPESCA Landings Historical — Coverage

## Temporal
- Years: 2001–2026 (fiscal year of landing)
- Source: AWS MariaDB historical table (conapesca_landings_historical)

## Geographic
- All Mexican coasts (Pacific + Gulf + Caribbean)
- 32 states covered

## Fleet types
- MAYORES  : Industrial fleet (large vessels, offshore)
- MENORES  : Artisanal fleet (small-scale, coastal)
- COSECHA  : Aquaculture production reports

## Species
- ~1,057 unique species × presentation combinations
- Taxonomy validated via WoRMS; FishBase/SeaLifeBase traits available for most
"""


# ── Auto-discover tool modules -----------------------------------------------

def _discover_tools() -> None:
    import tools as tools_pkg
    for _finder, name, _ispkg in pkgutil.iter_modules(tools_pkg.__path__):
        try:
            module = importlib.import_module(f"tools.{name}")
            if hasattr(module, "register"):
                module.register(mcp)
                logger.info(f"Registered tool module: {name}")
        except Exception as e:
            logger.error(f"Failed to load tool module '{name}': {e}")


_discover_tools()


# ── Auto-discover skills as MCP prompts --------------------------------------

def _discover_prompts() -> None:
    from mcp_server.prompts import discover_prompts
    discover_prompts(mcp)


_discover_prompts()
