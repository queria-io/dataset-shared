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

公開先は選ばない。データセットは `tabledoc.yml` の `name`、アカウントはトークンが決める（手元では `FDL_TOKEN` か `queria login`、GitHub Actions では OIDC で取るトークン）。
公開せずに 1 回転させたいときは queria-cli の `tools/rotate.py` をスタンドインに向けて回す
（fdl の `local` ターゲットに相当するものは無い。書き込みは Queria が発行する
一時認証情報を必ず経由する）。

## データファイルの書き込み先（fdl のデータセット）

`scripts/build-fdl.sh` は、新しいデータファイルを手元のフォルダに書き、コミットでアップロードします。

`FDL_DIRECT=1` を付けると、Queria のトークン（下の「Queria のトークン」）があれば Queria にデータセットのデータファイルの置き場（`fdl.json` の `data_dir`）だけに書ける鍵（削除はできない）を頼みます（`scripts/storage_key.py`）。鍵が出れば `fdl clone --storage` で、ビルドは新しいデータファイルを R2 に直接書きます。手元にファイルを置かないので、ディスクの小さいマシンでも大きいデータセットを作れます。鍵の有効期限は数分です。DuckDB の永続 secret を `PROVIDER credential_chain`（`REFRESH auto`）で作るので、期限が切れてストレージに断られると、DuckDB が `storage_key.py credential-process` で新しい鍵を取り直して続けます。鍵の値は環境変数にもログにも出ません。secret と AWS の設定ファイルは、ビルドが終わると消します。

既定で使わないのは、R2 に直接書くと dbt が数十倍遅くなり（geocoder の main.parcel は 918 秒から 3,310 秒）、1 時間を超えるビルドでは前の鍵で書いたファイルを読むときに 403 SignatureDoesNotMatch で落ちるためです（e_stat）。鍵が出ないとき（トークンが無い、Queria が断った）も手元のフォルダに書きます。

## 提供マクロ

`macros/` に共通の dbt マクロを配置。各データセットの `dbt_project.yml` で参照する:

```yaml
macro-paths: ["macros", "shared/macros"]
```

- `macros/catalog.sql`: dbt-duckdb の `duckdb__get_catalog` オーバーライド。全アタッチ DB を対象にする修正
- `macros/generate_schema_name.sql`: サブディレクトリ名をスキーマ名として使用
- `macros/local_government_code.sql`: 5 桁の市区町村コードに検査数字を付けて 6 桁の全国地方公共団体コードにする（`{{ local_government_code('city_code') }}`）

## 雛形

- `templates/profiles.yml`: 新しいデータセットの dbt プロファイル。`fdl clone` が作る作業カタログ（`FDL_WORKING`）の ATTACH と書き込み先（`FDL_DATA_PATH`、`DATA_INLINING_ROW_LIMIT 0` つき）、`QUERIA_USER_AGENT` の受け渡し、公開中のデータファイルをトークン付きで読む plugin（`scripts/fdl_read.py`）まで入っている。dbt の外の Python で公開中のカタログを読むときは、ATTACH の前に `fdl_read.statements()` を同じ接続で実行する。既存のリポジトリを写すと古い形が混ざるので、ここから作る

```bash
sed 's/DATASET_NAME/<name>/g' shared/templates/profiles.yml > profiles.yml
```

## 提供スクリプト

