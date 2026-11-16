"""Whether this build may skip running the dataset's code: since the last build, the
repository changed nothing but the description.

Usage:
  python built_from.py trailer               print the trailer that records this checkout (in GitHub Actions only)
  python built_from.py check <remote>        print the commit apply was last built from, when only the description changed since
  python built_from.py described <remote> <out>   write apply's dataset.json to <out>

Each version the build makes records in its message the commit whose code made its
data (`Built-From: <sha>`, a trailer as in Git). A version made from the description
alone keeps the commit of the build before it, so the head of apply always tells
which code its data came from. When the repository changed only description files
since that commit, the data would come out the same: the build commits the new
description on the data it already has.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

TRAILER = "Built-From"
_TRAILER = re.compile(rf"^{TRAILER}: ([0-9a-f]{{40}})$", re.MULTILINE)

#: The files fdl commit reads the description from. Anything else may change the data.
DESCRIPTION_FILES = ("dataset.yml", "dataset.yaml", "dataset.json", "README.md")
DESCRIPTION_SUFFIXES = (".table.yml", ".table.yaml", ".table.json")
DESCRIPTION_DIR = "docs/"


def is_description(path: str) -> bool:
    """Whether ``path`` (from the repository's root) only describes the dataset."""
    return (
        path in DESCRIPTION_FILES
        or path.endswith(DESCRIPTION_SUFFIXES)
        or path.startswith(DESCRIPTION_DIR)
    )


def built_from(message: str) -> str | None:
    """The commit a version's message records, if any."""
    found = _TRAILER.findall(message)
    return found[-1] if found else None


def with_trailer(message: str, sha: str) -> str:
    """``message`` with the trailer for ``sha``, replacing any it held."""
    body = _TRAILER.sub("", message).rstrip()
    return f"{body}\n\n{TRAILER}: {sha}"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def checkout_commit() -> str | None:
    """The commit a GitHub Actions run checked out.

    Only a run records it: a build on someone's machine may hold changes that are
    not in any commit, so the next run builds again. The run itself changes
    uv.lock (it upgrades queria and fdl-cli), which is not the code it builds.
    """
    sha = os.environ.get("GITHUB_SHA", "") if os.environ.get("GITHUB_ACTIONS") else ""
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None


def changed_since(sha: str) -> list[str] | None:
    """The paths that differ between ``sha`` and the checkout, or None when git cannot tell."""
    if _git("cat-file", "-e", f"{sha}^{{commit}}").returncode:
        # A shallow checkout (actions/checkout fetches one commit) lacks it.
        if _git("fetch", "--quiet", "--depth=1", "origin", sha).returncode:
            return None
    head = checkout_commit()
    if head is None:
        return None
    diff = _git("diff", "--name-only", sha, head)
    if diff.returncode:
        return None
    return [line for line in diff.stdout.splitlines() if line]


def description_only(store) -> str | None:
    """The commit apply was built from, when the checkout changed only the description since."""
    from fdl import repo

    head = repo.head(store, "refs/heads/apply")
    sha = head and built_from(head.message)
    if not sha:
        return None
    changed = changed_since(sha)
    if changed is None or not all(map(is_description, changed)):
        return None
    return sha


def main(argv: list[str]) -> int:
    if argv[:1] == ["trailer"] and len(argv) == 1:
        sha = checkout_commit()
        if sha:
            print(f"{TRAILER}: {sha}")
        return 0
    if argv[:1] == ["check"] and len(argv) == 2:
        from fdl.stores import open_store

        sha = description_only(open_store(argv[1]))
        if sha:
            print(sha)
        return 0
    if argv[:1] == ["described"] and len(argv) == 3:
        from fdl import diff as diffs
        from fdl import dataset, repo
        from fdl.stores import open_store

        store = open_store(argv[1])
        head = repo.head(store, "refs/heads/apply")
        described = head and diffs.described(store, head)
        if not described:
            print("apply has no dataset.json to take the SQL from", file=sys.stderr)
            return 1
        Path(argv[2]).write_bytes(dataset.to_json(described))
        return 0
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
