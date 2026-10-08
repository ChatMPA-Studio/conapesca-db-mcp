#!/usr/bin/env python3
"""
scripts/bench_mcp.py
--------------------
Mide el MISMO conjunto de preguntas contra un MCP de CONAPESCA y compara dos
corridas (por ejemplo el MCP viejo del droplet contra el nuevo de ECS) para
mostrar qué optimizaciones se notan y cuánto.

Solo lectura: todas las llamadas son consultas (get_*, *_count, health_check).
Usa solo `fastmcp`, que ya es dependencia del proyecto.

Uso:
    # 1) MCP nuevo (gateway público + API key)
    MCP_API_KEY=... python scripts/bench_mcp.py run --name nuevo \\
        --url https://mcp.chatmpa.ai/conapesca \\
        --header X-MCP-Api-Key --header-env MCP_API_KEY --out nuevo.json

    # 2) MCP viejo (droplet + Basic Auth); la credencial va en una variable de
    #    entorno para que no quede en el historial del shell
    OLD_AUTH='Basic <token>' python scripts/bench_mcp.py run --name viejo \\
        --url http://<host>/conapesca/ \\
        --header Authorization --header-env OLD_AUTH --out viejo.json

    # 3) Tabla lado a lado
    python scripts/bench_mcp.py compare viejo.json nuevo.json

    # Ver la lista de preguntas sin ejecutar nada
    python scripts/bench_mcp.py list

Cómo leer los tiempos
  * "base" es una llamada sin base de datos (list_tools): red + servidor. Los
    tiempos "netos" ya la restan, para que la distancia a cada servidor no
    cuente como lentitud del MCP.
  * 1ª es la primera llamada de la pregunta; mediana(2ª..) las siguientes. Con
    caché la 1ª ya sale caliente (warm-up); sin caché todas pagan lo mismo.
  * Ejecuta cada servidor a una hora tranquila: comparten la base de datos con
    otros usuarios, y las preguntas pesadas escanean tablas de ~12M filas.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

# ── la lista de preguntas ------------------------------------------------------------
# id, grupo, pregunta en lenguaje natural, tool, args, repeticiones, qué demuestra
PROBES: list[dict] = [
    # A. catálogo: lo que el caché + warm-up deja caliente
    dict(id="A1", group="caché", q="¿Qué estados tienen registros de desembarque?", tool="get_estados", args={}, reps=3,
         shows="caché (el viejo consulta la tabla entera cada vez)"),
    dict(id="A2", group="caché", q="¿Qué oficinas de pesca hay y cuántos registros tiene cada una?", tool="get_offices", args={}, reps=3,
         shows="caché"),
    dict(id="A3", group="caché", q="¿Cuántas especies distintas se han registrado?", tool="species_count", args={}, reps=3,
         shows="caché + timeout de lectura (la consulta tarda 60-100 s)"),
    dict(id="A4", group="caché", q="¿Cuál es el esquema de la tabla?", tool="schema_snapshot", args={}, reps=3,
         shows="caché"),
    dict(id="A5", group="caché", q="¿Qué versión de datos se usa?", tool="get_version", args={}, reps=3,
         shows="caché (consulta chica: aísla el costo de conexión)"),
    dict(id="A6", group="caché", q="¿Cuántos registros tiene la base y de qué años?", tool="record_count", args={}, reps=2,
         shows="COUNT(*) sobre toda la tabla; sin caché en ninguna versión"),
    # B. consultas filtradas: lo que los índices nuevos aceleran
    dict(id="B1", group="índices", q="5 especies con más peso desembarcado en Sonora en 2022, flota mayor", tool="get_species",
         args={"year": 2022, "estado": "SONORA", "tipo_aviso": "MAYORES", "top_n": 5}, reps=3, shows="índice (estado, año, tipo)"),
    dict(id="B2", group="índices", q="5 especies con más peso desembarcado en Baja California Sur en 2022", tool="get_species",
         args={"year": 2022, "estado": "BAJA CALIFORNIA SUR", "top_n": 5}, reps=3, shows="índice (estado, año)"),
    dict(id="B3", group="índices", q="¿Qué estados tienen registros en 2021?", tool="get_estados", args={"year": 2021}, reps=3,
         shows="índice (año, estado) + caché por año"),
    dict(id="B4", group="índices", q="Un registro de Sinaloa en 2023", tool="get_landings",
         args={"year": 2023, "estado": "SINALOA", "limit": 1}, reps=3, shows="índice (estado, año); el resultado es mínimo"),
    dict(id="B5", group="índices", q="Desembarques por año de la oficina Cabo San Lucas (la de Cabo Pulmo)", tool="get_landings",
         args={"estado": "BAJA CALIFORNIA SUR", "oficina": "CABO SAN LUCAS", "group_by": "year"}, reps=3, shows="índice por oficina"),
    dict(id="B6", group="índices", q="Viajes de Cabo San Lucas en 2022 (insumo del CPUE)", tool="get_landings",
         args={"estado": "BAJA CALIFORNIA SUR", "oficina": "CABO SAN LUCAS", "year": 2022, "group_by": "folio"}, reps=3,
         shows="consulta que usa el CPUE, acotada por año"),
    # C. pesadas: escaneos grandes (dependen sobre todo de la base de datos)
    dict(id="C1", group="pesadas", q="Desembarques nacionales por año (todo el histórico)", tool="get_landings",
         args={"group_by": "year"}, reps=2, heavy=True, shows="agregación completa; ninguna versión la cachea"),
    dict(id="C2", group="pesadas", q="Desembarques por estado en 2023", tool="landings_by_estado", args={"year": 2023}, reps=2, heavy=True,
         shows="agregación por año"),
    # D. honestidad del dato: el viejo corta en silencio, el nuevo lo avisa (no es de velocidad)
    dict(id="D1", group="honestidad", q="2,000 registros de Sonora en 2022 (se corta por tope)", tool="get_landings",
         args={"year": 2022, "estado": "SONORA", "limit": 2000}, reps=1, heavy=True,
         shows="el nuevo trae meta.truncated; el viejo no avisa"),
    dict(id="D2", group="honestidad", q="Viajes de Baja California Sur en 2022 (más de 5,000)", tool="get_landings",
         args={"estado": "BAJA CALIFORNIA SUR", "year": 2022, "group_by": "folio"}, reps=1, heavy=True,
         shows="tope de 5,000 filas: el nuevo avisa con meta.truncated"),
]

SLOW_FOR_CONCURRENCY = dict(tool="landings_by_estado", args={"year": 2023})   # ~10 s
POOL_CALLS = 20


# ── medición --------------------------------------------------------------------------

def _reps_for(probe: dict, max_reps: int | None) -> int:
    """Repeticiones de una pregunta, con tope opcional (--max-reps) para no cargar un servidor sin caché."""
    return min(probe["reps"], max_reps) if max_reps else probe["reps"]


def _make_client(url: str, header: str | None, header_env: str | None):
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    headers = {}
    if header:
        value = os.environ.get(header_env or "", "")
        if not value:
            sys.exit(f"La variable de entorno {header_env!r} está vacía: ahí va el valor del encabezado {header!r}.")
        headers[header] = value
    return Client(StreamableHttpTransport(url, headers=headers), timeout=300)


def _summarize(text: str) -> dict:
    """Qué devolvió, sin guardar los datos: filas, si trae meta.truncated y su valor."""
    try:
        d = json.loads(text)
    except Exception:
        return {"rows": None, "truncated": None, "has_truncated_key": False}
    meta = d.get("meta") if isinstance(d, dict) else None
    lists = [v for k, v in d.items() if isinstance(v, list)] if isinstance(d, dict) else []
    return {
        "rows": len(lists[0]) if lists else None,
        "has_truncated_key": isinstance(meta, dict) and "truncated" in meta,
        "truncated": meta.get("truncated") if isinstance(meta, dict) else None,
    }


async def _timed(client, tool: str, args: dict) -> dict:
    t0 = time.perf_counter()
    try:
        r = await client.call_tool(tool, args, raise_on_error=False)
        dt = time.perf_counter() - t0
        text = r.content[0].text if r.content else ""
        if r.is_error:
            return {"s": dt, "ok": False, "error": text[:160]}
        return {"s": dt, "ok": True, **_summarize(text)}
    except Exception as e:  # red, auth, timeout del cliente
        return {"s": time.perf_counter() - t0, "ok": False, "error": f"{type(e).__name__}: {str(e)[:140]}"}


async def run(args) -> None:
    only = set(args.only.split(",")) if args.only else None
    probes = [p for p in PROBES if (only is None or p["id"] in only) and not (args.skip_heavy and p.get("heavy"))]
    async with _make_client(args.url, args.header, args.header_env) as client:
        # base de red: list_tools no toca la base de datos
        base = []
        for _ in range(7):
            t0 = time.perf_counter(); await client.list_tools(); base.append(time.perf_counter() - t0)
        base_median = statistics.median(base)
        print(f"[{args.name}] base (red + servidor, sin BD): {base_median*1000:.0f} ms", flush=True)

        results = []
        for p in probes:
            calls = [await _timed(client, p["tool"], p["args"]) for _ in range(_reps_for(p, args.max_reps))]
            results.append({k: p[k] for k in ("id", "group", "q", "tool", "args", "shows")} | {"calls": calls})
            first = calls[0]
            tag = f"{first['s']:.2f}s" if first["ok"] else f"ERROR {first.get('error','')[:60]}"
            rest = [c["s"] for c in calls[1:] if c["ok"]]
            print(f"[{args.name}] {p['id']:3} {p['tool']:20} 1ª={tag:>9}"
                  + (f"  mediana(2ª..)={statistics.median(rest):.2f}s" if rest else ""), flush=True)

        extra = {}
        if not args.skip_extras:
            # pool: consultas mínimas seguidas (cada una ejecuta SELECT VERSION() en la BD)
            seq = [(await _timed(client, "health_check", {}))["s"] for _ in range(POOL_CALLS)]
            extra["pool"] = {"calls": POOL_CALLS, "median_s": statistics.median(seq), "p95_s": sorted(seq)[int(0.95 * len(seq)) - 1],
                             "first_s": seq[0]}
            print(f"[{args.name}] pool: {POOL_CALLS}× health_check mediana={extra['pool']['median_s']*1000:.0f} ms "
                  f"p95={extra['pool']['p95_s']*1000:.0f} ms", flush=True)
            # concurrencia: una consulta lenta y, 0.3 s después, una mínima
            slow = asyncio.ensure_future(_timed(client, SLOW_FOR_CONCURRENCY["tool"], SLOW_FOR_CONCURRENCY["args"]))
            await asyncio.sleep(0.3)
            fast = await _timed(client, "health_check", {})
            slow_r = await slow
            extra["concurrency"] = {"slow_tool": SLOW_FOR_CONCURRENCY["tool"], "slow_s": slow_r["s"], "fast_s": fast["s"],
                                    "fast_ok": fast["ok"], "slow_ok": slow_r["ok"]}
            print(f"[{args.name}] concurrencia: consulta lenta {slow_r['s']:.1f}s; health_check lanzado 0.3 s después: "
                  f"{fast['s']:.2f}s", flush=True)

    out = {"name": args.name, "url": args.url, "when": datetime.now(timezone.utc).isoformat(),
           "base_s": base_median, "probes": results, "extra": extra}
    with open(args.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[{args.name}] guardado en {args.out}")


# ── comparación -----------------------------------------------------------------------

def _stats(probe: dict, base: float) -> dict | None:
    ok = [c for c in probe["calls"] if c["ok"]]
    if not ok:
        return None
    first = ok[0]["s"]
    rest = [c["s"] for c in ok[1:]]
    return {"first": first, "rest": statistics.median(rest) if rest else None, "net_first": max(first - base, 0.0),
            "net_rest": max(statistics.median(rest) - base, 0.0) if rest else None, "last": ok[-1]}


def _fmt(x):
    return "—" if x is None else (f"{x*1000:.0f} ms" if x < 1 else f"{x:.1f} s")


def compare(args) -> None:
    a, b = (json.load(open(p)) for p in (args.old, args.new))
    ia = {p["id"]: p for p in a["probes"]}
    ib = {p["id"]: p for p in b["probes"]}
    print(f"\nviejo = {a['name']} ({a['url']})   base {a['base_s']*1000:.0f} ms")
    print(f"nuevo = {b['name']} ({b['url']})   base {b['base_s']*1000:.0f} ms")
    print("tiempos NETOS (restada la base de red de cada servidor); 2ª.. = mediana de las repeticiones siguientes\n")
    head = f"{'id':3} {'pregunta':58} {'viejo 1ª':>9} {'viejo 2ª..':>10} {'nuevo 1ª':>9} {'nuevo 2ª..':>10}  {'mejora(2ª)':>10}  filas  trunc(v/n)"
    print(head); print("-" * len(head))
    for pid in [p["id"] for p in PROBES if p["id"] in ia and p["id"] in ib]:
        sa, sb = _stats(ia[pid], a["base_s"]), _stats(ib[pid], b["base_s"])
        q = ia[pid]["q"][:56]
        if sa is None or sb is None:
            print(f"{pid:3} {q:58} {'ERROR' if sa is None else _fmt(sa['net_first']):>9} {'':>10} "
                  f"{'ERROR' if sb is None else _fmt(sb['net_first']):>9}")
            continue
        va = sa["net_rest"] if sa["net_rest"] is not None else sa["net_first"]
        vb = sb["net_rest"] if sb["net_rest"] is not None else sb["net_first"]
        gain = f"{va / vb:.0f}×" if vb and vb > 0.02 and va / vb >= 1.5 else ("≈ igual" if va and vb and 0.67 <= va / max(vb, 1e-9) <= 1.5 else "—")
        la, lb = sa["last"], sb["last"]
        rows = f"{la.get('rows')}/{lb.get('rows')}"
        trunc = f"{'sí' if la.get('has_truncated_key') else 'no'}/{'sí' if lb.get('has_truncated_key') else 'no'}"
        print(f"{pid:3} {q:58} {_fmt(sa['net_first']):>9} {_fmt(sa['net_rest']):>10} {_fmt(sb['net_first']):>9} "
              f"{_fmt(sb['net_rest']):>10}  {gain:>10}  {rows:>5}  {trunc}")
    ea, eb = a.get("extra", {}), b.get("extra", {})
    if "pool" in ea and "pool" in eb:
        print(f"\npool ({ea['pool']['calls']}× health_check seguidos): viejo mediana {_fmt(ea['pool']['median_s'])} "
              f"(p95 {_fmt(ea['pool']['p95_s'])})  |  nuevo mediana {_fmt(eb['pool']['median_s'])} (p95 {_fmt(eb['pool']['p95_s'])})")
        print("   (medido desde fuera de la red del servidor, la distancia domina; para ver el pool de verdad córrelo desde la misma VPC)")
    if "concurrency" in ea and "concurrency" in eb:
        ca, cb = ea["concurrency"], eb["concurrency"]
        print(f"\nconcurrencia: mientras corre una consulta lenta, un health_check lanzado 0.3 s después tarda:\n"
              f"   viejo {_fmt(ca['fast_s'])} (la lenta duró {_fmt(ca['slow_s'])})   |   nuevo {_fmt(cb['fast_s'])} (la lenta duró {_fmt(cb['slow_s'])})")
    print("\nParidad: la columna 'filas' es filas devueltas viejo/nuevo en la última repetición; deben coincidir salvo cambios documentados.")


def list_probes(_args) -> None:
    for p in PROBES:
        print(f"{p['id']:3} [{p['group']:10}] {p['q']}\n      -> {p['tool']}({json.dumps(p['args'], ensure_ascii=False)})  ×{p['reps']}   | {p['shows']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ejecuta las preguntas contra un MCP y guarda el JSON")
    r.add_argument("--name", required=True, help="etiqueta de esta corrida (viejo, nuevo, ...)")
    r.add_argument("--url", required=True)
    r.add_argument("--header", help="nombre del encabezado de autenticación (X-MCP-Api-Key, Authorization, ...)")
    r.add_argument("--header-env", help="variable de entorno con el VALOR del encabezado")
    r.add_argument("--out", required=True)
    r.add_argument("--only", help="ids separados por coma, p. ej. A1,B1,B2")
    r.add_argument("--skip-heavy", action="store_true", help="omite las pesadas (C*, D*)")
    r.add_argument("--skip-extras", action="store_true", help="omite las pruebas de pool y de concurrencia")
    r.add_argument("--max-reps", type=int, help="tope de repeticiones por pregunta (2 basta para ver si hay caché y es amable con un servidor sin él)")
    r.set_defaults(fn=lambda a: asyncio.run(run(a)))
    c = sub.add_parser("compare", help="tabla lado a lado de dos corridas")
    c.add_argument("old"); c.add_argument("new")
    c.set_defaults(fn=compare)
    l = sub.add_parser("list", help="muestra las preguntas sin ejecutar nada")
    l.set_defaults(fn=list_probes)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
