"""dbt_lineage.py のテスト。

実行: python -m unittest discover -s tests
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dbt_lineage  # noqa: E402


def _model(name, database="geo", schema="main", code=None):
    return {
        "resource_type": "model",
        "database": database,
        "schema": schema,
        "name": name,
        "compiled_code": code,
    }


MANIFEST = {
    "nodes": {
        "model.p.raw": _model("raw", code="select 1"),
        "model.p.scratch": _model("scratch", database="memory", code="select * from raw"),
        "model.p.mart": _model("mart", code="  select * from scratch  "),
        "model.p.other": _model("other", code="select 2"),
    },
    "sources": {"source.p.e_stat.census": {"resource_type": "source"}},
    "parent_map": {
        "model.p.raw": ["source.p.e_stat.census"],
        "model.p.scratch": ["model.p.raw"],
        "model.p.mart": ["model.p.scratch", "source.p.e_stat.census"],
        "model.p.other": [],
    },
}

LIVE = {("main", "raw"), ("main", "mart"), ("main", "other")}


class LineageTest(unittest.TestCase):
    def test_sql_and_parents_through_other_database(self):
        found = dbt_lineage.lineage(MANIFEST, LIVE)
        self.assertEqual(
            found[("main", "mart")], {"sql": "select * from scratch", "depends_on": ["main.raw"]}
        )

    def test_sources_are_not_parents(self):
        self.assertEqual(dbt_lineage.lineage(MANIFEST, LIVE)[("main", "raw")], {"sql": "select 1"})

    def test_only_tables_in_the_catalog(self):
        self.assertNotIn(("main", "scratch"), dbt_lineage.lineage(MANIFEST, LIVE))


class WithLineageTest(unittest.TestCase):
    FOUND = {
        ("main", "mart"): {"sql": "select 1", "depends_on": ["main.raw"]},
        ("main", "raw"): {"sql": "select 2"},
        ("main", "bare"): {},
    }

    def test_writer_wins(self):
        declared = {"name": "geo", "tables": [{"schema": "main", "name": "mart", "depends_on": []}]}
        tables = dbt_lineage.with_lineage(declared, self.FOUND)["tables"]
        self.assertEqual(tables[0], {"schema": "main", "name": "mart", "sql": "select 1", "depends_on": []})

    def test_adds_undescribed_tables_with_something_to_say(self):
        tables = dbt_lineage.with_lineage({"name": "geo"}, self.FOUND)["tables"]
        self.assertEqual(
            [(t["name"], t.get("sql")) for t in tables], [("mart", "select 1"), ("raw", "select 2")]
        )


class LiveRelationsTest(unittest.TestCase):
    def test_reads_live_tables_and_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work.sqlite"
            con = sqlite3.connect(path)
            con.executescript(
                """
                CREATE TABLE ducklake_schema (schema_id, schema_name, end_snapshot);
                CREATE TABLE ducklake_table (table_id, schema_id, table_name, end_snapshot);
                CREATE TABLE ducklake_view (view_id, schema_id, view_name, end_snapshot);
                INSERT INTO ducklake_schema VALUES (1, 'main', NULL), (2, 'gone', 5);
                INSERT INTO ducklake_table VALUES (1, 1, 'kept', NULL), (2, 1, 'dropped', 4), (3, 2, 'old', NULL);
                INSERT INTO ducklake_view VALUES (4, 1, 'v', NULL);
                """
            )
            con.commit()
            con.close()
            self.assertEqual(dbt_lineage.live_relations(path), {("main", "kept"), ("main", "v")})


if __name__ == "__main__":
    unittest.main()
