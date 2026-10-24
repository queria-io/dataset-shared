"""データセットの dbt プロファイルに queria.dbt_plugin を足した写しを作る。

使い方: python with_dbt_plugin.py <元の profiles.yml> <写しの出力先>

DuckDB はプロファイルの S3 secret を作るときにしか認証情報を取らず、期限
(15 分) が来ても取り直さない。dbt の実行がそれより長いと、途中から 403 で
読み書きできなくなる。queria.dbt_plugin がモデルやテストを流すたびに取り直すので、
全データセットのビルドにここで入れる。リポジトリの profiles.yml は書き換えない。

終了コード:
  0  写しを書いた。DBT_PROFILES_DIR をその置き場所に向けてよい
  3  入っている queria に queria.dbt_plugin が無い。写しは作らない。元の
     profiles.yml のまま dbt を回す (プラグインを書くと dbt が起動時に落ちる)
  それ以外  このスクリプトが壊れている。黙ってプラグインなしに戻ると、15 分を
     過ぎてからの 403 でしか気付けないので、ビルドを止める
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

PLUGIN = "queria.dbt_plugin"

#: queria にプラグインが無いときの終了コード。Python が例外で終わったときの 1 と分ける。
#: build-dataset.sh の case がこの値で分岐する
NO_PLUGIN = 3


def plugin_available() -> bool:
    try:
        return importlib.util.find_spec(PLUGIN) is not None
    except ModuleNotFoundError:  # queria 自体が入っていない
        return False


def with_plugin(profiles: dict) -> dict:
    """各プロファイルの threads: 1 のターゲットに、無ければプラグインを足す。

    プラグインはカーソルを取るたびに認証情報を取り直すが、threads が 2 以上だと
    実行中のモデルのカーソルに割り込むので、そのターゲットには足さない。
    """
    for name, profile in profiles.items():
        # 最上位の config: はプロファイルではなく dbt 全体の設定
        if name == "config" or not isinstance(profile, dict):
            continue
        for target in (profile.get("outputs") or {}).values():
            if not isinstance(target, dict) or target.get("type") != "duckdb":
                continue
            threads = target.get("threads", 1)
            if str(threads) != "1":
                print(f"{PLUGIN} を足さない: {name} の threads が {threads}", file=sys.stderr)
                continue
            plugins = target.get("plugins") or []
            if not any(isinstance(p, dict) and p.get("module") == PLUGIN for p in plugins):
                plugins.append({"module": PLUGIN})
            target["plugins"] = plugins
    return profiles


def main(source: str, destination: str) -> int:
    if not plugin_available():
        return NO_PLUGIN
    profiles = yaml.safe_load(Path(source).read_text()) or {}
    Path(destination).write_text(
        yaml.safe_dump(with_plugin(profiles), allow_unicode=True, sort_keys=False)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
