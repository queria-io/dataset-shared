"""Whether this build may skip running the dataset's code: since the last build, the
repository changed nothing but the description.

Usage:
  python built_from.py trailer               print the trailer that records this checkout (in GitHub Actions only)
  python built_from.py check <remote> <out>  when only the description changed since apply was last built,
                                            write apply's dataset.json to <out> and print the commit it was built from

Each version the build makes records in its message the commit whose code made its
data (`Built-From: <sha>`, a trailer as in Git). A version made from the description
alone keeps the commit of the build before it, so the head of apply always tells
which code its data came from. When the repository changed only description files
since that commit, the data would come out the same: the build commits the new
description on the data it already has.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

TRAILER = "Built-From"
_TRAILER = re.compile(rf"^{TRAILER}: ([0-9a-f]{{40}})$", re.MULTILINE)

#: The files queria metadata pack reads the description from (queria.yml, and the
#: older dataset.yml). Anything else may change the data.
DESCRIPTION_FILES = ("queria.yml", "dataset.yml")
#: Markdown anywhere (the tables' files, README.md, documents) and the older table files.
DESCRIPTION_PATTERNS = ("*.md", "*.table.yml")
DESCRIPTION_DIR = "docs/"


def is_description(path: str) -> bool:
    """Whether ``path`` (from the repository's root) only describes the dataset."""
    name = PurePosixPath(path).name
    return (
        path in DESCRIPTION_FILES
        or any(fnmatchcase(name, pattern) for pattern in DESCRIPTION_PATTERNS)
        or path.startswith(DESCRIPTION_DIR)
    )


def built_from(message: str) -> str | None:
    """The commit a version's message records, if any."""
    found = _TRAILER.findall(message)
    return found[-1] if found else None


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
    # -z: paths as they are (not quoted, as core.quotePath does to non-ASCII names).
    # --no-renames: a file moved keeps its old path in the list, so code moved under
    # docs/ is not taken for a description.
    diff = _git("diff", "--name-only", "--no-renames", "-z", sha, head)
    if diff.returncode:
        return None
    return [path for path in diff.stdout.split("\0") if path]


def description_only(store, out: Path) -> str | None:
    """The commit apply was built from, when the checkout changed only the description since.

    Then apply's dataset.json, which the SQL and catalogs are taken from, is written
    to ``out``. A push that changed nothing (an empty commit pushed to build again)
    builds as before.
    """
    from fdl import diff as diffs
    from fdl import repo

    head = repo.head(store, "refs/heads/apply")
    sha = head and built_from(head.message)
    if not sha:
        return None
    changed = changed_since(sha)
    if not changed or not all(map(is_description, changed)):
        return None
    described = diffs.described(store, head)
    if not described:
        print("apply has no dataset.json to take the SQL from: building", file=sys.stderr)
        return None
    out.write_text(json.dumps(described, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sha


def main(argv: list[str]) -> int:
    if argv[:1] == ["trailer"] and len(argv) == 1:
        sha = checkout_commit()
        if sha:
            print(f"{TRAILER}: {sha}")
        return 0
    if argv[:1] == ["check"] and len(argv) == 3:
        from fdl.stores import open_store

        sha = description_only(open_store(argv[1]), Path(argv[2]))
        if sha:
            print(sha)
        return 0
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
