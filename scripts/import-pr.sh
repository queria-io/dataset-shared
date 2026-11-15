#!/usr/bin/env bash
# Open (or update) a pull request that writes the description people changed on
# Queria into this repository (scripts/import_description.py). Called by
# build-fdl.sh in GitHub Actions; needs GH_TOKEN with `contents: write` and
# `pull-requests: write`.
#
# Usage: scripts/import-pr.sh <remote>
set -euo pipefail

remote="$1"
branch="queria/import-description"
written="$(uv run --with ruamel.yaml python "$(dirname "$0")/import_description.py" "$remote")"
if [ -z "$written" ]; then
    exit 0
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git switch -C "$branch"
# Removed files are not in the work tree, so add them with -A, path by path.
while IFS= read -r path; do
    git add -A -- "$path"
done <<<"$written"
git commit -q -m "docs: bring the description changed on Queria into dataset.yml" \
    -m "Written from main of $remote by scripts/import_description.py."
# The branch holds only this commit on top of the default branch, made again each time.
git push -q --force origin "$branch"
if [ -z "$(gh pr list --head "$branch" --state open --json number --jq '.[].number')" ]; then
    gh pr create --head "$branch" \
        --title "docs: bring the description changed on Queria into this repository" \
        --body "main on Queria holds description changes this code does not. Merging this makes the next build write the same description.

Files:
$(printf '%s\n' "$written" | sed 's/^/- /')"
fi
