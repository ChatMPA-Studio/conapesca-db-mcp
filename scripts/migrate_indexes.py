#!/usr/bin/env python3
"""
scripts/migrate_indexes.py
--------------------------
Applies (or rolls back) the index changes proposed in
docs/rds_index_recommendations.md to conapesca_landings_historical.
See docs/index_migration_runbook.md for the operator runbook.

Safety properties:
  * Dry-run by default: connects, runs pre-checks, prints the exact SQL it
    WOULD run, changes nothing. Nothing is written without --apply.
  * Idempotent: every step checks information_schema first and skips if it is
    already applied, so re-running after a failure or a timeout is safe.
  * Each step is ONE atomic ALTER TABLE (an index swap is DROP + ADD in the
    same statement): it either fully applies or leaves the table untouched.
  * ALGORITHM=INPLACE, LOCK=NONE: if MySQL cannot do it without blocking
    reads/writes, the statement fails immediately. It only falls back to
    LOCK=SHARED (reads allowed, writes blocked) if --allow-lock is passed.
  * SET SESSION lock_wait_timeout: if a long-running transaction holds the
    table, the ALTER gives up instead of queueing and blocking every query
    behind it.
  * Stops at the first failing step; later steps are not attempted.
  * Reversible: --rollback --apply restores the pre-migration index layout.
  * Asks the operator to type the target host before touching anything
    (skip with --yes for non-interactive use).

Connection: needs a user with ALTER/INDEX/DROP on the table (NOT the read-only
mcp user). Pass via flags or env vars MIGRATION_DB_HOST / _PORT / _USER /
_PASSWORD / _NAME; the password is prompted if not given.

Examples:
    python scripts/migrate_indexes.py --host H --user admin --database conapesca
    python scripts/migrate_indexes.py ... --apply
    python scripts/migrate_indexes.py ... --apply --allow-lock
    python scripts/migrate_indexes.py ... --rollback --apply
"""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pymysql

DEFAULT_TABLE = "conapesca_landings_historical"
TEXT_TYPES = {"tinytext", "text", "mediumtext", "longtext", "blob", "tinyblob", "mediumblob", "longblob"}

log = logging.getLogger("migrate_indexes")


# ── Table state ---------------------------------------------------------------

class State:
    """Snapshot of the table's indexes/columns, re-read after every step."""

    def __init__(self, cur, db: str, table: str, prefix_overrides: dict[str, int]):
        self.cur, self.db, self.table = cur, db, table
        self.prefix_overrides = prefix_overrides
        self.refresh()

    def refresh(self) -> None:
        self.cur.execute(
            "SELECT INDEX_NAME AS idx, SEQ_IN_INDEX AS seq, COLUMN_NAME AS col, "
            "SUB_PART AS sub, INDEX_TYPE AS itype "
            "FROM information_schema.statistics "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY INDEX_NAME, SEQ_IN_INDEX",
            (self.db, self.table),
        )
        self.indexes: dict[str, list[dict]] = {}
        for r in self.cur.fetchall():
            self.indexes.setdefault(r["idx"], []).append(r)
        self.cur.execute(
            "SELECT COLUMN_NAME AS col, DATA_TYPE AS dtype FROM information_schema.columns "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
            (self.db, self.table),
        )
        self.columns = {r["col"]: r["dtype"].lower() for r in self.cur.fetchall()}

    def has(self, index: str) -> bool:
        return index in self.indexes

    def index_type(self, index: str) -> str | None:
        return self.indexes[index][0]["itype"] if index in self.indexes else None

    def keypart(self, col: str) -> str:
        """`col` or `col`(N). The table's existing indexes use prefix lengths
        (typical of TEXT columns), so mirror whatever prefix they already use
        for that column; a new index must not be wider than the existing ones."""
        if col not in self.columns:
            raise SystemExit(f"Column {col!r} not found in {self.table}")
        n = self.prefix_overrides.get(col)
        if n is None:
            n = next((r["sub"] for rows in self.indexes.values() for r in rows
                      if r["col"] == col and r["sub"] is not None), None)
        if n is None and self.columns[col] in TEXT_TYPES:
            raise SystemExit(f"Column {col!r} is {self.columns[col]} and no existing index "
                             f"gives a prefix length for it; pass --prefix {col}=N")
        return f"`{col}`({int(n)})" if n is not None else f"`{col}`"

    def snapshot(self) -> str:
        lines = []
        for name, rows in sorted(self.indexes.items()):
            cols = ", ".join(r["col"] + (f"({r['sub']})" if r["sub"] else "") for r in rows)
            lines.append(f"    {name} [{rows[0]['itype']}] ({cols})")
        return "\n".join(lines) or "    (no indexes)"


