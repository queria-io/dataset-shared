"""Shared configuration for queria dataset build pipeline.

Resolves per-target settings (DuckLake catalog schema, R2 bucket, public URL)
from the dataset's pyproject.toml [tool.queria] section + environment variables.

The catalog backend is Neon PostgreSQL — one Neon database (NEON_DATABASE_URL)
holds DuckLake metadata for every dataset, isolated by Postgres schema
(META_SCHEMA). Schema naming:
    target=local   → dev_<dataset>
    target=default → <dataset>
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
    meta_schema: str  # Postgres schema where DuckLake metadata lives
    neon_dsn: str  # postgresql://... DSN for the shared Neon database
    s3_bucket: str
    s3_endpoint: str
    s3_access_key_id: str
    s3_secret_access_key: str
    cf_account_id: str
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
    def ducklake_uri(self) -> str:
        """ATTACH URI for the DuckLake catalog (postgres backend)."""
        return f"ducklake:postgres:{self.neon_dsn}"

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
        meta_schema = f"dev_{dataset}"
    else:
        bucket = _require_env("QUERIA_S3_BUCKET")
        public_url = os.environ.get("QUERIA_PUBLIC_URL", "https://data.queria.io")
        meta_schema = dataset

    return TargetConfig(
        name=target,
        dataset=dataset,
        meta_schema=meta_schema,
        neon_dsn=_require_env("NEON_DATABASE_URL"),
        s3_bucket=bucket,
        s3_endpoint=_require_env("QUERIA_S3_ENDPOINT"),
        s3_access_key_id=_require_env("QUERIA_S3_ACCESS_KEY_ID"),
        s3_secret_access_key=_require_env("QUERIA_S3_SECRET_ACCESS_KEY"),
        cf_account_id=_require_env("CF_ACCOUNT_ID"),
        public_url=public_url,
    )


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"environment variable {name} is required")
    return val
