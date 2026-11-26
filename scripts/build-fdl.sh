#!/usr/bin/env bash
# Build a dataset with fdl: clone the apply branch, build on it, push a new
# version to it, and bring it into main. Called from each dataset's scripts/build.sh.
# `tabledoc load` checks the description and writes it into the catalog's _tabledoc schema
# between the build and the commit, so a failed check makes no version.
#
# Usage:
#   scripts/build.sh                  # build and publish
#   scripts/build.sh python other.py  # ... with a different build command
#
# The build reads and writes the working catalog fdl checks out: FDL_WORKING is
# its path and FDL_DATA_PATH the folder new data files go to (profiles.yml
# attaches both). tabledoc load writes the description from tabledoc.yml and the
# files beside it into its _tabledoc schema, fdl commit freezes it into a
# version, and fdl push uploads it to refs/heads/apply. Only fdl writes under .fdl/.
#
# refs/heads/apply holds only versions built from this code, and this code is where
# the description is changed. apply never takes main in, so a change made on main
# would conflict with every later build; errors in the data go to the publisher of
# the source data. When main holds nothing but what earlier builds brought, apply is
# merged into main here (scripts/sync_branch.py). When someone changed main since,
# a change request from apply is opened instead, so nobody's change is merged unseen.
#
# Environment:
#   FDL_TOKEN    a Queria API token that may write the dataset (or `queria login`, which lets fdl ask queria for it).
#                Not used in a GitHub Actions job with `id-token: write` (see below)
#   FDL_REMOTE   the repository (default: https://data.queria.io/datasets/queria/<name>/)
#   FDL_MESSAGE  the version's message (default: build, or "Update the description" when the build is skipped)
#   FDL_AUTHOR   who made it (default: git's user.name)
#   FDL_ASSETS   a folder with the files the asset columns point at (linked into the folder fdl commit --assets takes)
#   FDL_UNDESCRIBED  hidden: tables with no description (no table file) are hidden from lists and search on Queria
#   FDL_DIRECT   1 to write new data files straight to storage when Queria gives a key
#                to them, instead of keeping them in a local folder until the commit (default: 0)
#   QUERIA_USER_AGENT  the name the build reads data.queria.io under (default: queria-build/fdl-<version>)
set -euo pipefail

if [ "$#" -eq 0 ]; then
    set -- python main.py
fi

# tabledoc.yml: the file tabledoc load reads the dataset from. A repository without it
# (one that still keeps queria.yml or dataset.yml) stops here instead of after the build.
described_by=tabledoc.yml
if [ ! -f "$described_by" ]; then
    echo "no tabledoc.yml: move the description in queria.yml or dataset.yml to tabledoc.yml" >&2
    exit 2
fi
name="$(sed -n 's/^name: *//p' "$described_by" 2>/dev/null | head -n 1 || true)"
if [ -z "$name" ]; then
    echo "$described_by has no name" >&2
    exit 2
fi
remote="${FDL_REMOTE:-https://data.queria.io/datasets/queria/$name/}"
work=".fdl/work.sqlite"

# The models follow the layers and the idempotency rules (README.md), or nothing is built.
python3 "$(dirname "$0")/check_models.py" models

# The fdl-cli and tabledoc every dataset builds with. A new release reaches the
# datasets only when these are raised here, so a build on this machine and the Sync
# workflow make and profile a version with the same ones, whatever uv.lock holds.
FDL_CLI_VERSION=0.41.0
TABLEDOC_VERSION=0.9.0
uv lock --quiet --upgrade-package "fdl-cli==$FDL_CLI_VERSION"

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
# The folder of the files fdl commit --assets uploads, made below.
assets=
# However the build ends: give the storage key back, and remove the folder of assets.
cleanup() {
    if [ -n "${storage[*]+x}" ]; then
        uv run python "$(dirname "$0")/storage_key.py" drop || true
    fi
    if [ -n "$assets" ]; then
        rm -rf "$assets"
    fi
}
trap cleanup EXIT
if [ "${FDL_DIRECT:-0}" = 1 ] \
    && keys="$(uv run python "$(dirname "$0")/storage_key.py" issue "$remote")"; then
    eval "$keys"
    storage=(--storage "$FDL_STORAGE")
fi

# What fdl status --json says of a working catalog: $2 of $1. fdl-cli before 0.35
# does not give remote, base and data_path there, so the build stops with that said.
fdl_status() {
    uv run fdl status "$1" --json | uv run python -c '
import json, sys

status = json.load(sys.stdin)
if sys.argv[1] not in status:
    sys.exit(f"fdl status --json gives no {sys.argv[1]}: the build needs fdl-cli 0.35 or later")
print(status[sys.argv[1]] or "")
' "$2"
}

