"""scripts/storage_key.py のテスト。

実行: uv run --no-project --with duckdb python -m unittest discover -s tests
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import storage_key  # noqa: E402

REMOTE = "https://data.queria.io/datasets/queria/mext/"

ANSWER = {
    "Version": 1,
    "AccessKeyId": "id",
    "Expiration": "2030-01-01T00:00:00Z",
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
        storage = storage_key.storage_of(ANSWER)
        self.assertEqual(storage["root"], "s3://queria/019a4f2b/")

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            storage_key.storage_of({**ANSWER, "QueriaStorage": {**ANSWER["QueriaStorage"], "mode": "worker"}})

    def test_a_key_for_the_whole_dataset_is_not_used(self):
        storage = {key: value for key, value in ANSWER["QueriaStorage"].items() if key != "prefix"}
        with self.assertRaises(ValueError):
            storage_key.key_of({**ANSWER, "QueriaStorage": storage})

    def test_the_credential_process_prints_the_key_alone(self):
        env = {"FDL_TOKEN": "t"}
        out = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch.object(storage_key, "request", return_value=ANSWER), \
                contextlib.redirect_stdout(out):
            self.assertEqual(storage_key.credential_process(REMOTE), 0)
        self.assertEqual(json.loads(out.getvalue()), {
            "Version": 1,
            "AccessKeyId": "id",
            "SecretAccessKey": "se'cret",
            "SessionToken": "token",
            "Expiration": "2030-01-01T00:00:00Z",
        })

    def test_the_secret_gets_its_key_through_the_profile(self):
        import duckdb

        name = "fdl_build_0123"
        storage = storage_key.storage_of(ANSWER)
        with tempfile.TemporaryDirectory() as folder:
            process = Path(folder) / "process.sh"
            key = Path(folder) / "key.json"
            key.write_text(json.dumps(storage_key.key_of(ANSWER)))
            process.write_text(f"#!/bin/sh\ncat {key}\n")
            process.chmod(0o755)
            config = Path(folder) / "aws.config"
            config.write_text(f"[profile {storage_key.PROFILE}]\ncredential_process = {process}\n")
            with mock.patch.dict("os.environ", {"AWS_CONFIG_FILE": str(config)}):
                con = duckdb.connect()
                con.execute("INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws;")
                con.execute(storage_key.secret_sql(name, storage).replace("PERSISTENT", "TEMPORARY"))
                (secret,) = con.execute(
                    "SELECT secret_string FROM duckdb_secrets() WHERE name = ?", [name]
                ).fetchone()
        self.assertIn("key_id=id", secret)
        self.assertIn("'refresh': auto", secret)
        self.assertIn("endpoint=acct.r2.cloudflarestorage.com", secret)

    def test_the_secret_is_scoped_to_data_alone(self):
        sql = storage_key.secret_sql("fdl_build_0123", storage_key.storage_of(ANSWER))
        self.assertIn("SCOPE 's3://queria/019a4f2b/data/'", sql)
        self.assertIn(f"PROFILE '{storage_key.PROFILE}'", sql)

    def test_the_profile_runs_this_script(self):
        config = storage_key.aws_config(REMOTE, Path("/tmp/fdl_build_0123.x/first.json"))
        self.assertIn(f"[profile {storage_key.PROFILE}]", config)
        self.assertIn(f"credential-process {REMOTE} /tmp/fdl_build_0123.x/first.json", config)

    def test_the_first_key_is_handed_over_once(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first.json"
            first.write_text('{"AccessKeyId": "first"}')
            out = io.StringIO()
            with mock.patch.object(storage_key, "request", side_effect=AssertionError), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(storage_key.credential_process(REMOTE, first), 0)
            self.assertEqual(json.loads(out.getvalue()), {"AccessKeyId": "first"})
            self.assertFalse(first.exists())
            out = io.StringIO()
            with mock.patch.dict("os.environ", {"FDL_TOKEN": "t"}, clear=True), \
                    mock.patch.object(storage_key, "request", return_value=ANSWER), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(storage_key.credential_process(REMOTE, first), 0)
            self.assertEqual(json.loads(out.getvalue())["AccessKeyId"], "id")

    def test_a_429_waits_for_the_time_queria_names(self):
        busy = urllib.error.HTTPError(REMOTE, 429, "Too Many Requests", {"Retry-After": "7"}, None)
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"FDL_TOKEN": "t"}, clear=True), \
                mock.patch.object(storage_key, "request", side_effect=[busy, ANSWER]), \
                mock.patch.object(storage_key.time, "sleep") as sleep, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(storage_key.credential_process(REMOTE), 0)
        sleep.assert_called_once_with(7)
        self.assertEqual(json.loads(out.getvalue())["AccessKeyId"], "id")

    def test_other_refusals_do_not_wait(self):
        refused = urllib.error.HTTPError(REMOTE, 402, "Payment Required", {}, None)
        with mock.patch.dict("os.environ", {"FDL_TOKEN": "t"}, clear=True), \
                mock.patch.object(storage_key, "request", side_effect=refused), \
                mock.patch.object(storage_key.time, "sleep") as sleep, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(storage_key.credential_process(REMOTE), 1)
        sleep.assert_not_called()

    def test_drop_touches_only_the_builds_own_secret(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(storage_key.drop(), 0)
        with mock.patch.dict("os.environ", {"FDL_STORAGE_SECRET": "other; DROP"}, clear=True):
            self.assertEqual(storage_key.drop(), 0)

    def test_no_token_means_no_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(storage_key.issue(REMOTE), 1)


if __name__ == "__main__":
    unittest.main()
