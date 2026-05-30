"""Direct DuckDB connection helpers for MotherDuck-backed DuckLake.

Use this from `main.py` when the dataset ingests data outside of dbt-duckdb
(e.g. via a custom API loader) and needs a `duckdb` connection that already
has the dataset's DuckLake attached.

Replaces the legacy `fdl.ducklake.connect(...)` context manager.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from collections.abc import Generator
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queria_config import load_target, require_motherduck_token  # noqa: E402


@contextmanager
def connect(target_name: str = "default") -> Generator[duckdb.DuckDBPyConnection]:
    """Open a fresh DuckDB session with the dataset's MotherDuck DuckLake attached.

    Yields the connection with the DuckLake attached under its dataset alias
    (e.g. `reinfolib`). The caller can run normal SQL — writes to attached
    tables go through MotherDuck (catalog) + R2 (Parquet) as configured at
    `CREATE DATABASE ... TYPE DUCKLAKE` time.
    """
    target = load_target(target_name)
    token = require_motherduck_token()

    conn = duckdb.connect(":memory:")
    try:
        conn.execute("INSTALL motherduck; LOAD motherduck;")
        conn.execute(f"SET motherduck_token = '{token}';")
        conn.execute(f'ATTACH \'md:{target.motherduck_db}\' AS "{target.dataset}";')
        yield conn
    finally:
        conn.close()
