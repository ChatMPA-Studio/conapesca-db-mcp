"""
scripts/load_dev_data.py
------------------------
Loads conapesca_landings_2001_2026.csv into a local SQLite database
for development/testing without a MySQL server.

Usage:
    python scripts/load_dev_data.py --csv PATH/TO/conapesca_landings_2001_2026.csv

Optional:
    --db   PATH/TO/output.sqlite   (default: dev/conapesca_dev.sqlite)
    --rows N                       (default: all rows; use e.g. 100000 for quick test)
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd


def parse_args():
    p = argparse.ArgumentParser(description="Load CONAPESCA CSV into SQLite dev DB")
    p.add_argument("--csv",  required=True, help="Path to conapesca_landings_2001_2026.csv")
    p.add_argument("--db",   default="dev/conapesca_dev.sqlite", help="Output SQLite path")
    p.add_argument("--rows", type=int, default=None, help="Max rows to load (default: all)")
    return p.parse_args()


def main():
    args = parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Loading: {csv_path}")
    print(f"Target : {db_path}")
    if args.rows:
        print(f"Rows   : first {args.rows:,} (dev sample)")
    else:
        print("Rows   : all (this may take a few minutes for 3.5 GB)")

    # Read CSV in chunks for memory efficiency
    dtype_map = {
        "anio_corte": "Int64",
        "clave_oficina": str,
        "clave_sitio_desembarque": str,
        "clave_lugar_captura": str,
        "clave_especie": str,
        "folio_aviso": str,
        "rnp_activo": str,
        "rnpa_unidad_economica": str,
        "numero_permiso": str,
        "worms_id": "Int64",
        "spec_code_fishbase": "Int64",
    }

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS conapesca_landings")

        chunk_size = 50_000
        total = 0
        first = True

        reader = pd.read_csv(
            csv_path,
            dtype=dtype_map,
            low_memory=False,
            chunksize=chunk_size,
            encoding="utf-8",
        )

        for chunk in reader:
            if args.rows and total >= args.rows:
                break
            if args.rows:
                remaining = args.rows - total
                chunk = chunk.iloc[:remaining]

            chunk.to_sql(
                "conapesca_landings",
                conn,
                if_exists="append" if not first else "replace",
                index=False,
            )
            total += len(chunk)
            first = False
            print(f"  Loaded {total:>10,} rows ...", end="\r")

        print(f"\nDone: {total:,} rows loaded into 'conapesca_landings'")

        # Basic indexes for query performance
        print("Creating indexes...")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_year  ON conapesca_landings(anio_corte)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_estado ON conapesca_landings(nombre_estado)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_especie ON conapesca_landings(nombre_especie)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tipo ON conapesca_landings(tipo_aviso)")
        conn.commit()
        print("Indexes created.")

        # Quick summary
        cur = conn.execute("SELECT COUNT(*) FROM conapesca_landings")
        n = cur.fetchone()[0]
        print(f"\nFinal row count: {n:,}")
        print(f"SQLite DB ready: {db_path.resolve()}")
        print("\nTo use it, set in your .env:")
        print("  USE_SQLITE=true")
        print(f"  SQLITE_PATH={db_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
