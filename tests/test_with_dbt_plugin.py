"""scripts/with_dbt_plugin.py のテスト。

実行: uv run --no-project --with pyyaml python -m unittest discover -s tests
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import with_dbt_plugin  # noqa: E402

PLUGIN = {"module": "queria.dbt_plugin"}


def _profiles(**target) -> dict:
    return {"calendar": {"target": "default", "outputs": {"default": {"type": "duckdb", **target}}}}


class WithPluginTest(unittest.TestCase):
    def test_adds_the_plugin_to_a_profile_without_plugins(self):
        out = with_dbt_plugin.with_plugin(_profiles(threads=1))
        self.assertEqual(out["calendar"]["outputs"]["default"]["plugins"], [PLUGIN])

    def test_keeps_plugins_the_profile_already_has(self):
        other = {"module": "excel"}
        out = with_dbt_plugin.with_plugin(_profiles(plugins=[other]))
        self.assertEqual(out["calendar"]["outputs"]["default"]["plugins"], [other, PLUGIN])

    def test_does_not_add_it_twice(self):
        out = with_dbt_plugin.with_plugin(_profiles(plugins=[dict(PLUGIN)]))
        self.assertEqual(out["calendar"]["outputs"]["default"]["plugins"], [PLUGIN])

    def test_leaves_a_target_with_more_than_one_thread_alone(self):
        out = with_dbt_plugin.with_plugin(_profiles(threads=4))
        self.assertNotIn("plugins", out["calendar"]["outputs"]["default"])

    def test_leaves_other_adapters_and_the_config_block_alone(self):
        profiles = {
            "config": {"send_anonymous_usage_stats": False},
            "warehouse": {"outputs": {"prod": {"type": "postgres"}}},
        }
        out = with_dbt_plugin.with_plugin(profiles)
        self.assertNotIn("plugins", out["warehouse"]["outputs"]["prod"])
        self.assertEqual(out["config"], {"send_anonymous_usage_stats": False})


class PluginAvailableTest(unittest.TestCase):
    def test_false_when_queria_is_not_installed(self):
        with mock.patch.object(with_dbt_plugin.importlib.util, "find_spec", side_effect=ModuleNotFoundError):
            self.assertFalse(with_dbt_plugin.plugin_available())

    def test_false_when_queria_has_no_plugin(self):
        with mock.patch.object(with_dbt_plugin.importlib.util, "find_spec", return_value=None):
            self.assertFalse(with_dbt_plugin.plugin_available())


class MainTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.source = self.dir / "profiles.yml"
        # Jinja の式とクォートした値がそのまま写ること
        self.source.write_text(
            "calendar:\n"
            "  outputs:\n"
            "    default:\n"
            "      type: duckdb\n"
            "      threads: 1\n"
            "      secrets:\n"
            "        - endpoint: \"{{ env_var('QUERIA_S3_ENDPOINT_HOST', '') }}\"\n"
            "      attach:\n"
            "        - options:\n"
            "            META_TYPE: \"'sqlite'\"\n"
            "            DATA_INLINING_ROW_LIMIT: 0\n"
        )
        self.destination = self.dir / "copy" / "profiles.yml"
        self.destination.parent.mkdir()

    def test_writes_a_copy_with_the_plugin(self):
        with mock.patch.object(with_dbt_plugin, "plugin_available", return_value=True):
            code = with_dbt_plugin.main(str(self.source), str(self.destination))
        self.assertEqual(code, 0)
        target = yaml.safe_load(self.destination.read_text())["calendar"]["outputs"]["default"]
        self.assertEqual(target["plugins"], [PLUGIN])
        self.assertEqual(target["secrets"][0]["endpoint"], "{{ env_var('QUERIA_S3_ENDPOINT_HOST', '') }}")
        self.assertEqual(target["attach"][0]["options"], {"META_TYPE": "'sqlite'", "DATA_INLINING_ROW_LIMIT": 0})

    def test_the_repository_profile_is_not_touched(self):
        before = self.source.read_text()
        with mock.patch.object(with_dbt_plugin, "plugin_available", return_value=True):
            with_dbt_plugin.main(str(self.source), str(self.destination))
        self.assertEqual(self.source.read_text(), before)

    def test_writes_nothing_when_queria_has_no_plugin(self):
        with mock.patch.object(with_dbt_plugin, "plugin_available", return_value=False):
            code = with_dbt_plugin.main(str(self.source), str(self.destination))
        self.assertEqual(code, with_dbt_plugin.NO_PLUGIN)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
