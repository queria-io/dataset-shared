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

# Keep dbt's credentials alive however long its run takes. DuckDB fetches them
# once, when the profile's S3 secret is created, and they expire after fifteen
# minutes; queria.dbt_plugin re-issues them as dbt runs each model or test. Add
# it to a copy of the profile rather than to the repository's, and only when
# the installed queria has it -- a profile naming a module that is not there
# stops dbt at startup. A DBT_PROFILES_DIR set by hand is left alone.
if [ -f profiles.yml ] && [ -n "${DBT_PROFILES_DIR:-}" ]; then
    echo "DBT_PROFILES_DIR is set: dbt runs without queria.dbt_plugin, and one longer than fifteen minutes loses its credentials" >&2
elif [ -f profiles.yml ]; then
    profiles_dir="$(mktemp -d)"
    status=0
    uv run python "$(dirname "$0")/with_dbt_plugin.py" profiles.yml "$profiles_dir/profiles.yml" || status=$?
    # 3 is with_dbt_plugin.NO_PLUGIN
    case "$status" in
        0) export DBT_PROFILES_DIR="$profiles_dir" ;;
        3) rm -rf "$profiles_dir" ;; # the installed queria has no plugin yet: build with the profile as it is
        *) rm -rf "$profiles_dir"; echo "could not add queria.dbt_plugin to profiles.yml (exit $status)" >&2; exit 1 ;;
    esac
fi

# Queria's own datasets keep their last seven versions, so the history tab has
# something to show. The CLI keeps only the latest by default. Keep this in step
# with .github/workflows/checkpoint.yml.
exec uv run queria sync --keep-versions 7 -- "$@"
