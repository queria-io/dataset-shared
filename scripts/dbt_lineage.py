"""dbt が作ったテーブルの SQL と依存先を、fdl commit に渡す記述に書き足す。

使い方: python dbt_lineage.py <作業場のカタログ> <manifest.json> <出力先のフォルダ>

fdl commit は dataset.yml と *.table.yml に書かれたことしか dataset.json に入れない。
dbt のモデルの SQL (compiled_code) と依存先 (parent_map) はそこに無いので、
Queria の画面のリネージュとテーブルの SQL が空になる。ここで manifest から
`sql` と `depends_on` (`"{schema}.{name}"` の配列) を書き足す。書き手が記述に書いた値が勝つ。

出力先のフォルダには、表をすべて含めた dataset.json と、README.md・docs/ へのリンクを置く。
fdl commit は記述のあるフォルダから *.table.yml と README.md・docs/ を探すので、
元のフォルダの記述を渡すと表が 2 度記述されたことになる。

足すのは作業場のカタログにあるテーブルとビューだけ。依存先は同じデータベースに書かれた
モデルだけで、別のデータベースのモデル (メモリ上の作業用テーブルなど) はその親をたどる。
ソース (他のデータセットや取り込んだファイル) は依存先に入れない。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

Key = tuple[str, str]

_LIVE = """
SELECT s.schema_name, t.table_name FROM ducklake_table t
JOIN ducklake_schema s ON s.schema_id = t.schema_id AND s.end_snapshot IS NULL
WHERE t.end_snapshot IS NULL
UNION
SELECT s.schema_name, v.view_name FROM ducklake_view v
JOIN ducklake_schema s ON s.schema_id = v.schema_id AND s.end_snapshot IS NULL
WHERE v.end_snapshot IS NULL
"""


def live_relations(working: Path) -> set[Key]:
    """作業場のカタログ (SQLite) にある、今のテーブルとビュー。"""
    con = sqlite3.connect(f"file:{working}?mode=ro", uri=True)
    try:
        return {(schema, name) for schema, name in con.execute(_LIVE)}
    finally:
        con.close()


def _model(nodes: dict[str, Any], unique_id: str) -> dict[str, Any] | None:
    node = nodes.get(unique_id)
    return node if isinstance(node, dict) and node.get("resource_type") == "model" else None


def _key(node: dict[str, Any]) -> Key:
    return (str(node.get("schema", "")), str(node.get("name", "")))


def _database(node: dict[str, Any]) -> str | None:
    database = node.get("database")
    return database.lower() if isinstance(database, str) else None


def _parents(manifest: dict[str, Any], unique_id: str) -> list[str]:
    """同じデータベースに書かれた親のモデル。別のデータベースのモデルは、その親をたどる。"""
    nodes = manifest.get("nodes") or {}
    parent_map = manifest.get("parent_map") or {}
    database = _database(nodes[unique_id])

    # 親の数だけ再帰が深くなるので、ループでたどる
    found: set[Key] = set()
    seen: set[str] = set()
    pending = list(parent_map.get(unique_id) or [])
    while pending:
        parent_id = pending.pop()
        if parent_id in seen:
            continue
        seen.add(parent_id)
        parent = _model(nodes, parent_id)
        if parent is None:
            continue
        if _database(parent) == database:
            found.add(_key(parent))
        else:
            pending.extend(parent_map.get(parent_id) or [])
    return sorted(f"{schema}.{name}" for schema, name in found - {_key(nodes[unique_id])})


def lineage(manifest: dict[str, Any], live: set[Key]) -> dict[Key, dict[str, Any]]:
    """カタログにあるモデルごとの `sql` と `depends_on`。値の無い項目は入れない。"""
    nodes = manifest.get("nodes") or {}

    def entry(unique_id: str, node: dict[str, Any]) -> dict[str, Any]:
        code = node.get("compiled_code")
        sql = code.strip() if isinstance(code, str) else ""
        depends_on = _parents(manifest, unique_id)
        return {
            **({"sql": sql} if sql else {}),
            **({"depends_on": depends_on} if depends_on else {}),
        }

    return {
        _key(node): entry(unique_id, node)
        for unique_id in nodes
        if (node := _model(nodes, unique_id)) is not None and _key(node) in live
    }


def with_lineage(declared: dict[str, Any], found: dict[Key, dict[str, Any]]) -> dict[str, Any]:
    """記述の表に `sql` と `depends_on` を足す。書き手の値が勝つ。記述に無い表は足す。"""
    tables = declared.get("tables") or []
    described = {(t.get("schema"), t.get("name")) for t in tables if isinstance(t, dict)}

    def enrich(table: Any) -> Any:
        if not isinstance(table, dict):
            return table
        return {**found.get((table.get("schema"), table.get("name")), {}), **table}

    added = [
        {"schema": schema, "name": name, **extra}
        for (schema, name), extra in sorted(found.items())
        if (schema, name) not in described and extra
    ]
    return {**declared, "tables": [*map(enrich, tables), *added]}


def stage(declared: dict[str, Any], root: Path, out: Path) -> Path:
    """fdl commit に渡す記述を ``out`` に書く。README.md と docs/ は ``root`` のものを指す。"""
    out.mkdir(parents=True, exist_ok=True)
    for name in ("README.md", "docs"):
        if (root / name).exists() and not (out / name).exists():
            (out / name).symlink_to((root / name).resolve())
    path = out / "dataset.json"
    path.write_text(
        json.dumps(declared, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.splitlines()[2], file=sys.stderr)
        return 2
    working, manifest_path, out = map(Path, argv)
    from fdl import dataset  # fdl-cli はデータセットの依存に入っている

    description = dataset.find()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = with_lineage(dataset.gather(description), lineage(manifest, live_relations(working)))
    print(stage(declared, description.resolve().parent, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
