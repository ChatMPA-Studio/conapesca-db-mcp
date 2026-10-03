# Changelog

## [0.3.0] — 2026-08-17

### Changed
- `get_landings`: expanded to cover all panel skills without future per-skill patches:
  - New filters: `nombre_principal` (exact match), `nombre_cientifico_canonico` (exact match),
    `year_from` / `year_to` (inclusive year range, alternative to `year`)
  - New `group_by` modes:
    - `"year_fleet"` — annual totals per `anio_corte × tipo_aviso`. No row limit.
      Used by `conapesca-landings-timeseries` and `conapesca-comparative-summary`.
    - `"office_year_fleet"` — annual totals per `oficina × anio_corte × tipo_aviso`
      for all offices matching the filters. No row limit.
      Used by `conapesca-national-ranking` (compare one office vs national universe).
  - `group_by="folio"` keeps one row per trip (`folio_aviso`). `nombre_principal` and
    `nombre_cientifico_canonico` filter in the WHERE (server-side) but are not added to
    the SELECT or GROUP BY: grouping by species split multi-species trips into several
    rows and miscounted trips in `conapesca-cpue` (see chatmpa-skills#16).
  - Default (no group_by) now includes `nombre_principal` and `nombre_cientifico_canonico`
    in the SELECT columns.
  - All modes share a unified `active_filters` dict in `meta` for consistency.

## [0.2.0] — 2026-06-29

### Added
- `skills/conapesca-temporal-trends/` — species trend skill (temporal, by estado, by litoral)
- `mcp_server/prompts.py` — auto-discovery of skills as MCP prompts
- `skills/registry.py`, `skills/contracts/`, `skills/README.md`
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `scripts/deploy.sh`, `scripts/install_skills.sh`
- `CHANGELOG.md`

### Changed
- `get_landings`: replaced `group_by_year: bool` with `group_by: str` ("year" / "estado" / "litoral")
- `mcp_server/server.py`: added `_discover_prompts()`, fixed coverage (2001–2026, ambas costas)
- `mcp_server/db.py`: fixed `%` escaping for MySQL LIKE patterns
- `tools/data_access.py`: fixed `IndentationError` in `register()` (decorators lines 29, 48)

### Fixed
- `landings_by_estado` and `landings_by_year` failing on MySQL due to unescaped `%` in LIKE clauses
- All tools in `data_access.py` silently missing due to indentation error

## [0.1.0] — 2026-06-01

### Added
- Initial MCP server with tools: `get_estados`, `get_species`, `species_count`,
  `get_landings`, `get_offices`, `get_taxonomy`, `landings_by_year`,
  `landings_by_estado`, `landings_by_fleet_type`, `health_check`, `schema_snapshot`
