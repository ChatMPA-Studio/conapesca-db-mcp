# conapesca-db-mcp

MCP server for querying the CONAPESCA Pacific landings database (2001–2026).

Exposes tools for extraction and consultation of standardized fishing landings
data (*avisos de arribo*) from the Mexican Pacific coast.

## Tools available

### Data access (raw extraction)
| Tool | Description |
|------|-------------|
| `get_years()` | Available years in the database |
| `get_estados(year?)` | States with landings |
| `get_fleet_types()` | MAYORES / MENORES / COSECHA breakdown |
| `get_species(year?, estado?, tipo_aviso?)` | Species list with total weight |
| `get_landings(year?, estado?, especie?, ...)` | Individual landing records |
| `get_offices(estado?)` | CONAPESCA offices with record counts |
| `get_taxonomy(especie)` | Taxonomy + FishBase traits for a species |

### Reporting (summaries)
| Tool | Description |
|------|-------------|
| `numeralia()` | Overall totals: records, species, states, weight |
| `landings_by_year(estado?, tipo_aviso?)` | Annual weight + value |
| `landings_by_estado(year?, tipo_aviso?)` | By state |
| `landings_by_species(year?, estado?, top_n?)` | Top species by weight |
| `landings_by_fleet_type(year?)` | MAYORES vs MENORES vs COSECHA |
| `landings_by_fishing_type(year?, estado?)` | ARTESANAL vs INDUSTRIAL vs ALTURA |

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

The table expected in MySQL is `conapesca_landings` with the same schema
as the pipeline output (`conapesca_landings_2001_2026.rds`).

## Resources

- `conapesca://data-dictionary` — full column descriptions
- `conapesca://coverage` — temporal and geographic coverage notes
