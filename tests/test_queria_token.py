"""scripts/queria_token.py のテスト。

実行: uv run --no-project --with duckdb python -m unittest discover -s tests
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import queria_token  # noqa: E402

LATER = "2999-01-01T00:00:00Z"
OIDC = {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://gh.invalid/token?x=1", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "b"}


class TokenTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.kept = Path(self.temp.name) / "queria-token.json"

    def env(self, **extra):
        return mock.patch.dict("os.environ", {"RUNNER_TEMP": self.temp.name, **extra}, clear=True)

    def test_exchanges_the_oidc_token_and_keeps_the_answer(self):
        with self.env(**OIDC, QUERIA_TOKEN="old"), \
                mock.patch.object(queria_token, "_github_oidc_token", return_value="jwt"), \
                mock.patch.object(queria_token, "_post", return_value={"token": "qk_new", "expires_at": LATER}) as post:
            self.assertEqual(queria_token.token(), "qk_new")
            self.assertEqual(queria_token.token(), "qk_new")
        post.assert_called_once_with("https://queria.io/api/oidc/github/token", {"token": "jwt"})
        self.assertEqual(json.loads(self.kept.read_text())["token"], "qk_new")
        self.assertEqual(self.kept.stat().st_mode & 0o777, 0o600)

    def test_asks_again_when_the_kept_token_is_about_to_expire(self):
        self.kept.write_text(json.dumps({"token": "qk_old", "expires_at": "2000-01-01T00:00:00Z"}))
        with self.env(**OIDC), \
                mock.patch.object(queria_token, "_github_oidc_token", return_value="jwt"), \
                mock.patch.object(queria_token, "_post", return_value={"token": "qk_new", "expires_at": LATER}):
            self.assertEqual(queria_token.token(), "qk_new")

    def test_stops_when_queria_refuses_in_github_actions(self):
        refused = urllib.error.HTTPError("u", 403, "not registered", {}, None)
        with self.env(**OIDC, FDL_TOKEN="mine", QUERIA_TOKEN="old"), \
                mock.patch.object(queria_token, "_github_oidc_token", return_value="jwt"), \
                mock.patch.object(queria_token, "_post", side_effect=refused), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(queria_token.token())
            self.assertEqual(queria_token.main([]), 1)
        self.assertFalse(self.kept.exists())

    def test_uses_fdl_token_outside_github_actions(self):
        with self.env(FDL_TOKEN="mine", QUERIA_TOKEN="old"), \
                mock.patch.object(queria_token, "_post") as post:
            self.assertEqual(queria_token.token(), "mine")
        post.assert_not_called()

    def test_answers_as_a_credential_helper(self):
        out = io.StringIO()
        with self.env(QUERIA_TOKEN="old"), contextlib.redirect_stdout(out):
            self.assertEqual(queria_token.main(["get"]), 0)
        self.assertEqual(out.getvalue(), "password=old\n")

    def test_exits_1_without_any_token(self):
        with self.env(), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(queria_token.main([]), 1)


if __name__ == "__main__":
    unittest.main()
