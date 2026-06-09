"""Upload dbt artifacts for catalog consumption.

Supports both S3 (default target) and local storage.
Usage:
    python scripts/upload_artifacts.py [target]
    # target: "default" for S3, "local" for local storage
"""

from __future__ import annotations

import os
import shutil
import sys
import tomllib
from pathlib import Path

TARGET_DIR = Path.cwd() / "target"
FDL_TOML = Path.cwd() / "fdl.toml"

ARTIFACTS = ["manifest.json", "catalog.json", "semantic_manifest.json"]


def upload_s3(datasource: str) -> None:
    import boto3

    bucket = os.environ["FDL_S3_BUCKET"]
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["FDL_S3_ENDPOINT"],
        aws_access_key_id=os.environ["FDL_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["FDL_S3_SECRET_ACCESS_KEY"],
    )

    for name in ARTIFACTS:
        src = TARGET_DIR / name
        if not src.exists():
            print(f"  {name}: not found, skipping")
            continue
        key = f"{datasource}/dbt/{name}"
        client.upload_file(
            str(src),
            bucket,
            key,
            ExtraArgs={"ContentType": "application/json; charset=utf-8"},
        )
        print(f"  s3://{bucket}/{key}")


def upload_local(datasource: str, base_dir: Path) -> None:
    dest = base_dir / datasource / "dbt"
    dest.mkdir(parents=True, exist_ok=True)

    for name in ARTIFACTS:
        src = TARGET_DIR / name
        if not src.exists():
            print(f"  {name}: not found, skipping")
            continue
        shutil.copy2(src, dest / name)
        print(f"  {dest / name}")


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "default"

    with open(FDL_TOML, "rb") as f:
        config = tomllib.load(f)
    datasource = config["name"]
    target_config = config.get("targets", {}).get(target, {})
    url = target_config.get("url", "")

    if url.startswith("s3://"):
        upload_s3(datasource)
    else:
        base_dir = Path(os.path.expanduser(url))
        upload_local(datasource, base_dir)

    print(f"Uploaded artifacts for {datasource} ({target})")


if __name__ == "__main__":
    main()
