#!/usr/bin/env bash
# Build a dataset with fdl: clone the apply branch, build on it, push a new
# version to it, and bring it into main. Called from each dataset's scripts/build.sh.
# `queria validate` runs between the build and the commit, so a failed check
# makes no version.
#
# Usage:
#   scripts/build.sh                  # build and publish
#   scripts/build.sh python other.py  # ... with a different build command
#
# The build reads and writes the working catalog fdl checks out: FDL_WORKING is
# its path and FDL_DATA_PATH the folder new data files go to (profiles.yml
# attaches both). fdl commit freezes it into a version described by dataset.yml,
# and fdl push uploads it to refs/heads/apply.
#
# refs/heads/apply holds only versions built from this code. main is where people
# change the dataset's descriptions on Queria (its screens, fdl clone and push,
# change requests). Queria keeps people from changing rows in a dataset that has
# an apply branch: apply never takes main in, so a row changed on main would
# conflict with every later build. Errors in the data go to the publisher of the
# source data. When main holds nothing but what earlier builds brought, apply is
# merged into main here (scripts/sync_branch.py). When people changed main since,
# a change request from apply is opened instead, so nobody's change is merged unseen.
#
# Environment:
#   FDL_TOKEN    a Queria API token that may write the dataset (or `queria login`, which lets fdl ask queria for it).
#                Not used in a GitHub Actions job with `id-token: write` (see below)
#   FDL_REMOTE   the repository (default: https://data.queria.io/datasets/queria/<name>/)
#   FDL_MESSAGE  the version's message (default: build)
#   FDL_AUTHOR   who made it (default: git's user.name)
#   FDL_ASSETS   a folder with the files the asset columns point at (fdl commit --assets)
#   FDL_UNDESCRIBED  unlisted: tables with no description (*.table.yml) are not listed on Queria
#   FDL_DIRECT   1 to write new data files straight to storage when Queria gives a key
#                to them, instead of keeping them in a local folder until the commit (default: 0)
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

# fdl is in beta: build with the latest, as the Apply workflow does, so that a build on this
# machine makes and profiles the version like the Apply workflow even when uv.lock is old.
uv lock --quiet --upgrade-package fdl-cli

