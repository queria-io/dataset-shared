"""drop_stale.py のテスト。

実行: python -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import drop_stale  # noqa: E402


def node(kind: str, schema: str, name: str, materialized: str = "table", database: str = "ds", **extra) -> dict:
    return {
        "resource_type": kind,
        "database": database,
        "schema": schema,
        "name": name,
        "alias": extra.get("alias", name),
        "config": {"materialized": materialized},
    }


MANIFEST = {
    "nodes": {
        "model.ds.x": node("model", "a", "x"),
        "model.ds.y": node("model", "a", "y_model", "view", alias="y"),
        "model.ds.stg_x": node("model", "a", "stg_x", "ephemeral"),
        "seed.ds.codes": node("seed", "main", "codes"),
        "model.ds.other": node("model", "a", "other", database="elsewhere"),
        "test.ds.t": {"resource_type": "test", "database": "ds", "schema": "a", "name": "t", "config": {}},
    },
    "sources": {
        "source.ds.src.loaded": {"database": "ds", "schema": "_source", "name": "loaded"},
        "source.ds.pipe.p": {"database": "ds", "schema": "pipe", "name": "p"},
        "source.ds.ext.t": {"database": "geo", "schema": "b", "name": "t"},
    },
}


class KeptTest(unittest.TestCase):
    def test_kept(self):
        relations, sources = drop_stale.kept(MANIFEST, "ds")
        self.assertEqual(relations, {("a", "x"), ("a", "y"), ("main", "codes")})
        self.assertEqual(sources, {"_source", "pipe"})

    def test_stale(self):
        relations, sources = drop_stale.kept(MANIFEST, "ds")
        existing = [
            ("a", "x", "TABLE"),
            ("a", "y", "VIEW"),
            ("a", "stg_x", "TABLE"),  # made ephemeral
            ("a", "removed", "VIEW"),
            ("main", "codes", "TABLE"),
            ("metricflow", "time_spine", "TABLE"),  # a schema whose models are all gone
            ("_source", "loaded", "TABLE"),
            ("_source", "_dlt_loads", "TABLE"),
            ("_source_staging", "loaded", "TABLE"),
            ("pipe", "q", "TABLE"),
        ]
        self.assertEqual(
            drop_stale.stale(existing, relations, sources),
            [("a", "removed", "VIEW"), ("a", "stg_x", "TABLE"), ("metricflow", "time_spine", "TABLE")],
        )


class DuckLakeTest(unittest.TestCase):
    def test_drops_from_the_catalog(self):
        import duckdb

        with tempfile.TemporaryDirectory() as tmp:
            work, data = f"{tmp}/work.sqlite", f"{tmp}/data/"
            con = duckdb.connect()
            con.execute("INSTALL ducklake; LOAD ducklake")
            con.execute(f"ATTACH 'ducklake:{work}' AS ds (DATA_PATH '{data}', META_TYPE 'sqlite')")
            for sql in [
                "CREATE SCHEMA ds.a",
                "CREATE SCHEMA ds._source",
                "CREATE TABLE ds.a.x AS SELECT 1 AS v",
                "CREATE TABLE ds.a.stg_x AS SELECT 1 AS v",
                "CREATE VIEW ds.a.removed AS SELECT 1 AS v",
                "CREATE TABLE ds._source.loaded AS SELECT 1 AS v",
            ]:
                con.execute(sql)
            con.close()
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps(MANIFEST))

            with mock.patch.dict(os.environ, {"FDL_WORKING": work, "FDL_DATA_PATH": data}):
                drop_stale.main(str(manifest), "ds")

            con = duckdb.connect()
            con.execute("LOAD ducklake")
            con.execute(f"ATTACH 'ducklake:{work}' AS ds (DATA_PATH '{data}', META_TYPE 'sqlite')")
            left = con.execute(
                "SELECT schema_name || '.' || table_name FROM duckdb_tables() WHERE database_name = 'ds' "
                "UNION ALL SELECT schema_name || '.' || view_name FROM duckdb_views() WHERE database_name = 'ds' "
                "AND NOT internal ORDER BY 1"
            ).fetchall()
            self.assertEqual([r[0] for r in left], ["_source.loaded", "a.x"])


if __name__ == "__main__":
    unittest.main()
