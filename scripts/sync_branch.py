"""Whether the build may bring the apply branch into main by itself.

Usage:
  python sync_branch.py <remote> [<main version>]
  python sync_branch.py --target <remote>     print the Queria API and <handle>/<name>

The build runs on refs/heads/apply, which only ever holds versions built from
this repository's code. main is where people change the dataset (on Queria's
screens, or with fdl clone, push and change requests). Prints one of:

  new      main does not exist yet
  start    main exists and the apply branch does not: start it at main
  auto     main holds nothing but what earlier builds brought: merge apply into it
  request  main has changes of its own since the last build: open a change request
"""

from __future__ import annotations

import os
import re
import sys

from fdl import repo
from fdl.stores import open_store

from storage_key import API


def decide(store, main_id: str | None = None) -> str:
    """Judge ``main_id`` (the version a clone of main got), or main as it is now.

    Judging the version the merge will build on keeps a push to main between
    the judgment and the clone from being merged unseen.
    """
    apply = repo.head(store, "refs/heads/apply")
    main = repo.read_version(store, main_id) if main_id else repo.head(store, "refs/heads/main")
    if main is None:
        return "new"
    if apply is None:
        return "start"
    # main is a merge of a version on apply (a build's merge, or a change request
    # from apply merged on Queria), or main is behind apply. apply only ever grows
    # by first parent, so both walk its first-parent history, which stops at the
    # version's seq instead of reading every version behind apply. A merge of an
    # older apply version still counts, so a run that pushed apply and stopped
    # before the merge does not leave every later build asking for a request.
    if any(repo.in_history(store, merged, apply) for merged in main.parents[1:]):
        return "auto"
    if repo.in_history(store, main.id, apply):
        return "auto"
    return "request"


def target(remote: str) -> tuple[str, str]:
    """The Queria API that holds ``remote``'s change requests, and its <handle>/<name>."""
    match = re.fullmatch(r"https://([^/]+)/datasets/([^/]+)/([^/]+)/?", remote)
    base = match and (os.environ.get("QUERIA_API_URL") or API.get(match[1]))
    if not base:
        raise SystemExit(f"{remote}: no Queria API to open a change request on (set QUERIA_API_URL)")
    return base, f"{match[2]}/{match[3]}"


if __name__ == "__main__":
    if sys.argv[1] == "--target":
        print(*target(sys.argv[2]))
    else:
        print(decide(open_store(sys.argv[1]), *sys.argv[2:3]))
