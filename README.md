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

## dbt の認証情報

`scripts/build-dataset.sh` は dbt を回す前に、データセットの `profiles.yml` を一時ディレクトリへ写して `queria.dbt_plugin` を足し、`DBT_PROFILES_DIR` をそこへ向ける。DuckDB はプロファイルの S3 secret を作るときにしか認証情報を取らないので、15 分を超える dbt の実行はこのプラグインが取り直す。リポジトリの `profiles.yml` は書き換えない。入っている queria にこのモジュールが無いとき、または `DBT_PROFILES_DIR` を自分で指定しているときは何もしない（`scripts/with_dbt_plugin.py`）。

## データファイルの書き込み先（fdl のデータセット）

`scripts/build-fdl.sh` は、Queria のトークン（下の「Queria のトークン」）があれば Queria にデータセットの `data/` だけに書ける鍵（削除はできない）を頼みます（`scripts/storage_key.py`）。鍵が出れば `fdl checkout --storage` で、ビルドは新しいデータファイルを R2 に直接書きます。手元にファイルを置かないので、ディスクの小さいマシンでも大きいデータセットを作れます。鍵の有効期限は数分です。DuckDB の永続 secret を `PROVIDER credential_chain`（`REFRESH auto`）で作るので、期限が切れてストレージに断られると、DuckDB が `storage_key.py credential-process` で新しい鍵を取り直して続けます。鍵の値は環境変数にもログにも出ません。secret と AWS の設定ファイルは、ビルドが終わると消します。

鍵が出ないとき（トークンが無い、Queria が断った、fdl が古い）や `FDL_DIRECT=0` のときは、これまでどおり手元のフォルダに書いてコミットでアップロードします。

## 提供マクロ

`macros/` に共通の dbt マクロを配置。各データセットの `dbt_project.yml` で参照する:

```yaml
macro-paths: ["macros", "shared/macros"]
```

- `macros/catalog.sql`: dbt-duckdb の `duckdb__get_catalog` オーバーライド。全アタッチ DB を対象にする修正
- `macros/generate_schema_name.sql`: サブディレクトリ名をスキーマ名として使用
- `macros/local_government_code.sql`: 5 桁の市区町村コードに検査数字を付けて 6 桁の全国地方公共団体コードにする（`{{ local_government_code('city_code') }}`）

## 雛形

- `templates/profiles.yml`: 新しいデータセットの dbt プロファイル。DuckLake カタログの ATTACH（`DATA_INLINING_ROW_LIMIT 0` つき）、`credential_chain` の S3 シークレット、`QUERIA_USER_AGENT` の受け渡しまで入っている。既存のリポジトリを写すと古い形が混ざるので、ここから作る

```bash
sed 's/DATASET_NAME/<name>/g' shared/templates/profiles.yml > profiles.yml
```

## 提供スクリプト

- `scripts/build-dataset.sh`: `queria sync`（pull → ビルド → push）を回す
- `scripts/build-fdl.sh`: `fdl checkout` → ビルド → `fdl commit` を回す。ビルドが dbt の `target/manifest.json` を書いたときは、モデルの SQL（`sql`）と、profiles.yml が読むだけで ATTACH する公開データセットのエイリアス（`catalogs`）を記述に追加してから commit する（`scripts/dbt_lineage.py`）。依存先（`depends_on`）は fdl commit が SQL から作る。記述に書いた値が優先される

## 取得の共通部品

`fetch.py` は、データセットの取得処理（`main.py` や `pipelines/*.py`）で使う部品。標準ライブラリだけで書いてある。各データセットリポからは submodule の置き場所経由で読む。

```python
from shared.fetch import RangeFile, RangeNotSupported, download, fetch, replacing, write_atomic

body = fetch(url, user_agent=USER_AGENT, not_found_ok=True)  # 404 は None
write_atomic(Path("data/csv/01.csv"), body)

with replacing(Path("data/places/place.ndjson")) as partial:
    rows = write_rows(partial)
    if rows == 0:
        raise RuntimeError("no rows")  # 前回の place.ndjson は残る
```

