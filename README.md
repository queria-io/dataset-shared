# dataset-shared

Queria データセットリポジトリの共通スクリプト。

各データセットリポが submodule として参照し、ビルド・デプロイの共通処理を提供する。

書き込みカタログは Neon Postgres (DuckLake metadata backend)、Parquet は R2 に直書き、
Web 配信用には Neon の dataset 別 schema を DuckDB ファイルとして R2 に snapshot する。

## アーキテクチャ

```
[各データセットの dbt パイプライン]
   ↓ ducklake extension で Neon Postgres カタログに書き込み (META_SCHEMA で dataset 分離)
   ↓ Parquet は R2 (BYOB) に直書き
[Neon Postgres (DuckLake メタデータ、9 schema)] + [R2 (Parquet, BYOB)]
   ↓ snapshot-to-r2.py: Neon の <dataset> schema → ローカル DuckDB の main schema → R2 アップ
[R2: data.queria.io/<dataset>/ducklake.duckdb]
   ↓ ATTACH (read-only)
[Web (DuckDB WASM)]
```

ターゲット:

| target  | Neon META_SCHEMA | R2 バケット     | snapshot 公開先                                       |
|---------|------------------|------------------|-------------------------------------------------------|
| local   | `dev_<dataset>`  | `queria-dev`     | https://pub-0292714ad4094bd0aaf8d36835b0972a.r2.dev   |
| default | `<dataset>`      | `queria-prod` 等 | https://data.queria.io                                |

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
NEON_DATABASE_URL=postgres://...@...neon.tech/neondb?sslmode=require
QUERIA_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
QUERIA_S3_ACCESS_KEY_ID=<R2 access key>
QUERIA_S3_SECRET_ACCESS_KEY=<R2 secret>
QUERIA_S3_BUCKET=<本番バケット名>          # target=default 必須
CF_ACCOUNT_ID=<Cloudflare account id>
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

3. `profiles.yml` を Neon DuckLake 接続で書く（dataset-zipcode を参考）
4. Neon 側で本番・開発の schema を 1 回だけ作成:

   ```sql
   CREATE SCHEMA <dataset>;
   CREATE SCHEMA dev_<dataset>;
   ```

   META_SCHEMA で物理的に分離するので、9 dataset 全部を 1 つの Neon DB
   (`neondb`) に同居させてよい。

5. `scripts/build.sh default` でビルド・公開

## 既存 (fdl 時代の R2 ducklake.duckdb) からの移行

R2 上の `<dataset>/ducklake.duckdb` の中身を Neon に一回だけ移送する:

```bash
uv run python shared/scripts/migrate-r2-to-neon.py default
```

これは `ducklake_*` メタテーブルをすべて Neon の `<dataset>` schema に
コピーし、`ducklake_data_file.path` は元のまま (Parquet 実体は移動しない)。

移行後は通常通り `scripts/build.sh` を実行できる。

## 提供マクロ

`macros/` に共通の dbt マクロを配置。各データセットの `dbt_project.yml` で参照する:

```yaml
macro-paths: ["macros", "shared/macros"]
```

- `macros/catalog.sql`: dbt-duckdb の `duckdb__get_catalog` オーバーライド。全アタッチ DB を対象にする修正
- `macros/generate_schema_name.sql`: サブディレクトリ名をスキーマ名として使用

## 提供スクリプト

- `scripts/build-dataset.sh`: データセットのビルド + artifacts push + snapshot + catalog 自動リビルド
- `scripts/queria_config.py`: target → Neon META_SCHEMA / R2 バケット / 公開 URL を解決
- `scripts/snapshot-to-r2.py`: Neon の dataset schema → ローカル DuckDB → R2 へアップロード
- `scripts/migrate-r2-to-neon.py`: R2 上の旧 `ducklake.duckdb` を Neon に移送 (一度きり)
- `scripts/upload_artifacts.py`: dbt artifacts (manifest.json 等) + `[tool.queria]` から生成した meta.json を R2 にアップロード
- `scripts/checkpoint.py`: DuckLake snapshot expire + orphan 削除メンテ
