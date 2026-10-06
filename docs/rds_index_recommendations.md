# Index recommendations for `conapesca_landings_historical` (RDS MySQL)

## 0. What this document is, and is not

This MCP server's container has **no visibility into the live RDS instance's
actual current indexes** — it only ever holds a MySQL connection with
`SELECT` privileges on this table (see `mcp_server/db.py`), never
`SHOW INDEX` / `information_schema` access as part of normal operation, and
this analysis was written by reading the query code in `tools/data_access.py`,
`tools/reporting.py` and `mcp_server/schema.py`, not by running anything
against production. Every recommendation below is therefore a **hypothesis
inferred from query shape**, not a confirmed fix. It needs to be verified by
whoever owns the RDS instance by running Section 1 first, comparing the
`EXPLAIN` output against the "expected" notes, and only then deciding whether
Section 2's `CREATE INDEX` statements are still needed (an index recommended
here might already exist, might already be superseded by something built for
other consumers of this table, or might turn out not to move the needle once
real `EXPLAIN` rows/cost are visible).

Nothing here has been applied or tested against a MySQL server of this
table's actual size (12.75M rows). Treat every `CREATE INDEX` in Section 2 as
a draft to review, not a migration to run as-is.

## 1. Run this first: confirm current index state

```sql
SHOW INDEX FROM conapesca_landings_historical;
```

Also worth pulling once, to size how expensive a full index rebuild would be:

```sql
SELECT table_rows, data_length, index_length
FROM information_schema.tables
WHERE table_schema = DATABASE()
  AND table_name = 'conapesca_landings_historical';
```

Then run `EXPLAIN` on a representative sample of the actual queries the tools
issue (copied from the current code, with placeholder params filled in with
plausible values) to see current access type (`ALL` = full table scan is the
expected finding on most of these today) and whether MySQL's optimizer would
even pick up a new index once added:

```sql
-- get_estados(year=2023)  — tools/data_access.py _get_estados_sync
EXPLAIN SELECT DISTINCT nombre_estado FROM conapesca_landings_historical
WHERE anio_corte = 2023
ORDER BY nombre_estado;

-- get_species(year=2023, estado='SINALOA', tipo_aviso='MAYORES')
-- tools/data_access.py _get_species_sync
EXPLAIN SELECT nombre_especie, nombre_cientifico,
  ROUND(SUM(peso_desembarcado_kg), 1) AS total_kg,
  ROUND(SUM(valor_pesos_estimado), 0) AS total_valor_mxn,
  COUNT(*) AS n_records
FROM conapesca_landings_historical
WHERE anio_corte = 2023 AND nombre_estado = 'SINALOA' AND tipo_aviso = 'MAYORES'
GROUP BY nombre_especie, nombre_cientifico
ORDER BY total_kg DESC;

-- get_landings() default (ungrouped) variant, especie filter included
-- tools/data_access.py _get_landings_sync
EXPLAIN SELECT anio_corte, fecha_aviso, tipo_aviso, folio_aviso,
  nombre_estado, nombre_oficina, nombre_sitio_desembarque,
  unidad_economica, nombre_especie, nombre_cientifico,
  peso_desembarcado_kg, valor_pesos_estimado, tipo_pesca_canonico,
  dias_efectivos, dias_efectivos_fuente,
  flag_fecha_generica, flag_dias_efectivos_sospechoso, flag_periodo_futuro
FROM conapesca_landings_historical
WHERE anio_corte = 2023 AND nombre_estado = 'SINALOA'
  AND (nombre_especie LIKE '%CAMARON%' OR nombre_cientifico LIKE '%CAMARON%')
ORDER BY fecha_aviso DESC
LIMIT 500;

-- get_landings(group_by='folio') — tools/data_access.py _get_landings_sync
EXPLAIN SELECT folio_aviso, anio_corte, tipo_aviso,
  nombre_estado, nombre_oficina,
  MAX(dias_efectivos) AS dias_efectivos,
  ROUND(SUM(peso_desembarcado_kg), 3) AS peso_desembarcado_kg
FROM conapesca_landings_historical
WHERE anio_corte = 2023
GROUP BY folio_aviso, anio_corte, tipo_aviso, nombre_estado, nombre_oficina
ORDER BY anio_corte, folio_aviso;

-- get_taxonomy('camaron') — tools/data_access.py _get_taxonomy_sync
-- (the unindexable leading-wildcard LIKE — see Section 3)
EXPLAIN SELECT DISTINCT nombre_especie, nombre_cientifico,
  kingdom, phylum, class, `order`, family, genus, worms_id
FROM conapesca_landings_historical
WHERE nombre_especie LIKE '%CAMARON%' OR nombre_cientifico LIKE '%CAMARON%'
LIMIT 10;

-- landings_by_year() — tools/reporting.py _landings_by_year_sync
-- (no filters is a valid call: estado/tipo_aviso are both optional)
EXPLAIN SELECT anio_corte,
  ROUND(SUM(peso_desembarcado_kg), 1) AS total_kg,
  ROUND(SUM(valor_pesos_estimado), 0) AS total_valor_mxn,
  COUNT(*) AS n_records
FROM conapesca_landings_historical
GROUP BY anio_corte ORDER BY anio_corte;

-- schema.py get_row_count / get_coverage — unconditional full-table aggregates,
-- included for completeness: no WHERE/GROUP/ORDER means no index can help
-- these two, they will show `ALL` (full scan) before and after any migration.
EXPLAIN SELECT COUNT(*) AS n FROM conapesca_landings_historical;
EXPLAIN SELECT MIN(anio_corte) AS year_min, MAX(anio_corte) AS year_max,
  COUNT(DISTINCT nombre_estado) AS unique_estados,
  COUNT(DISTINCT tipo_aviso) AS unique_fleet_types
FROM conapesca_landings_historical;
```

