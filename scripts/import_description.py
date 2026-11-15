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
import sys
from pathlib import Path
from typing import Any

from fdl import repo
from fdl import diff as diffs
from fdl import merge as merging
from fdl import objects
from fdl.stores import open_store
from ruamel.yaml import YAML

FILLED = {"tables", "catalogs", "sql", "depends_on", "rows", "kind", "fields", "type", "nullable"}
MISSING = object()

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096


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
                field = {"name": name}
                fields.append(field)
            apply_keys(field, keys)


def table_files(root: Path) -> dict[tuple, Path]:
    """Each *.table.yml below ``root`` by (schema, name), skipping hidden folders."""
    found = {}
    for path in sorted(root.rglob("*.table.yml")):
        if any(part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue
        record = yaml.load(path.read_text(encoding="utf-8")) or {}
        found[(record.get("schema"), record.get("name"))] = path
    return found


def write_yaml(path: Path, record: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(record, fh)


def import_description(store, root: Path) -> list[str]:
    main = repo.head(store, "refs/heads/main")
    apply = repo.head(store, "refs/heads/apply")
    if main is None or apply is None:
        return []
    bases = repo.merge_bases(store, main, apply)
    if len(bases) != 1:
        return []
    base = bases[0]
    described = [merging.declared(diffs.described(store, v)) for v in (base, main, apply)]
    if any(d is merging.MISSING for d in described):
        return []
    written: list[str] = []

    dataset_path = root / "dataset.yml"
    dataset = yaml.load(dataset_path.read_text(encoding="utf-8"))
    keys = changed(*described)
    tables = table_changes(*described)
    files = table_files(root)
    inline = by_key(dataset.get("tables", []) or [], "schema", "name")
    dataset_changed = bool(keys)
    for (schema, name), change in tables.items():
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

    trees = [
        {e["path"]: e for e in (diffs.listed(store, v) or {}).get("entries", [])}
        for v in (base, main, apply)
    ]
    for path in sorted(set(trees[0]) | set(trees[1])):
        old, ours, theirs = (t.get(path) for t in trees)
        sha = lambda entry: entry and entry["sha256"]  # noqa: E731
        if sha(ours) == sha(old) or sha(ours) == sha(theirs):
            continue
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
