#!/usr/bin/env bash
# Move the shared submodule to dataset-shared's main and pin every reusable
# workflow the repository calls (`uses: queria-io/dataset-shared/...@<ref>`) to
# the same commit. Run from the root of a dataset repository, then commit.
#
#   shared/scripts/pin-shared.sh            # main
#   shared/scripts/pin-shared.sh <commit>   # a given commit
set -euo pipefail
# Fetch every branch so that a commit outside main (or a short SHA) can be given
git -C shared fetch -q origin
git -C shared checkout -q "${1:-origin/main}"
sha="$(git -C shared rev-parse HEAD)"
for file in .github/workflows/*.yml .github/workflows/*.yaml; do
    [ -f "$file" ] || continue
    perl -pi -e "s{(queria-io/dataset-shared/\.github/workflows/[\w.-]+\.ya?ml)\@[^\s\"']+}{\$1\@$sha}g" "$file"
done
git add shared .github/workflows
echo "shared and its workflows -> $sha"
