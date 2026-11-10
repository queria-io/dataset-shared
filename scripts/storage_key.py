"""Let a build write a dataset's data files straight to its storage.

Usage:
  python storage_key.py issue <remote>               print `export` lines for the shell,
                                                     and save a DuckDB secret for the build
  python storage_key.py credential-process <remote> [<first>]
                                                     print one key, the way AWS's
                                                     credential_process wants it (the one
                                                     saved in <first> once, if given)
  python storage_key.py drop                         delete that secret and its AWS config

Queria gives a key to the dataset's `data/` only (it reads and writes, and cannot
delete), and like any of its keys it lasts minutes. With it, `fdl checkout
--storage` has the build write new data files straight to the repository, so
nothing is kept on this machine until the commit.

The build never holds the key itself. `issue` writes an AWS config whose profile
runs `credential-process`, and saves a persistent DuckDB secret that gets its key
through that profile (`PROVIDER credential_chain`, `REFRESH auto`). When storage
refuses an expired key, DuckDB runs the process again and retries, so a build of
any length keeps writing, in dbt and in `fdl commit` alike, without a change to
profiles.yml.

Each build names its own secret and config (printed as FDL_STORAGE_SECRET and
AWS_CONFIG_FILE), so builds running side by side on one machine do not replace or
delete each other's. `drop` deletes them; build-fdl.sh does so when the build
ends, however it ends. Every secret names the same profile, so whichever secret of
a dataset DuckDB picks (another build's, or one a killed build left behind), it
takes its next key through this build's config.

Exit codes of `issue`: 0 printed the exports; 1 no key (no FDL_TOKEN, a remote
outside Queria, or Queria refused): build through a local folder as before.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

#: The prefix of the persistent secret and folder each build makes.
SECRET = "fdl_build"
#: The AWS profile in each build's config that runs `credential-process`.
PROFILE = "fdl_build"
#: The longest wait for a key when Queria answers 429 with Retry-After (a key's life).
MAX_WAIT = 15 * 60
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


def request(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps({"scope": "data"}).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "queria-build/storage-key",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def storage_of(answer: dict) -> dict[str, str]:
    """Where the key reaches, checked to be the dataset's data/ alone."""
    storage = answer["QueriaStorage"]
    if storage.get("mode") != "s3":
        raise ValueError(f"unknown upload mode {storage.get('mode')!r}")
    # A Queria that does not know scope "data" answers with its key for the
    # whole dataset, which can delete.
    if storage.get("prefix") != f"{storage['base_prefix']}data/":
        raise ValueError("Queria gave no key for data/ alone")
    return {
        "endpoint": storage["endpoint"],
        "region": storage.get("region") or "auto",
        "root": f"s3://{storage['bucket']}/{storage['base_prefix']}",
        # The key reaches data/ alone, so the secret is used there alone: a 403
        # elsewhere would only make DuckDB take a new key that is refused again.
        "data": f"s3://{storage['bucket']}/{storage['prefix']}",
    }


def key_of(answer: dict) -> dict:
    """The fields AWS's credential_process reads."""
    storage_of(answer)
    keys = ("Version", "AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration")
    return {key: answer[key] for key in keys}


