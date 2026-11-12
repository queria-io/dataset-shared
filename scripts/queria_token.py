"""Give a build a Queria API token without a long-lived secret.

Usage:
  python queria_token.py        print a token
  python queria_token.py get    print it as fdl's credential helper answers (password=<token>)

In GitHub Actions, when the job may ask for an OIDC token (`permissions:
id-token: write`), Queria exchanges that for an API token that lasts an hour
(POST /api/oidc/github/token). The token is kept in RUNNER_TEMP and used again
while more than ten minutes of it remain, so a build of any length asks again
only when it needs to. Queria gives one only to the repositories, workflows and
branches registered with it.

In such a job there is no other token: when the exchange fails, the build
stops. Elsewhere (a build on your own machine), FDL_TOKEN or QUERIA_TOKEN is used.

Exit codes: 0 printed a token; 1 none.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

#: The audience Queria accepts in the OIDC token.
AUDIENCE = "https://queria.io"
#: A kept token is used again while this many seconds of it remain.
MARGIN = 10 * 60


def _post(url: str, body: dict, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "queria-build/token", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def _github_oidc_token() -> str | None:
    """The job's OIDC token for Queria, or None outside a job allowed to ask."""
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    bearer = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        return None
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(
        f"{url}{sep}audience={urllib.parse.quote(AUDIENCE, safe='')}",
        headers={"Authorization": f"Bearer {bearer}", "User-Agent": "queria-build/token"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)["value"]


def _kept() -> Path:
    return Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / "queria-token.json"


def _read_kept(path: Path, now: float) -> str | None:
    try:
        kept = json.loads(path.read_text())
        expires = datetime.fromisoformat(kept["expires_at"].replace("Z", "+00:00")).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return kept["token"] if expires - now > MARGIN else None


def _keep(path: Path, answer: dict) -> None:
    path.touch(mode=0o600, exist_ok=True)
    path.write_text(json.dumps({"token": answer["token"], "expires_at": answer["expires_at"]}))


def exchanged() -> str | None:
    """An hour's token from Queria for this GitHub Actions job, or None."""
    path = _kept()
    kept = _read_kept(path, time.time())
    if kept:
        return kept
    oidc = _github_oidc_token()
    if oidc is None:
        return None
    base = os.environ.get("QUERIA_API_URL") or "https://queria.io"
    answer = _post(f"{base.rstrip('/')}/api/oidc/github/token", {"token": oidc})
    _keep(path, answer)
    return answer["token"]


def token() -> str | None:
    """The token to write with: the exchanged one in GitHub Actions, else FDL_TOKEN or QUERIA_TOKEN."""
    if not os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"):
        return os.environ.get("FDL_TOKEN") or os.environ.get("QUERIA_TOKEN") or None
    try:
        return exchanged()
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(f"queria token: Queria did not exchange the OIDC token ({exc})", file=sys.stderr)
        return None


def main(argv: list[str]) -> int:
    if argv not in ([], ["get"]):
        print(__doc__, file=sys.stderr)
        return 2
    found = token()
    if not found:
        print("queria token: none (no exchanged token, FDL_TOKEN or QUERIA_TOKEN)", file=sys.stderr)
        return 1
    print(f"password={found}" if argv == ["get"] else found)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
