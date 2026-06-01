"""Snapshot the Neon DuckLake catalog (per dataset) to a standalone DuckDB file in R2.

The Neon Postgres database holds DuckLake metadata for every dataset, isolated
by Postgres schema (one schema per dataset). This script copies one dataset's
ducklake_* metadata tables into a fresh local DuckDB file (in its `main`
schema, matching what a sqlite/duckdb-backed DuckLake catalog looks like) and
uploads that file to R2 at <bucket>/<dataset>/ducklake.duckdb.

queria-web ATTACHs the uploaded file via `ducklake:https://...ducklake.duckdb`
(DuckDB-backed DuckLake), so the file must have its ducklake_* tables in the
`main` schema, not in the source `<dataset>` schema used inside Postgres.

Usage:
    python scripts/snapshot-to-r2.py [target]
    # target: "local" (default) or "default" (production)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import boto3
import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queria_config import TargetConfig, load_target  # noqa: E402


def snapshot(target: TargetConfig, snapshot_path: Path) -> None:
    """Copy each ducklake_* metadata table from Neon to a local DuckDB file.

    Uses plain Postgres ATTACH on the source and CTAS into the DuckDB file.
    The resulting file is a self-contained DuckLake catalog: ducklake_data_file.path
    values are preserved verbatim and continue to point at the BYOB R2 objects.
    """
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL postgres; LOAD postgres;")
    conn.execute(f"ATTACH '{target.neon_dsn}' AS src_pg (TYPE POSTGRES, READ_ONLY)")
    conn.execute(f"ATTACH '{snapshot_path}' AS snap")

    tables = [
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_catalog='src_pg' AND table_schema=? "
            "AND table_name LIKE 'ducklake_%' ORDER BY 1",
            [target.meta_schema],
        ).fetchall()
    ]
    if not tables:
        raise RuntimeError(
            f"no ducklake_* tables found in Neon schema {target.meta_schema!r}"
        )

    for t in tables:
        conn.execute(
            f'CREATE TABLE snap.main."{t}" AS '
            f'SELECT * FROM src_pg.{target.meta_schema}."{t}"'
        )

    # Snapshot consumers (queria-web via DuckDB WASM) read parquet over HTTPS
    # without R2 credentials, so the embedded data_path must be the public URL.
    public_data_path = f"{target.public_url.rstrip('/')}/{target.dataset}/ducklake.duckdb.files/"
    conn.execute(
        f"UPDATE snap.main.ducklake_metadata "
        f"SET value = '{public_data_path}' WHERE key = 'data_path'"
    )
    conn.close()


def upload(target: TargetConfig, snapshot_path: Path) -> None:
    """Upload the snapshot DuckDB file to R2."""
    client = boto3.client(
        "s3",
        endpoint_url=target.s3_endpoint,
        aws_access_key_id=target.s3_access_key_id,
        aws_secret_access_key=target.s3_secret_access_key,
    )
    client.upload_file(
        str(snapshot_path),
        target.s3_bucket,
        target.snapshot_key,
        ExtraArgs={"ContentType": "application/octet-stream"},
    )
    print(f"  uploaded snapshot → r2://{target.s3_bucket}/{target.snapshot_key}")

def run(target_name: str) -> None:
    target = load_target(target_name)
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "ducklake.duckdb"
        snapshot(target, snapshot_path)
        print(
            f"  snapshot built: {snapshot_path} "
            f"({snapshot_path.stat().st_size:,} bytes)"
        )
        upload(target, snapshot_path)


def main() -> None:
    target_name = sys.argv[1] if len(sys.argv) > 1 else "local"
    run(target_name)


if __name__ == "__main__":
    main()
