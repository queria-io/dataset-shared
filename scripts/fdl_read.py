"""A dbt-duckdb plugin that lets the build read the dataset's own published data files.

An incremental model reads what is published already (``{{ this }}``). The
working catalog that ``fdl clone`` made points at those files over HTTP
(``https://data.queria.io/datasets/...``), and a private dataset answers 404
to a read without a token. This plugin gives DuckDB the repository's token as
an HTTP secret scoped to the repository, each time dbt opens a connection and
again when each model or test starts. DuckDB does not ask for a new token on a
401, and a token from GitHub Actions lasts an hour (see queria_token.py), so a
build longer than that needs the secret replaced as it goes.

profiles.yml (templates/profiles.yml has it):

    module_paths: [shared/scripts]
    plugins:
      - module: fdl_read

A dataset's own Python that reads the published catalog (to skip what is
published already, say) calls ``fdl_read.refresh(conn)`` on its DuckDB connection
before it attaches the working catalog, and again inside a long loop, outside a
transaction: it replaces the secret when ``REFRESH_SECONDS`` have passed since the
last time on that connection. A query or transaction keeps the secret it started
with, so one that runs longer than the token still fails.

Without FDL_WORKING and FDL_REMOTE (a PR check builds into a local file) or
without a token, it does nothing.
"""

from __future__ import annotations

import os
import time
from typing import Callable

from dbt.adapters.duckdb.plugins import BasePlugin


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def setup(remote: str | None, token_for: Callable[[str], str | None]) -> list[str]:
    """The statements that let DuckDB read the repository ``remote``."""
    if not remote or not remote.startswith("https://"):
        return []
    token = token_for(remote)
    if not token:
        return []
    return [f"CREATE OR REPLACE SECRET fdl_read (TYPE http, BEARER_TOKEN {_quoted(token)}, SCOPE {_quoted(remote)})"]


def _token(remote: str) -> str | None:
    from fdl.stores import open_store

    return getattr(open_store(remote), "token", None)


def statements() -> list[str]:
    """The statements for this build's working catalog (FDL_WORKING), cloned from
    FDL_REMOTE (build-fdl.sh sets both)."""
    return setup(os.environ.get("FDL_REMOTE") if os.environ.get("FDL_WORKING") else None, _token)


#: How often refresh() replaces the secret. queria_token.py hands out a token with
#: more than 45 minutes left, so a connection refreshed this often never reads with
#: an expired one.
REFRESH_SECONDS = 10 * 60
#: When refresh() last set the secret, by connection.
_set_at: dict[int, float] = {}


def refresh(conn) -> None:
    """Set the secret on ``conn`` the first time, and again once REFRESH_SECONDS have
    passed. Call it outside a transaction: one keeps the secret it began with."""
    now = time.monotonic()
    if now - _set_at.get(id(conn), -REFRESH_SECONDS) < REFRESH_SECONDS:
        return
    for statement in statements():
        conn.execute(statement)
    _set_at[id(conn)] = now


class Plugin(BasePlugin):
    def configure_connection(self, conn) -> None:
        for statement in statements():
            conn.execute(statement)

    def configure_cursor(self, cursor) -> None:
        # dbt takes a cursor for each model and test. The secret belongs to the
        # database, so replacing it here gives every later read the fresh token.
        for statement in statements():
            cursor.execute(statement)
