---
name: conapesca-temporal-trends
description: Use this skill when the user asks how production, landings, or catch of a species has changed over time, asks for a trend or time series for a species, asks which state or coast produces the most of a species, or asks questions like "cómo ha cambiado", "evolución de", "producción histórica de", "dónde se pesca más", "por estado", "por litoral" for a specific fish or invertebrate species.
domain: conapesca
data-source: conapesca_landings_historical
output-type: table + chart + narrative
tags: [trends, species, temporal, geographic, landings]
status: active
version: 1.1.0
---

# CONAPESCA Species Analysis

## Purpose

Retrieve and interpret landing data for a specific species across time, states, or coasts using the CONAPESCA historical database (2001–2026).

## When to Use

- "¿Cómo ha cambiado la producción de X a lo largo del tiempo?"
- "Evolución histórica de los desembarques de X"
- "¿En qué año se pescó más X?"
- "¿En qué estado se captura más X?"
- "¿En qué litoral se desembarca más X?"
- Any question asking for a trend, ranking, or geographic breakdown for a named species

Always clarify upfront that the database covers **2001–2026** only. Do not speculate about years outside this range.

## MCP Tools

| Step | Tool | Key parameters |
|------|------|---------------|
| 1 | `get_taxonomy` | `especie` = name as provided by user |
| 2 | `get_landings` | `especie` = `nombre_especie` from step 1, `group_by` = "year" / "estado" / "litoral" |

## Workflow

1. **Identify the canonical name.** Call `get_taxonomy(especie=<user_input>)`. The user may provide a scientific name (e.g. *Sphyrna lewini*) or a common name (e.g. "tiburón martillo"). Extract the `nombre_especie` value from the result — this is the CONAPESCA standard name used in the database.

2. **Choose the aggregation** based on what the user is asking:
   - Trend over time → `get_landings(especie=<nombre_especie>, group_by="year")`
   - Which state catches the most → `get_landings(especie=<nombre_especie>, group_by="estado")`
   - Pacific vs Gulf comparison → `get_landings(especie=<nombre_especie>, group_by="litoral")`
   - Trend for a specific state → `get_landings(especie=<nombre_especie>, estado=<estado>, group_by="year")`

3. **Present the results** as a table. If `n_records` is very low for any row (< 5), flag it as potentially unreliable.

4. **Generate a chart** appropriate to the aggregation:
   - `group_by="year"` → line chart, x = year, y = total_kg. Title: "Desembarques anuales de <nombre_especie> (kg) — México". Mark the peak year.
   - `group_by="estado"` → horizontal bar chart, sorted by total_kg desc. Title: "Producción de <nombre_especie> por estado (kg acumulado)".
   - `group_by="litoral"` → bar chart. Title: "Producción de <nombre_especie> por litoral (kg acumulado)".

5. **Narrate the results.** For trends: identify peak year, trough, notable changes. For geographic: identify the leading state/coast and its share of total production.

6. **Add the coverage caveat.** Always close with: "Los datos cubren 2001–2026. No hay información disponible para años posteriores en esta base de datos."

## Interpretation Guide

- `n_records` = number of individual landing reports, not number of fishing trips. A low count with high `total_kg` is normal for industrial fleet (MAYORES).
- If `get_taxonomy` returns multiple `nombre_especie` entries for the same scientific name, call `get_landings` for each and sum across them, noting the breakdown.
- If `get_taxonomy` returns no match, inform the user and suggest trying the common name or a partial name.

## Success Criteria

- Table with one row per year, covering all years with data
- Line chart with year on x-axis and total_kg on y-axis
- Peak and trough years identified
- Coverage caveat included
- No invented data for years outside 2001–2026
