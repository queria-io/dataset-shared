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


def decide(versions: dict, refs: dict, main_id: str | None = None, default: str | None = None) -> str:
    """refs は ref 名からバージョン ID、versions は ID から parents、default は HEAD が指すブランチ"""

    def head(_store, name="HEAD"):
        found = refs.get((default or MAIN) if name == "HEAD" else name)
        return V(id=found, parents=versions[found]) if found else None

    def in_history(_store, ancestor, descendant):
        current = descendant.id
        while current is not None:
            if current == ancestor:
                return True
            parents = versions[current]
            current = parents[0] if parents else None
        return False

    def read_version(_store, version_id):
        return V(id=version_id, parents=versions[version_id])

    with mock.patch.object(sync_branch.repo, "head", head), mock.patch.object(
        sync_branch.repo, "in_history", in_history
    ), mock.patch.object(sync_branch.repo, "read_version", read_version):
        return sync_branch.decide(None, main_id)


MAIN, SYNC, TRUNK = "refs/heads/main", "refs/heads/apply", "refs/heads/trunk"


class DecideTest(unittest.TestCase):
    def test_no_main(self):
        self.assertEqual(decide({}, {}), "new")

    def test_no_apply_branch_starts_it(self):
        self.assertEqual(decide({"a": ()}, {MAIN: "a"}), "start")

    def test_main_behind_apply_is_merged(self):
        self.assertEqual(decide({"a": (), "b": ("a",)}, {MAIN: "a", SYNC: "b"}), "auto")

    def test_main_that_merged_the_last_build_is_merged_again(self):
        versions = {"a": (), "h": ("a",), "b": ("a",), "m": ("h", "b"), "c": ("b",)}
        self.assertEqual(decide(versions, {MAIN: "m", SYNC: "c"}), "auto")

    def test_a_change_on_main_asks_for_a_request(self):
        versions = {"a": (), "h": ("a",), "b": ("a",)}
        self.assertEqual(decide(versions, {MAIN: "h", SYNC: "b"}), "request")

    def test_main_that_merged_an_earlier_build_is_merged_again(self):
        # a run pushed b2 to apply and stopped before merging it
        versions = {"a": (), "b": ("a",), "m": ("a", "b"), "b2": ("b",), "c": ("b2",)}
        self.assertEqual(decide(versions, {MAIN: "m", SYNC: "c"}), "auto")

    def test_a_merge_of_another_branch_asks_for_a_request(self):
        versions = {"a": (), "x": ("a",), "b": ("a",), "m": ("a", "x"), "c": ("b",)}
        self.assertEqual(decide(versions, {MAIN: "m", SYNC: "c"}), "request")

    def test_a_change_after_the_last_merge_asks_for_a_request(self):
        versions = {"a": (), "b": ("a",), "m": ("a", "b"), "h": ("m",), "c": ("b",)}
        self.assertEqual(decide(versions, {MAIN: "h", SYNC: "c"}), "request")


class CurrentTest(unittest.TestCase):
    def test_a_change_on_main_with_no_new_build_needs_nothing(self):
        versions = {"a": (), "h": ("a",)}
        self.assertEqual(decide(versions, {MAIN: "h", SYNC: "a"}), "current")


class JudgedVersionTest(unittest.TestCase):
    def test_the_cloned_version_is_judged_not_main_now(self):
        versions = {"a": (), "b": ("a",), "h": ("a",)}
        self.assertEqual(decide(versions, {MAIN: "h", SYNC: "b"}, main_id="a"), "auto")


class DefaultBranchTest(unittest.TestCase):
    def test_main_is_judged_at_the_branch_head_names(self):
        versions = {"a": (), "b": ("a",), "h": ("a",)}
        refs = {MAIN: "h", TRUNK: "a", SYNC: "b"}
        self.assertEqual(decide(versions, refs, default=TRUNK), "auto")
        self.assertEqual(decide(versions, refs), "request")

    def test_an_unborn_default_branch_is_new(self):
        self.assertEqual(decide({"a": ()}, {MAIN: "a"}, default=TRUNK), "new")

    def test_default_branch_is_the_branch_head_names(self):
        with mock.patch.object(sync_branch.refs, "resolve", return_value=(TRUNK, None)):
            self.assertEqual(sync_branch.default_branch(None), TRUNK)

    def test_head_naming_a_tag_stops(self):
        with mock.patch.object(sync_branch.refs, "resolve", return_value=("refs/tags/v1", "a")):
            with self.assertRaises(SystemExit):
                sync_branch.default_branch(None)


class TargetTest(unittest.TestCase):
    def test_data_queria_io(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                sync_branch.target("https://data.queria.io/datasets/queria/towns/"),
                ("https://queria.io", "queria/towns"),
            )

    def test_another_host_needs_queria_api_url(self):
        remote = "https://data-stg.queria.io/datasets/queria/towns/"
        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(SystemExit):
            sync_branch.target(remote)
        with mock.patch.dict("os.environ", {"QUERIA_API_URL": "https://stg.queria.io"}):
            self.assertEqual(sync_branch.target(remote), ("https://stg.queria.io", "queria/towns"))


if __name__ == "__main__":
    unittest.main()
