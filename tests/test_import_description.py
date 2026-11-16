"""scripts/import_description.py のテスト。

実行: uv run --no-project --with fdl-cli python -m unittest discover -s tests
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb
from fdl import catalog, repo
from fdl import diff as diffs
from fdl import merge as merging
from fdl.cli import app
from fdl.stores import LocalStore
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import import_description  # noqa: E402
import sync_branch  # noqa: E402

runner = CliRunner()


def fdl(*args):
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    return result.output


def build(remote: Path, store: LocalStore, working: Path, sql: str, branch: str) -> None:
    fdl("clone", remote, working, "--branch", branch)
    con = duckdb.connect()
    for statement in catalog.attach_sql(working, alias="lake", data_path=repo.data_url(store)):
        if statement:
            con.execute(statement)
    con.execute(sql)
    con.close()
    fdl("commit", working, "-m", "build", "--author", "ci")
    fdl("push", working)


DATASET = {
    "name": "towns",
    "title": "Towns",
    "tables": [{"schema": "main", "name": "towns", "description": "Towns", "fields": [{"name": "id", "title": "ID"}]}],
}


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(os.chdir, os.getcwd())
        self.code = self.tmp / "code"
        self.code.mkdir()
        # What queria metadata pack writes for fdl commit
        (self.code / "dataset.json").write_text(json.dumps(DATASET))
        (self.code / "README.md").write_text("# Towns\n")
        self.remote = self.tmp / "remote"
        self.store = LocalStore(self.remote)
        fdl("init", self.remote)
        os.chdir(self.code)
        build(self.remote, self.store, self.code / ".fdl" / "a.sqlite", "CREATE TABLE lake.main.towns AS SELECT 1 AS id", "apply")
        fdl("branch", self.remote, "main", "refs/heads/apply")

    def edit_main(self, change) -> None:
        """Someone clones main into an empty folder, edits the description and pushes."""
        person = self.tmp / "person"
        person.mkdir()
        os.chdir(person)
        fdl("clone", self.remote, "p.sqlite")
        # What a newer fdl clone writes itself, written here for any fdl
        main = repo.head(self.store, "refs/heads/main")
        described = merging.declared(diffs.described(self.store, main))
        (person / "dataset.json").write_text(json.dumps(described, indent=2))
        (person / "README.md").write_text((self.code / "README.md").read_text())
        change(person)
        fdl("commit", "p.sqlite", "-m", "edit", "--author", "ud")
        fdl("push", "p.sqlite")
        os.chdir(self.code)

    def test_nothing_changed_writes_nothing(self):
        out = self.tmp / "versions"
        self.assertFalse(import_description.versions_to_import(self.store, out))
        self.assertFalse(out.exists())

    def test_main_and_its_base_are_written_when_main_changed(self):
        def change(folder: Path):
            path = folder / "dataset.json"
            path.write_text(path.read_text().replace('"title": "ID"', '"title": "Town ID"'))
            (folder / "README.md").write_text("# Towns of Japan\n")

        self.edit_main(change)
        out = self.tmp / "versions"
        self.assertTrue(import_description.versions_to_import(self.store, out))
        main = json.loads((out / "main" / "dataset.json").read_text())
        self.assertEqual(main["tables"][0]["fields"][0]["title"], "Town ID")
        self.assertEqual((out / "main" / "README.md").read_text(), "# Towns of Japan\n")
        self.assertEqual((out / "base" / "README.md").read_text(), "# Towns\n")

    def test_a_change_the_code_already_holds_is_not_brought_back(self):
        self.edit_main(lambda f: (f / "README.md").write_text("# Same\n"))
        (self.code / "README.md").write_text("# Same\n")
        build(self.remote, self.store, self.code / ".fdl" / "b.sqlite", "SELECT 1", "apply")
        self.assertFalse(import_description.versions_to_import(self.store, self.tmp / "versions"))

    def heads(self):
        return repo.head(self.store, "refs/heads/main"), repo.head(self.store, "refs/heads/apply")

    def test_settled_once_the_code_holds_the_words_main_changed(self):
        self.edit_main(lambda f: (f / "README.md").write_text("# Same\n"))
        self.assertFalse(sync_branch.settled(self.store, *self.heads()))
        (self.code / "README.md").write_text("# Same\n")
        build(self.remote, self.store, self.code / ".fdl" / "b.sqlite", "SELECT 1", "apply")
        self.assertTrue(sync_branch.settled(self.store, *self.heads()))

    def test_not_settled_when_main_changed_rows(self):
        person = self.tmp / "rows"
        person.mkdir()
        os.chdir(person)
        (person / "dataset.json").write_text('{"name": "towns", "title": "Towns"}')
        build(self.remote, self.store, person / "p.sqlite", "INSERT INTO lake.main.towns VALUES (2)", "main")
        os.chdir(self.code)
        self.assertFalse(sync_branch.settled(self.store, *self.heads()))


if __name__ == "__main__":
    unittest.main()