def secret_sql(name: str, storage: dict[str, str]) -> str:
    def quoted(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    endpoint = storage["endpoint"]
    use_ssl = "false" if endpoint.startswith("http://") else "true"
    host = re.sub(r"^https?://", "", endpoint).rstrip("/")
    return (
        f"CREATE OR REPLACE PERSISTENT SECRET {name} (TYPE s3, "
        f"PROVIDER credential_chain, CHAIN 'process', PROFILE {quoted(PROFILE)}, REFRESH auto, "
        f"ENDPOINT {quoted(host)}, URL_STYLE 'path', USE_SSL {use_ssl}, "
        f"REGION {quoted(storage['region'])}, "
        f"SCOPE {quoted(storage['data'])})"
    )


def aws_config(remote: str, first: Path) -> str:
    # The process runs in later build steps, after an ephemeral environment that ran
    # `issue` may be gone; it needs nothing outside the standard library, so the
    # base interpreter does.
    python = getattr(sys, "_base_executable", "") or sys.executable
    script = str(Path(__file__).resolve())
    command = shlex.join([python, script, "credential-process", remote, str(first)])
    return f"[profile {PROFILE}]\ncredential_process = {command}\n"


def _ask(remote: str) -> dict | None:
    token = os.environ.get("FDL_TOKEN")
    url = api_url(remote)
    if not token or not url:
        return None
    return request(url, token)


def _wait(exc: urllib.error.HTTPError) -> int | None:
    """Seconds until Queria has room for another key, from a 429's Retry-After."""
    if exc.code != 429:
        return None
    try:
        seconds = int(exc.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return seconds if 0 < seconds <= MAX_WAIT else None


def _take_first(first: Path | None) -> str | None:
    """The key `issue` saved for the secret's creation, once."""
    if first is None:
        return None
    try:
        key = first.read_text()
    except FileNotFoundError:
        return None
    first.unlink(missing_ok=True)
    return key


def credential_process(remote: str, first: Path | None = None) -> int:
    key = _take_first(first)
    if key:
        print(key)
        return 0
    try:
        try:
            answer = _ask(remote)
        except urllib.error.HTTPError as exc:
            # Queria limits how many keys live at once and says when one expires.
            # Failing here would stop a build mid-way, so wait for that once.
            wait = _wait(exc)
            if wait is None:
                raise
            print(f"storage key: waiting {wait}s for Queria to give another key", file=sys.stderr)
            time.sleep(wait)
            answer = _ask(remote)
        if answer is None:
            print("storage key: needs FDL_TOKEN and a Queria remote", file=sys.stderr)
            return 1
        print(json.dumps(key_of(answer)))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(f"storage key: {exc}", file=sys.stderr)
        return 1
    return 0


def issue(remote: str) -> int:
    try:
        answer = _ask(remote)
        if answer is None:
            return 1
        storage = storage_of(answer)
        key = json.dumps(key_of(answer))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(f"storage key: none ({exc}); building through a local folder", file=sys.stderr)
        return 1
    try:
        import duckdb
    except ImportError as exc:
        print(f"storage key: none ({exc}); building through a local folder", file=sys.stderr)
        return 1

    name = f"{SECRET}_{secrets.token_hex(8)}"
    folder = Path(tempfile.mkdtemp(prefix=f"{name}."))
    config = folder / "aws.config"
    # Creating the secret runs the process once; it hands over this key rather
    # than asking Queria for a second one, which would hold another of the
    # account's live keys for nothing.
    first = folder / "first.json"
    first.write_text(key)
    config.write_text(aws_config(remote, first))
    os.environ["AWS_CONFIG_FILE"] = str(config)
    try:
        con = duckdb.connect()
        try:
            con.execute("INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws;")
            con.execute(secret_sql(name, storage))
        finally:
            con.close()
    except duckdb.Error as exc:
        shutil.rmtree(folder, ignore_errors=True)
        print(f"storage key: no secret ({exc}); building through a local folder", file=sys.stderr)
        return 1
    first.unlink(missing_ok=True)
    for key, value in {
        "AWS_CONFIG_FILE": str(config),
        "FDL_STORAGE": storage["root"],
        "FDL_STORAGE_SECRET": name,
    }.items():
        print(f"export {key}={shlex.quote(value)}")
    return 0


def drop() -> int:
    name = os.environ.get("FDL_STORAGE_SECRET", "")
    if not re.fullmatch(rf"{SECRET}_[0-9a-f]+", name):
        return 0
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"DROP PERSISTENT SECRET IF EXISTS {name}")
    con.close()
    config = Path(os.environ.get("AWS_CONFIG_FILE", ""))
    if config.parent.name.startswith(f"{name}."):
        shutil.rmtree(config.parent, ignore_errors=True)
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["issue"] and len(argv) == 2:
        return issue(argv[1])
    if argv[:1] == ["credential-process"] and len(argv) in (2, 3):
        return credential_process(argv[1], Path(argv[2]) if len(argv) == 3 else None)
    if argv == ["drop"]:
        return drop()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
