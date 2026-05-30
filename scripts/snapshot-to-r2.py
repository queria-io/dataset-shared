"""Snapshot a MotherDuck DuckLake catalog to a standalone DuckDB file in R2.

Reads pyproject.toml [tool.queria] to identify the dataset, then:
1. ATTACH the internal MotherDuck metadata DB (md:__ducklake_metadata_<db>) as a plain DuckDB.
2. CTAS each ducklake_* metadata table into a fresh local DuckDB file.
3. Upload the resulting DuckDB file to R2 at <bucket>/<dataset>/ducklake.duckdb.

CTAS is used instead of COPY FROM DATABASE because COPY FROM DATABASE leaves
MotherDuck's DuckLake DB in a temporary invalid state (md:<db> returns an
internal error until the DB is recreated). CTAS only reads from the metadata
DB without mutating it, so md:<db> remains queryable throughout.

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
from queria_config import TargetConfig, load_target, require_motherduck_token  # noqa: E402


def snapshot(target: TargetConfig, token: str, snapshot_path: Path) -> None:
    """Copy each ducklake_* metadata table from MotherDuck into a local DuckDB file.

    Why per-table CTAS instead of `COPY FROM DATABASE` (the DuckLake backup
    pattern at https://ducklake.select/docs/stable/duckdb/guides/backups_and_recovery)
    or `rclone` (https://ducklake.select/docs/stable/duckdb/guides/public_ducklake_on_object_storage)?

    - `COPY FROM DATABASE` requires `ATTACH 'md:__ducklake_metadata_<db>'`
      which is rejected by MotherDuck with "Catalog has been deleted" from
      external sessions (the internal metadata DB is ephemeral and only
      stays attached transiently after `md:<db>` is touched in the same
      process). Reproduced on 2026-05-30.
    - `rclone` only works when the source is a real DuckDB file. MotherDuck
      manages the metadata DB server-side; there is no file to sync.
    - CTAS reads each metadata table through the DuckLake view layer of
      `md:<db>` which IS attachable, and writes to a fresh local DuckDB.
      The resulting file is a valid DuckLake catalog (no extension marker
      needed — DuckLake recognizes the table layout).

    The output is functionally identical to `COPY FROM DATABASE` for the
    snapshot use case (Parquet refs in `ducklake_data_file.path` are
    preserved as-is, pointing at the BYOB R2 bucket).
    """
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL motherduck; LOAD motherduck;")
    conn.execute(f"SET motherduck_token = '{token}';")
    conn.execute(f"ATTACH 'md:__ducklake_metadata_{target.motherduck_db}' AS src_md;")
    conn.execute(f"ATTACH '{snapshot_path}' AS snap;")

    tables = [
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_catalog='src_md' AND table_schema='main'"
        ).fetchall()
    ]
    for t in tables:
        conn.execute(f'CREATE TABLE snap.main."{t}" AS SELECT * FROM src_md.main."{t}"')
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

def main() -> None:
    """CLI entry point.

    Note: MotherDuck's `__ducklake_metadata_<db>` is ephemeral and is only
    attachable shortly after `md:<db>` has been touched in the same process.
    Running this as a standalone CLI long after the last dbt build may fail
    with "Catalog has been deleted". Prefer invoking `run()` from main.py
    immediately after dbt build.
    """
    target_name = sys.argv[1] if len(sys.argv) > 1 else "local"
    target = load_target(target_name)
    token = require_motherduck_token()

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "ducklake.duckdb"
        snapshot(target, token, snapshot_path)
        print(f"  snapshot built: {snapshot_path} ({snapshot_path.stat().st_size:,} bytes)")
        upload(target, snapshot_path)


if __name__ == "__main__":
    main()
