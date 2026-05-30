"""Upload dbt artifacts + meta.json to R2.

dbt artifacts (manifest.json, catalog.json, semantic_manifest.json)
are uploaded under <dataset>/dbt/. The meta.json is generated from
pyproject.toml [tool.queria] and uploaded to <dataset>/meta.json so
dataset-catalog can pick it up.

Usage:
    python scripts/upload_artifacts.py [target]
    # target: "local" (default) or "default" (production)
"""

from __future__ import annotations

import json
import sys
import tempfile
import tomllib
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queria_config import TargetConfig, load_target  # noqa: E402

TARGET_DIR = Path.cwd() / "target"
PYPROJECT = Path.cwd() / "pyproject.toml"
ARTIFACTS = ["manifest.json", "catalog.json", "semantic_manifest.json"]


def build_meta_json(target: TargetConfig) -> dict:
    """Convert pyproject.toml [tool.queria] into the meta.json layout
    that dataset-catalog's read_dataset_meta macro expects.
    """
    with open(PYPROJECT, "rb") as f:
        cfg = tomllib.load(f)
    q = cfg.get("tool", {}).get("queria", {})
    return {
        "datasource": q.get("name", target.dataset),
        "title": q.get("title", ""),
        "description": q.get("description", ""),
        "cover": q.get("cover", ""),
        "tags": q.get("tags", []),
        "repository_url": q.get("repository_url", ""),
        "schedule": q.get("schedule", ""),
        "license": q.get("license", ""),
        "license_url": q.get("license_url", ""),
        "source_url": q.get("source_url", ""),
        "ducklake_url": f"{target.public_url.rstrip('/')}/{target.dataset}/ducklake.duckdb",
        "schemas": q.get("schemas", {}),
    }


def upload(target: TargetConfig) -> None:
    client = boto3.client(
        "s3",
        endpoint_url=target.s3_endpoint,
        aws_access_key_id=target.s3_access_key_id,
        aws_secret_access_key=target.s3_secret_access_key,
    )

    for name in ARTIFACTS:
        src = TARGET_DIR / name
        if not src.exists():
            print(f"  {name}: not found, skipping")
            continue
        key = f"{target.dataset}/dbt/{name}"
        client.upload_file(
            str(src),
            target.s3_bucket,
            key,
            ExtraArgs={"ContentType": "application/json; charset=utf-8"},
        )
        print(f"  r2://{target.s3_bucket}/{key}")

    meta = build_meta_json(target)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        meta_path = f.name
    key = f"{target.dataset}/meta.json"
    client.upload_file(
        meta_path,
        target.s3_bucket,
        key,
        ExtraArgs={"ContentType": "application/json; charset=utf-8"},
    )
    print(f"  r2://{target.s3_bucket}/{key}")
    Path(meta_path).unlink()


def main() -> None:
    target_name = sys.argv[1] if len(sys.argv) > 1 else "default"
    target = load_target(target_name)
    upload(target)
    print(f"Uploaded artifacts for {target.dataset} ({target.name})")


if __name__ == "__main__":
    main()
