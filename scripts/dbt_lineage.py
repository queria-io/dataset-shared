"""dbt が作ったテーブルの SQL と、他のデータセットのエイリアスを、fdl commit に渡す記述に書き足す。

使い方: python dbt_lineage.py <作業場のカタログ> <manifest.json か -> <profiles.yml> <出力先のフォルダ>

fdl commit は SQL を持つテーブルの depends_on を、その SQL から作る。dbt で作ったテーブルの
SQL (compiled_code) はカタログに残らないので、ここで manifest から `sql` を書き足す。
SQL の中の他のデータセットはエイリアス (`e_stat.census.…`) で書かれているので、profiles.yml が
読むだけで ATTACH する公開データセットから `catalogs` (エイリアス → リポジトリの URL) を作る。
どちらも書き手が記述に書いた値が勝つ。

出力先のフォルダには、表をすべて含めた dataset.json と、README.md・docs/ へのリンクを置く。
fdl commit は記述のあるフォルダから *.table.yml と README.md・docs/ を探すので、
元のフォルダの記述を渡すと表が 2 度記述されたことになる。

SQL を足すのは作業場のカタログにあるテーブルとビューだけ。

FDL_UNDESCRIBED=unlisted のときは、記述 (*.table.yml) の無いテーブルとビューを Queria に
掲載しない (`annotations.queria.published: false` を足す)。取り込んだままの元表や中間テーブルが
数百あるデータセット向けで、データセットの scripts/build.sh が指定する。dbt を流さないビルドでも
足せるように、manifest の代わりに `-` を受け取る。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections.abc import Mapping
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


def compiled_sql(manifest: dict[str, Any], live: set[Key]) -> dict[Key, str]:
    """カタログにあるモデルごとの、コンパイル済みの SQL。

    テーブルの名前は `alias` (`config(alias=...)`)、無ければモデルの名前。
    Python のモデルの compiled_code は Python のコードなので含めない。
    """
    found: dict[Key, str] = {}
    for node in (manifest.get("nodes") or {}).values():
        if not isinstance(node, dict) or node.get("resource_type") != "model":
            continue
        if node.get("language", "sql") != "sql":
            continue
        code = node.get("compiled_code")
        key = (str(node.get("schema", "")), str(node.get("alias") or node.get("name", "")))
        if isinstance(code, str) and code.strip() and key in live:
            found[key] = code.strip()
    return found


_ENV_VAR = re.compile(
    r"""\{\{\s*env_var\(\s*(['"])(.+?)\1\s*(?:,\s*(['"])(.*?)\3\s*)?\)\s*\}\}"""
)


def _render(text: str, env: Mapping[str, str]) -> str | None:
    """``text`` の `{{ env_var('NAME', 'default') }}` を値に置き換える。値が決まらないときは None。"""
    unresolved = False

    def value(match: re.Match[str]) -> str:
        nonlocal unresolved
        name, default = match.group(2), match.group(4)
        if name in env:
            return env[name]
        if default is None:
            unresolved = True
            return ""
        return default

    rendered = _ENV_VAR.sub(value, text)
    return None if unresolved or "{" in rendered else rendered


_CATALOG_URL = re.compile(r"(https?://.+?/)(?:versions/[^/]+/)?ducklake\.duckdb")


def _repository(path: str, env: Mapping[str, str]) -> str | None:
    """ATTACH の path から、リポジトリの URL。

    main のカタログ (`ducklake:https://…/ducklake.duckdb`) と、バージョンのカタログ
    (`ducklake:https://…/versions/<ID>/ducklake.duckdb`) のどちらでもリポジトリの URL にする。
    """
    rendered = _render(path, env)
    if rendered is None:
        return None
    match = _CATALOG_URL.fullmatch(rendered.removeprefix("ducklake:"))
    return match.group(1) if match else None


def _target(profile: dict[str, Any], env: Mapping[str, str]) -> dict[str, Any] | None:
    """dbt が使うターゲット: DBT_TARGET、無ければプロファイルの `target` (既定は default)。"""
    name = env.get("DBT_TARGET") or _render(str(profile.get("target") or "default"), env)
    target = (profile.get("outputs") or {}).get(name)
    return target if isinstance(target, dict) else None


def catalogs(profiles: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, str]:
    """プロファイルが読むだけで ATTACH する他のデータセットの、エイリアス → リポジトリの URL。"""
    env = os.environ if env is None else env
    return {
        str(attach["alias"]): repository
        for name, profile in profiles.items()
        if name != "config" and isinstance(profile, dict)
        and (target := _target(profile, env)) is not None
        for attach in target.get("attach") or []
        if isinstance(attach, dict)
        and attach.get("alias")
        and (repository := _repository(str(attach.get("path", "")), env)) is not None
    }


def with_lineage(
    declared: dict[str, Any], sql: dict[Key, str], aliases: dict[str, str]
) -> dict[str, Any]:
    """記述の表に `sql` を、記述に `catalogs` を足す。書き手の値が勝つ。記述に無い表は足す。"""
    tables = declared.get("tables") or []
    written = declared.get("catalogs") or {}
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
        # 書き手の catalogs が対応表でないときはそのまま渡し、fdl commit に断らせる
        **({"catalogs": {**aliases, **written}} if aliases and isinstance(written, dict) else {}),
        "tables": [*map(enrich, tables), *added],
    }


def unlisted(declared: dict[str, Any], live: set[Key]) -> dict[str, Any]:
    """記述に無いテーブルとビューを、Queria に掲載しないものとして足す。書き手の値が勝つ。"""
    tables = declared.get("tables") or []
    written = {
        (t.get("schema"), t.get("name"))
        for t in tables
        if isinstance(t, dict) and set(t) - {"schema", "name", "sql"}
    }

    def hide(table: Any) -> Any:
        if not isinstance(table, dict) or (table.get("schema"), table.get("name")) in written:
            return table
        return {**table, "annotations": {"queria": {"published": False}}}

    present = {(t.get("schema"), t.get("name")) for t in tables if isinstance(t, dict)}
    added = [{"schema": schema, "name": name} for schema, name in sorted(live - present)]
    return {**declared, "tables": [*map(hide, tables), *map(hide, added)]}


def stage(declared: dict[str, Any], root: Path, out: Path) -> Path:
    """fdl commit に渡す記述を ``out`` に書く。README.md と docs/ は ``root`` のものを指す。"""
    from fdl import dataset

    out.mkdir(parents=True, exist_ok=True)
    for name in ("README.md", "docs"):
        if (root / name).exists() and not (out / name).exists():
            (out / name).symlink_to((root / name).resolve())
    path = out / "dataset.json"
    path.write_bytes(dataset.to_json(declared))
    return path


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.splitlines()[2], file=sys.stderr)
        return 2
    working, manifest_path, profiles_path, out = map(Path, argv)
    import yaml  # dbt が依存に持つ
    from fdl import dataset  # fdl-cli はデータセットの依存に入っている

    description = dataset.find()
    manifest = (
        {} if str(manifest_path) == "-" else json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    profiles = (
        yaml.safe_load(profiles_path.read_text(encoding="utf-8")) if profiles_path.is_file() else {}
    )
    # catalogs を知らない fdl (0.21 まで) は、知らないキーとして記述ごと断る
    aliases = catalogs(profiles or {}) if "catalogs" in dataset.DATASET_KEYS else {}
    live = live_relations(working)
    declared = with_lineage(dataset.gather(description), compiled_sql(manifest, live), aliases)
    if os.environ.get("FDL_UNDESCRIBED") == "unlisted":
        declared = unlisted(declared, live)
    print(stage(declared, description.resolve().parent, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
