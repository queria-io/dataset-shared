"""scripts/sync_branch.py のテスト。

実行: uv run --no-project --with fdl-cli python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as V
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sync_branch  # noqa: E402


def decide(versions: dict, refs: dict) -> str:
    """refs は ref 名からバージョン ID、versions は ID から parents"""

    def head(_store, name):
        found = refs.get(name)
        return V(id=found, parents=versions[found]) if found else None

    def is_ancestor(_store, ancestor, descendant):
        todo = [descendant.id]
        while todo:
            current = todo.pop()
            if current == ancestor:
                return True
            todo += versions[current]
        return False

    with mock.patch.object(sync_branch.repo, "head", head), mock.patch.object(
        sync_branch.repo, "is_ancestor", is_ancestor
    ):
        return sync_branch.decide(None)


MAIN, SYNC = "refs/heads/main", "refs/heads/sync"


class DecideTest(unittest.TestCase):
    def test_no_main(self):
        self.assertEqual(decide({}, {}), "new")

    def test_no_sync_starts_it(self):
        self.assertEqual(decide({"a": ()}, {MAIN: "a"}), "start")

    def test_main_behind_sync_is_merged(self):
        self.assertEqual(decide({"a": (), "b": ("a",)}, {MAIN: "a", SYNC: "b"}), "auto")

    def test_main_that_merged_the_last_build_is_merged_again(self):
        versions = {"a": (), "h": ("a",), "b": ("a",), "m": ("h", "b"), "c": ("b",)}
        self.assertEqual(decide(versions, {MAIN: "m", SYNC: "c"}), "auto")

    def test_a_change_on_main_asks_for_a_request(self):
        versions = {"a": (), "h": ("a",), "b": ("a",)}
        self.assertEqual(decide(versions, {MAIN: "h", SYNC: "b"}), "request")

    def test_a_change_after_the_last_merge_asks_for_a_request(self):
        versions = {"a": (), "b": ("a",), "m": ("a", "b"), "h": ("m",), "c": ("b",)}
        self.assertEqual(decide(versions, {MAIN: "h", SYNC: "c"}), "request")


if __name__ == "__main__":
    unittest.main()
