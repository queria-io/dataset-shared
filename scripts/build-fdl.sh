#!/usr/bin/env bash
# Build a dataset with fdl: clone the apply branch, build on it, push a new
# version to it, and bring it into main. Called from each dataset's scripts/build.sh.
# `queria metadata pack` checks the description and writes what fdl commit takes
# between the build and the commit, so a failed check makes no version.
#
# Usage:
#   scripts/build.sh                  # build and publish
#   scripts/build.sh python other.py  # ... with a different build command
#
# The build reads and writes the working catalog fdl checks out: FDL_WORKING is
# its path and FDL_DATA_PATH the folder new data files go to (profiles.yml
# attaches both). fdl commit freezes it into a version described by the folder
# queria metadata pack writes from queria.yml and the table Markdown files, and
# fdl push uploads it to refs/heads/apply.
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
#   FDL_MESSAGE  the version's message (default: build, or "Update the description" when the build is skipped)
#   FDL_AUTHOR   who made it (default: git's user.name)
#   FDL_ASSETS   a folder with the files the asset columns point at (fdl commit --assets)
#   FDL_UNDESCRIBED  unlisted: tables with no description (no table Markdown file) are not listed on Queria
#   FDL_DIRECT   1 to write new data files straight to storage when Queria gives a key
#                to them, instead of keeping them in a local folder until the commit (default: 0)
#   QUERIA_USER_AGENT  the name the build reads data.queria.io under (default: queria-build/fdl-<version>)
set -euo pipefail

if [ "$#" -eq 0 ]; then
    set -- python main.py
fi

# queria.yml, or dataset.yml in a repository that still keeps the older files.
described_by=queria.yml
[ -f "$described_by" ] || described_by=dataset.yml
name="$(sed -n 's/^name: *//p' "$described_by" 2>/dev/null | head -n 1 || true)"
if [ -z "$name" ]; then
    echo "$described_by has no name" >&2
    exit 2
fi
remote="${FDL_REMOTE:-https://data.queria.io/datasets/queria/$name/}"
work=".fdl/work.sqlite"

# The models follow the layers and the idempotency rules (README.md), or nothing is built.
python3 "$(dirname "$0")/check_models.py" models

# fdl clone writes the version's description (dataset.json and its files, such as
# README.md) into the folder it runs in, unless dataset.json or dataset.yml is
# there, and refuses when one of those files already is. The description here is
# queria.yml and the files beside it, so clone from an empty folder: only the
# working metadata is kept.
clone() {
    local here="$PWD" empty status=0
    empty="$(mktemp -d)"
    (cd "$empty" && uv run --project "$here" fdl clone "$1" "$here/$2" "${@:3}") || status=$?
    rm -rf "$empty"
    return "$status"
}

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
# runs tens of times slower against storage than against a local folder. DuckDB
# before 1.5.5 cannot take a new key for the secret, so storage_key.py gives no key
# there and the build goes through a local folder. By default clone keeps new files
# in a local folder that the commit uploads.
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
clone "$remote" "$work" --branch apply ${storage[@]+"${storage[@]}"}
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
    clone "$remote" "$work" --branch apply
    FDL_DATA_PATH="$(data_path)"
fi
export FDL_WORKING="$PWD/$work"
export FDL_DATA_PATH
# Reads of other public datasets go by this name (profiles.yml's custom_user_agent),
# so data.queria.io does not count the build among its users.
QUERIA_USER_AGENT="${QUERIA_USER_AGENT:-queria-build/fdl-$(uv run python -c 'import importlib.metadata as m; print(m.version("fdl-cli"))')}"
export QUERIA_USER_AGENT

