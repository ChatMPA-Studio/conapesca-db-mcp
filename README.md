# conapesca-db-mcp

MCP server for querying the CONAPESCA historical landings database (2001–2026),
covering both the Pacific and Gulf coasts of Mexico.

Exposes tools for extraction and consultation of standardized fishing landings
data (*avisos de arribo*).

## Tools available

### Data access (raw extraction)
| Tool | Description |
|------|-------------|
| `get_estados(year?)` | States with landings, optional year filter |
| `get_species(year?, estado?, tipo_aviso?, top_n?)` | Species list with total weight and value |
| `species_count()` | Unique scientific names classified by taxonomic level |
| `get_landings(year?, estado?, especie?, tipo_aviso?, oficina?, limit?, group_by?)` | Landing records or aggregates; `group_by`: `"year"`, `"estado"`, `"litoral"` |
| `get_offices(estado?)` | CONAPESCA offices with record counts |
| `get_taxonomy(especie)` | Taxonomy + FishBase traits for a species |

### Reporting (summaries)
| Tool | Description |
|------|-------------|
| `landings_by_year(estado?, tipo_aviso?)` | Annual totals: weight, value, species count |
| `landings_by_estado(year?, tipo_aviso?)` | Totals by state |
| `landings_by_fleet_type(year?)` | MAYORES vs MENORES vs COSECHA |

### Core
| Tool | Description |
|------|-------------|
| `health_check()` | DB connectivity check |
| `schema_snapshot()` | Column names, types, row count |

## Quick start (development)

### 1. Install
```bash
pip install -e ".[dev]"
```

### 2. Load dev data (SQLite)
```bash
python scripts/load_dev_data.py \
  --csv /path/to/conapesca_landings_2001_2026.csv \
  --db dev/conapesca_dev.sqlite \
  --rows 200000   # optional sample for fast testing
```

### 3. Configure
```bash
cp .env.example .env
# .env already has USE_SQLITE=true by default
```

### 4. Run
```bash
python -m mcp_server
```

## Production (MySQL)

Set in `.env`:
```
USE_SQLITE=false
CONAPESCA_DB_HOST=...
CONAPESCA_DB_USER=...
CONAPESCA_DB_PASSWORD=...
CONAPESCA_DB_NAME=...
```

The table expected in MySQL is `conapesca_landings_historical` with the same schema
as the pipeline output.

## Resources

- `conapesca://data-dictionary` — full column descriptions
- `conapesca://coverage` — temporal and geographic coverage notes
