"""Whether this build may skip running the dataset's code: since the last build, the
repository changed nothing but the description and the GitHub Actions settings.

Usage:
  python built_from.py trailer               print the trailer that records this checkout (in GitHub Actions only)
  python built_from.py check <remote>        when only the description changed since apply was last built,
                                            print the commit it was built from
                                            (also when only .github/ changed besides)

Each version the build makes records in its message the commit whose code made its
data (`Built-From: <sha>`, a trailer as in Git). A version made from the description
alone keeps the commit of the build before it, so the head of apply always tells
which code its data came from. When the repository changed only description files
and .github/ since that commit, the data would come out the same: the build commits
the new description on the data it already has (or nothing, when the description is
the same).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

TRAILER = "Built-From"
_TRAILER = re.compile(rf"^{TRAILER}: ([0-9a-f]{{40}})$", re.MULTILINE)

#: The files tabledoc reads the dataset's description from (tabledoc.yml, queria.yml, and
#: the older dataset.yml). Anything else may change the data.
DESCRIPTION_FILES = ("tabledoc.yml", "queria.yml", "dataset.yml")
#: Markdown anywhere (the tables' files, README.md, documents) and the older table files.
DESCRIPTION_PATTERNS = ("*.md", "*.table.yml")
DESCRIPTION_DIR = "docs/"
#: The GitHub settings. A workflow also passes the build its inputs (secrets, mode,
#: the runner, the pinned shared workflow), so it keeps the data only when its jobs
#: changed nothing but the time limit. The shared submodule is outside: its scripts
#: make the data.
CI_DIR = ".github/"
WORKFLOWS_DIR = ".github/workflows/"
TIME_LIMITS = ("timeout_minutes", "timeout-minutes")
#: The schema that holds the description and the models' SQL in a version. tabledoc
#: load --schema _queria keeps the SQL from it alone, so a version that holds only
#: the earlier _meta schema is built.
DESCRIBED = "_queria"


def is_description(path: str) -> bool:
    """Whether ``path`` (from the repository's root) only describes the dataset."""
    name = PurePosixPath(path).name
    return (
        path in DESCRIPTION_FILES
        or any(fnmatchcase(name, pattern) for pattern in DESCRIPTION_PATTERNS)
        or path.startswith(DESCRIPTION_DIR)
    )


def _without_time_limits(value):
    if isinstance(value, dict):
        return {k: _without_time_limits(v) for k, v in value.items() if k not in TIME_LIMITS}
    if isinstance(value, list):
        return [_without_time_limits(v) for v in value]
    return value


def _jobs(sha: str, path: str):
    """A workflow's jobs at ``sha``, without their time limits; None when it cannot be read."""
    import yaml

    shown = _git("show", f"{sha}:{path}")
    if shown.returncode:
        return None
    try:
        workflow = yaml.safe_load(shown.stdout)
    except yaml.YAMLError:
        return None
    return _without_time_limits(workflow.get("jobs")) if isinstance(workflow, dict) else None


def keeps_data(path: str, before: str, after: str) -> bool:
    """Whether the change to ``path`` between two commits leaves the data as it is.

    The description does, and .github/ does except a workflow whose jobs changed
    more than their time limit (when, not what, the build runs).
    """
    if is_description(path):
        return True
    if not path.startswith(CI_DIR):
        return False
    if not path.startswith(WORKFLOWS_DIR):
        return True
    jobs = _jobs(before, path)
    return jobs is not None and jobs == _jobs(after, path)


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


def description_only(store) -> str | None:
    """The commit apply was built from, when the checkout changed only the description and .github/ since.

    A version with no _queria schema (one from before it, even with the earlier
    _meta) builds, as tabledoc load cannot keep its models' SQL. A push
    that changed nothing (an empty commit pushed to build again) builds as before.
    """
    from fdl import diff as diffs
    from fdl import repo

    head = repo.head(store, "refs/heads/apply")
    sha = head and built_from(head.message)
    if not sha:
        return None
    changed = changed_since(sha)
    checkout = checkout_commit()
    if not changed or checkout is None or not all(keeps_data(path, sha, checkout) for path in changed):
        return None
    tables = (diffs.indexed(store, head) or {}).get("tables") or {}
    if not any(name.split(".", 1)[0] == DESCRIBED for name in tables):
        print("apply holds no _queria schema, so its SQL cannot be kept: building", file=sys.stderr)
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
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
