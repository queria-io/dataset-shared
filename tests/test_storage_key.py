"""scripts/storage_key.py のテスト。

実行: uv run --no-project --with duckdb python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import storage_key  # noqa: E402

ANSWER = {
    "AccessKeyId": "id",
    "SecretAccessKey": "se'cret",
    "SessionToken": "token",
    "QueriaStorage": {
        "mode": "s3",
        "bucket": "queria",
        "endpoint": "https://acct.r2.cloudflarestorage.com",
        "region": "auto",
        "base_prefix": "019a4f2b/",
        "prefix": "019a4f2b/data/",
    },
}


class StorageKeyTest(unittest.TestCase):
    def test_the_endpoint_follows_the_remote(self):
        self.assertEqual(
            storage_key.api_url("https://data.queria.io/datasets/queria/mext/"),
            "https://queria.io/api/datasets/queria/mext/credentials",
        )
        self.assertIsNone(storage_key.api_url("s3://bucket/lake/"))
        self.assertIsNone(storage_key.api_url("https://data.example.org/datasets/a/b/"))

    def test_the_storage_is_the_datasets_root_in_its_bucket(self):
        values = storage_key.exports(ANSWER)
        self.assertEqual(values["FDL_STORAGE"], "s3://queria/019a4f2b/")
        self.assertEqual(values["AWS_SESSION_TOKEN"], "token")

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            storage_key.exports({**ANSWER, "QueriaStorage": {**ANSWER["QueriaStorage"], "mode": "worker"}})

    def test_a_key_for_the_whole_dataset_is_not_used(self):
        storage = {key: value for key, value in ANSWER["QueriaStorage"].items() if key != "prefix"}
        with self.assertRaises(ValueError):
            storage_key.exports({**ANSWER, "QueriaStorage": storage})

    def test_the_secret_is_valid_sql(self):
        import duckdb

        sql = storage_key.secret_sql(storage_key.exports(ANSWER))
        self.assertIn("ENDPOINT 'acct.r2.cloudflarestorage.com'", sql)
        con = duckdb.connect()
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute(sql.replace("PERSISTENT", "TEMPORARY"))

    def test_no_token_means_no_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(storage_key.issue("https://data.queria.io/datasets/queria/mext/"), 1)


if __name__ == "__main__":
    unittest.main()