# A push to main that changed only the description and .github/ since apply's last
# build makes a version of the new description on the data apply holds, without
# running the dataset's code: the data would come out the same (scripts/built_from.py).
# With the description unchanged too, fdl commit makes no version. Each
# version records the commit whose code made its data as a trailer of its message.
# The check also writes apply's dataset.json, which the SQL and catalogs are taken from.
built=
if [ "${GITHUB_EVENT_NAME:-}" = push ]; then
    built="$(uv run python "$(dirname "$0")/built_from.py" check "$remote" .fdl/previous.json)" || built=
    # Anything but a commit (a stray line on stdout) builds.
    [[ "$built" =~ ^[0-9a-f]{40}$ ]] || built=
fi

# 前に手元で回した dbt の manifest を読まないように、ビルドの前に消す
rm -f target/manifest.json
if [ -n "$built" ]; then
    echo "only the description or .github/ changed since the build from $built: not running the build"
    trailer="Built-From: $built"
else
    uv run "$@"
    # Each build starts from the last version: drop what no dbt model makes any more
    # (a model removed, renamed or made ephemeral), or it stays in every later version.
    if [ -f target/manifest.json ]; then
        uv run python "$(dirname "$0")/drop_stale.py" target/manifest.json "$name"
    fi
    trailer="$(uv run python "$(dirname "$0")/built_from.py" trailer)"
fi

# Queria's checks before the version is made (licenses, themes, join keys and each
# table's primary_key against the data just built), then the folder fdl commit takes:
# dataset.json, README.md, the tables' guides and the documents. From dbt's
# target/manifest.json it adds each model's SQL and dependencies, and from
# profiles.yml the other datasets it attaches (catalogs). When the build was
# skipped there is no manifest: they come from apply's dataset.json (--base).
# The dataset's own environment may not hold queria, so it runs as a tool.
# @latest: uvx otherwise reuses whatever queria it cached first.
pack=(--quiet --profiles "${DBT_PROFILES_DIR:-.}/profiles.yml" --out .fdl/pack)
[ -n "$built" ] && pack+=(--base .fdl/previous.json)
[ "${FDL_UNDESCRIBED:-}" = unlisted ] && pack+=(--unlisted)
uvx queria@latest metadata pack "${pack[@]}"

author="${FDL_AUTHOR:-$(git config user.name || echo build)}"
message="${FDL_MESSAGE:-$([ -n "$built" ] && echo "Update the description" || echo build)}"
uv run fdl commit "$work" -m "$message${trailer:+$'\n\n'$trailer}" --author "$author" --dataset .fdl/pack/dataset.json ${FDL_ASSETS:+--assets "$FDL_ASSETS"}
uv run fdl push "$work"

# Bring apply into main, or ask people to. main is judged at the version its
# clone got, so a push to main in between is not merged unseen; a push after the
# clone makes the push below fail, which opens a change request.
main=".fdl/main.sqlite"
decision="$(uv run python "$(dirname "$0")/sync_branch.py" "$remote")"
if [ "$decision" != new ]; then
    clone "$remote" "$main" >/dev/null
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
        --title "$message" --body "Built from ${GITHUB_REPOSITORY:-this repository}${GITHUB_SHA:+ at $GITHUB_SHA}" 2>&1)" \
        || case "$out" in *request_exists* | *"already has open request"*) echo "the open change request from apply now shows this build" >&2 ;; *) echo "$out" >&2; exit 1 ;; esac
    echo "$out"
fi

# The description people changed on Queria is written into this checkout, so
# that the workflow's next step can bring it back as a pull request and the code
# follows main (scripts/import_description.py, queria metadata unpack,
# scripts/import-pr.sh). Only in GitHub Actions; the version is already published,
# so a failure here only warns.
if [ -n "${GITHUB_ACTIONS:-}" ]; then
    versions="${RUNNER_TEMP:?}/queria-versions"
    if found="$(uv run python "$(dirname "$0")/import_description.py" "$remote" "$versions")"; then
        if [ -n "$found" ]; then
            uvx queria@latest metadata unpack "$versions/main" --base "$versions/base" >"$RUNNER_TEMP/queria-import.txt" \
                || echo "warning: could not write the description changed on Queria" >&2
        fi
    else
        echo "warning: could not read the description changed on Queria" >&2
    fi
fi