Save this `EXPLAIN` output before making any change — it's the only way to
tell afterward whether a new index actually got picked up by the optimizer
(a low-selectivity column, e.g. `tipo_aviso` with only 3 distinct values
MAYORES/MENORES/COSECHA, is exactly the kind of index MySQL's cost-based
optimizer sometimes ignores in favor of a full scan even once it exists).

## 2. Proposed indexes (untested — for review, not ready to run)

These are derived from a static tally of every `WHERE` / `GROUP BY` /
`ORDER BY` column across the 16 distinct `SELECT` statements in
`tools/data_access.py` and `tools/reporting.py` (full breakdown at the end of
this section). Column order in each composite follows MySQL's leftmost-prefix
rule: a composite index also serves lookups on just its leading column(s), so
`tipo_aviso` (only 3 distinct values) is never placed leading, and the two
columns that are filtered independently of each other in different tools
(`nombre_estado`, `anio_corte`) each get to lead one index.

```sql
-- Serves: get_species, get_landings (default/estado/litoral variants),
-- landings_by_estado, get_landings(group_by='folio') partially (leftmost
-- prefix nombre_estado + anio_corte). Leads with nombre_estado since it's
-- filtered alone (without anio_corte) in get_offices/landings_by_year too,
-- where this index still helps via its leftmost column.
CREATE INDEX idx_estado_anio_tipo
  ON conapesca_landings_historical (nombre_estado, anio_corte, tipo_aviso);

-- Serves: get_estados, landings_by_fleet_type, get_landings(group_by='year'),
-- landings_by_estado (year+tipo_aviso, no estado in WHERE) — none of these
-- benefit from idx_estado_anio_tipo above because anio_corte isn't its
-- leading column. Deliberately narrower (2 columns) than a mirrored 3-column
-- twin of the index above, to limit duplicate write overhead; add tipo_aviso
-- as a third column here only if EXPLAIN in Section 1 shows it's still
-- needed once this exists.
CREATE INDEX idx_anio_tipo
  ON conapesca_landings_historical (anio_corte, tipo_aviso);

-- Serves: get_landings(oficina=...) filter, get_offices (GROUP BY + ORDER BY
-- start with nombre_oficina in one form or nombre_estado in the other —
-- get_offices's own WHERE nombre_estado='...' is already covered by the
-- index above, so this one is for the exact-match nombre_oficina filter and
-- for get_landings(group_by='folio')'s GROUP BY, which includes it).
CREATE INDEX idx_nombre_oficina
  ON conapesca_landings_historical (nombre_oficina);

-- Serves: get_landings(group_by='folio')'s GROUP BY / ORDER BY
-- (folio_aviso, anio_corte). Does not fully cover that GROUP BY (which also
-- groups by tipo_aviso, nombre_estado, nombre_oficina) — MySQL will still
-- need a temp table for the trailing group columns, but this at least lets
-- it seek/stream by folio_aviso instead of scanning the full table.
CREATE INDEX idx_folio_anio
  ON conapesca_landings_historical (folio_aviso, anio_corte);

-- Serves: get_landings()'s default (ungrouped) variant's
-- `ORDER BY fecha_aviso DESC` when no WHERE filter narrows the row set much
-- (e.g. no year/estado given) — otherwise idx_estado_anio_tipo /
-- idx_anio_tipo above should already limit the row set enough that this
-- ORDER BY is a cheap in-memory sort. DESC requires MySQL 8+ to be stored
-- descending rather than scanned in reverse; confirm the RDS engine version
-- before assuming this avoids a filesort.
CREATE INDEX idx_fecha_aviso
  ON conapesca_landings_historical (fecha_aviso DESC);
```

