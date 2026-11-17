"""built_from.py のテスト。

実行: python -m unittest discover -s tests
"""

import json
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
            "queria.yml",
            "README.md",
            "models/a/population.md",
            "models/README.md",
            "docs/index.md",
            "dataset.yml",
            "models/a/population.table.yml",
        ):
            self.assertTrue(built_from.is_description(path), path)

    def test_code_and_other_files(self):
        for path in ("models/a/population.sql", "main.py", "uv.lock", "shared", "profiles.yml"):
            self.assertFalse(built_from.is_description(path), path)


def _version(message="build", annotations=None):
    return mock.Mock(message=message, annotations=annotations or {})


class BuiltFromTest(unittest.TestCase):
    def test_reads_the_annotation(self):
        version = _version(f"build\n\nBuilt-From: {'f' * 40}", {"queria": {"built_from": {"commit": SHA}}})
        self.assertEqual(built_from.built_from(version), SHA)

    def test_reads_the_last_trailer_of_an_older_version(self):
        self.assertEqual(built_from.built_from(_version(f"build\n\nBuilt-From: {'f' * 40}\nBuilt-From: {SHA}")), SHA)

    def test_nothing_recorded(self):
        self.assertIsNone(built_from.built_from(_version()))
        self.assertIsNone(built_from.built_from(_version("Built-From: abc")))
        self.assertIsNone(built_from.built_from(_version(annotations={"queria": {"built_from": {"commit": "abc"}}})))

    def test_annotation(self):
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "o/r"}):
            name, _, value = built_from.annotation(SHA).partition("=")
        self.assertEqual(name, "queria")
        self.assertEqual(json.loads(value), {"built_from": {"repository": "o/r", "commit": SHA}})
        self.assertNotIn(" ", value)


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

    def _check(self, changed):
        head = _version(annotations={"queria": {"built_from": {"commit": SHA}}})
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("fdl.repo.head", return_value=head), \
                mock.patch("fdl.diff.described", return_value={"name": "x"}), \
                mock.patch.object(built_from, "changed_since", return_value=changed):
            out = Path(tmp) / "previous.json"
            return built_from.description_only(object(), out), out.exists()

    def test_description_changed(self):
        self.assertEqual(self._check(["dataset.yml"]), (SHA, True))

    def test_code_changed(self):
        self.assertEqual(self._check(["dataset.yml", "main.py"]), (None, False))

    def test_nothing_changed_builds(self):
        self.assertEqual(self._check([]), (None, False))


if __name__ == "__main__":
    unittest.main()