- `fetch` / `content_length`: 5xx・429・接続断・タイムアウト・本文の途中切れ・TLS の失敗を、5 / 10 / 20 / 40 秒の間隔で4回まで再試行する。それ以外の 4xx はすぐ上げる。`timeout` / `retries` / `backoff` で変えられる
- `RangeFile`: HTTP の Range で読むシーク可能なファイル。`zipfile.ZipFile(io.BufferedReader(RangeFile(url), 1 << 16))` のように渡すと、大きな zip の目次と必要なメンバーだけを読む。Range が効かずに 200 で全体が返ってきたら `RangeNotSupported` で止める。同じ配信元でも、要求を送る場所（手元と GitHub のランナー）で Range を無視することがあるので、受けた側は `download` に切り替える
- `download`: 本文を少しずつファイルに書く。途中で切れたら最初から取り直し、書き終わるまでは置き換えない
- `replacing` / `write_atomic`: 一時ファイル（`<名前>.part`）に書いてから置き換える。途中で失敗したときと、`with` の中で例外を投げたとき（0件だった、など）は前回の出力を残す。取得済みのファイルを `exists()` で飛ばすデータセットでは、書きかけのファイルが取得済みとして残らない

データセットに新しく取得処理を書くときは、`urlopen` を直接呼ばずにこれを使う。テストは `uv run --no-project --python 3.13 python -m unittest discover -s tests` で流す。

### submodule の版を上げる

各データセットリポは `shared` のコミットと、呼び出すワークフロー（`uses: queria-io/dataset-shared/.github/workflows/<name>@<SHA>`）を同じ SHA に固定している。ここを直しても、各リポで固定を上げる PR をマージするまでは効かない。dataset-shared の main に誤って入った変更が、全データセットのシークレットと書き込み権を持って一度に動かないようにするため。上げる PR では dataset-shared の差分を確認する。

```bash
shared/scripts/pin-shared.sh
git commit -m "chore: dataset-shared を更新する"
```

`shared` が `pin-shared.sh` を含まない古いコミットを指しているリポでは、初回だけ `git -C shared fetch origin && git -C shared checkout origin/main` で submodule を先に更新してから実行する。

## Queria のトークン（fdl のデータセット）

GitHub Actions のジョブに `id-token: write` があると、`scripts/build-fdl.sh` はリポジトリのシークレットを使わず、ジョブの OIDC トークンを Queria で 1 時間の API トークンに交換して書きます（`scripts/queria_token.py`）。fdl は credential helper として、`storage_key.py` は鍵を頼む前に、トークンが要るたびにこれを呼びます。残りが 10 分を切ると交換し直すので、1 時間を超えるビルドも止まりません。交換できるのは Queria に登録したリポジトリ・ワークフロー・ブランチ（dataset-shared の Sync を呼ぶ、各リポの main のジョブ）だけです。ワークフローを SHA で固定した呼び出しも、パスが同じなら交換できます。どの SHA を動かすかは各リポの main が決めるので、固定を上げる PR では dataset-shared の差分を確認します。

呼び出し側の Sync に `permissions` を書きます。書かないジョブと、Queria が交換を断ったときは、これまでどおり `QUERIA_TOKEN` で書きます。

```yaml
jobs:
  sync:
    permissions:
      contents: read
      id-token: write
    uses: queria-io/dataset-shared/.github/workflows/sync.yml@<shared の SHA>
    secrets:
      QUERIA_TOKEN: ${{ secrets.QUERIA_TOKEN }}
      DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
```

Queria の照合は、SHA で固定したワークフローもパスが登録と同じなら通す。

