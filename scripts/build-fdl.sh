#!/usr/bin/env bash
# Build a dataset with fdl: check out the published version, build on it, and
# commit a new version back to Queria. Called from each dataset's scripts/build.sh.
# `queria validate` runs between the build and the commit, so a failed check
# makes no version.
#
# Usage:
#   scripts/build.sh                  # build and publish
#   scripts/build.sh python other.py  # ... with a different build command
#
# The build reads and writes the working catalog fdl checks out: FDL_WORKING is
# its path and FDL_DATA_PATH the folder new data files go to (profiles.yml
# attaches both). fdl commit freezes it into a version described by dataset.yml;
# fdl push uploads the new files and moves main; Queria publishes it. An fdl
# without push still does both in commit.
#
# Environment:
#   FDL_TOKEN    a Queria API token that may write the dataset (or `queria login`, which lets fdl ask queria for it).
#                Not used in a GitHub Actions job with `id-token: write` (see below)
#   FDL_REMOTE   the repository (default: https://data.queria.io/datasets/queria/<name>/)
#   FDL_MESSAGE  the version's message (default: build)
#   FDL_AUTHOR   who made it (default: git's user.name)
#   FDL_ASSETS   a folder with the files the asset columns point at (fdl commit --assets)
#   FDL_DIRECT   0 to keep new data files in a local folder until the commit, even when
#                Queria gives a key to write them straight to storage (default: 1)
#   QUERIA_USER_AGENT  the name the build reads data.queria.io under (default: queria-build/fdl-<version>)
set -euo pipefail

if [ "$#" -eq 0 ]; then
    set -- python main.py
fi

name="$(sed -n 's/^name: *//p' dataset.yml | head -n 1)"
if [ -z "$name" ]; then
    echo "dataset.yml has no name" >&2
    exit 2
fi
remote="${FDL_REMOTE:-https://data.queria.io/datasets/queria/$name/}"
work=".fdl/work.sqlite"

# In a GitHub Actions job that may ask for an OIDC token (`permissions: id-token:
# write`), fdl and the storage key take an hour's token from Queria whenever they
# need one (scripts/queria_token.py), so FDL_TOKEN is not used. If Queria does not
# exchange, the helper answers with QUERIA_TOKEN.
if [ -n "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]; then
    export FDL_CONFIG="${RUNNER_TEMP:-$(mktemp -d)}/fdl-config.toml"
    helper="$(command -v python3) $(cd "$(dirname "$0")" && pwd)/queria_token.py"
    printf '[credential."%s"]\nhelper = "%s"\n' "$remote" "$helper" >"$FDL_CONFIG"
    unset FDL_TOKEN
fi

# New data files go straight to the dataset's storage when Queria gives a key to
# its data/ (write and read, no delete): nothing stays on this machine, so a small
# disk can build a large dataset. The key lasts minutes; DuckDB takes a new one
# whenever storage refuses the old (scripts/storage_key.py). Without one, checkout
# keeps new files in a local folder that the commit uploads.
storage=()
# (An fdl older than checkout --storage keeps the local folder.) The help is read
# without colors: on GitHub Actions typer colors it, which splits "--storage".
if [ "${FDL_DIRECT:-1}" != 0 ] \
    && env -u GITHUB_ACTIONS -u FORCE_COLOR -u PY_COLORS uv run fdl checkout --help 2>/dev/null \
        | grep -- --storage >/dev/null \
    && keys="$(uv run python "$(dirname "$0")/storage_key.py" issue "$remote")"; then
    eval "$keys"
    trap 'uv run python "$(dirname "$0")/storage_key.py" drop || true' EXIT
    storage=(--storage "$FDL_STORAGE")
fi

# Every build starts from what is published now, so a version is built on main.
rm -rf .fdl
uv run fdl checkout "$remote" "$work" ${storage[@]+"${storage[@]}"}
export FDL_WORKING="$PWD/$work"
# Where the build writes new data files. Over HTTP checkout keeps them in a local
# folder (recorded beside the working catalog) until commit uploads them;
# otherwise they go where the catalog says.
FDL_DATA_PATH="$(uv run python - "$work" <<'PY'
import json, sqlite3, sys
from pathlib import Path

work = Path(sys.argv[1])
state = json.loads(work.with_name(work.name + ".fdl.json").read_text())
if "data" in state:
    print(Path(state["data"]).resolve().as_posix() + "/")
else:
    con = sqlite3.connect(work)
    print(con.execute("SELECT value FROM ducklake_metadata WHERE key = 'data_path'").fetchone()[0])
PY
)"
export FDL_DATA_PATH
# Reads of other public datasets go by this name (profiles.yml's custom_user_agent),
# so data.queria.io does not count the build among its users.
QUERIA_USER_AGENT="${QUERIA_USER_AGENT:-queria-build/fdl-$(uv run python -c 'import importlib.metadata as m; print(m.version("fdl-cli"))')}"
export QUERIA_USER_AGENT

# 前に手元で回した dbt の manifest を読まないように、ビルドの前に消す
rm -f target/manifest.json
uv run "$@"

# Queria's checks before the version is made: the values Queria reads (licenses,
# annotations.queria) and each table's primary_key against the data just built.
# The dataset's own environment may not hold queria, so it runs as a tool.
# @latest: uvx otherwise reuses whatever queria it cached first, and a queria
# from before fdl datasets were understood refuses every annotations key.
uvx queria@latest validate --quiet

# dbt で作ったときは、モデルの SQL と、他のデータセットのエイリアス (profiles.yml の attach) を
# 記述に書き足してから渡す。依存先は fdl commit が SQL から作る (scripts/dbt_lineage.py)。
describe=()
if [ -f target/manifest.json ]; then
    described="$(uv run python "$(dirname "$0")/dbt_lineage.py" "$work" target/manifest.json "${DBT_PROFILES_DIR:-.}/profiles.yml" .fdl/describe)"
    describe=(--dataset "$described")
fi

author="${FDL_AUTHOR:-$(git config user.name || echo build)}"
uv run fdl commit "$work" -m "${FDL_MESSAGE:-build}" --author "$author" ${describe[@]+"${describe[@]}"} ${FDL_ASSETS:+--assets "$FDL_ASSETS"}
if uv run fdl push --help >/dev/null 2>&1; then
    uv run fdl push "$work"
fi