Notes / caveats that apply to all five:

- **Not free.** Each index adds storage (roughly proportional to the
  indexed columns' width × 12.75M rows) and a write-time cost on every
  `INSERT`/`UPDATE` against this table (rebuild/`load_dev_data.py`-style bulk
  loads will get slower). If this table is bulk-reloaded periodically,
  consider whether these should be dropped before a load and recreated after.
- **`idx_estado_anio_tipo` and `idx_anio_tipo` overlap in intent.** Section 1's
  `EXPLAIN` output, or better, a look at actual production query frequency
  (slow query log / `performance_schema.events_statements_summary_by_digest`
  if enabled), should decide whether both are worth keeping or whether one
  subsumes the other in practice for this specific traffic pattern.
- **`SUM(peso_desembarcado_kg)` / `SUM(valor_pesos_estimado)` ordering
  (`ORDER BY total_kg DESC` etc.) is not addressed here** — these are
  post-aggregation aliases with no ordinary column index that can help; the
  server will do a temp table + filesort regardless of any index on the base
  columns. Out of scope for an index migration.
- None of this touches `mcp_server/schema.py`'s `get_row_count` /
  `get_coverage` — both are unconditional full-table aggregates with no
  `WHERE`/`GROUP`/`ORDER` at all, so no index changes them. If their latency
  matters, the fix is a maintained counter/materialized summary row, not an
  index.

### Column frequency this is based on (for reference)

| Column | WHERE | GROUP BY | ORDER BY | Notes |
|---|---|---|---|---|
| `anio_corte` | 9 queries | 2 | 3 | universal `year` filter |
| `nombre_estado` | 8 | 4 | 2 | tied with `anio_corte` as hottest |
| `nombre_cientifico` | 9 (3 via `TRIM()`/`UPPER()`, non-sargable) | 2 | 0 | see Section 3 |
| `tipo_aviso` | 8 | 2 | 0 | only 3 distinct values — low selectivity |
| `nombre_oficina` | 5 | 2 | 1 | exact-match `oficina` filter |
| `nombre_especie` | 6 (via the `especie` LIKE filter) | 1 | 0 | see Section 3 |
| `folio_aviso` | 0 | 2 | 2 | only in `get_landings(group_by='folio')` |
| `fecha_aviso` | 0 | 0 | 1 | default `get_landings` ORDER BY |
| `litoral` | 0 | 1 | 0 | only in `get_landings(group_by='litoral')` |

`peso_desembarcado_kg` / `valor_pesos_estimado` appear only inside
`SUM(...)`; taxonomy columns and `dias_efectivos` only in `SELECT`/`MAX()` —
none of those are indexable filter/sort targets as used today.

## 3. The `especie LIKE '%...%'` problem — indexing cannot fix this

`get_landings(especie=...)` and `get_taxonomy(especie=...)` both build:

```python
# tools/data_access.py, _get_landings_sync
conditions.append("(nombre_especie LIKE ? OR nombre_cientifico LIKE ?)")
params.extend([f"%{especie.upper()}%", f"%{especie.upper()}%"])
```

```python
# tools/data_access.py, _get_taxonomy_sync
"WHERE nombre_especie LIKE ? OR nombre_cientifico LIKE ? "
(f"%{especie.upper()}%", f"%{especie.upper()}%")
```

A **leading** `%` wildcard defeats a standard B-tree index: MySQL can only
use such an index to seek by matching a left-anchored prefix, so
`LIKE '%CAMARON%'` still forces a full scan of the table (or of the index, if
a covering index exists) to evaluate every row's substring — no index on
`nombre_especie` or `nombre_cientifico`, however it's shaped, changes that.
Against 12.75M rows this is the query shape most likely to still be slow (or
still time out) even after every index in Section 2 is applied. It needs a
different mechanism, not a different index. Two concrete options:

### Option A — MySQL `FULLTEXT` index + `MATCH ... AGAINST` (natural language mode)

```sql
ALTER TABLE conapesca_landings_historical
  ADD FULLTEXT INDEX ft_especie_cientifico (nombre_especie, nombre_cientifico);
```

```sql
SELECT DISTINCT nombre_especie, nombre_cientifico
FROM conapesca_landings_historical
WHERE MATCH(nombre_especie, nombre_cientifico) AGAINST ('camaron' IN NATURAL LANGUAGE MODE);
```

- **Migration effort:** low — one `ALTER TABLE`, no new table, no backfill.
  Building a `FULLTEXT` index over 12.75M rows still needs to run as an
  online/maintenance-window DDL on RDS (test the duration on a snapshot/
  staging copy — do not run untested on prod).
- **Application changes:** `tools/data_access.py`'s two query builders swap
  their `LIKE` clause for `MATCH ... AGAINST`; the `%`-wrapping and `.upper()`
  normalization in both functions goes away (`FULLTEXT` is case-insensitive
  and tokenizes on its own).
- **Result semantics change:** natural language mode ranks by relevance and
  uses MySQL's built-in stopword list and a default 50%-relevance threshold
  (words in more than ~50% of rows are ignored) — a search for a very common
  species name could behave differently than the current substring match.
  `BOOLEAN MODE` (`MATCH ... AGAINST ('camaron*' IN BOOLEAN MODE)`) avoids
  the 50% threshold and supports a trailing wildcard for prefix search, but
  still isn't a true substring match (no interior-wildcard equivalent to
  `%camaron%`) — species names are usually multi-word Spanish common names
  or Latin binomials, so word-boundary tokenization should cover most real
  queries, but this should be checked against a sample of real `especie=`
  values callers actually pass before committing to it.