`secrets: inherit` は使わない。inherit はリポジトリのシークレットを全部渡すので、使うものだけを明示的に渡す。取得元の API キー（`ESTAT_API_KEY`・`REINFOLIB_API_KEY`・`EDINET_API_KEY`）は、使うデータセットだけが追加する。

手元で流すときは `FDL_TOKEN` か `queria login` を使います。

## 共通ワークフロー

`.github/workflows/sync.yml` と `.github/workflows/checkpoint.yml` を各データセットリポが
`uses: queria-io/dataset-shared/.github/workflows/<name>@<SHA>` で呼ぶ。SHA は submodule と同じコミットで、
`shared/scripts/pin-shared.sh` が両方を上げる。

Sync を打ち切るまでの分数は `timeout_minutes` で決まる。既定は 90 分で、数えるのは
job が走り出してからの時間（`concurrency` の待ち時間は入らない）。これを超える
データセットは自分の実測値を呼び出し側から渡す。

```yaml
jobs:
  sync:
    uses: queria-io/dataset-shared/.github/workflows/sync.yml@<shared の SHA>
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

fdl で上げるデータセットは、新しいデータファイルを手元に書いてから `fdl commit` で上げるので、
1 回のビルドが書く量だけランナーのディスクが要る。
書く量がランナーの空きに近いデータセットは `free_disk: true` を渡すと、ビルドの前に使わないツール
（.NET・Android SDK・GHC・CodeQL）を消して空ける。前後の空きはログに出る。

### 手元で流す作業を Issue に残す

`.github/workflows/local-run.yml` は、GitHub ホステッドのランナーでは取り込めない更新
（取得元が日本国外の IP を弾く、メモリが足りない など）を見つけたときに呼ぶ。
手元で `scripts/build.sh` を流す作業を、organization の変数 `LOCAL_RUN_ISSUE_REPO`
（`owner/name`）のリポジトリに Issue として立てる。同じデータセットの Issue が開いていれば
コメントを足す。トークンは secret の `LOCAL_RUN_ISSUE_TOKEN`（そのリポジトリの Issues だけを
書ける fine-grained token）で、変数かトークンが無いときは何もしない。

```yaml
jobs:
  check:
    runs-on: ubuntu-latest
    outputs:
      reason: ${{ steps.check.outputs.reason }}
    steps:
      - id: check
        run: echo "reason=..." >> "$GITHUB_OUTPUT"  # 手元で流すときだけ空でない値を出す
  local-run:
    needs: check
    if: needs.check.outputs.reason != ''
    uses: queria-io/dataset-shared/.github/workflows/local-run.yml@<shared の SHA>
    with:
      reason: ${{ needs.check.outputs.reason }}
    secrets:
      LOCAL_RUN_ISSUE_TOKEN: ${{ secrets.LOCAL_RUN_ISSUE_TOKEN }}
```

## 層の切り方

出荷物の本体は Silver に置く。型が揃い名寄せ済みで、用途に依存しない粒度のもの。
多くは `models/*/mart` に置く。`models/*/raw` は取得したままの形を保つ Bronze で、
提供元から取り直さずに作り直せることだけを担い、そのままは出荷しない。
`models/*/stg` は型付けと名寄せの途中を置く場所だが、そこで Silver の条件を
満たしたものは出荷してよい（dataset-edinet の `stg_financial_facts`）。

出荷されるのは、ビルドがカタログに書いた表すべて。fdl はカタログの表を `dataset.json` に
全部載せる。Queria の画面と検索に出さない表は、`*.table.yml` に
`annotations: { queria: { published: false } }` と書く。層はモデルを読む側のための区切りで、
ディレクトリでは出荷も掲載も決まらない。

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

## 列名の語彙

列名は一般のデータなら schema.org、日本の公的データなら GIF に揃える。列名は
データ項目の語彙に、`semantic: { role: entity, name: ... }` の名前は実体の語彙に揃える。
語彙と決め方は [queria-io/vocabulary](https://github.com/queria-io/vocabulary) にある。

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

