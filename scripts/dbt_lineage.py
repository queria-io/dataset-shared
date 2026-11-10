"""dbt が作ったテーブルの SQL と、他のデータセットのエイリアスを、fdl commit に渡す記述に書き足す。

使い方: python dbt_lineage.py <作業場のカタログ> <manifest.json> <profiles.yml> <出力先のフォルダ>

fdl commit は SQL を持つテーブルの depends_on を、その SQL から作る。dbt で作ったテーブルの
SQL (compiled_code) はカタログに残らないので、ここで manifest から `sql` を書き足す。
SQL の中の他のデータセットはエイリアス (`e_stat.census.…`) で書かれているので、profiles.yml が
読むだけで ATTACH する公開データセットから `catalogs` (エイリアス → リポジトリの URL) を作る。
どちらも書き手が記述に書いた値が勝つ。

出力先のフォルダには、表をすべて含めた dataset.json と、README.md・docs/ へのリンクを置く。
fdl commit は記述のあるフォルダから *.table.yml と README.md・docs/ を探すので、
元のフォルダの記述を渡すと表が 2 度記述されたことになる。

SQL を足すのは作業場のカタログにあるテーブルとビューだけ。
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


def compiled_sql(manifest: dict[str, Any], live: set[Key]) -> dict[Key, str]:
    """カタログにあるモデルごとの、コンパイル済みの SQL。"""
    nodes = manifest.get("nodes") or {}

    def sql_of(node: dict[str, Any]) -> str:
        code = node.get("compiled_code")
        return code.strip() if isinstance(code, str) else ""

    return {
        _key(node): sql_of(node)
        for unique_id in nodes
        if (node := _model(nodes, unique_id)) is not None
        and _key(node) in live
        and sql_of(node)
    }


def _repository(path: str) -> str | None:
    """ATTACH の path (`ducklake:https://…/ducklake.duckdb`) から、リポジトリの URL。"""
    url = path.removeprefix("ducklake:")
    if not url.startswith(("https://", "http://")) or "{" in url:
        return None
    return url.removesuffix("ducklake.duckdb") if url.endswith("/ducklake.duckdb") else None


def catalogs(profiles: dict[str, Any]) -> dict[str, str]:
    """プロファイルが読むだけで ATTACH する他のデータセットの、エイリアス → リポジトリの URL。"""
    return {
        str(attach["alias"]): repository
        for name, profile in profiles.items()
        if name != "config" and isinstance(profile, dict)
        for target in (profile.get("outputs") or {}).values()
        if isinstance(target, dict)
        for attach in target.get("attach") or []
        if isinstance(attach, dict)
        and attach.get("alias")
        and (repository := _repository(str(attach.get("path", "")))) is not None
    }


def with_lineage(
    declared: dict[str, Any], sql: dict[Key, str], aliases: dict[str, str]
) -> dict[str, Any]:
    """記述の表に `sql` を、記述に `catalogs` を足す。書き手の値が勝つ。記述に無い表は足す。"""
    tables = declared.get("tables") or []
    described = {(t.get("schema"), t.get("name")) for t in tables if isinstance(t, dict)}

    def enrich(table: Any) -> Any:
        if not isinstance(table, dict):
            return table
        found = sql.get((table.get("schema"), table.get("name")))
        return {**({"sql": found} if found else {}), **table}

    added = [
        {"schema": schema, "name": name, "sql": code}
        for (schema, name), code in sorted(sql.items())
        if (schema, name) not in described
    ]
    return {
        **declared,
        **({"catalogs": {**aliases, **(declared.get("catalogs") or {})}} if aliases else {}),
        "tables": [*map(enrich, tables), *added],
    }


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
    if len(argv) != 4:
        print(__doc__.splitlines()[2], file=sys.stderr)
        return 2
    working, manifest_path, profiles_path, out = map(Path, argv)
    import yaml  # dbt が依存に持つ
    from fdl import dataset  # fdl-cli はデータセットの依存に入っている

    description = dataset.find()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profiles = (
        yaml.safe_load(profiles_path.read_text(encoding="utf-8")) if profiles_path.is_file() else {}
    )
    declared = with_lineage(
        dataset.gather(description),
        compiled_sql(manifest, live_relations(working)),
        catalogs(profiles or {}),
    )
    print(stage(declared, description.resolve().parent, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
