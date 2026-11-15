"""Whether the Sync may bring the sync branch into main by itself.

Usage: python sync_branch.py <remote>

The Sync builds on refs/heads/sync, which only ever holds versions built from
this repository's code. main is where people change the dataset (on Queria's
screens, or with fdl clone, push and change requests). Prints one of:

  new      main does not exist yet
  start    main exists and the sync branch does not: start it at main
  auto     main holds nothing but what earlier Syncs brought: merge sync into it
  request  main has changes of its own since the last Sync: open a change request
"""

from __future__ import annotations

import sys

from fdl import repo
from fdl.stores import open_store


def decide(store) -> str:
    sync = repo.head(store, "refs/heads/sync")
    main = repo.head(store, "refs/heads/main")
    if main is None:
        return "new"
    if sync is None:
        return "start"
    # main is behind sync, or is a merge of this build's parent or of sync itself
    # (a Sync's merge, or a change request from sync merged on Queria).
    synced = {sync.id, *sync.parents[:1]}
    if repo.is_ancestor(store, main.id, sync) or set(main.parents[1:]) & synced:
        return "auto"
    return "request"


if __name__ == "__main__":
    print(decide(open_store(sys.argv[1])))
