"""One-shot migration: copy DuckLake metadata from R2 ducklake.duckdb to Neon Postgres.

For each dataset, the current production state has a self-contained
`<dataset>/ducklake.duckdb` in R2 (a DuckDB file with `ducklake_*` metadata
tables in `main`). This script downloads that file, drops/recreates the
matching Postgres schema in Neon, and copies every `ducklake_*` table into it.

The Parquet objects on R2 are untouched — `ducklake_data_file.path` values
are preserved verbatim, so queries continue to read the same files.

Usage:
    python scripts/migrate-r2-to-neon.py [target]
    # target: "local" (default) or "default" (production)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import boto3
import duckdb
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queria_config import TargetConfig, load_target  # noqa: E402


def ensure_schema(target: TargetConfig) -> None:
    with psycopg.connect(target.neon_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{target.meta_schema}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{target.meta_schema}"')
    print(f"  schema {target.meta_schema!r} (re)created in Neon")


def download_snapshot(target: TargetConfig, dst: Path) -> None:
    s3 = boto3.client(
        "s3",
        endpoint_url=target.s3_endpoint,
        aws_access_key_id=target.s3_access_key_id,
        aws_secret_access_key=target.s3_secret_access_key,
    )
    s3.download_file(target.s3_bucket, target.snapshot_key, str(dst))
    print(
        f"  downloaded r2://{target.s3_bucket}/{target.snapshot_key} → {dst} "
        f"({dst.stat().st_size:,} bytes)"
    )


def migrate(target: TargetConfig, src_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL postgres; LOAD postgres;")
    conn.execute(f"ATTACH '{src_path}' AS src (TYPE DUCKDB, READ_ONLY)")
    conn.execute(f"ATTACH '{target.neon_dsn}' AS dst (TYPE POSTGRES)")

    tables = [
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_catalog='src' AND table_schema='main' "
            "AND table_name LIKE 'ducklake_%' ORDER BY 1"
        ).fetchall()
    ]
    if not tables:
        raise RuntimeError("source ducklake.duckdb has no ducklake_* tables")

    for t in tables:
        conn.execute(
            f'CREATE TABLE dst.{target.meta_schema}."{t}" AS '
            f'SELECT * FROM src.main."{t}"'
        )
    print(f"  migrated {len(tables)} tables to Neon schema {target.meta_schema!r}")

    # Repoint catalog data_path to the writeable r2:// endpoint; data_file.path
    # values are relative and resolve against this new base.
    conn.execute(
        f"UPDATE dst.{target.meta_schema}.ducklake_metadata "
        f"SET value = '{target.data_path}' WHERE key = 'data_path'"
    )
    print(f"  data_path → {target.data_path}")

    snap_cnt = conn.execute(
        f'SELECT COUNT(*) FROM dst.{target.meta_schema}.ducklake_snapshot'
    ).fetchone()[0]
    table_cnt = conn.execute(
        f'SELECT COUNT(*) FROM dst.{target.meta_schema}.ducklake_table'
    ).fetchone()[0]
    file_cnt = conn.execute(
        f'SELECT COUNT(*) FROM dst.{target.meta_schema}.ducklake_data_file'
    ).fetchone()[0]
    print(f"  snapshots={snap_cnt}, tables={table_cnt}, data_files={file_cnt}")
    conn.close()


def run(target_name: str) -> None:
    target = load_target(target_name)
    print(f"== migrate {target.dataset!r} ({target_name}) → Neon schema {target.meta_schema!r} ==")
    ensure_schema(target)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.duckdb"
        download_snapshot(target, src)
        migrate(target, src)
    print("== done ==")


def main() -> None:
    target_name = sys.argv[1] if len(sys.argv) > 1 else "local"
    run(target_name)


if __name__ == "__main__":
    main()
