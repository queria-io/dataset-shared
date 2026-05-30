#!/usr/bin/env bash
# Common build script for all dataset repositories.
# Called from each dataset's scripts/build.sh.
#
# Usage:
#   scripts/build.sh [target]
#   # target: "local" (default) or "default" (production)
#
# Required environment variables:
#   MOTHERDUCK_TOKEN
#   QUERIA_S3_BUCKET (default target only)
#   QUERIA_S3_ENDPOINT
#   QUERIA_S3_ACCESS_KEY_ID
#   QUERIA_S3_SECRET_ACCESS_KEY
set -euo pipefail
target="${1:-local}"
script_dir="$(cd "$(dirname "$0")" && pwd)"

# 1. dbt build + snapshot (in one Python process, see main.py)
#    dbt build writes models into MotherDuck DuckLake (metadata) + R2 (Parquet).
#    snapshot runs inline because `__ducklake_metadata_<db>` is only
#    attachable in the same process as the most recent md:<db> activity.
uv run python main.py "$target"

# 2. Upload dbt artifacts (manifest.json, catalog.json, semantic_manifest.json)
uv run python "$script_dir/upload_artifacts.py" "$target"

# 3. Rebuild catalog (local only)
if [ "$target" = "local" ]; then
    catalog_dir="$(pwd)/../dataset-catalog"
    if [ -d "$catalog_dir" ]; then
        echo "Rebuilding catalog..."
        (cd "$catalog_dir" && bash scripts/build.sh local)
    fi
fi
