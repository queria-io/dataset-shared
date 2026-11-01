#!/usr/bin/env bash
# Build a dataset with fdl: check out the published version, build on it, and
# commit a new version back to Queria. Called from each dataset's scripts/build.sh.
#
# Usage:
#   scripts/build.sh                  # build and publish
#   scripts/build.sh python other.py  # ... with a different build command
#
# The build reads and writes the working catalog fdl checks out, whose path is
# FDL_WORKING (profiles.yml attaches it; the catalog records where data files go). fdl commit freezes it into a version described by dataset.yml,
# uploads the new files and moves main; Queria publishes it.
#
# Environment:
#   FDL_TOKEN    a Queria API token that may write the dataset (or `fdl login data.queria.io`)
#   FDL_REMOTE   the repository (default: https://data.queria.io/datasets/queria/<name>/)
#   FDL_MESSAGE  the version's message (default: build)
#   FDL_AUTHOR   who made it (default: git's user.name)
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

# Every build starts from what is published now, so a version is built on main.
rm -rf .fdl
uv run fdl checkout "$remote" "$work"
export FDL_WORKING="$PWD/$work"

uv run "$@"

author="${FDL_AUTHOR:-$(git config user.name || echo build)}"
uv run fdl commit "$work" -m "${FDL_MESSAGE:-build}" --author "$author"
