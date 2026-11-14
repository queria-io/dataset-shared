"""scripts/fdl_read.py のテスト。

実行: uv run --no-project --with duckdb --with dbt-duckdb python -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fdl_read  # noqa: E402

REMOTE = "https://data.queria.io/datasets/queria/gijiroku/"


def working(state: dict | None) -> str:
    folder = Path(tempfile.mkdtemp())
    path = folder / "work.sqlite"
    if state is not None:
        path.with_name(path.name + ".fdl.json").write_text(json.dumps(state))
    return str(path)


class SetupTest(unittest.TestCase):
    def test_secret_for_the_remote(self):
        statements = fdl_read.setup(working({"remote": REMOTE}), lambda remote: "to'ken")
        con = duckdb.connect()
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
        for statement in statements:
            con.execute(statement)
        found = con.execute("SELECT type, scope FROM duckdb_secrets() WHERE name = 'fdl_read'").fetchall()
        self.assertEqual(found, [("http", [REMOTE])])

    def test_nothing_without_working_or_token(self):
        self.assertEqual(fdl_read.setup(None, lambda remote: "token"), [])
        self.assertEqual(fdl_read.setup(working(None), lambda remote: "token"), [])
        self.assertEqual(fdl_read.setup(working({"remote": REMOTE}), lambda remote: None), [])
        self.assertEqual(fdl_read.setup(working({"remote": "/tmp/repo"}), lambda remote: "token"), [])


if __name__ == "__main__":
    unittest.main()