# Where the build writes new data files (fdl status's data_path). Over HTTP clone
# keeps them in a local folder until commit uploads them; with --storage they go
# to the repository's data_dir in its storage; otherwise they go where the catalog
# says.
data_path() {
    fdl_status "$work" data_path
}

# Every build starts from the last build, on the apply branch. Before the first,
# the branch starts where main is. "main" in this script is the dataset's default
# branch, the branch HEAD names (refs/heads/main unless it was changed).
rm -rf .fdl
# Assigned first, so that set -e stops the build when the decision cannot be
# read: cloning an apply branch that was never started would build from nothing.
decision="$(uv run python "$(dirname "$0")/sync_branch.py" "$remote")"
if [ "$decision" = start ]; then
    uv run fdl branch "$remote" apply HEAD >/dev/null
fi
uv run fdl clone "$remote" "$work" --branch apply ${storage[@]+"${storage[@]}"}
FDL_DATA_PATH="$(data_path)"
# Queria's key reaches the data files alone (FDL_STORAGE_DATA). If they are
# somewhere else, the key cannot write them: build through a local folder.
if [ -n "${storage[*]+x}" ] && [ "$FDL_DATA_PATH" != "${FDL_STORAGE_DATA:-}" ]; then
    echo "storage key: not used (data files are in $FDL_DATA_PATH, the key reaches ${FDL_STORAGE_DATA:-nothing}); building through a local folder" >&2
    uv run python "$(dirname "$0")/storage_key.py" drop || true
    unset FDL_STORAGE FDL_STORAGE_DATA FDL_STORAGE_SECRET AWS_CONFIG_FILE
    storage=()
    rm -rf .fdl
    uv run fdl clone "$remote" "$work" --branch apply
    FDL_DATA_PATH="$(data_path)"
fi
export FDL_WORKING="$PWD/$work"
export FDL_DATA_PATH
# The repository the working catalog came from: fdl_read.py sends the token to it.
export FDL_REMOTE="$remote"
# Reads of other public datasets go by this name (profiles.yml's custom_user_agent),
# so data.queria.io does not count the build among its users.
QUERIA_USER_AGENT="${QUERIA_USER_AGENT:-queria-build/fdl-$(uv run python -c 'import importlib.metadata as m; print(m.version("fdl-cli"))')}"
export QUERIA_USER_AGENT

# A push to main that changed only the description and .github/ (a workflow only in
# its time limit) since apply's last build makes a version of the new description on
# the data apply holds, without running the dataset's code: the data would come out the same (scripts/built_from.py).
# With the description unchanged too, fdl commit makes no version. Each version
# records the commit whose code made its data as a trailer of its message. The
# SQL and dependencies of the models come from the _tabledoc schema apply holds, so a
# version without it (one from before it, with the earlier _queria or _meta schema) is built.
built=
if [ "${GITHUB_EVENT_NAME:-}" = push ]; then
    built="$(uv run python "$(dirname "$0")/built_from.py" check "$remote")" || built=
    # Anything but a commit (a stray line on stdout) builds.
    [[ "$built" =~ ^[0-9a-f]{40}$ ]] || built=
fi

build() {
    # 前に手元で回した dbt の manifest を読まないように、ビルドの前に消す
    rm -f target/manifest.json
    uv run "$@"
    # Each build starts from the last version: drop what no dbt model makes any more
    # (a model removed, renamed or made ephemeral), or it stays in every later version.
    if [ -f target/manifest.json ]; then
        uv run python "$(dirname "$0")/drop_stale.py" target/manifest.json "$name"
    fi
    trailer="$(uv run python "$(dirname "$0")/built_from.py" trailer)"
}

if [ -n "$built" ]; then
    echo "only the description or .github/ changed since the build from $built: not running the build"
    rm -f target/manifest.json
    trailer="Built-From: $built"
else
    build "$@"
fi

