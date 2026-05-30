"""Run DuckLake maintenance (expire snapshots, delete orphans, checkpoint)
against a MotherDuck-hosted DuckLake catalog.

Usage:
    python scripts/checkpoint.py [target]
    # target: "default" (default) or "local"
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queria_config import load_target, require_motherduck_token  # noqa: E402

RETENTION = "INTERVAL '7 days'"


def main() -> None:
    target_name = sys.argv[1] if len(sys.argv) > 1 else "default"
    target = load_target(target_name)
    token = require_motherduck_token()

    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL ducklake; LOAD ducklake;")
    conn.execute("INSTALL motherduck; LOAD motherduck;")
    conn.execute(f"SET motherduck_token = '{token}';")
    conn.execute(
        f"ATTACH 'ducklake:md:__ducklake_metadata_{target.motherduck_db}' AS db "
        f"(DATA_PATH '{target.data_path}');"
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

    print(f"checkpoint completed for md:{target.motherduck_db}")


if __name__ == "__main__":
    main()
