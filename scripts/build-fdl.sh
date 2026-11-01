#!/usr/bin/env bash
# Build a dataset with fdl: check out the published version, build on it, and
# commit a new version back to Queria. Called from each dataset's scripts/build.sh.
#
# Usage:
#   scripts/build.sh                  # build and publish
#   scripts/build.sh python other.py  # ... with a different build command
#
# The build reads and writes the working catalog fdl checks out: FDL_WORKING is
# its path and FDL_DATA_PATH the folder new data files go to (profiles.yml
# attaches both). fdl commit freezes it into a version described by dataset.yml,
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

uv run "$@"

author="${FDL_AUTHOR:-$(git config user.name || echo build)}"
uv run fdl commit "$work" -m "${FDL_MESSAGE:-build}" --author "$author"