# ── Steps ---------------------------------------------------------------------
# Each step returns the body of an ALTER TABLE (without the ALGORITHM/LOCK
# clauses, which the executor appends), or None when there is nothing to do.

@dataclass
class Step:
    name: str
    why: str
    applied: Callable[[State], bool]
    apply: Callable[[State], str]
    rollback: Callable[[State], str]


def _estado_anio_tipo_apply(s: State) -> str:
    parts = ["DROP INDEX `idx_estado_anio`"] if s.has("idx_estado_anio") else []
    cols = ", ".join([s.keypart("nombre_estado"), s.keypart("anio_corte"), s.keypart("tipo_aviso")])
    parts.append(f"ADD INDEX `idx_estado_anio_tipo` ({cols})")
    return ", ".join(parts)


def _estado_anio_tipo_rollback(s: State) -> str:
    cols = ", ".join([s.keypart("nombre_estado"), s.keypart("anio_corte")])
    return f"DROP INDEX `idx_estado_anio_tipo`, ADD INDEX `idx_estado_anio` ({cols})"


STEPS: list[Step] = [
    Step(
        "estado_anio_tipo",
        "get_species(): idx_estado_anio only filters 10% inside the index; adding tipo_aviso "
        "makes it a superset replacement (swapped atomically).",
        lambda s: s.has("idx_estado_anio_tipo"),
        _estado_anio_tipo_apply,
        _estado_anio_tipo_rollback,
    ),
    Step(
        "anio_estado",
        "get_estados(): lets MySQL read states in order per year (low priority).",
        lambda s: s.has("idx_anio_estado"),
        lambda s: f"ADD INDEX `idx_anio_estado` ({s.keypart('anio_corte')}, {s.keypart('nombre_estado')})",
        lambda s: "DROP INDEX `idx_anio_estado`",
    ),
    Step(
        "ft_especie",
        "especie LIKE '%x%' full-scans all rows; FULLTEXT enables MATCH ... AGAINST. "
        "Does nothing until the tools are changed to use MATCH. First FULLTEXT index "
        "rebuilds the table (needs --allow-lock).",
        lambda s: s.has("ft_especie_cientifico"),
        lambda s: "ADD FULLTEXT INDEX `ft_especie_cientifico` (`nombre_especie`, `nombre_cientifico`)",
        lambda s: "DROP INDEX `ft_especie_cientifico`",
    ),
]

# EXPLAIN checks run after the migration: (step, label, sql, expected key/type)
def _verify_queries(t: str) -> list[tuple[str, str, str, str]]:
    return [
        ("estado_anio_tipo", "get_species shape",
         f"EXPLAIN SELECT nombre_especie, SUM(peso_desembarcado_kg) FROM `{t}` "
         "WHERE anio_corte=2023 AND nombre_estado='SINALOA' AND tipo_aviso='MAYORES' "
         "GROUP BY nombre_especie, nombre_cientifico", "idx_estado_anio_tipo"),
        ("anio_estado", "get_estados shape",
         f"EXPLAIN SELECT DISTINCT nombre_estado FROM `{t}` WHERE anio_corte=2023 "
         "ORDER BY nombre_estado", "idx_anio_estado"),
        ("ft_especie", "FULLTEXT species search",
         f"EXPLAIN SELECT nombre_especie FROM `{t}` WHERE MATCH(nombre_especie, nombre_cientifico) "
         "AGAINST ('camaron*' IN BOOLEAN MODE) LIMIT 10", "ft_especie_cientifico"),
    ]


# ── Execution -----------------------------------------------------------------

def run_alter(cur, table: str, body: str, allow_lock: bool) -> None:
    attempts = [("INPLACE", "NONE")] + ([("INPLACE", "SHARED")] if allow_lock else [])
    for i, (alg, lock) in enumerate(attempts):
        sql = f"ALTER TABLE `{table}` {body}, ALGORITHM={alg}, LOCK={lock}"
        log.info("    SQL: %s", sql)
        t0 = time.time()
        try:
            cur.execute(sql)
        except pymysql.MySQLError as e:
            code = e.args[0] if e.args else None
            # 1845/1846: ALGORITHM/LOCK not supported for this operation (nothing was changed)
            if code in (1845, 1846) and i + 1 < len(attempts):
                log.warning("    LOCK=%s not supported (%s); retrying with LOCK=%s",
                            lock, e.args[1] if len(e.args) > 1 else e, attempts[i + 1][1])
                continue
            raise
        log.info("    done in %.1fs (ALGORITHM=%s, LOCK=%s)", time.time() - t0, alg, lock)
        return


