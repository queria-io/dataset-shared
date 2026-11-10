"""Get a key that writes a dataset's data files straight to its storage.

Usage:
  python storage_key.py issue <remote>   print `export` lines for the shell, and
                                         save the key as a DuckDB secret for dbt
  python storage_key.py drop             delete that secret

`issue` asks Queria for a key to the dataset's `data/` only (it reads and writes,
and cannot delete), valid for the build (FDL_KEY_TTL seconds, 12 hours at most).
With it, `fdl checkout --storage` has the build write new data files straight to
the repository, so nothing is kept on this machine until the commit.

The key is saved as a persistent DuckDB secret (`fdl_build`) so that dbt-duckdb
finds it without a change to profiles.yml. `drop` deletes it; build-fdl.sh does
so when the build ends, however it ends.

Exit codes of `issue`: 0 printed the key; 1 no key (no FDL_TOKEN, a remote
outside Queria, or Queria refused): build through a local folder as before.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import urllib.error
import urllib.request

SECRET = "fdl_build"
#: The longest key Queria gives for data/ (R2_DATA_WRITE_MAX_TTL_SECONDS).
MAX_TTL = 12 * 60 * 60
#: Where each data host's API is.
API = {"data.queria.io": "https://queria.io"}


def api_url(remote: str) -> str | None:
    """The credentials endpoint for ``remote``, or None outside Queria."""
    match = re.fullmatch(r"https://([^/]+)/datasets/([^/]+)/([^/]+)/?", remote)
    if not match:
        return None
    host, handle, name = match.groups()
    base = os.environ.get("QUERIA_API_URL") or API.get(host)
    if not base:
        return None
    return f"{base.rstrip('/')}/api/datasets/{handle}/{name}/credentials"


def request(url: str, token: str, ttl: int) -> dict:
    body = json.dumps({"scope": "data", "ttl_seconds": ttl}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "queria-build/storage-key",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def exports(answer: dict) -> dict[str, str]:
    storage = answer["QueriaStorage"]
    if storage.get("mode") != "s3":
        raise ValueError(f"unknown upload mode {storage.get('mode')!r}")
    # A Queria that does not know scope "data" answers with its short key for the
    # whole dataset, which a long build would outlive.
    if storage.get("prefix") != f"{storage['base_prefix']}data/":
        raise ValueError("Queria gave no key for data/ alone")
    return {
        "AWS_ACCESS_KEY_ID": answer["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": answer["SecretAccessKey"],
        "AWS_SESSION_TOKEN": answer["SessionToken"],
        "AWS_ENDPOINT_URL": storage["endpoint"],
        "AWS_REGION": storage.get("region") or "auto",
        "FDL_STORAGE": f"s3://{storage['bucket']}/{storage['base_prefix']}",
    }


def secret_sql(values: dict[str, str]) -> str:
    def quoted(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    endpoint = re.sub(r"^https?://", "", values["AWS_ENDPOINT_URL"]).rstrip("/")
    return (
        f"CREATE OR REPLACE PERSISTENT SECRET {SECRET} (TYPE s3, "
        f"KEY_ID {quoted(values['AWS_ACCESS_KEY_ID'])}, "
        f"SECRET {quoted(values['AWS_SECRET_ACCESS_KEY'])}, "
        f"SESSION_TOKEN {quoted(values['AWS_SESSION_TOKEN'])}, "
        f"ENDPOINT {quoted(endpoint)}, URL_STYLE 'path', "
        f"REGION {quoted(values['AWS_REGION'])}, "
        f"SCOPE {quoted(values['FDL_STORAGE'])})"
    )


def issue(remote: str) -> int:
    token = os.environ.get("FDL_TOKEN")
    url = api_url(remote)
    if not token or not url:
        return 1
    ttl = min(int(os.environ.get("FDL_KEY_TTL", MAX_TTL)), MAX_TTL)
    try:
        values = exports(request(url, token, ttl))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(f"storage key: none ({exc}); building through a local folder", file=sys.stderr)
        return 1
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(secret_sql(values))
    con.close()
    github = os.environ.get("GITHUB_ACTIONS") == "true"
    for key, value in values.items():
        if github and key.startswith("AWS_") and key not in ("AWS_ENDPOINT_URL", "AWS_REGION"):
            # Hidden from the job's log before anything can print them.
            print(f"echo ::add-mask::{shlex.quote(value)}")
        print(f"export {key}={shlex.quote(value)}")
    return 0


def drop() -> int:
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"DROP PERSISTENT SECRET IF EXISTS {SECRET}")
    con.close()
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["issue"] and len(argv) == 2:
        return issue(argv[1])
    if argv == ["drop"]:
        return drop()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
