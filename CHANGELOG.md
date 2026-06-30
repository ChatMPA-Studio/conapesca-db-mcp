# Changelog

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
