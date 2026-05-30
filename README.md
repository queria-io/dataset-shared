# dataset-shared

Queria データセットリポジトリの共通スクリプト。

各データセットリポが submodule として参照し、ビルド・デプロイの共通処理を提供する。

書き込みカタログは MotherDuck Managed DuckLake (BYOB R2)、Parquet は R2 に直書き、
Web 配信用には MotherDuck カタログを DuckDB ファイルとして R2 に snapshot する。

## アーキテクチャ

```
[各データセットの dbt パイプライン]
   ↓ ducklake extension で MotherDuck カタログに書き込み
   ↓ Parquet は R2 (BYOB) に直書き
[MotherDuck (DuckLake メタデータ)] + [R2 (Parquet, BYOB)]
   ↓ snapshot-to-r2.py: MotherDuck DuckLake → ローカル DuckDB → R2 アップロード
[R2: data.queria.io/<dataset>/ducklake.duckdb]
   ↓ ATTACH (read-only)
[Web (DuckDB WASM)]
```

ターゲット:

| target  | MotherDuck DB    | R2 バケット        | snapshot 公開先                                       |
|---------|------------------|--------------------|-------------------------------------------------------|
| local   | `dev_<dataset>`  | `queria-dev`       | https://pub-0292714ad4094bd0aaf8d36835b0972a.r2.dev   |
| default | `<dataset>`      | `queria-prod` 等   | https://data.queria.io                                |

## 使い方

各データセットリポで:

```bash
# submodule 追加（初回のみ）
git submodule add https://github.com/flo8s/dataset-shared.git shared

# ビルド
scripts/build.sh local
```

## 環境変数

```
MOTHERDUCK_TOKEN=<MotherDuck の service token>
QUERIA_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
QUERIA_S3_ACCESS_KEY_ID=<R2 access key>
QUERIA_S3_SECRET_ACCESS_KEY=<R2 secret>
QUERIA_S3_BUCKET=<本番バケット名>          # target=default 必須
QUERIA_DEV_S3_BUCKET=queria-dev            # target=local 用 (デフォルトあり)
QUERIA_DEV_PUBLIC_URL=https://pub-...      # target=local 用 (デフォルトあり)
QUERIA_PUBLIC_URL=https://data.queria.io   # target=default 用 (デフォルトあり)
```

## 新規データセットのセットアップ

1. データセットリポを作成し、本リポを `shared/` に submodule で追加
2. `pyproject.toml` に `[tool.queria]` セクションを書く（最低限 `name` が必須）

   ```toml
   [tool.queria]
   name = "<dataset>"
   title = "..."
   description = "..."
   ...
   ```

3. `profiles.yml` を MotherDuck DuckLake 接続で書く（dataset-zipcode を参考）
4. MotherDuck で本番 DB を作成:

   ```sql
   -- R2 認証情報を MotherDuck に登録（全データセットで 1 回だけ）
   CREATE SECRET r2_secret IN MOTHERDUCK (
       TYPE R2,
       KEY_ID '<R2 access key>',
       SECRET '<R2 secret>',
       ACCOUNT_ID '<Cloudflare account id>'
   );

   -- 本番 DB
   CREATE DATABASE <dataset> (TYPE DUCKLAKE, DATA_PATH 'r2://<prod-bucket>/<dataset>/ducklake.duckdb.files/');

   -- 開発 DB
   CREATE DATABASE dev_<dataset> (TYPE DUCKLAKE, DATA_PATH 'r2://queria-dev/<dataset>/ducklake.duckdb.files/');
   ```

   DATA_PATH には必ず `ducklake.duckdb.files/` を含めること。DuckLake は内部で
   `<schema>/<table>__dbt_tmp/<file>.parquet` を補完するので、DATA_PATH が
   `r2://<bucket>/<dataset>/` だと Parquet 配置と噛み合わず 404 になる。

5. `scripts/build.sh default` でビルド・公開

## 既知の制約: snapshot は dbt build と同一 Python セッション内のみ

`__ducklake_metadata_<db>` を外部 DuckDB クライアントから `ATTACH` する操作は、
**同一 Python プロセス内で `md:<db>` を touch した直後（dbt build 直後）にしか動かない**。
別 process から `ATTACH 'md:__ducklake_metadata_<db>' AS x` を試すと
"Catalog 'x' has been deleted" を返す（MotherDuck の internal metadata DB は
ephemeral 扱いで、`md:<db>` の通常 attach の副作用としてのみ activate される）。

そのため `snapshot-to-r2.py` は `main.py` の dbt build 直後に同じ Python process
から呼び出す必要がある（`build-dataset.sh` から別 process で呼ぶと失敗）。
`main.py` は次のパターンに従う:

```python
from dbt.cli.main import dbtRunner
dbt = dbtRunner()
dbt.invoke(["build", "--target", target])
# 直後、同 process で snapshot
snapshot_to_r2.run(target)
```

## 提供マクロ

`macros/` に共通の dbt マクロを配置。各データセットの `dbt_project.yml` で参照する:

```yaml
macro-paths: ["macros", "shared/macros"]
```

- `macros/catalog.sql`: dbt-duckdb の `duckdb__get_catalog` オーバーライド。全アタッチ DB を対象にする修正
- `macros/generate_schema_name.sql`: サブディレクトリ名をスキーマ名として使用

## 提供スクリプト

- `scripts/build-dataset.sh`: データセットのビルド + artifacts push + snapshot + catalog 自動リビルド
- `scripts/queria_config.py`: target → MotherDuck DB / R2 バケット / 公開 URL を解決
- `scripts/snapshot-to-r2.py`: MotherDuck DuckLake → ローカル DuckDB → R2 へアップロード
- `scripts/upload_artifacts.py`: dbt artifacts (manifest.json 等) + `[tool.queria]` から生成した meta.json を R2 にアップロード
- `scripts/checkpoint.py`: DuckLake snapshot expire + orphan 削除メンテ
- `scripts/ducklake_helper.py`: dbt 外で MotherDuck DuckLake に直接接続する duckdb context manager (ingest 用)
