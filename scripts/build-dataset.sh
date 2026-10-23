#!/usr/bin/env bash
# Common build script for all dataset repositories.
# Called from each dataset's scripts/build.sh.
#
# Usage:
#   scripts/build.sh                  # build and publish to Queria
#   scripts/build.sh python other.py  # ... with a different build command
#
# There is no target to choose any more. Queria takes the dataset from
# dataset.yml and the account from QUERIA_TOKEN, and the build writes its
# parquet straight into that dataset's own storage.
#
# `sync` is pull, then the build, then push. Starting from what is published is
# what makes the push conditional on it, so two publishers cannot overwrite each
# other. push publishes dbt's artifacts itself, and Queria builds the catalog it
# serves from what push left behind -- neither needs a step here.
#
# To rehearse without publishing, run the dataset against the stand-in in
# queria-cli's tools/ (see its README). fdl's `local` target has no counterpart:
# every write now goes through credentials only Queria can mint.
set -euo pipefail

if [ "$#" -eq 1 ] && { [ "$1" = "local" ] || [ "$1" = "default" ]; }; then
    echo "scripts/build.sh no longer takes a target ('$1')." >&2
    echo "  publish:  scripts/build.sh" >&2
    echo "  rehearse: tools/rotate.py in queria-cli, against the stand-in" >&2
    exit 2
fi

if [ "$#" -eq 0 ]; then
    set -- python main.py
fi

# Queria's own datasets keep their last seven versions, so the history tab has
# something to show. The CLI keeps only the latest by default. Keep this in step
# with .github/workflows/checkpoint.yml.
exec uv run queria sync --keep-versions 7 -- "$@"
