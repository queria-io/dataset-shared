"""Run DuckLake maintenance (expire snapshots, delete orphans, checkpoint)
against the Neon-hosted DuckLake catalog for one dataset.

Usage:
    python scripts/checkpoint.py [target]
    # target: "default" (default) or "local"
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queria_config import load_target  # noqa: E402

RETENTION = "INTERVAL '7 days'"


def main() -> None:
    target_name = sys.argv[1] if len(sys.argv) > 1 else "default"
    target = load_target(target_name)

    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL ducklake; LOAD ducklake;")
    conn.execute("INSTALL postgres; LOAD postgres;")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute(
        "CREATE SECRET r2 (TYPE r2, KEY_ID ?, SECRET ?, ACCOUNT_ID ?)",
        [target.s3_access_key_id, target.s3_secret_access_key, target.cf_account_id],
    )
    conn.execute(
        f"ATTACH '{target.ducklake_uri}' AS db "
        f"(DATA_PATH '{target.data_path}', META_SCHEMA '{target.meta_schema}')"
    )

    # ducklake_set_option(..., 'expire_older_than', ...) only records the option,
    # so the actual maintenance must be invoked explicitly (DuckLake v1.0+).
    conn.execute(
        f"CALL ducklake_expire_snapshots('db', older_than => NOW() - {RETENTION});"
    )
    conn.execute(
        f"CALL ducklake_delete_orphaned_files('db', older_than => NOW() - {RETENTION});"
    )
    conn.execute("CHECKPOINT;")
    conn.close()

    print(f"checkpoint completed for Neon schema {target.meta_schema!r}")


if __name__ == "__main__":
    main()
