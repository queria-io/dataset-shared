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
        for path in ("dataset.yml", "README.md", "models/a/population.table.yml", "docs/index.md"):
            self.assertTrue(built_from.is_description(path), path)

    def test_code_and_other_files(self):
        for path in ("models/a/population.sql", "main.py", "uv.lock", "shared", "models/README.md"):
            self.assertFalse(built_from.is_description(path), path)


class TrailerTest(unittest.TestCase):
    def test_reads_the_last_trailer(self):
        message = f"build\n\nBuilt-From: {'f' * 40}\nBuilt-From: {SHA}"
        self.assertEqual(built_from.built_from(message), SHA)

    def test_no_trailer(self):
        self.assertIsNone(built_from.built_from("build"))
        self.assertIsNone(built_from.built_from("Built-From: abc"))

    def test_with_trailer_replaces(self):
        message = built_from.with_trailer(f"build\n\nBuilt-From: {'f' * 40}", SHA)
        self.assertEqual(message, f"build\n\nBuilt-From: {SHA}")


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
        Path("dataset.yml").write_text("name: x\n")
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
        Path("dataset.yml").write_text("name: x\ntitle: X\n")
        head = self._commit()
        self.assertEqual(self._changed(head), ["dataset.yml"])

    def test_code_changed(self):
        Path("main.py").write_text("print(2)\n")
        head = self._commit()
        self.assertEqual(self._changed(head), ["main.py"])

    def test_outside_actions(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "", "GITHUB_SHA": self.first}):
            self.assertIsNone(built_from.changed_since(self.first))


if __name__ == "__main__":
    unittest.main()
