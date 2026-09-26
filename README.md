# dataset-shared

Queria データセットリポジトリの共通スクリプト。

各データセットリポが submodule として参照し、ビルド・デプロイの共通処理を提供する。

## 使い方

各データセットリポで:

```bash
# submodule 追加（初回のみ）
git submodule add https://github.com/queria-io/dataset-shared.git shared

# ビルドして Queria に公開する
scripts/build.sh
```

公開先は選ばない。データセットは `dataset.yml`、アカウントは `QUERIA_TOKEN` が決める。
公開せずに 1 回転させたいときは queria-cli の `tools/rotate.py` をスタンドインに向けて回す
（fdl の `local` ターゲットに相当するものは無い。書き込みは Queria が発行する
一時認証情報を必ず経由する）。

## 提供マクロ

`macros/` に共通の dbt マクロを配置。各データセットの `dbt_project.yml` で参照する:

```yaml
macro-paths: ["macros", "shared/macros"]
```

- `macros/catalog.sql`: dbt-duckdb の `duckdb__get_catalog` オーバーライド。全アタッチ DB を対象にする修正
- `macros/generate_schema_name.sql`: サブディレクトリ名をスキーマ名として使用

## 提供スクリプト

- `scripts/build-dataset.sh`: `queria sync`（pull → ビルド → push）を回す

## 共通ワークフロー

`.github/workflows/sync.yml` と `.github/workflows/checkpoint.yml` を各データセットリポが
`uses: queria-io/dataset-shared/.github/workflows/<name>@main` で呼ぶ。`@main` なので
ここへのマージは全リポの次のランに効く。

Sync を打ち切るまでの分数は `timeout_minutes` で決まる。既定は 90 分で、数えるのは
job が走り出してからの時間（`concurrency` の待ち時間は入らない）。これを超える
データセットは自分の実測値を呼び出し側から渡す。

```yaml
jobs:
  sync:
    uses: queria-io/dataset-shared/.github/workflows/sync.yml@main
    with:
      timeout_minutes: 240
```

| データセット | 渡す値 | 成功ランの実測（最大） |
| --- | --- | --- |
| reinfolib | 180 | 131 分 |
| edinet | 240 | 147 分 |
| nlftp | 240 | 149 分 |
| e-stat | 300 | 210 分 |
| meti | 360 | 280 分 |

残りは既定の 90 分で足りる。実測の最大は metro_tokyo の 38 分と jma の 33 分で、
他の 14 リポは 5 分以内。

`meti` の 360 分は GitHub ホステッドランナーの上限で、それ以上は渡せない。2026-08 に
1 分から 280 分へ増えて以降 275〜280 分で並んでおり、上限まで 80 分しかない。次に
取り込み範囲を広げる前にビルドを分ける。

Checkpoint の上限は 180 分の固定値。実測の最大は nlftp の 74 分。

## 層の切り方

出荷物の本体は Silver に置く。型が揃い名寄せ済みで、用途に依存しない粒度のもの。
多くは `models/*/mart` に置く。`models/*/raw` は取得したままの形を保つ Bronze で、
提供元から取り直さずに作り直せることだけを担い、そのままは出荷しない。
`models/*/stg` は型付けと名寄せの途中を置く場所だが、そこで Silver の条件を
満たしたものは出荷してよい（dataset-edinet の `stg_financial_facts`）。

出荷するかどうかを決めるのはディレクトリではなく、`*.table.yml` の `published`。
層はモデルを読む側のための区切りで、配布の境界は宣言が持つ。

用途別の集計はデータセットに入れない。ショーケース用のデモデータセットへ寄せる。
Queria が配るのは集計の結論ではなく、利用者が自分の用途で組み立てられる素材のため。
Gold にあたる層をデータセット側に持つと、用途の数だけテーブルが増え、
どれが素材でどれが誰かの解釈なのかが利用者から見分けられなくなる。

メダリオン（Bronze / Silver / Gold）と dbt のレイヤ（staging / intermediate / marts）は
同じものの別名ではなく、直交する枠組み。メダリオンはストレージとデータフローの区切りで
各層が実体を持ち、dbt のレイヤはモデル責務の区切りで ephemeral にすれば実体を持たない。
層を数えるとき、前者は保管と再処理の単位を、後者はコードの保守単位を数えている。
上の `raw` / `stg` / `mart` はディレクトリの区切りであって、materialization は
データセットごとに違ってよい。

## 記述の規約

データセットについて分かったことを、カラム・テーブル・データセットのどの階層に
書くか、`keywords` に何を入れるかは
[docs.queria.io/publish/writing-descriptions](https://docs.queria.io/publish/writing-descriptions)
にある。ここには置かない。このリポジトリを submodule で参照するのは queria 自身の
データセットだけだが、規約はすべての発行者に等しく効くため。

クックブックの原稿だけは事情が違う。各リポジトリの `docs/*.md` に置いたものが
docs.queria.io のクックブックに取り込まれる。取り込みはカタログに登録済みの
リポジトリからしか行われないので、これは first-party 固有の話になる。

メタデータは `dataset.yml` と `*.table.yml` にだけ書く。dbt の `meta:` には書かない。
配布されるのは `queria compile` が出力する `dataset.json` で、その入力は宣言だけ。

**`ai_context` は廃止する。** 書いても読む先が無いので埋める必要はない。既に書いてある
リポジトリは、`instructions` を各階層の説明へ、`synonyms` を `keywords` へ畳んで
外す。畳み終わってから `queria` の `DATASET_KEYS` とスキーマからも落とす。