# In a GitHub Actions job that may ask for an OIDC token (`permissions: id-token:
# write`), fdl and the storage key take an hour's token from Queria whenever they
# need one (scripts/queria_token.py), so FDL_TOKEN is not used. If Queria does not
# exchange, the build stops.
if [ -n "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]; then
    export FDL_CONFIG="${RUNNER_TEMP:-$(mktemp -d)}/fdl-config.toml"
    helper="$(command -v python3) $(cd "$(dirname "$0")" && pwd)/queria_token.py"
    printf '[credential."%s"]\nhelper = "%s"\n' "$remote" "$helper" >"$FDL_CONFIG"
    unset FDL_TOKEN
fi

# With FDL_DIRECT=1, new data files go straight to the dataset's storage when Queria
# gives a key to its data files (the data_dir in fdl.json; write and read, no
# delete): nothing stays on this machine, so a small disk can build a large
# dataset. The key lasts minutes; DuckDB is meant to take a new one whenever
# storage refuses the old (scripts/storage_key.py). It is off by default: dbt
# runs tens of times slower against storage than against a local folder, and
# builds past an hour fail with 403 SignatureDoesNotMatch when reading files
# written with an earlier key. By default clone keeps new files in a local
# folder that the commit uploads.
storage=()
if [ "${FDL_DIRECT:-0}" = 1 ] \
    && keys="$(uv run python "$(dirname "$0")/storage_key.py" issue "$remote")"; then
    eval "$keys"
    trap 'uv run python "$(dirname "$0")/storage_key.py" drop || true' EXIT
    storage=(--storage "$FDL_STORAGE")
fi

# Where the build writes new data files. Over HTTP clone keeps them in a local
# folder (recorded beside the working catalog) until commit uploads them; with
# --storage they go to the repository's data_dir in its storage; otherwise they go
# where the catalog says. The catalog's own data_path is where readers find the
# files (https://...), which DuckDB cannot write to.
data_path() {
    uv run python - "$work" <<'PY'
import json, sqlite3, sys
from pathlib import Path

work = Path(sys.argv[1])
state = json.loads(work.with_name(work.name + ".fdl.json").read_text())
if "data" in state:
    print(Path(state["data"]).resolve().as_posix() + "/")
elif "storage" in state:
    from fdl import repo
    from fdl.stores import open_store

    print(repo.storage_data_path(state["storage"], repo.read_config(open_store(state["remote"]))))
else:
    con = sqlite3.connect(work)
    print(con.execute("SELECT value FROM ducklake_metadata WHERE key = 'data_path'").fetchone()[0])
PY
}

# Every build starts from the last build, on the apply branch. Before the first,
# the branch starts where main is.
rm -rf .fdl
# Assigned first, so that set -e stops the build when the decision cannot be
# read: cloning an apply branch that was never started would build from nothing.
decision="$(uv run python "$(dirname "$0")/sync_branch.py" "$remote")"
if [ "$decision" = start ]; then
    uv run fdl branch "$remote" apply refs/heads/main >/dev/null
fi
uv run fdl clone "$remote" "$work" --branch apply ${storage[@]+"${storage[@]}"}
FDL_DATA_PATH="$(data_path)"
# Queria's key reaches the data files alone (FDL_STORAGE_DATA). If they are
# somewhere else, the key cannot write them: build through a local folder.
if [ -n "${storage[*]+x}" ] && [ "$FDL_DATA_PATH" != "${FDL_STORAGE_DATA:-}" ]; then
    echo "storage key: not used (data files are in $FDL_DATA_PATH, the key reaches ${FDL_STORAGE_DATA:-nothing}); building through a local folder" >&2
    uv run python "$(dirname "$0")/storage_key.py" drop || true
    trap - EXIT
    unset FDL_STORAGE FDL_STORAGE_DATA FDL_STORAGE_SECRET AWS_CONFIG_FILE
    storage=()
    rm -rf .fdl
    uv run fdl clone "$remote" "$work" --branch apply
    FDL_DATA_PATH="$(data_path)"
fi
export FDL_WORKING="$PWD/$work"
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
# FDL_UNDESCRIBED=unlisted なら、記述の無いテーブルを Queria に掲載しないものとして足す。
describe=()
manifest=-
[ -f target/manifest.json ] && manifest=target/manifest.json
if [ "$manifest" != - ] || [ "${FDL_UNDESCRIBED:-}" = unlisted ]; then
    described="$(uv run python "$(dirname "$0")/dbt_lineage.py" "$work" "$manifest" "${DBT_PROFILES_DIR:-.}/profiles.yml" .fdl/describe)"
    describe=(--dataset "$described")
fi

author="${FDL_AUTHOR:-$(git config user.name || echo build)}"
uv run fdl commit "$work" -m "${FDL_MESSAGE:-build}" --author "$author" ${describe[@]+"${describe[@]}"} ${FDL_ASSETS:+--assets "$FDL_ASSETS"}
uv run fdl push "$work"

# Bring apply into main, or ask people to. main is judged at the version its
# clone got, so a push to main in between is not merged unseen; a push after the
# clone makes the push below fail, which opens a change request.
main=".fdl/main.sqlite"
decision="$(uv run python "$(dirname "$0")/sync_branch.py" "$remote")"
if [ "$decision" != new ]; then
    uv run fdl clone "$remote" "$main" >/dev/null
    base="$(uv run python -c 'import json, sys; print(json.load(open(sys.argv[1]))["base"])' "$main.fdl.json")"
    decision="$(uv run python "$(dirname "$0")/sync_branch.py" "$remote" "$base")"
fi
request=
case "$decision" in
new)
    uv run fdl branch "$remote" main refs/heads/apply
    ;;
current)
    echo "main already holds what apply holds"
    ;;
auto)
    if ! { uv run fdl merge refs/heads/apply "$main" --author "$author" -m "Merge apply" && uv run fdl push "$main"; }; then
        echo "apply could not be merged into main: opening a change request" >&2
        request=1
    fi
    ;;
request)
    echo "main has changes of its own since the last build: opening a change request" >&2
    request=1
    ;;
*)
    echo "sync_branch.py answered ${decision:-nothing}: main is left as it is" >&2
    exit 1
    ;;
esac
if [ -n "$request" ]; then
    target="$(uv run python "$(dirname "$0")/sync_branch.py" --target "$remote")"
    read -r api slug <<<"$target"
    # An empty QUERIA_TOKEN asks queria for anonymous access, so set it only when
    # there is a token; otherwise queria uses its own login.
    if token="$(uv run python "$(dirname "$0")/queria_token.py")"; then
        export QUERIA_TOKEN="$token"
    fi
    # queria prints the server's message ("apply already has open request #N"), not its code.
    out="$(QUERIA_API_URL="$api" uvx queria@latest dataset request create "$slug" --branch apply \
        --title "${FDL_MESSAGE:-build}" --body "Built from ${GITHUB_REPOSITORY:-this repository}${GITHUB_SHA:+ at $GITHUB_SHA}" 2>&1)" \
        || case "$out" in *request_exists* | *"already has open request"*) echo "the open change request from apply now shows this build" >&2 ;; *) echo "$out" >&2; exit 1 ;; esac
    echo "$out"
fi

# The description people changed on Queria is written into this checkout, so
# that the workflow's next step can bring it back as a pull request and the code
# follows main (scripts/import_description.py, scripts/import-pr.sh). Only in
# GitHub Actions; the version is already published, so a failure here only warns.
if [ -n "${GITHUB_ACTIONS:-}" ]; then
    uv run --with ruamel.yaml python "$(dirname "$0")/import_description.py" "$remote" >"${RUNNER_TEMP:?}/queria-import.txt" \
        || echo "warning: could not read the description changed on Queria" >&2
fi
