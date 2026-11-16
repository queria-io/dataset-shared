"""check_models.py のテスト。

実行: python -m unittest discover -s tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_models  # noqa: E402


def models(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp()) / "models"
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


if __name__ == "__main__":
    unittest.main()
