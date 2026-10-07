# Changelog

## [Unreleased]

### Fixed
- Timeout root cause: every query opened and closed a brand-new MySQL/TLS
  connection (`mcp_server/db.py`) — replaced with a process-wide `DBUtils`
  `PooledDB` pool (`DB_POOL_SIZE`/`DB_POOL_MAX_OVERFLOW` env vars).
- Timeout root cause: all tools were sync functions, which FastMCP 2.x runs
  directly on the event loop (no thread offload) — a single slow query froze
  the entire server for every other concurrent call, including its own
  `health_check()`. All tools in `mcp_server/server.py`, `tools/data_access.py`
  and `tools/reporting.py` are now `async def` and hand their blocking DB call
  to a worker thread via `asyncio.to_thread`. Verified empirically (real HTTP
  requests against Docker containers built from before/after this change,
  2M-row SQLite dataset): the fast call went from 6.8s (blocked behind the
  slow one) to 8ms (unaffected by it).
- `get_landings(group_by=...)` docstrings falsely claimed some branches had
  "No row limit" — every query is actually capped by `security.enforce_limit`
  (default 5000 rows), silently, with no indication in the response. Fixed
  the docstrings and added a `meta.truncated` boolean to every branch
  (fetches one row past the cap to tell "exactly at the cap" apart from
  "more rows exist beyond it").

- The `[0.3.0]` docs said "No row limit" for `folio`, `year`, `year_fleet`,
  `office_year_fleet`, `estado` and `litoral`, but every mode is capped by
  `security.enforce_limit`. Docstrings corrected and `meta.truncated` added to the new
  modes too. Measured against the Dev MCP: one state-year of folios (BCS, Sinaloa and
  Sonora 2022) already exceeds the 5000-row cap, and an office with no year returned only
  2000-2009. `meta.truncated` makes that visible; covering the full history needs
  pagination, which is not part of this change.

### Changed
- `TESTED_DB_VERSION` bumped `0.0.3` → `0.0.4` (`mcp_server/config.py`) to match
  the live DB behind the `release` MCP (MySQL 8.4.11, 12,750,506 rows, 77 columns,
  7 new office geographic-key columns). Without this the server logs a
  DB-version-mismatch warning on startup. No tool query needed changes: every
  column the tools use exists in v0.0.4. The `conapesca://coverage` resource and
  the README version history were updated to match.
- `CACHE_TTL_SECONDS` default raised `300` → `3600`. Measured on the Dev MCP
  (12.75M rows), the cold calls are slow — `species_count` ~80s, `schema_snapshot`
  ~28s, `get_estados()` ~18s — and their data only changes when the table is
  reloaded, so a 5-minute TTL made one client in every five minutes pay for them.
  If the ECS task definition sets `CACHE_TTL_SECONDS` explicitly, that value wins.

- Integrated the CPUE-skill work (`[0.3.0]` below) into the async tools: the
  `nombre_principal` / `nombre_cientifico_canonico` filters, `year_from` / `year_to`,
  the `year_fleet` and `office_year_fleet` modes, `litoral` and the species columns in
  row-level records, and one row per trip in `group_by="folio"` — all keeping
  `asyncio.to_thread`, the cache and `meta.truncated`. Output contract changes for
  anything reading the raw JSON (the orchestrator and the skills do not read these
  by key): `get_species` rows carry `nombre_cientifico_canonico` instead of
  `nombre_cientifico`; `species_count` classifies on the canonical name and its summary
  key is `total_unique_nombre_cientifico_canonico`; `get_taxonomy` returns one row per
  canonical name with `nombres_especie_conapesca`, searching the canonical name first
  and falling back to `nombre_especie` (`meta.search_field` says which one answered);
  `meta.filters` lists `year_from`, `year_to`, `nombre_principal` and
  `nombre_cientifico_canonico` in every mode. The `especie` filter is unchanged.

- DB read timeout raised `60s` → `110s` and made configurable (`DB_READ_TIMEOUT_SECONDS`;
  `DB_CONNECT_TIMEOUT_SECONDS` is a separate setting and stays at `60s`). It was a constant
  in `mcp_server/db.py`. Found in the Dev logs of the cache warm-up: the first
  `species_count` query takes 60-80s on the full table, so the 60s read timeout dropped the
  connection (`OperationalError 2013`, `Lost connection to MySQL server ... read operation
  timed out`) on every attempt and its cache entry was never filled — so any question that
  needs it failed too. 110s stays under the 120s the orchestrator waits for this MCP, so
  the MCP answers with its own error before the caller gives up. The connect timeout is
  not raised: an unreachable DB should still fail fast. A longer read timeout also lets a
  runaway query hold a connection longer.
- The cache warm-up now gives up on a failing call after `MAX_RETRY_ATTEMPTS = 2` retries
  and waits for the next full pass (which tries it again), logging an error. Before, it
  retried forever with a wait capped at 10 minutes, so a query that could never finish
  (species_count in Dev, 2 minutes per attempt) kept loading the DB all day.

### Added
- `mcp_server/warmup.py` — pre-fills the cache at startup with the no-argument
  calls of `get_version`, `get_offices`, `get_estados`, `schema_snapshot` and
  `species_count`, so the first client after each deploy doesn't pay for them,
  and then renews them before they expire (refresh-ahead), so no client pays for
  them later either. It runs in a background daemon thread started from
  `mcp_server/__main__.py` (the server accepts requests immediately; the
  container health check is unaffected), through the real tools via the in-memory
  `fastmcp.Client`, one call at a time. Disable with `CACHE_WARMUP=false`. Each
  task keeps its own process-local cache.
  - A full pass starts every 80% of `CACHE_TTL_SECONDS` (48 min at the default).
    Each pass runs inside `cache.refreshing()` (new in `mcp_server/cache.py`,
    thread-local): the tool recomputes and `cache.set()` swaps in the fresh entry,
    while the threads serving clients keep reading the previous one — there is no
    cold gap. `cache.expires_at()` lets the warm-up confirm each entry was renewed.
  - A call that fails keeps its old entry and is retried on its own, waiting
    60s and doubling up to 10 min, never later than the next full pass — so a
    heavy query that keeps timing out is not hammered. A warning is logged if a
    full pass takes longer than 80% of the TTL (then raise `CACHE_TTL_SECONDS`).
  - Verified against the real HTTP server with 3s of artificial latency per query
    and `CACHE_TTL_SECONDS=60`: 375 calls to the five cached tools over ~2.5 minutes
    (three TTL expirations, three renewals) — none took longer than 0.5s, versus
    3s for an uncached call.
  - Not covered: `get_estados(year=...)` / `get_offices(estado=...)` variants, and
    the first minutes after a task starts, until its first full pass finishes.
- `mcp_server/cache.py` — in-memory TTL cache (`CACHE_TTL_SECONDS`, default 3600s)
  for near-static tools that used to hit the DB on every call:
  `get_estados`, `get_offices`, `species_count`, `get_version`, `schema_snapshot`.
- `tests/` — no automated tests existed before this pass. Added
  `test_data_access.py`, `test_reporting.py` (in-memory `fastmcp.Client`
  integration tests via a SQLite fixture DB), `test_db_pool.py` (pooling
  unit tests, MySQL faked at the `pymysql`/`dbutils` boundary), and
  `test_concurrency.py` (the regression test for the event-loop-blocking
  fix — verified it actually fails if `asyncio.to_thread` is reverted).
- `docs/rds_index_recommendations.md` — proposed indexes for the production
  RDS table, derived from the actual query shapes in the tool code. Not
  applied — this container has no visibility into the live RDS's current
  indexes; needs verification by whoever owns the database.

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
