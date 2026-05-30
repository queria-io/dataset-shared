"""Shared configuration for queria dataset build pipeline.

Resolves per-target settings (MotherDuck DB name, R2 bucket, public URL)
from the dataset's pyproject.toml [tool.queria] section + environment variables.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TargetConfig:
    name: str  # "local" or "default"
    dataset: str  # e.g. "zipcode"
    motherduck_db: str  # e.g. "dev_zipcode" or "zipcode"
    s3_bucket: str
    s3_endpoint: str
    s3_access_key_id: str
    s3_secret_access_key: str
    public_url: str  # base URL where snapshots are read from

    @property
    def data_path(self) -> str:
        """DuckLake DATA_PATH (R2 URL).

        Parquet is placed under <dataset>/ducklake.duckdb.files/<schema>/<table>/
        so the snapshot DuckDB and the Parquet sit side-by-side under the
        same dataset prefix (matches the existing queria-web URL convention).
        """
        return f"r2://{self.s3_bucket}/{self.dataset}/ducklake.duckdb.files/"

    @property
    def snapshot_key(self) -> str:
        """R2 object key for the snapshot DuckDB file."""
        return f"{self.dataset}/ducklake.duckdb"

    @property
    def snapshot_url(self) -> str:
        """Public HTTPS URL of the snapshot DuckDB file."""
        return f"{self.public_url.rstrip('/')}/{self.snapshot_key}"


def load_dataset_name(project_root: Path | None = None) -> str:
    """Read the dataset name from pyproject.toml [tool.queria].name."""
    root = project_root or Path.cwd()
    with open(root / "pyproject.toml", "rb") as f:
        cfg = tomllib.load(f)
    queria = cfg.get("tool", {}).get("queria", {})
    name = queria.get("name")
    if not name:
        raise RuntimeError("pyproject.toml [tool.queria].name is required")
    return name


def load_target(target: str, project_root: Path | None = None) -> TargetConfig:
    """Resolve TargetConfig for `local` or `default`."""
    if target not in ("local", "default"):
        raise ValueError(f"unknown target: {target!r} (expected 'local' or 'default')")

    dataset = load_dataset_name(project_root)

    if target == "local":
        bucket = os.environ.get("QUERIA_DEV_S3_BUCKET", "queria-dev")
        public_url = os.environ.get(
            "QUERIA_DEV_PUBLIC_URL",
            "https://pub-0292714ad4094bd0aaf8d36835b0972a.r2.dev",
        )
        md_db = f"dev_{dataset}"
    else:
        bucket = _require_env("QUERIA_S3_BUCKET")
        public_url = os.environ.get("QUERIA_PUBLIC_URL", "https://data.queria.io")
        md_db = dataset

    return TargetConfig(
        name=target,
        dataset=dataset,
        motherduck_db=md_db,
        s3_bucket=bucket,
        s3_endpoint=_require_env("QUERIA_S3_ENDPOINT"),
        s3_access_key_id=_require_env("QUERIA_S3_ACCESS_KEY_ID"),
        s3_secret_access_key=_require_env("QUERIA_S3_SECRET_ACCESS_KEY"),
        public_url=public_url,
    )


def require_motherduck_token() -> str:
    return _require_env("MOTHERDUCK_TOKEN")


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"environment variable {name} is required")
    return val
