"""scripts/fdl_read.py のテスト。

実行: uv run --no-project --with duckdb --with dbt-duckdb python -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fdl_read  # noqa: E402

REMOTE = "https://data.queria.io/datasets/queria/gijiroku/"


def working(state: dict | None) -> str:
    folder = Path(tempfile.mkdtemp())
    path = folder / "work.sqlite"
    if state is not None:
        state_file = path.with_name(path.name + ".fdl") / "state.json"
        state_file.parent.mkdir()
        state_file.write_text(json.dumps(state))
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

    def test_reads_the_state_an_earlier_fdl_kept(self):
        path = Path(tempfile.mkdtemp()) / "work.sqlite"
        path.with_name(path.name + ".fdl.json").write_text(json.dumps({"remote": REMOTE}))
        self.assertEqual(len(fdl_read.setup(str(path), lambda remote: "token")), 1)

    def test_nothing_without_working_or_token(self):
        self.assertEqual(fdl_read.setup(None, lambda remote: "token"), [])
        self.assertEqual(fdl_read.setup(working(None), lambda remote: "token"), [])
        self.assertEqual(fdl_read.setup(working({"remote": REMOTE}), lambda remote: None), [])
        self.assertEqual(fdl_read.setup(working({"remote": "/tmp/repo"}), lambda remote: "token"), [])

    def test_statements_read_fdl_working(self):
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(fdl_read.statements(), [])


class CursorTest(unittest.TestCase):
    def test_each_cursor_replaces_the_secret_with_a_fresh_token(self):
        executed = []

        class Recorder:
            def execute(self, statement):
                executed.append(statement)

        plugin = fdl_read.Plugin.__new__(fdl_read.Plugin)
        tokens = iter(["first", "second"])
        with unittest.mock.patch.object(fdl_read, "_token", lambda remote: next(tokens)), \
                unittest.mock.patch.dict("os.environ", {"FDL_WORKING": working({"remote": REMOTE})}):
            plugin.configure_connection(Recorder())
            plugin.configure_cursor(Recorder())
        self.assertEqual(len(executed), 2)
        self.assertIn("'first'", executed[0])
        self.assertIn("'second'", executed[1])

if __name__ == "__main__":
    unittest.main()
