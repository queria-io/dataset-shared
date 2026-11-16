"""Check that a dataset's dbt models follow the layers and the idempotency rules.

README.md「層の切り方」「冪等に作る」. Run from the dataset's repository; build-fdl.sh
runs it before the build, so a model that breaks a rule makes no version.

- every model sits in a raw, stg or mart folder (models/<schema>/<layer>/...)
- only raw reads files (read_parquet, read_csv, st_read, ...), except the tables kept
  by hand under reference/
- stg refs raw only, mart refs stg or mart only, raw refs no stg or mart
- an incremental model has a unique_key or an is_incremental() block, so building
  the same input again adds no row twice

Usage: python check_models.py [models dir]   (default: models)
Exit status 1 with one line per problem.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LAYERS = ("raw", "stg", "mart")
# A read whose first argument is a path under reference/ (tables kept by hand) is allowed anywhere.
READS = re.compile(
    r"\b(?:read_(?:parquet|csv|csv_auto|json|json_auto|json_objects|ndjson|ndjson_objects|text|blob|xlsx)|st_read)"
    r"\s*\(\s*(?!\[?\s*'reference/)",
    re.I,
)
REF = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
INCREMENTAL = re.compile(r"materialized\s*=\s*['\"]incremental['\"]")
COMMENT = re.compile(r"\{#.*?#\}|--[^\n]*|/\*.*?\*/", re.S)
MAY_REF = {"raw": {"raw"}, "stg": {"raw"}, "mart": {"stg", "mart"}}


def layer(path: Path) -> str | None:
    return next((part for part in path.parts[:-1] if part in LAYERS), None)


def problems(models: Path) -> list[str]:
    files = sorted(models.rglob("*.sql"))
    layers = {f.stem: layer(f.relative_to(models)) for f in files}
    found = []
    for f in files:
        rel = f.relative_to(models)
        own = layers[f.stem]
        sql = COMMENT.sub("", f.read_text())
        if own is None:
            found.append(f"{rel}: not in a raw, stg or mart folder")
            continue
        if own != "raw" and READS.search(sql):
            found.append(f"{rel}: reads files outside raw")
        for name in REF.findall(sql):
            # A ref to anything but a model (a seed from reference/) is allowed.
            other = layers.get(name)
            if other and other not in MAY_REF[own]:
                found.append(f"{rel}: {own} refs {other} model {name}")
        if INCREMENTAL.search(sql) and "unique_key" not in sql and "is_incremental()" not in sql:
            found.append(f"{rel}: incremental with no unique_key or is_incremental() filter")
    return found


def main(argv: list[str]) -> int:
    models = Path(argv[1] if len(argv) > 1 else "models")
    if not models.is_dir():
        return 0
    found = problems(models)
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
