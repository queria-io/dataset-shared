"""Drop the tables and views the dbt project no longer makes from the working catalog.

README.md「モデルを消したとき」. Each build starts from the last version, so a table
whose model was removed, renamed or made ephemeral stays in the catalog and goes into
every later version. build-fdl.sh runs this after the build, before
`tabledoc load`, while dbt's target/manifest.json is the one this build wrote.

Kept:
- the tables and views of the models, seeds and snapshots that are not ephemeral
- every table in a schema a dbt source reads (the tables the pipelines load, such
  as _source), and every table in a schema whose name starts with _ (dlt's
  _source_staging and the like)

Everything else in the dataset's own catalog is dropped and printed, and so is a
schema that holds nothing afterwards. Names are compared without case, as DuckDB
looks them up: a table whose name differs from its model's only in case is the
model's table.

Usage: python drop_stale.py <target/manifest.json> <catalog name>
The catalog is attached from FDL_WORKING and FDL_DATA_PATH, as profiles.yml does.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BUILT = ("model", "seed", "snapshot")


def kept(manifest: dict, catalog: str) -> tuple[set[tuple[str, str]], set[str]]:
    """The (schema, name) the project makes in the catalog, and the schemas its sources read."""
    nodes = manifest.get("nodes", {}).values()
    relations = {
        (n["schema"].lower(), (n.get("alias") or n["name"]).lower())
        for n in nodes
        if n.get("resource_type") in BUILT
        and n.get("database") == catalog
        and n.get("config", {}).get("materialized") != "ephemeral"
    }
    sources = {
        s["schema"].lower() for s in manifest.get("sources", {}).values() if s.get("database") == catalog
    }
    return relations, sources


def stale(
    existing: list[tuple[str, str, str]], relations: set[tuple[str, str]], sources: set[str]
) -> list[tuple[str, str, str]]:
    """(schema, name, kind) in the catalog that no node makes and no source reads."""
    return sorted(
        (schema, name, kind)
        for schema, name, kind in existing
        if (schema.lower(), name.lower()) not in relations and not _protected(schema, sources)
    )


def empty(
    schemas: list[str],
    left: list[tuple[str, str, str]],
    relations: set[tuple[str, str]],
    sources: set[str],
) -> list[str]:
    """The schemas that hold nothing in ``left`` and that no node writes to."""
    used = {schema.lower() for schema, _, _ in left} | {schema for schema, _ in relations}
    return sorted(
        schema
        for schema in schemas
        if schema.lower() not in used and schema.lower() != "main" and not _protected(schema, sources)
    )


def _protected(schema: str, sources: set[str]) -> bool:
    """A schema a source reads, or one whose name starts with _: the pipelines write there."""
    return schema.lower() in sources or schema.startswith("_")


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main(manifest_path: str, catalog: str) -> None:
    import duckdb

    relations, sources = kept(json.loads(Path(manifest_path).read_text()), catalog)
    if not relations:
        # A manifest with no node in this catalog: the build wrote somewhere else. Drop nothing.
        print(f"drop_stale: no model writes to {catalog}; nothing dropped", file=sys.stderr)
        return
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake")
    con.execute(
        f"ATTACH {literal('ducklake:' + os.environ['FDL_WORKING'])} AS {quote(catalog)} "
        f"(DATA_PATH {literal(os.environ['FDL_DATA_PATH'])}, OVERRIDE_DATA_PATH true, META_TYPE 'sqlite')"
    )
    existing = con.execute(
        "SELECT schema_name, table_name, 'TABLE' FROM duckdb_tables() WHERE database_name = ? "
        "UNION ALL SELECT schema_name, view_name, 'VIEW' FROM duckdb_views() "
        "WHERE database_name = ? AND NOT internal",
        [catalog, catalog],
    ).fetchall()
    schemas = [
        row[0]
        for row in con.execute(
            "SELECT schema_name FROM duckdb_schemas() WHERE database_name = ? AND NOT internal", [catalog]
        ).fetchall()
    ]
    dropped = stale(existing, relations, sources)
    left = [entry for entry in existing if entry not in dropped]
    # One transaction: the drops make one snapshot, and a failed drop leaves the catalog as it was.
    con.execute("BEGIN")
    for schema, name, kind in dropped:
        con.execute(f"DROP {kind} {quote(catalog)}.{quote(schema)}.{quote(name)}")
        print(f"dropped {kind.lower()} {schema}.{name}: no dbt model makes it")
    for schema in empty(schemas, left, relations, sources):
        con.execute(f"DROP SCHEMA {quote(catalog)}.{quote(schema)}")
        print(f"dropped schema {schema}: no dbt model writes to it")
    con.execute("COMMIT")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