- **Minimum MySQL version:** `FULLTEXT` on InnoDB requires MySQL 5.6+;
  confirm the RDS engine version, though this is very unlikely to be a
  blocker in 2026.

### Option B — normalized species lookup/catalog table

Build a small separate table, one row per distinct
`(nombre_especie, nombre_cientifico)` pair (bounded — likely low thousands of
rows, not 12.75M), maintained by a periodic job (or a trigger, or as part of
whatever process refreshes `conapesca_landings_historical` — see
`scripts/load_dev_data.py` / `mcp_server/db.py`'s `get_db_version_log` for
the existing refresh pattern this could hook into):

```sql
CREATE TABLE species_catalog (
  nombre_especie     VARCHAR(255) NOT NULL,
  nombre_cientifico  VARCHAR(255),
  PRIMARY KEY (nombre_especie),
  INDEX idx_especie_prefix (nombre_especie),
  INDEX idx_cientifico_prefix (nombre_cientifico)
);
```

Tool code would first resolve `especie=` against this small table (an
ordinary indexed prefix/exact match — cheap even without `FULLTEXT`, since
the table is tiny), then use the matched exact `nombre_especie` /
`nombre_cientifico` value(s) as an `=` (or `IN (...)`) filter against
`conapesca_landings_historical` — turning an unindexable substring scan of
12.75M rows into an indexed equality lookup once `idx_estado_anio_tipo` /
`idx_anio_tipo` from Section 2 (or a dedicated index on the species columns)
are in place.

- **Migration effort:** higher than Option A — new table, a population/
  refresh job to keep it in sync with the main table (species names are
  presumably static-ish reference data, but any refresh of the main table
  needs this kept current too), and it only does exact or prefix matching
  unless paired with its own fuzzy step.
- **Application changes:** more than Option A — `_get_landings_sync` and
  `_get_taxonomy_sync` both need a two-step lookup (resolve against
  `species_catalog`, then query the main table with the resolved value(s))
  instead of a single query.
- **Result semantics change:** smallest of the two options if the catalog is
  built to preserve substring matching (e.g. keep `LIKE '%...%'` against the
  catalog table itself — still non-sargable, but now against a table small
  enough that a full scan of it is cheap); becomes a bigger semantics change
  only if it's narrowed to prefix/exact matching for speed.
- **Upside:** this table would double as the single place to dedupe/curate
  `nombre_especie` spelling variants, which today land as separate group-by
  buckets in `get_species` / `species_count` — a benefit beyond just fixing
  the LIKE performance problem, but also scope creep beyond "add an index."

### Recommendation

Option A is the smaller, lower-risk change (one DDL statement, contained
query-builder edit, no new refresh job) and is the natural first thing to
prototype against a staging copy of the table. Option B is worth it only if
the relevance-ranking/tokenization behavior of `FULLTEXT` natural language
mode turns out to not match what CONAPESCA species-name searches actually
need, or if the species-catalog's deduping side benefit is independently
wanted. Either way: prototype against a non-production copy of the table
first and compare real result sets for a handful of known `especie=` values
against today's `LIKE '%...%'` output before switching the tool code over —
this is exactly the kind of change where "faster but silently returns
different rows" is worse than "slow."
