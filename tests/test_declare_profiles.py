"""scripts/declare_profiles.py のテスト。

実行: python -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import declare_profiles  # noqa: E402


class CommandsTest(unittest.TestCase):
    def test_only_what_differs(self):
        have = {"_queria.tables", "m.old"}
        want = {"_queria.tables", "m.hidden"}
        self.assertEqual(
            declare_profiles.commands(have, want, {"_queria.tables", "m.hidden", "m.old"}),
            [["m.hidden", "false"], ["m.old", "true"]],
        )

    def test_leaves_what_it_did_not_decide(self):
        self.assertEqual(declare_profiles.commands({"m.large"}, set(), {"m.towns"}), [])

    def test_nothing_to_do(self):
        self.assertEqual(declare_profiles.commands({"a.b"}, {"a.b"}, {"a.b"}), [])


class ReadSecretTest(unittest.TestCase):
    def _secret(self, state, token="t'k"):
        with tempfile.TemporaryDirectory() as tmp:
            working = Path(tmp) / "work.sqlite"
            if state is not None:
                Path(f"{working}.fdl.json").write_text(json.dumps(state))
            with mock.patch("fdl.stores.open_store", return_value=mock.Mock(token=token)):
                return declare_profiles._read_secret(str(working))

    def test_token_for_the_remote(self):
        remote = "https://data.queria.io/datasets/queria/x/"
        self.assertEqual(
            self._secret({"remote": remote}),
            [f"CREATE OR REPLACE SECRET fdl_read (TYPE http, BEARER_TOKEN 't''k', SCOPE '{remote}')"],
        )

    def test_no_token(self):
        self.assertEqual(self._secret({"remote": "https://data.queria.io/datasets/queria/x/"}, token=None), [])

    def test_not_over_https(self):
        self.assertEqual(self._secret({"remote": "/tmp/repo"}), [])

    def test_not_a_clone(self):
        self.assertEqual(self._secret(None), [])


if __name__ == "__main__":
    unittest.main()