def prechecks(cur, st: State, args) -> None:
    cur.execute("SELECT VERSION() AS v, @@read_only AS ro, CURRENT_USER() AS u, DATABASE() AS d")
    r = cur.fetchone()
    log.info("Server %s | user %s | database %s", r["v"], r["u"], r["d"])
    if r["ro"]:
        raise SystemExit("ABORT: server is read_only (replica / read-only endpoint?). Use the writer endpoint.")
    major = r["v"].split(".")[0]
    if not major.isdigit() or int(major) < 8 or "mariadb" in r["v"].lower():
        log.warning("Tested on MySQL 8.0 only; this server is %s", r["v"])

    cur.execute("SELECT TABLE_ROWS AS n, DATA_LENGTH AS data FROM information_schema.tables "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (args.database, args.table))
    t = cur.fetchone()
    if not t:
        raise SystemExit(f"ABORT: table {args.database}.{args.table} not found")
    gb = t["data"] / 1024**3
    log.info("Table ~%s rows, data %.1f GB. Make sure the instance has >= %.0f GB free storage "
             "(index builds and the first FULLTEXT table rebuild need temp space).",
             f"{t['n']:,}", gb, max(2 * gb, 10))

    try:
        cur.execute("SHOW GRANTS FOR CURRENT_USER()")
        grants = " ".join(str(list(g.values())[0]).upper() for g in cur.fetchall())
        if "ALL PRIVILEGES" not in grants and not all(p in grants for p in ("ALTER", "INDEX", "DROP")):
            log.warning("Could not confirm ALTER/INDEX/DROP privileges from SHOW GRANTS (roles?). "
                        "A step will fail cleanly if they are missing.")
    except pymysql.MySQLError:
        log.warning("Could not read grants")

    try:
        cur.execute("SELECT COUNT(*) AS n FROM information_schema.innodb_trx "
                    "WHERE trx_started < NOW() - INTERVAL 5 MINUTE")
        n = cur.fetchone()["n"]
        if n:
            log.warning("%d transaction(s) open for >5 min: they can hold metadata locks and make "
                        "the ALTER time out (lock_wait_timeout=%ss).", n, args.lock_wait_timeout)
    except pymysql.MySQLError:
        log.info("Could not check open transactions (needs PROCESS privilege)")

    log.info("Current indexes:\n%s", st.snapshot())


def verify(cur, st: State, table: str, steps: list[Step]) -> bool:
    ok = True
    names = {s.name for s in steps}
    for step, label, sql, expected in _verify_queries(table):
        if step not in names:
            continue
        cur.execute(sql)
        plan = cur.fetchone()
        used = plan.get("key") or "-"
        good = expected in str(plan.get("key")) or (step == "ft_especie" and plan.get("type") == "fulltext")
        log.info("  EXPLAIN %-24s type=%-9s key=%-24s %s", label, plan.get("type"), used,
                 "OK" if good else "WARN: expected " + expected)
        ok &= good
    return ok


