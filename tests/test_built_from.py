"""built_from.py のテスト。

実行: python -m unittest discover -s tests
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import built_from  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"


class DescriptionTest(unittest.TestCase):
    def test_description_files(self):
        for path in (
            "tabledoc.yml",
            "README.md",
            "models/a/population.tabledoc.yml",
            "models/a/population.md",
            "models/README.md",
            "docs/index.md",
        ):
            self.assertTrue(built_from.is_description(path), path)

    def test_code_and_other_files(self):
        for path in ("models/a/population.sql", "main.py", "uv.lock", "shared", "profiles.yml", "queria.yml", "dataset.yml"):
            self.assertFalse(built_from.is_description(path), path)


class KeepsDataTest(unittest.TestCase):
    """The workflows at two commits of a real repository."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.dir.name)
        for args in (
            ["init", "-q"],
            ["config", "user.email", "t@example.org"],
            ["config", "user.name", "t"],
            ["config", "commit.gpgsign", "false"],
        ):
            subprocess.run(["git", *args], check=True)
        self.before = self._commit("timeout_minutes: 120\n    secrets:\n      A: x\n")

    def tearDown(self):
        os.chdir(self.cwd)
        self.dir.cleanup()

    def _commit(self, tail):
        Path(".github/workflows").mkdir(parents=True, exist_ok=True)
        Path(".github/workflows/sync.yml").write_text(
            "name: Apply\njobs:\n  sync:\n    uses: o/r/.github/workflows/sync.yml@abc\n    with:\n      " + tail
        )
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "c"], check=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def test_description_and_other_settings(self):
        for path in ("README.md", "tabledoc.yml", ".github/dependabot.yml"):
            self.assertTrue(built_from.keeps_data(path, self.before, self.before), path)

    def test_code_and_shared(self):
        for path in ("models/a/population.sql", "main.py", "shared", ".dbtignore", "github/x.yml"):
            self.assertFalse(built_from.keeps_data(path, self.before, self.before), path)

    def test_time_limit_changed(self):
        after = self._commit("timeout_minutes: 240\n    secrets:\n      A: x\n")
        self.assertTrue(built_from.keeps_data(".github/workflows/sync.yml", self.before, after))

    def test_inputs_changed(self):
        after = self._commit("timeout_minutes: 120\n    secrets:\n      A: x\n      B: y\n")
        self.assertFalse(built_from.keeps_data(".github/workflows/sync.yml", self.before, after))

    def test_workflow_added(self):
        self.assertFalse(built_from.keeps_data(".github/workflows/new.yml", self.before, self.before))


class TrailerTest(unittest.TestCase):
    def test_reads_the_last_trailer(self):
        message = f"build\n\nBuilt-From: {'f' * 40}\nBuilt-From: {SHA}"
        self.assertEqual(built_from.built_from(message), SHA)

    def test_no_trailer(self):
        self.assertIsNone(built_from.built_from("build"))
        self.assertIsNone(built_from.built_from("Built-From: abc"))


class ChangedSinceTest(unittest.TestCase):
    """A real repository: the commit recorded, then a change to the description or the code."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.dir.name)
        for args in (
            ["init", "-q"],
            ["config", "user.email", "t@example.org"],
            ["config", "user.name", "t"],
            ["config", "commit.gpgsign", "false"],
        ):
            subprocess.run(["git", *args], check=True)
        Path("tabledoc.yml").write_text("name: x\n")
        Path("main.py").write_text("print(1)\n")
        self.first = self._commit()

    def tearDown(self):
        os.chdir(self.cwd)
        self.dir.cleanup()

    def _commit(self):
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-qm", "c"], check=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def _changed(self, head):
        env = {"GITHUB_ACTIONS": "true", "GITHUB_SHA": head}
        with mock.patch.dict(os.environ, env):
            return built_from.changed_since(self.first)

    def test_description_only(self):
        Path("tabledoc.yml").write_text("name: x\ntitle: X\n")
        head = self._commit()
        self.assertEqual(self._changed(head), ["tabledoc.yml"])

    def test_code_changed(self):
        Path("main.py").write_text("print(2)\n")
        head = self._commit()
        self.assertEqual(self._changed(head), ["main.py"])

    def test_non_ascii_path(self):
        Path("docs").mkdir()
        Path("docs/概要.md").write_text("x\n")
        head = self._commit()
        self.assertEqual(self._changed(head), ["docs/概要.md"])

    def test_code_moved_under_docs(self):
        Path("docs").mkdir()
        subprocess.run(["git", "mv", "main.py", "docs/main.py"], check=True)
        head = self._commit()
        self.assertEqual(sorted(self._changed(head)), ["docs/main.py", "main.py"])

    def test_outside_actions(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "", "GITHUB_SHA": self.first}):
            self.assertIsNone(built_from.changed_since(self.first))


class DescriptionOnlyTest(unittest.TestCase):
    """Whether to skip, given the paths changed since the commit apply records."""

    WITH_DESCRIPTION = {"tables": {"_tabledoc.tables": {}, "main.towns": {}}}

    def _check(self, changed, indexed=WITH_DESCRIPTION):
        head = mock.Mock(message=f"build\n\nBuilt-From: {SHA}")
        with mock.patch("fdl.repo.head", return_value=head), \
                mock.patch("fdl.diff.indexed", return_value=indexed), \
                mock.patch.object(built_from, "changed_since", return_value=changed), \
                mock.patch.object(built_from, "checkout_commit", return_value="f" * 40), \
                mock.patch.object(built_from, "_jobs", side_effect=lambda sha, path: {"sync": sha == SHA}):
            return built_from.description_only(object())

    def test_description_changed(self):
        self.assertEqual(self._check(["tabledoc.yml"]), SHA)

    def test_code_changed(self):
        self.assertIsNone(self._check(["tabledoc.yml", "main.py"]))

    def test_version_with_only_an_earlier_schema_builds(self):
        # tabledoc load does not read the earlier _queria and _meta schemas
        for schema in ("_queria", "_meta"):
            self.assertIsNone(self._check(["tabledoc.yml"], {"tables": {f"{schema}.tables": {}}}), schema)

    def test_version_before_meta(self):
        self.assertIsNone(self._check(["tabledoc.yml"], {"tables": {"main.towns": {}}}))

    def test_ci_changed(self):
        self.assertEqual(self._check([".github/dependabot.yml"]), SHA)

    def test_workflow_inputs_changed(self):
        self.assertIsNone(self._check([".github/workflows/sync.yml"]))

    def test_shared_moved(self):
        self.assertIsNone(self._check([".github/workflows/sync.yml", "shared"]))

    def test_nothing_changed_builds(self):
        self.assertIsNone(self._check([]))


if __name__ == "__main__":
    unittest.main()