# tabledoc checks the description and writes it as the _tabledoc tables of the working
# catalog: the dataset, its tables and columns, and the files (README.md, the
# tables' guides and the documents). It declares with fdl declare what fdl reads:
# the primary keys, the asset columns, the other datasets the views read
# (catalogs in profiles.yml), and no profile for its own tables and the tables
# Queria does not list, so it runs where this project's fdl is on PATH (uv run).
# It reads the rows it keeps from apply's files with FDL_TOKEN, which in GitHub
# Actions is the exchanged token (scripts/queria_token.py) and elsewhere FDL_TOKEN
# or QUERIA_TOKEN, else the token fdl reads the remote with (its helper after `queria login`).
# Into a folder outside .fdl/ it puts the files fdl commit --assets uploads:
# those documents, and the files of FDL_ASSETS linked in. From dbt's
# target/manifest.json it adds each model's SQL and dependencies; when the build
# was skipped, they stay as apply's _tabledoc holds them. The dataset's own
# environment may not hold tabledoc, so it runs as a tool, at TABLEDOC_VERSION.
# --isolated: uvx otherwise runs a tabledoc installed with `uv tool install` whenever
# it meets the bound, however old (uvx ignores --upgrade-package for it).
# --refresh-package: uvx otherwise resolves against the index it cached.
tabledoc=(uvx --isolated --refresh-package tabledoc --from "tabledoc==$TABLEDOC_VERSION" tabledoc)
assets="$(mktemp -d)"
load=(--quiet --profiles "${DBT_PROFILES_DIR:-.}/profiles.yml" --out "$assets")
[ "${FDL_UNDESCRIBED:-}" = hidden ] && load+=(--hide-undescribed)
[ -n "${FDL_ASSETS:-}" ] && load+=(--assets "$FDL_ASSETS")
fdl_token() {
    uv run python "$(dirname "$0")/queria_token.py" 2>/dev/null && return 0
    # Otherwise the token fdl itself reads the remote with: its credential helper
    # (`queria login` on this machine) answers when FDL_TOKEN is not set.
    uv run python - "$remote" <<'PY'
import sys

from fdl.stores import credentials_for

token = credentials_for(sys.argv[1])
if not token:
    sys.exit(1)
print(token)
PY
}
run_load() {
    local token
    if token="$(fdl_token)"; then
        FDL_TOKEN="$token" uv run "${tabledoc[@]}" load "${load[@]}"
    else
        uv run "${tabledoc[@]}" load "${load[@]}"
    fi
}
status=0
run_load || status=$?
# 2: the catalog could not be read or written, such as apply's _tabledoc when its files
# cannot be read here. Without the build there is nothing else to take the SQL
# from, so build. 1 is the description's own error, which a build does not fix.
if [ "$status" = 2 ] && [ -n "$built" ]; then
    echo "could not keep the SQL from apply's _tabledoc: running the build" >&2
    built=
    build "$@"
    status=0
    run_load || status=$?
fi
[ "$status" = 0 ] || exit "$status"
# The description of earlier versions, in the _meta or _queria schema, is replaced by _tabledoc.
uv run python - "$work" <<'PY'
import sys

import duckdb

con = duckdb.connect()
con.execute("INSTALL ducklake; LOAD ducklake; INSTALL sqlite; LOAD sqlite;")
con.execute(f"ATTACH 'ducklake:{sys.argv[1]}' AS lake (META_TYPE 'sqlite', DATA_INLINING_ROW_LIMIT 0)")
for schema in ("_meta", "_queria"):
    if con.execute(
        "SELECT 1 FROM duckdb_schemas() WHERE database_name = 'lake' AND schema_name = ?", [schema]
    ).fetchone():
        con.execute(f"DROP SCHEMA lake.{schema} CASCADE")
        print(f"dropped the {schema} schema of earlier versions")
PY

author="${FDL_AUTHOR:-$(git config user.name || echo build)}"
message="${FDL_MESSAGE:-$([ -n "$built" ] && echo "Update the description" || echo build)}"
uv run fdl commit "$work" -m "$message${trailer:+$'\n\n'$trailer}" --author "$author" --assets "$assets"
uv run fdl push "$work"

# Bring apply into main, or ask people to. main is judged at the version its
# clone got, so a push to main in between is not merged unseen; a push after the
# clone makes the push below fail, which opens a change request.
main=".fdl/main.sqlite"
# The default branch, named to fdl clone and fdl branch: without it they take
# refs/heads/main whatever HEAD names.
default="$(uv run python "$(dirname "$0")/sync_branch.py" --default-branch "$remote")"
decision="$(uv run python "$(dirname "$0")/sync_branch.py" "$remote")"
if [ "$decision" != new ]; then
    uv run fdl clone "$remote" "$main" --branch "$default" >/dev/null
    base="$(fdl_status "$main" base)"
    decision="$(uv run python "$(dirname "$0")/sync_branch.py" "$remote" "$base")"
fi
request=
case "$decision" in
new)
    uv run fdl branch "$remote" "${default#refs/heads/}" refs/heads/apply
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
