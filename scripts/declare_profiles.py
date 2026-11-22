"""Declare which tables fdl push makes no profile of: those Queria does not list.

Usage: python declare_profiles.py <working.sqlite> <data path> [<schema>]

The tables of the description's own schema (default: _queria), and the tables
its `tables` marks `published = false` (FDL_UNDESCRIBED=unlisted), get
`fdl declare profile <name> false`: nobody reads their profiles. A table that
is listed again gets `true`. Only what differs from `fdl declare list` is run.
"""

from __future__ import annotations

import json
import subprocess
import sys


def _string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def unlisted(working: str, data: str, schema: str) -> set[str]:
    """The tables and views, as schema.name, that Queria does not list."""
    import duckdb

    con = duckdb.connect()
    try:
        con.execute("INSTALL ducklake; LOAD ducklake; INSTALL sqlite; LOAD sqlite; INSTALL httpfs; LOAD httpfs;")
        con.execute(
            f"ATTACH {_string('ducklake:' + working)} AS lake "
            f"(READ_ONLY, META_TYPE 'sqlite', DATA_PATH {_string(data)}, OVERRIDE_DATA_PATH true)"
        )
        own = {
            f"{s}.{n}"
            for s, n in con.execute(
                "SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = 'lake' AND schema_name = ? "
                "UNION SELECT schema_name, view_name FROM duckdb_views() WHERE database_name = 'lake' AND schema_name = ?",
                [schema, schema],
            ).fetchall()
        }
        if f"{schema}.tables" not in own:
            return own
        quoted = '"' + schema.replace('"', '""') + '"'
        hidden = {
            f"{s}.{n}"
            for s, n in con.execute(f'SELECT "schema", name FROM lake.{quoted}.tables WHERE published = false').fetchall()
        }
        return own | hidden
    finally:
        con.close()


def commands(have: set[str], want: set[str]) -> list[list[str]]:
    """The `fdl declare profile` arguments that make ``have`` (unprofiled now) say ``want``."""
    return [[name, "false"] for name in sorted(want - have)] + [[name, "true"] for name in sorted(have - want)]


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    working, data = argv[0], argv[1]
    schema = argv[2] if len(argv) == 3 else "_queria"
    listed = subprocess.run(["fdl", "declare", "list", working, "--json"], check=True, capture_output=True, text=True)
    have = set(json.loads(listed.stdout).get("unprofiled") or [])
    for name, profiled in commands(have, unlisted(working, data, schema)):
        subprocess.run(["fdl", "declare", "profile", working, name, profiled], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
