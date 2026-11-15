#!/usr/bin/env bash
# Open (or update) a pull request with the description people changed on Queria,
# which build-fdl.sh wrote into this checkout (scripts/import_description.py)
# and listed in <list>, one path a line. A step of its own in the Apply workflow:
# GH_TOKEN (`contents: write`, `pull-requests: write`) reaches this step only, not
# the build's code, and the checkout keeps no credentials.
#
# Usage: scripts/import-pr.sh <list>
set -euo pipefail

list="$1"
branch="queria/import-description"
[ -s "$list" ] || exit 0
origin="https://x-access-token:${GH_TOKEN:?}@github.com/${GITHUB_REPOSITORY:?}.git"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git switch -C "$branch"
# Removed files are not in the work tree, so add them with -A, path by path.
# A file the build makes and git ignores is not this repository's to change.
paths=()
while IFS= read -r path; do
    if git check-ignore -q -- "$path"; then
        continue
    fi
    git add -A -- "$path"
    paths+=("$path")
done <"$list"
if git diff --cached --quiet; then
    exit 0
fi
# A build that started before an import pull request merged still sees the old
# files. When the default branch already holds these files, there is nothing to bring.
if git fetch -q "$origin" HEAD 2>/dev/null && git diff --cached --quiet FETCH_HEAD -- "${paths[@]}"; then
    exit 0
fi
git commit -q -m "docs: bring the description changed on Queria into this repository" \
    -m "Written from main on Queria by scripts/import_description.py."
# The branch holds only this commit on top of the default branch, made again
# each time. When its files are what the branch already holds, nothing is pushed
# or opened: an open pull request stays as it is, and one closed on purpose is
# not opened again until main changes something else.
if git fetch -q "$origin" "$branch" 2>/dev/null && git diff --quiet FETCH_HEAD HEAD; then
    exit 0
fi
git push -q --force "$origin" "$branch"
if [ -z "$(gh pr list --head "$branch" --state open --json number --jq '.[].number')" ]; then
    gh pr create --head "$branch" \
        --title "docs: bring the description changed on Queria into this repository" \
        --body "main on Queria holds description changes this code does not. Merging this makes the next build write the same description.

Files:
$(sed 's/^/- /' "$list")"
fi
