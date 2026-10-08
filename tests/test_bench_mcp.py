"""
Tests for scripts/bench_mcp.py: the pure logic (summaries, net times, the
side-by-side table). Nothing here touches a server.
"""

from __future__ import annotations
import importlib.util
import json
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "bench_mcp", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "bench_mcp.py"
)
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def _call(seconds, rows=5, has_trunc=False, ok=True):
    c = {"s": seconds, "ok": ok}
    if ok:
        c.update({"rows": rows, "has_truncated_key": has_trunc, "truncated": False if has_trunc else None})
    return c


def _run(name, base, per_probe):
    """per_probe = {id: [seconds, ...]}"""
    probes = []
    for p in bench.PROBES:
        if p["id"] in per_probe:
            probes.append({k: p[k] for k in ("id", "group", "q", "tool", "args", "shows")}
                          | {"calls": [_call(s) for s in per_probe[p["id"]]]})
    return {"name": name, "url": f"http://{name}", "when": "x", "base_s": base, "probes": probes, "extra": {}}


def test_summarize_reports_rows_and_whether_the_server_flags_truncation():
    flagged = json.dumps({"landings": [1, 2, 3], "meta": {"truncated": True}})
    legacy = json.dumps({"landings": [1, 2, 3], "meta": {"row_count": 3}})
    assert bench._summarize(flagged) == {"rows": 3, "has_truncated_key": True, "truncated": True}
    assert bench._summarize(legacy)["has_truncated_key"] is False
    assert bench._summarize("no es json")["rows"] is None


def test_net_times_subtract_the_network_base_and_never_go_negative():
    probe = _run("x", 0.5, {"A1": [10.5, 10.5, 0.4]})["probes"][0]
    s = bench._stats(probe, 0.5)
    assert round(s["net_first"], 3) == 10.0                    # 10.5 - 0.5
    assert round(s["net_rest"], 2) == 4.95                     # mediana de [10.5, 0.4] = 5.45, menos 0.5
    # una llamada más rápida que la base no da tiempo negativo
    fast = bench._stats(_run("x", 0.5, {"A1": [0.4, 0.3]})["probes"][0], 0.5)
    assert fast["net_first"] == 0.0 and fast["net_rest"] == 0.0


def test_a_probe_with_no_successful_call_has_no_stats():
    probe = {"calls": [_call(1.0, ok=False)]}
    assert bench._stats(probe, 0.1) is None


def test_fmt_uses_ms_below_a_second():
    assert bench._fmt(0.25) == "250 ms"
    assert bench._fmt(12.34) == "12.3 s"
    assert bench._fmt(None) == "—"


def test_compare_prints_the_speedup_of_the_cached_second_call(tmp_path, capsys):
    old = _run("viejo", 0.1, {"A1": [20.1, 20.1, 20.1]})            # sin caché: paga 20 s siempre
    new = _run("nuevo", 0.1, {"A1": [0.6, 0.6, 0.6]})               # con caché: ~0.5 s netos
    po, pn = tmp_path / "o.json", tmp_path / "n.json"
    po.write_text(json.dumps(old)); pn.write_text(json.dumps(new))

    bench.compare(type("A", (), {"old": str(po), "new": str(pn)})())

    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.startswith("A1 "))
    assert "20.0 s" in line and "500 ms" in line and "40×" in line


def test_compare_marks_equal_times_as_equal_and_not_as_a_gain(tmp_path, capsys):
    a = _run("viejo", 0.1, {"B1": [1.1, 1.1, 1.1]})
    b = _run("nuevo", 0.1, {"B1": [1.1, 1.2, 1.1]})
    po, pn = tmp_path / "o.json", tmp_path / "n.json"
    po.write_text(json.dumps(a)); pn.write_text(json.dumps(b))

    bench.compare(type("A", (), {"old": str(po), "new": str(pn)})())

    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("B1 "))
    assert "≈ igual" in line


def test_compare_skips_probes_missing_from_one_run(tmp_path, capsys):
    a = _run("viejo", 0.1, {"A1": [1.0, 1.0]})
    b = _run("nuevo", 0.1, {"A1": [1.0, 1.0], "A2": [1.0, 1.0]})
    po, pn = tmp_path / "o.json", tmp_path / "n.json"
    po.write_text(json.dumps(a)); pn.write_text(json.dumps(b))

    bench.compare(type("A", (), {"old": str(po), "new": str(pn)})())

    out = capsys.readouterr().out
    assert any(l.startswith("A1 ") for l in out.splitlines())
    assert not any(l.startswith("A2 ") for l in out.splitlines())


def test_probe_ids_are_unique_and_every_probe_has_what_the_table_needs():
    ids = [p["id"] for p in bench.PROBES]
    assert len(ids) == len(set(ids))
    for p in bench.PROBES:
        assert {"id", "group", "q", "tool", "args", "reps", "shows"} <= set(p)
