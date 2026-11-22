"""scripts/declare_profiles.py のテスト。

実行: python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import declare_profiles  # noqa: E402


class CommandsTest(unittest.TestCase):
    def test_only_what_differs(self):
        have = {"_queria.tables", "m.old"}
        want = {"_queria.tables", "m.hidden"}
        self.assertEqual(
            declare_profiles.commands(have, want),
            [["m.hidden", "false"], ["m.old", "true"]],
        )

    def test_nothing_to_do(self):
        self.assertEqual(declare_profiles.commands({"a.b"}, {"a.b"}), [])


if __name__ == "__main__":
    unittest.main()