- `scripts/build-fdl.sh`: `refs/heads/apply` ブランチで `fdl clone` → ビルド → `fdl commit` → `fdl push` を回し、apply を main に取り込む。apply ブランチにはこのコードから作ったバージョンだけが積まれる。main に前回のビルドより後の人の変更（`fdl clone` と `fdl push`、apply 以外からの変更リクエストのマージ）が無ければ `fdl merge` して main を進め、あれば apply からの変更リクエストを作って人が確認するまで main を変えない（`scripts/sync_branch.py`）。merge が衝突したときや main に push できないとき（承認必須のデータセット）も変更リクエストにする。apply は main を取り込まないので、main で行や説明を変えると以後のビルドが毎回変更リクエストになる。説明はこのリポジトリのファイルで変更し、データの誤りは元データの公開元に報告する。commit の前に `tabledoc load`（[tabledoc](https://github.com/queria-io/tabledoc)）が説明を検査し、作業カタログの `_queria` スキーマのテーブル（普通の DuckLake のテーブル）に書き直す。説明と README.md・テーブルのガイドなどのファイルは、データと同じバージョンに入る。主キー・asset 列・ビューが読む別のデータセットと、`_queria` のテーブルや `FDL_UNDESCRIBED=unlisted` で説明の無いテーブルにプロファイルを作らないことは、tabledoc が `fdl declare` で宣言する。それより前のバージョンの説明を持つ `_meta` スキーマは、load の後に削除する。あわせて `fdl commit --assets` に渡すファイル（README.md・テーブルのガイド・ドキュメントと、`FDL_ASSETS` のファイルへのリンク）を `.fdl/` の外の一時フォルダに書き出す。`.fdl/` の下には fdl だけが書く。ビルドが dbt の `target/manifest.json` を書いたときは、モデルの SQL（`sql`）と依存先（`depends_on`）、profiles.yml が読むだけで ATTACH する公開データセットのエイリアス（`catalogs`）も tabledoc が入れる。記述に書いた値が優先される。main への push で、前回のビルドから説明のファイル（tabledoc.yml・Markdown・docs/、まだ移していないリポジトリでは queria.yml・dataset.yml・*.table.yml）と `.github/`（ワークフローは打ち切り時間の変更だけ）しか変わっていないときは、ビルドを実行せず、apply のデータのまま新しい説明でバージョンを作る（SQL と依存先は apply の先頭の `_queria` から引き継ぐ。apply の先頭が `_queria` を持たないバージョン（それより前の `_meta` だけを持つバージョンを含む）のときと、そのファイルを読めないときはビルドする）。説明も変わっていなければバージョンを作らない。`shared` の submodule を上げたときはビルドする。どのコミットのコードでデータを作ったかは、各バージョンのメッセージの `Built-From: <SHA>` に記録する（`scripts/built_from.py`）。スケジュールと手動の実行は常にビルドする

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

## PDF の本文

`pdf_text.py` は、PDF からページごとの本文を取り出す部品。PyMuPDF を使うので、読むデータセットは `pyproject.toml` に `pymupdf` を追加する。

```python
from shared.pdf_text import load_cid_map, pdf_pages

cid_map = load_cid_map(USER_AGENT)
pages = pdf_pages(Path("data/pdf/a.pdf").read_bytes(), cid_map)  # Page(page, label, text, cid_repaired_chars)
```

- 本文は NFKC で正規化する
- 文字コードの対応表（ToUnicode）が無いフォントでは、PyMuPDF が文字の代わりに Adobe-Japan1 の CID を返す。ページの中でその文字の 5% 超が普段の日本語の文書に出ない文字コードになるフォントでは、U+3000 より前の文字を CID とみなし、Adobe の [Adobe-Japan1-UCS2](https://github.com/adobe-type-tools/mapping-resources-pdf)（コミット固定）で文字に戻す。戻した文字の数は `cid_repaired_chars`
- テストは CI の test.yml で他のテストと一緒に流す（手元では `--with pymupdf` を付ける）

## 行政事業レビューが挙げる計画・通知等

`review_plans.py` は、行政事業レビュー見える化サイトのシート（1-3 政策・施策、法令等、2024 年度から）が「関係する計画・通知等」に挙げる URL のうち、指定したサイトの PDF を取得して、ページごとの本文の Parquet にする部品。省庁のデータセットが、自分のサイトの文書だけを取り込むのに使う。`pymupdf` と `duckdb` を使う。URL は、シートをテーブルにした Queria の公開データセット `queria/rssystem` の `review.related_plans` から読む（`QUERIA_USER_AGENT` があればその名前で読む）。

```python
from shared.review_plans import build_plans_parquet, download_plans

download_plans({"www.mlit.go.jp"}, user_agent=USER_AGENT, pdf_dir=Path("data/plans"))
build_plans_parquet(user_agent=USER_AGENT, pdf_dir=Path("data/plans"), parquet_dir=Path("data/parquet"))
```

- 出力は `plans_documents.parquet`（url・sha256・title・bytes・page_count・fetched_url・fetched_at、1 行がシートの 1 URL）と `plans_pages.parquet`（sha256・page・page_label・text・cid_repaired_chars）
- PDF は 1 秒に 1 回の頻度で取得する。404 などのクライアントエラーと PDF ではない応答は飛ばし、5xx は止める。開けない PDF（壊れている・パスワードが要る）も飛ばす
- 手元では取得済みの URL を `pdf_dir/index.json` で覚えて取り直さない
- 同じ URL に複数の名前があるときは、新しい年度のシートで最も多く書かれている名前を使う
- サイトが移転してシートの URL が転送のお知らせになっているときは、`moved`（シートの URL の先頭 → 移転先の URL の先頭の辞書）を渡す。移転先が `hosts` のサイトになる URL も集め、移転先から取得する。`url` はシートの URL のまま、取得した URL は `fetched_url` に入る
- 通常の HTTP クライアントでは取得できないサイトは、`fetch`（URL と保存先を受け、`fetch_file` と同じ形の行を返す関数。取得できない URL は `None`）を渡す。省くと `fetch_plan(user_agent)` で、`shared/fetch.py` の `fetch_file` を使う

### submodule の版を上げる

各データセットリポは `shared` のコミットと、呼び出すワークフロー（`uses: queria-io/dataset-shared/.github/workflows/<name>@<SHA>`）を同じ SHA に固定している。ここを直しても、各リポで固定を上げる PR をマージするまでは効かない。dataset-shared の main に誤って入った変更が、全データセットのシークレットと書き込み権を持って一度に動かないようにするため。上げる PR では dataset-shared の差分を確認する。

```bash
shared/scripts/pin-shared.sh
git commit -m "chore: dataset-shared を更新する"
```

`shared` が `pin-shared.sh` を含まない古いコミットを指しているリポでは、初回だけ `git -C shared fetch origin && git -C shared checkout origin/main` で submodule を先に更新してから実行する。

## Queria のトークン（fdl のデータセット）

GitHub Actions のジョブに `id-token: write` があると、`scripts/build-fdl.sh` はリポジトリのシークレットを使わず、ジョブの OIDC トークンを Queria で 1 時間の API トークンに交換して書きます（`scripts/queria_token.py`）。fdl は credential helper として、`storage_key.py` は鍵を頼む前に、トークンが要るたびにこれを呼びます。残りが 10 分を切ると交換し直すので、1 時間を超えるビルドも止まりません。交換できるのは Queria に登録したリポジトリ・ワークフロー・ブランチ（dataset-shared の Apply を呼ぶ、各リポの main のジョブ）だけです。ワークフローを SHA で固定した呼び出しも、パスが同じなら交換できます。どの SHA を動かすかは各リポの main が決めるので、固定を上げる PR では dataset-shared の差分を確認します。

呼び出し側の Apply に `permissions` を書きます。Queria のトークンはシークレットで渡しません。Queria が交換を断ったときは、ビルドが止まります。

```yaml
name: Apply

jobs:
  sync:
    permissions:
      contents: read
      id-token: write
    uses: queria-io/dataset-shared/.github/workflows/sync.yml@<shared の SHA>
    secrets:
      DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
```

Queria の照合は、SHA で固定したワークフローもパスが登録と同じなら通す。

`secrets: inherit` は使わない。inherit はリポジトリのシークレットを全部渡すので、使うものだけを明示的に渡す。取得元の API キー（`ESTAT_API_KEY`・`REINFOLIB_API_KEY`・`EDINET_API_KEY`・`GBIZINFO_API_TOKEN`・`HOUJIN_BANGOU_APP_ID`）は、使うデータセットだけが追加する。

手元で流すときは `FDL_TOKEN` か `queria login` を使います。

## 共通ワークフロー

`.github/workflows/sync.yml` を各データセットリポが
`uses: queria-io/dataset-shared/.github/workflows/sync.yml@<SHA>` で呼ぶ。SHA は submodule と同じコミットで、
`shared/scripts/pin-shared.sh` が両方を上げる。

Apply を打ち切るまでの分数は `timeout_minutes` で決まる。既定は 90 分で、数えるのは
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

月次は全件、日次は差分のように、同じデータセットをモードを変えて作るリポは、日次のワークフローからも
共通の Apply を呼び、`mode` を渡す。値は `SYNC_MODE` としてビルドに渡り、意味は各リポの `main.py` が決める。

```yaml
jobs:
  sync:
    uses: queria-io/dataset-shared/.github/workflows/sync.yml@<shared の SHA>
    with:
      mode: incremental
```

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
どのデータセットも、出荷する表を `raw` → `stg` → `mart` の 3 層で作る。変換が薄くても層を省かない。

- `models/*/raw`: 取得したままの形を保つ Bronze。列名と値を変えず、型は文字列のままでよい。
  提供元から取り直さずに作り直せることだけを担い、出荷しない。取得ファイルを読むのはこの層だけ
- `models/*/stg`: raw を 1 つ読んで、型付け・列名の付け替え・コードや表記の正規化・名寄せをする。
  raw のほかは読まない
- `models/*/mart`: stg だけを読み、結合・縦持ちと横持ちの変換・粒度の決定をする。出荷する表はここに置く

`pipelines/` の Python は取得と、Excel・PDF・GML などを表に読める形へ変えるところまでを担う。
縦持ちへの変換、列名の付け替え、コードの変換など中身の加工は dbt の stg・mart で行う。
手で管理する参照表（`reference/` の CSV など）は、stg か mart から読んでよい。
他のデータセットの表を読むときは、その表を raw とみなし、stg で読む。

出荷されるのは、ビルドがカタログに書いた表すべて。fdl はカタログの表を
全部バージョンに入れる。Queria の画面と検索に出さない表は、テーブルの Markdown のフロントマターに
`published: false` と書く。層はモデルを読む側のための区切りで、
ディレクトリでは出荷も掲載も決まらない。

用途別の集計はデータセットに入れない。ショーケース用のデモデータセットへ寄せる。
Queria が配るのは集計の結論ではなく、利用者が自分の用途で組み立てられる素材のため。
Gold にあたる層をデータセット側に持つと、用途の数だけテーブルが増え、
どれが素材でどれが誰かの解釈なのかが利用者から見分けられなくなる。

`scripts/build-fdl.sh` はビルドの前に `scripts/check_models.py` を実行し、次のどれかに当たるモデルがあると止まる。

- `raw`・`stg`・`mart` のどのディレクトリにも無い
- `raw` 以外でファイルを読む（`read_parquet`・`read_csv`・`st_read` など）。`reference/` の下のファイルは除く
- `stg` が `raw` 以外を、`mart` が `stg`・`mart` 以外を、`raw` が `stg`・`mart` を `ref` で読む

### モデルを消したとき

ビルドは前のバージョンのカタログから始まるため、モデルを消す・改名する・`ephemeral` に変えると、
前のテーブルがカタログに残る。`scripts/build-fdl.sh` はビルドのあとに `scripts/drop_stale.py` を実行し、
dbt の `target/manifest.json` にあるモデル・seed・snapshot（`ephemeral` を除く）のどれにも当たらない
テーブルとビューをカタログから削除して、削除したものをログに出す。次のスキーマのテーブルは削除しない。

- dbt の source が読むスキーマ（パイプラインが取り込む `_source` など）
- 名前が `_` で始まるスキーマ（dlt の `_source_staging` など）

パイプラインがテーブルを書くときは、このどちらかのスキーマに書く。

メダリオン（Bronze / Silver / Gold）と dbt のレイヤ（staging / intermediate / marts）は
同じものの別名ではなく、直交する枠組み。メダリオンはストレージとデータフローの区切りで
各層が実体を持ち、dbt のレイヤはモデル責務の区切りで ephemeral にすれば実体を持たない。
層を数えるとき、前者は保管と再処理の単位を、後者はコードの保守単位を数えている。
上の `raw` / `stg` / `mart` はディレクトリの区切りであって、materialization は
データセットごとに違ってよい。

## 冪等に作る

同じ入力から何度ビルドしても、同じテーブルができるように作る。途中で止まったビルドは、そのまま流し直せば続きから正しく作れる状態にする。

- 取得のスクリプトは、書き出すファイルを毎回上書きする。前回のファイルに追記しない。
  変わっていないファイルの取得を飛ばすのはよい（`shared.fetch.fetch_file`）
- 前回の状態を手元のファイルに持たない。取得を始める位置などが要るときは、公開中のバージョン（`FDL_WORKING`）から読む
- dbt のモデルは毎回作り直す（`table`・`view`・`ephemeral`）

### 差分更新

全件の作り直しが現実的でないときだけ、差分更新にする。現実的でないのは次のようなとき。差分更新にする理由をリポジトリの README に書く。

- 1 回の取得とビルドが、ワークフローの実行時間の上限に収まらない
- 配布元のシステムの都合で、毎回全件を取得できない。API のリクエスト数や頻度の制限がある、利用規約が大量の連続アクセスを禁じている、
  全件の取得が配布元のサーバーに負荷をかける、配布元が直近の分しか置いていない、など

差分更新も冪等に作る。

- `incremental` のモデルには `unique_key` を付けて `delete+insert` にするか、`append` なら `is_incremental()` の中で既にある行を除く。
  同じ期間を流し直しても、行が重複したり欠けたりしないようにする。`scripts/check_models.py` はどちらも無いモデルで止まる。
  `materialized='incremental'` はモデルの `config()` に書く。`dbt_project.yml` や `.yml` に書くと検査が読めないので、`scripts/check_models.py` が止まる
- 遅れて登録される行や訂正を拾うために直近の期間を取り直すときは、`delete+insert` で置き換える。
  行の中身が後から変わらない前提で `append` にするときは、その前提をモデルのコメントに書く
- 公開中のバージョンにテーブルが無いときは、最初から全件を取得して作れるようにする。作り直しは初回と同じ手順で行う
- 前回の最後の日付から続きを取るときは、途中で欠けた日が次の実行で埋まるようにする

## 出典の URL 列

1 行が元のサイトの 1 ページ（条文・発言・議案・レビューシートなど）に対応する表には、
その行を元のサイトで開く URL の列を持たせる。利用者や AI がその行の値を元の文書で
確認できるようにするため。

- 列名は `url`、1 行に複数あるときは `speech_url`・`text_url` のように `*_url` にする
- ページの中の位置が分かるときはアンカーまで付ける（例 e-Gov 法令検索の条の id）
- 元のサイトに行ごとのページが無いときは、その行だけに絞った検索結果の URL にする
- URL を推測で組み立てない。元のサイトで開けることを確かめた形だけを使う。
  アンカーが決まらない行はページの URL まで、ページが決まらない行は NULL にする
- テーブルの説明に、その URL で何が開くかを書く

## 元のファイル

Excel・CSV・PDF・ZIP など、配布元がファイルで配るデータは、取得したファイルの一覧を `source_file` テーブルにして公開し、
各行に元のファイルの `source_sha256` を持たせる。どの行をどのファイルから、いつ、どこから取得したかを確認できるようにするため。
列の形は [docs.queria.io/publish/conventions](https://docs.queria.io/publish/conventions#元のファイル) にある。

ファイル本体は、URL から取得し直せるものは保存しない。取得し直すのが難しいもの（API キーが要る・1 件ずつ大量に取る・
掲載ページから外れる・同じ URL で差し替わる）だけを FDL のアセットとして入れる。再配布を認めていない配布元のファイルは入れない。

- アセットにするファイルは `data/assets/<sha256>.<拡張子>` に置く。アップロードするときのメディアタイプは拡張子から決まる
- アセットを指す列に、テーブルの Markdown のフロントマターで `asset: true` を付ける。付けないと `--assets` を渡しても何もアップロードされない。
  `fdl push` は、`asset: true` の列にリポジトリにも手元にも無い sha256 があると止まる。
  `source_file` の一部のファイルだけをアセットにするときは `sha256` 列には付けず、アセットにしたファイルの sha256 だけを入れて
  ほかは NULL にした列（`asset_sha256`）に付ける
- `scripts/build.sh` で `mkdir -p data/assets` と `export FDL_ASSETS="${FDL_ASSETS:-$PWD/data/assets}"` を書く。
  `fdl commit --assets` はフォルダが無いと止まるので、アセットにするファイルが無い回もフォルダを作る。
  `build-fdl.sh` が `fdl commit --assets` に渡し、`fdl push` でリポジトリに無いファイルだけがアップロードされる
- 前のバージョンのファイルを読み直すときは、アセットにしたファイルのうち手元に無いものをリポジトリの `assets/<sha256>` から取得し、
  sha256 を確認してから使う。アセットにしていないファイルは URL から取得し直す
- `source_file` も raw → stg → mart で作る。raw は取得の記録（Parquet）を読む
- 取得には `shared.fetch.fetch_file` を使う。`source_file` の 1 行（`url`・`sha256`・`bytes`・`media_type`・`retrieved_at`・`etag`・`last_modified`）を返し、
  前回の行を `previous` に渡すと条件付き GET で変わっていないファイルのダウンロードをスキップする
- 取得のときに応答の `ETag`・`Last-Modified` も記録する。次の取得で `ETag`・`Last-Modified`・サイズが前回と同じで、
  そのファイルが手元か（アセットなら）リポジトリにあるときだけダウンロードをスキップする。
  `ETag` と `Last-Modified` がどちらも無い応答は比較せずに取得する

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

メタデータは `tabledoc.yml` とテーブルの Markdown にだけ書く。dbt の `meta:` には書かない。
配布されるのは `tabledoc load` がカタログの `_queria` スキーマに書くテーブルで、その入力はこれらのファイルだけ。
ビルドと保守の手順は README.md ではなく CONTRIBUTING.md に書く（README.md はデータセットの概要として Queria に表示される）。

**`ai_context` は廃止する。** 書いても読む先が無いので埋める必要はない。既に書いてある
リポジトリは、`instructions` を各階層の説明へ、`synonyms` を `keywords` へ畳んで
外す。畳み終わってから `queria` の `DATASET_KEYS` とスキーマからも落とす。

