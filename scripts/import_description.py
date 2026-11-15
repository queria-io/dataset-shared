"""Write the description people changed on Queria back into this repository.

Usage: python import_description.py <remote>

main may hold changes to the description (dataset.json, README.md, docs/) that
were made on Queria and are not in this code. They are the changes main made
since its merge base with refs/heads/apply, less those the apply branch already
holds. Each is written to the file it comes from: the dataset's keys to
dataset.yml, a table's to its *.table.yml (or its entry in dataset.yml), and
the files of tree.json as they are. Comments and order in the YAML are kept.
Prints each path written, one per line; nothing when there is nothing to bring back.

What the build fills in (types, rows, sql, depends_on, catalogs) is not written.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from fdl import repo
from fdl import dataset as datasets
from fdl import diff as diffs
from fdl import merge as merging
from fdl import objects
from fdl.stores import open_store

FILLED = {"tables", "catalogs", "sql", "depends_on", "rows", "kind", "fields", "type", "nullable"}
MISSING = object()



def _yaml():
    """A round-trip YAML that keeps comments and the repositories' indentation.

    Imported here so that sync_branch.py, which reads only the drift, runs without ruamel.
    """
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    # The dataset repositories indent list items under their key (`fields:\n  - name:`)
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


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


def apply_keys(record: dict, keys: dict) -> None:
    for key, value in keys.items():
        if value is MISSING:
            record.pop(key, None)
        else:
            record[key] = value


def apply_table(record: dict, change: dict) -> None:
    apply_keys(record, change["keys"])
    if change["fields"]:
        fields = record.setdefault("fields", [])
        for name, keys in change["fields"].items():
            field = next((f for f in fields if f.get("name") == name), None)
            if field is None:
                # Removing what the file never held changes nothing.
                if all(value is MISSING for value in keys.values()):
                    continue
                field = {"name": name}
                fields.append(field)
            apply_keys(field, keys)


def table_files(root: Path) -> dict[tuple, Path]:
    """Each table file fdl commit reads below ``root`` by (schema, name), skipping hidden folders."""
    found = {}
    paths = (path for pattern in datasets.TABLE_FILES for path in root.rglob(pattern))
    for path in sorted(paths):
        if any(part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue
        record = _yaml().load(path.read_text(encoding="utf-8")) or {}
        found[(record.get("schema"), record.get("name"))] = path
    return found


def write_yaml(path: Path, record: Any) -> None:
    if path.suffix == ".json":
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8") as fh:
        _yaml().dump(record, fh)


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


def import_description(store, root: Path) -> list[str]:
    main = repo.head(store, "refs/heads/main")
    apply = repo.head(store, "refs/heads/apply")
    base = merge_base(store, main, apply) if main and apply else None
    found = drift(store, base, main, apply) if base else None
    if found is None or is_empty(found):
        return []
    yaml = _yaml()
    written: list[str] = []

    dataset_path = root / "dataset.yml"
    dataset = yaml.load(dataset_path.read_text(encoding="utf-8"))
    keys = found["keys"]
    files = table_files(root)
    inline = by_key(dataset.get("tables", []) or [], "schema", "name")
    dataset_changed = bool(keys)
    for (schema, name), change in found["tables"].items():
        if (schema, name) in files:
            path = files[(schema, name)]
            record = yaml.load(path.read_text(encoding="utf-8"))
            apply_table(record, change)
            write_yaml(path, record)
            written.append(path.relative_to(root).as_posix())
        elif (schema, name) in inline:
            apply_table(inline[(schema, name)], change)
            dataset_changed = True
        else:
            # A table the code does not describe: a file of its own, beside those of its schema.
            near = next((p.parent for (s, _), p in files.items() if s == schema), root)
            path = near / f"{name}.table.yml"
            record = {"schema": schema, "name": name}
            apply_table(record, change)
            write_yaml(path, record)
            written.append(path.relative_to(root).as_posix())
    if dataset_changed:
        apply_keys(dataset, keys)
        write_yaml(dataset_path, dataset)
        written.insert(0, "dataset.yml")

    for path, ours in found["tree"].items():
        local = root / objects.check_path(path)
        if ours is None:
            local.unlink(missing_ok=True)
        else:
            body = store.get(objects.asset_key(ours["sha256"]))
            if hashlib.sha256(body).hexdigest() != ours["sha256"]:
                raise SystemExit(f"{path}: the file's sha256 is not the one tree.json records")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(body)
        written.append(path)
    return written


if __name__ == "__main__":
    for path in import_description(open_store(sys.argv[1]), Path(".")):
        print(path)
