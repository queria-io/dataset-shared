"""Find the description people changed on Queria and write the versions to compare.

Usage: python import_description.py <remote> <out>

main may hold changes to the description (dataset.json and the files of its tree)
that were made on Queria and are not in this code. They are the changes main made
since its merge base with refs/heads/apply, less those the apply branch already
holds. When there are any, main is written to <out>/main and the merge base to
<out>/base, each as fdl clone writes a version (dataset.json and its files), and
<out> is printed; nothing is printed when there is nothing to bring back.
`queria metadata unpack <out>/main --base <out>/base` then writes the changes
into queria.yml and the table Markdown files (or the older dataset.yml and
*.table.yml), keeping comments and what this code changed itself.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from fdl import diff as diffs
from fdl import merge as merging
from fdl import objects
from fdl import repo
from fdl.stores import StoreError, open_store

FILLED = {"tables", "catalogs", "sql", "depends_on", "rows", "kind", "fields", "type", "nullable"}
MISSING = object()



def changed(base: dict, ours: dict, theirs: dict) -> dict[str, Any]:
    """Keys ``ours`` changed since ``base`` to a value ``theirs`` does not hold (MISSING: removed)."""
    keys = (set(base) | set(ours)) - FILLED
    return {
        key: ours.get(key, MISSING)
        for key in sorted(keys)
        if ours.get(key, MISSING) != base.get(key, MISSING)
        and ours.get(key, MISSING) != theirs.get(key, MISSING)
    }


def by_key(items: list[dict], *names: str) -> dict:
    return {tuple(item.get(name) for name in names): item for item in items}


def table_changes(base: dict, ours: dict, theirs: dict) -> dict[tuple, dict]:
    """Per table (schema, name): its changed keys, and its fields' under ``fields``."""
    found = {}
    tables = [by_key(d.get("tables", []), "schema", "name") for d in (base, ours, theirs)]
    for key, table in tables[1].items():
        # Only tables this code builds: describing any other fails the commit.
        if key not in tables[2]:
            continue
        old, other = tables[0].get(key, {}), tables[2].get(key, {})
        keys = changed(old, table, other)
        fields = [by_key(t.get("fields", []), "name") for t in (old, table, other)]
        columns = {
            name: diff
            for name, field in fields[1].items()
            if (diff := changed(fields[0].get(name, {}), field, fields[2].get(name, {})))
        }
        if keys or columns:
            found[key] = {"keys": keys, "fields": columns}
    return found


def sha(entry: dict | None) -> str | None:
    return entry["sha256"] if entry else None


def merge_base(store, main, apply):
    """The latest apply version main holds: main itself, or one a merge in its history took.

    apply only grows by first parent, and main reaches it through its own
    first parents and the merges that took apply in, so this walks main back
    only past the changes made since, not the whole history.
    """
    version = main
    while version is not None:
        if repo.in_history(store, version.id, apply):
            return version
        for merged in version.parents[1:]:
            if repo.in_history(store, merged, apply):
                return repo.read_version(store, merged)
        version = repo.read_version(store, version.parents[0]) if version.parents else None
    return None


def drift(store, base, main, apply) -> dict | None:
    """The description main changed since ``base`` that apply does not hold.

    ``keys`` are the dataset's, ``tables`` each table's (see table_changes),
    ``tree`` the paths of tree.json with main's entry (None: removed).
    None when a version has no dataset.json to compare.
    """
    described = [merging.declared(diffs.described(store, v)) for v in (base, main, apply)]
    if any(d is merging.MISSING for d in described):
        return None
    trees = [
        {e["path"]: e for e in (diffs.listed(store, v) or {}).get("entries", [])}
        for v in (base, main, apply)
    ]
    tree = {}
    for path in sorted(set(trees[0]) | set(trees[1])):
        old, ours, theirs = (t.get(path) for t in trees)
        if sha(ours) != sha(old) and sha(ours) != sha(theirs):
            tree[path] = ours
    return {"keys": changed(*described), "tables": table_changes(*described), "tree": tree}


def is_empty(found: dict) -> bool:
    return not (found["keys"] or found["tables"] or found["tree"])


def read_tree_file(store, sha256: str) -> bytes:
    """A file of tree.json. fdl 0.32 keeps them under objects/; earlier versions wrote them under assets/."""
    if hasattr(objects, "tree_key"):
        try:
            return store.get(objects.tree_key(sha256))
        except StoreError as error:
            if getattr(error, "status", None) != 404:
                raise
    return store.get(objects.asset_key(sha256))


def write_version(store, version, folder: Path) -> None:
    """``version``'s dataset.json and the files of its tree.json, written into ``folder``."""
    folder.mkdir(parents=True)
    described = diffs.described(store, version) or {}
    (folder / "dataset.json").write_text(json.dumps(described, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for entry in (diffs.listed(store, version) or {}).get("entries", []):
        body = read_tree_file(store, entry["sha256"])
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise SystemExit(f"{entry['path']}: the file's sha256 is not the one tree.json records")
        local = folder / objects.check_path(entry["path"])
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(body)


def versions_to_import(store, out: Path) -> bool:
    """Write main and its merge base with apply into ``out`` when main changed the description."""
    main = repo.head(store, "refs/heads/main")
    apply = repo.head(store, "refs/heads/apply")
    base = merge_base(store, main, apply) if main and apply else None
    found = drift(store, base, main, apply) if base else None
    if found is None or is_empty(found):
        return False
    write_version(store, main, out / "main")
    write_version(store, base, out / "base")
    return True


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__.splitlines()[2], file=sys.stderr)
        raise SystemExit(2)
    if versions_to_import(open_store(sys.argv[1]), Path(sys.argv[2])):
        print(sys.argv[2])
