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


def _model(name, code=None):
    return {"resource_type": "model", "schema": "main", "name": name, "compiled_code": code}


MANIFEST = {
    "nodes": {
        "model.p.raw": _model("raw", code="  select 1  "),
        "model.p.scratch": _model("scratch", code="select 2"),
        "model.p.empty": _model("empty", code=""),
        "test.p.t": {"resource_type": "test", "schema": "main", "name": "raw", "compiled_code": "x"},
        "model.p.main_renamed": {**_model("main_renamed", code="select 3"), "alias": "renamed"},
        "model.p.py": {**_model("py", code="def model(dbt, session): ..."), "language": "python"},
    },
}

LIVE = {("main", "raw"), ("main", "empty"), ("main", "renamed"), ("main", "py")}

PROFILES = {
    "config": {"send_anonymous_usage_stats": False},
    "geo": {
        "outputs": {
            "default": {
                "attach": [
                    {"path": "ducklake:{{ env_var('FDL_WORKING') }}", "alias": "geo"},
                    {
                        "path": "ducklake:https://data.queria.io/datasets/queria/e_stat/ducklake.duckdb",
                        "alias": "e_stat",
                    },
                    {"path": "ducklake:/tmp/local.duckdb", "alias": "local"},
                ]
            }
        }
    },
}


class CompiledSqlTest(unittest.TestCase):
    def test_only_models_in_the_catalog_with_sql(self):
        self.assertEqual(
            dbt_lineage.compiled_sql(MANIFEST, LIVE),
            {("main", "raw"): "select 1", ("main", "renamed"): "select 3"},
        )


class CatalogsTest(unittest.TestCase):
    def test_public_datasets_the_profile_attaches(self):
        self.assertEqual(
            dbt_lineage.catalogs(PROFILES, env={}),
            {"e_stat": "https://data.queria.io/datasets/queria/e_stat/"},
        )

    def test_env_var_with_a_default(self):
        url = "ducklake:https://data.queria.io/datasets/queria/houjin_bangou/ducklake.duckdb"
        profiles = {
            "p": {
                "outputs": {
                    "default": {
                        "attach": [
                            {"path": f"{{{{ env_var('HOUJIN_BANGOU', '{url}') }}}}", "alias": "houjin_bangou"},
                        ]
                    }
                }
            }
        }
        self.assertEqual(
            dbt_lineage.catalogs(profiles, env={}),
            {"houjin_bangou": "https://data.queria.io/datasets/queria/houjin_bangou/"},
        )
        self.assertEqual(dbt_lineage.catalogs(profiles, env={"HOUJIN_BANGOU": "ducklake:/tmp/h.duckdb"}), {})

    def test_only_the_target_in_use(self):
        stg = {"path": "ducklake:https://stg.example.org/e_stat/ducklake.duckdb", "alias": "e_stat"}
        profiles = {
            "geo": {
                "target": "default",
                "outputs": {**PROFILES["geo"]["outputs"], "stg": {"attach": [stg]}},
            }
        }
        self.assertEqual(
            dbt_lineage.catalogs(profiles, env={}),
            {"e_stat": "https://data.queria.io/datasets/queria/e_stat/"},
        )
        self.assertEqual(
            dbt_lineage.catalogs(profiles, env={"DBT_TARGET": "stg"}),
            {"e_stat": "https://stg.example.org/e_stat/"},
        )


class WithLineageTest(unittest.TestCase):
    SQL = {("main", "mart"): "select 1", ("main", "raw"): "select 2"}
    ALIASES = {"e_stat": "https://data.queria.io/datasets/queria/e_stat/"}

    def test_writer_wins(self):
        declared = {
            "name": "geo",
            "catalogs": {"e_stat": "https://example.org/e_stat/"},
            "tables": [{"schema": "main", "name": "mart", "sql": "select 9"}],
        }
        out = dbt_lineage.with_lineage(declared, self.SQL, self.ALIASES)
        self.assertEqual(out["tables"][0]["sql"], "select 9")
        self.assertEqual(out["catalogs"], {"e_stat": "https://example.org/e_stat/"})

    def test_adds_undescribed_tables(self):
        out = dbt_lineage.with_lineage({"name": "geo"}, self.SQL, {})
        self.assertEqual([t["name"] for t in out["tables"]], ["mart", "raw"])
        self.assertNotIn("catalogs", out)

    def test_leaves_malformed_catalogs_to_fdl(self):
        out = dbt_lineage.with_lineage({"name": "geo", "catalogs": ["e_stat"]}, {}, self.ALIASES)
        self.assertEqual(out["catalogs"], ["e_stat"])


class UnlistedTest(unittest.TestCase):
    def test_hides_tables_without_a_description(self):
        declared = {
            "name": "geo",
            "tables": [
                {"schema": "main", "name": "mart", "title": "Mart", "sql": "select 1"},
                {"schema": "main", "name": "raw", "sql": "select 2"},
            ],
        }
        live = {("main", "mart"), ("main", "raw"), ("_source", "loaded")}
        out = dbt_lineage.unlisted(declared, live)
        hidden = {
            (t["schema"], t["name"])
            for t in out["tables"]
            if t.get("annotations") == {"queria": {"published": False}}
        }
        self.assertEqual(hidden, {("main", "raw"), ("_source", "loaded")})
        self.assertEqual(out["tables"][0], declared["tables"][0])

    def test_keeps_a_table_the_writer_described(self):
        declared = {"name": "geo", "tables": [{"schema": "main", "name": "t", "description": "d"}]}
        out = dbt_lineage.unlisted(declared, {("main", "t")})
        self.assertEqual(out["tables"], declared["tables"])


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
