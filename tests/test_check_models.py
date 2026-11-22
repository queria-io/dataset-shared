"""check_models.py のテスト。

実行: python -m unittest discover -s tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_models  # noqa: E402


_tmp = tempfile.TemporaryDirectory()


def models(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(dir=_tmp.name)) / "models"
    for name, sql in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(sql)
    return root


GOOD = {
    "a/raw/raw_x.sql": "select * from read_parquet('data/x/*.parquet')",
    "a/stg/stg_x.sql": "select * from {{ ref('raw_x') }}",
    "a/mart/x.sql": "select * from {{ ref('stg_x') }} join read_csv('reference/a/codes.csv') using (code)",
    "a/mart/y.sql": "select * from {{ ref('x') }} join {{ ref('seed_codes') }} using (code)",
}


class CheckModelsTest(unittest.TestCase):
    def test_layered_models_pass(self):
        self.assertEqual(check_models.problems(models(GOOD)), [])

    def test_model_outside_the_layers(self):
        found = check_models.problems(models({**GOOD, "a/z.sql": "select 1"}))
        self.assertEqual(found, ["a/z.sql: not in a raw, stg or mart folder"])

    def test_file_read_outside_raw(self):
        found = check_models.problems(models({**GOOD, "a/stg/stg_x.sql": "select * from read_csv('data/x.csv')"}))
        self.assertEqual(found, ["a/stg/stg_x.sql: reads files outside raw"])

    def test_a_read_in_a_comment_is_ignored(self):
        sql = "-- read_parquet('data/x')\n{# read_csv('data/y') #}\nselect * from {{ ref('raw_x') }}"
        self.assertEqual(check_models.problems(models({**GOOD, "a/stg/stg_x.sql": sql})), [])

    def test_refs_go_one_way(self):
        found = check_models.problems(
            models({**GOOD, "a/stg/stg_x.sql": "select * from {{ ref('x') }}", "a/mart/y.sql": "select * from {{ ref('raw_x') }}"})
        )
        self.assertEqual(found, ["a/mart/y.sql: mart refs raw model raw_x", "a/stg/stg_x.sql: stg refs mart model x"])

    def test_incremental_needs_a_key_or_a_filter(self):
        bare = "{{ config(materialized='incremental') }}\nselect * from {{ ref('stg_x') }}"
        keyed = "{{ config(materialized='incremental', unique_key='id') }}\nselect * from {{ ref('stg_x') }}"
        filtered = bare + "\n{% if is_incremental() %} where id not in (select id from {{ this }}) {% endif %}"
        self.assertEqual(
            check_models.problems(models({**GOOD, "a/mart/x.sql": bare})),
            ["a/mart/x.sql: incremental with no unique_key or is_incremental() filter"],
        )
        self.assertEqual(check_models.problems(models({**GOOD, "a/mart/x.sql": keyed})), [])
        self.assertEqual(check_models.problems(models({**GOOD, "a/mart/x.sql": filtered})), [])

    def test_a_glob_or_dashes_in_a_string_hide_nothing(self):
        sql = "select '--' as a, * from {{ ref('raw_x') }} where p like 'a/*' union select * from {{ ref('x') }} where q like 'b*/'"
        found = check_models.problems(models({**GOOD, "a/stg/stg_x.sql": sql}))
        self.assertEqual(found, ["a/stg/stg_x.sql: stg refs mart model x"])

    def test_refs_with_whitespace_control(self):
        found = check_models.problems(models({**GOOD, "a/stg/stg_x.sql": "select * from {{- ref('x') -}}"}))
        self.assertEqual(found, ["a/stg/stg_x.sql: stg refs mart model x"])

    def test_other_file_reads_outside_raw(self):
        for sql in ("select * from 'data/x.parquet'", "select * from parquet_scan('data/x.parquet')"):
            found = check_models.problems(models({**GOOD, "a/stg/stg_x.sql": sql}))
            self.assertEqual(found, ["a/stg/stg_x.sql: reads files outside raw"], sql)
        plain = "select * from {{ ref('raw_x') }} where a is distinct from 'x' or b in (select * from 'reference/a.csv')"
        self.assertEqual(check_models.problems(models({**GOOD, "a/stg/stg_x.sql": plain})), [])

    def test_incremental_in_yml(self):
        root = models(GOOD)
        (root.parent / "dbt_project.yml").write_text("models:\n  a:\n    +materialized: incremental\n")
        self.assertEqual(
            check_models.problems(root),
            ["dbt_project.yml: incremental set outside the model; set it in the model's config()"],
        )

    def test_no_description_in_dbt_yml(self):
        root = models({
            **GOOD,
            "a/mart/_models.yml": "models:\n  - name: x\n    description: X\n    config:\n      meta:\n        title: X\n",
            "a/mart/_tests.yml": "models:\n  - name: y\n    columns:\n      - name: id\n        data_tests: [unique]\n",
            "a/mart/x.tabledoc.yml": "$schema: https://queria.io/schemas/table/v1.json\ndescription: X\n",
            "a/mart/y.table.yml": "sources:\n  - path: https://x.example\ndescription: Y\n",
            "a/mart/_metrics.yml": "semantic_models: []\nmetrics:\n  - name: total\n    description: Sum\n",
            "a/mart/_more.yaml": "models:\n  - name: y\n    description: Y\n",
        })
        self.assertEqual(
            check_models.problems(root),
            [
                "a/mart/_models.yml: description and meta in dbt's yml; write the description in the table's own tabledoc file",
                "a/mart/_more.yaml: description in dbt's yml; write the description in the table's own tabledoc file",
            ],
        )


if __name__ == "__main__":
    unittest.main()
