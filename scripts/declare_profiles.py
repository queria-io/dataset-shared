"""Declare which tables fdl push makes no profile of: those Queria does not list.

Usage: python declare_profiles.py <working.sqlite> <data path> [<schema>]

The tables of the description's own schema (default: _queria), and the tables
its `tables` marks `published = false` (FDL_UNDESCRIBED=unlisted), get
`fdl declare profile <name> false`: nobody reads their profiles. A table that
is listed again gets `true`. Only what differs from `fdl declare list` is run,
and only for the tables the description lists: a table declared `false` some
other way is left as it is.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _read_secret(working: str) -> list[str]:
    """The HTTP secret that lets DuckDB read the files the working catalog took from its
    repository: rows tabledoc kept are still in those files, and a private dataset
    answers 404 to a read without a token (as fdl_read.py gives dbt)."""
    state = Path(working + ".fdl.json")
    remote = json.loads(state.read_text()).get("remote") if state.exists() else None
    if not remote or not remote.startswith("https://"):
        return []
    from fdl.stores import open_store

    token = getattr(open_store(remote), "token", None)
    if not token:
        return []
    return [f"CREATE OR REPLACE SECRET fdl_read (TYPE http, BEARER_TOKEN {_string(token)}, SCOPE {_string(remote)})"]


def unlisted(working: str, data: str, schema: str) -> tuple[set[str], set[str]]:
    """The tables and views, as schema.name, that Queria does not list, and all those this script decides."""
    import duckdb

    # Reads of data.queria.io go by the build's name, so they are not counted among its users.
    agent = os.environ.get("QUERIA_USER_AGENT")
    con = duckdb.connect(config={"custom_user_agent": agent} if agent else {})
    try:
        con.execute("INSTALL ducklake; LOAD ducklake; INSTALL sqlite; LOAD sqlite; INSTALL httpfs; LOAD httpfs;")
        for statement in _read_secret(working):
            con.execute(statement)
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
            return own, own
        quoted = '"' + schema.replace('"', '""') + '"'
        rows = con.execute(f'SELECT "schema", name, published FROM lake.{quoted}.tables').fetchall()
        hidden = {f"{s}.{n}" for s, n, published in rows if published is False}
        return own | hidden, own | {f"{s}.{n}" for s, n, _ in rows}
    finally:
        con.close()


def commands(have: set[str], want: set[str], decided: set[str]) -> list[list[str]]:
    """The `fdl declare profile` arguments that make ``have`` (unprofiled now) say ``want``, for the tables in ``decided``."""
    return [[name, "false"] for name in sorted(want - have)] + [
        [name, "true"] for name in sorted((have - want) & decided)
    ]


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    working, data = argv[0], argv[1]
    schema = argv[2] if len(argv) == 3 else "_queria"
    listed = subprocess.run(["fdl", "declare", "list", working, "--json"], check=True, capture_output=True, text=True)
    have = set(json.loads(listed.stdout).get("unprofiled") or [])
    want, decided = unlisted(working, data, schema)
    for name, profiled in commands(have, want, decided):
        subprocess.run(["fdl", "declare", "profile", working, name, profiled], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