def main() -> int:
    env = os.getenv
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=env("MIGRATION_DB_HOST"))
    p.add_argument("--port", type=int, default=int(env("MIGRATION_DB_PORT", "3306")))
    p.add_argument("--user", default=env("MIGRATION_DB_USER"))
    p.add_argument("--database", default=env("MIGRATION_DB_NAME"))
    p.add_argument("--table", default=DEFAULT_TABLE)
    p.add_argument("--apply", action="store_true", help="actually execute (default: dry-run)")
    p.add_argument("--rollback", action="store_true", help="undo the migration instead of applying it")
    p.add_argument("--only", help="comma-separated steps: " + ",".join(s.name for s in STEPS))
    p.add_argument("--allow-lock", action="store_true",
                   help="if LOCK=NONE is unsupported, retry with LOCK=SHARED (blocks writes during the ALTER)")
    p.add_argument("--lock-wait-timeout", type=int, default=60,
                   help="seconds an ALTER waits for a metadata lock before giving up (default 60)")
    p.add_argument("--prefix", action="append", default=[], metavar="COL=N",
                   help="override the index prefix length for a column")
    p.add_argument("--ssl-ca", help="CA bundle to verify the server cert (default: TLS without verification)")
    p.add_argument("--no-ssl", action="store_true", help="disable TLS (local testing only)")
    p.add_argument("--yes", action="store_true", help="skip the interactive host confirmation")
    p.add_argument("--log-file", default=f"migrate_indexes_{datetime.now():%Y%m%d_%H%M%S}.log")
    args = p.parse_args()

    for flag in ("host", "user", "database"):
        if not getattr(args, flag):
            p.error(f"--{flag} (or MIGRATION_DB_{flag.upper() if flag != 'database' else 'NAME'}) is required")
    overrides = {k: int(v) for k, v in (x.split("=", 1) for x in args.prefix)}
    wanted = set(args.only.split(",")) if args.only else {s.name for s in STEPS}
    unknown = wanted - {s.name for s in STEPS}
    if unknown:
        p.error(f"unknown step(s): {', '.join(sorted(unknown))}")
    steps = [s for s in STEPS if s.name in wanted]
    if args.rollback:
        steps.reverse()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(args.log_file)])
    log.info("Log file: %s", args.log_file)

    password = env("MIGRATION_DB_PASSWORD") or getpass.getpass(f"Password for {args.user}@{args.host}: ")
    ssl = None if args.no_ssl else ({"ca": args.ssl_ca} if args.ssl_ca else {"ca": None})
    conn = pymysql.connect(host=args.host, port=args.port, user=args.user, password=password,
                           database=args.database, charset="utf8mb4", ssl=ssl, connect_timeout=30,
                           cursorclass=pymysql.cursors.DictCursor, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION lock_wait_timeout = %s", (args.lock_wait_timeout,))
            st = State(cur, args.database, args.table, overrides)
            prechecks(cur, st, args)

            mode = "ROLLBACK" if args.rollback else "APPLY"
            pending = [s for s in steps if s.applied(st) == args.rollback]
            for s in steps:
                if s not in pending:
                    log.info("[%s] %s: already %s, skipping", mode, s.name,
                             "absent" if args.rollback else "applied")
            if not pending:
                log.info("Nothing to do.")
                if not args.rollback:
                    verify(cur, st, args.table, steps)
                return 0

            log.info("%s plan (%s):", mode, "EXECUTING" if args.apply else "DRY-RUN, nothing will be changed")
            for s in pending:
                body = (s.rollback if args.rollback else s.apply)(st)
                log.info("  - %s: %s\n      ALTER TABLE `%s` %s, ALGORITHM=INPLACE, LOCK=NONE",
                         s.name, s.why, args.table, body)
            if not args.apply:
                log.info("Dry-run only. Re-run with --apply to execute.")
                return 0

            if not args.yes:
                typed = input(f"\nType the host name to confirm {mode} on {args.host}/{args.database}: ").strip()
                if typed != args.host:
                    log.error("Host did not match; aborting, nothing changed.")
                    return 2

            for s in pending:
                body = (s.rollback if args.rollback else s.apply)(st)
                log.info("[%s] %s ...", mode, s.name)
                try:
                    run_alter(cur, args.table, body, args.allow_lock)
                except pymysql.MySQLError as e:
                    log.error("FAILED at step %s: %s", s.name, e)
                    log.error("The ALTER is atomic: this step changed nothing. Earlier steps stay applied; "
                              "re-running is safe (they will be skipped).")
                    if e.args and e.args[0] in (1845, 1846):
                        log.error("It needs a table lock: re-run with --allow-lock in a quiet window.")
                    if e.args and e.args[0] == 1205:
                        log.error("Timed out waiting for a metadata lock: find and finish the long "
                                  "transaction (SHOW PROCESSLIST), then re-run.")
                    return 1
                st.refresh()
                if s.applied(st) == args.rollback:
                    log.error("Step %s ran but the index state is not what was expected.", s.name)
                    return 1

            log.info("Indexes after:\n%s", st.snapshot())
            if not args.rollback:
                log.info("Post-migration EXPLAIN checks:")
                if not verify(cur, st, args.table, steps):
                    log.warning("Some plans do not use the new index (see WARN). The migration itself "
                                "succeeded; the optimizer may need ANALYZE TABLE or real data volume.")
            log.info("%s finished OK.", mode)
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
