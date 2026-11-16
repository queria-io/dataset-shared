"""行政事業レビューのシートが「関係する計画・通知等」に挙げる PDF を、配布元のサイトごとにページの本文にする共通部品。

各データセットリポからは submodule の置き場所（shared）経由で読む:

    from shared.review_plans import build_plans_parquet, download_plans

    HOSTS = {"www.mlit.go.jp"}
    download_plans(HOSTS, user_agent=USER_AGENT, pdf_dir=Path("data/plans"))
    # 移転したページは、シートの URL の先頭を移転先に置き換えて取得する
    download_plans(HOSTS, user_agent=USER_AGENT, pdf_dir=Path("data/plans"), moved={"https://old/": "https://new/"})
    # 取得の方法を変えるときは fetch に関数を渡す（url と保存先を受け、fetch_file と同じ形の行か None を返す）
    download_plans(HOSTS, user_agent=USER_AGENT, pdf_dir=Path("data/plans"), fetch=my_fetch)
    build_plans_parquet(user_agent=USER_AGENT, pdf_dir=Path("data/plans"), parquet_dir=Path("data/parquet"))

1. 行政事業レビュー見える化サイト（https://rssystem.go.jp）のシートをテーブルにした Queria の公開データセット
   queria/rssystem の review.related_plans から、計画・通知等の URL のうち hosts のサイトの PDF を集める。
   moved（シートの URL の先頭 → 移転先の URL の先頭）を渡すと、移転先が hosts のサイトになる URL も集め、移転先から取得する
2. PDF を 1 秒に 1 回の頻度で fetch_file で取得して sha256 を付ける。応答の ETag・Last-Modified も記録する。
   手元では取得済みの URL を pdf_dir/index.json で覚えて取り直さない
3. ページごとに文字を取り出し（shared/pdf_text.py）、Parquet にする
   （<parquet_dir>/plans_documents.parquet: 1 行がシートの 1 URL で、取得した URL を fetched_url に持つ。
   plans_pages.parquet: 1 行が 1 ページ。
   plans_source_file.parquet: 取得したファイルの一覧。1 行が取得した URL とファイルの 1 組で、列は README「元のファイル」の source_file）

使うデータセットは pyproject.toml に pymupdf と duckdb を追加する。
"""

import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from http.client import InvalidURL
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit

import duckdb
import pymupdf
from shared.fetch import fetch_file, write_atomic
from shared.pdf_text import load_cid_map, pdf_pages

logger = logging.getLogger("pipelines")

# 行政事業レビューのシートをテーブルにした Queria の公開データセット（queria/rssystem）。
# 行政事業レビュー見える化サイトから直接 CSV を取ると、GitHub のランナーによっては画面の HTML が返るので、こちらを読む
RSSYSTEM = "ducklake:https://data.queria.io/datasets/queria/rssystem/metadata.db"
INTERVAL = 1.0

# URL を path に取得し、fetch_file と同じ形の行を返す。取得できない URL（リンク切れなど）は None を返す
Fetch = Callable[[str, Path], dict | None]


def fetch_plan(user_agent: str) -> Fetch:
    """shared/fetch.py の fetch_file で取得する。4xx と送れない URL は None、5xx はそのまま止める。"""

    def fetch(url: str, path: Path) -> dict | None:
        try:
            return fetch_file(url, path, user_agent=user_agent)
        except HTTPError as error:
            # 404 以外のクライアントエラー（403・410 など）もリンク切れとして扱う。
            # 5xx は再試行しても失敗したときにそのまま止める
            if error.code >= 500:
                raise
            return None
        except InvalidURL:
            # シートの URL に空白などが入っていて、要求を送れない
            return None

    return fetch


def review_rows(user_agent: str) -> dict[int, list[dict]]:
    """年度 → その年度のシートの計画・通知等の行（plan_title・plan_url）。

    data.queria.io はビルドの読み出しを利用者に数えないよう、build-fdl.sh が渡す QUERIA_USER_AGENT で読む。
    """
    agent = os.environ.get("QUERIA_USER_AGENT") or user_agent
    with duckdb.connect(config={"custom_user_agent": agent}) as con:
        con.execute("INSTALL ducklake; LOAD ducklake; INSTALL httpfs; LOAD httpfs")
        con.execute(f"ATTACH '{RSSYSTEM}' AS rssystem (READ_ONLY)")
        rows = con.execute(
            "SELECT review_year, plan_title, plan_url FROM rssystem.review.related_plans"
            " ORDER BY review_year, project_id, plan_number"
        ).fetchall()
    years: defaultdict[int, list[dict]] = defaultdict(list)
    for year, title, url in rows:
        years[year].append({"plan_title": title, "plan_url": url})
    return dict(years)


def relocate(url: str, moved: Mapping[str, str] | None = None) -> str:
    """移転したページの URL を移転先の URL にする。moved はシートの URL の先頭 → 移転先の URL の先頭。"""
    return next(
        (new + url[len(old) :] for old, new in (moved or {}).items() if url.startswith(old)),
        url,
    )


def is_target(url: str, hosts: Iterable[str]) -> bool:
    parts = urlsplit(url.strip())
    return parts.hostname in set(hosts) and parts.path.lower().endswith(".pdf")


def year_titles(
    rows: Iterable[dict], hosts: Iterable[str], moved: Mapping[str, str] | None = None
) -> dict[str, str]:
    """1 年度のシートの行から、hosts の PDF の URL（移転したものは移転先が hosts のもの）→ 名前。

    同じ URL に複数の名前があるときは、最も多く書かれている名前を使う（同数なら先に出てきた名前）。
    """
    names: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        # シートの URL には途中に空白が入ったものがある（keizai- shimon など）ので、空白を除く
        url = re.sub(r"\s+", "", row.get("plan_url") or "")
        if is_target(relocate(url, moved), hosts):
            names[url][(row.get("plan_title") or "").strip()] += 1
    return {url: counts.most_common(1)[0][0] for url, counts in names.items()}


def plan_urls(
    hosts: Iterable[str], user_agent: str, moved: Mapping[str, str] | None = None
) -> dict[str, str]:
    """計画・通知等の URL → 名前。同じ URL は後の年度のシートの名前を使う。"""
    years = review_rows(user_agent)
    if not years:
        raise SystemExit("plans: 行政事業レビューの計画・通知等が 1 行も無い")
    titles: dict[str, str] = {}
    for year in sorted(years):
        titles |= year_titles(years[year], hosts, moved)
    return titles


def read_index(pdf_dir: Path) -> dict[str, dict]:
    path = pdf_dir / "index.json"
    return json.loads(path.read_text()) if path.exists() else {}


def download_plans(
    hosts: Iterable[str],
    *,
    user_agent: str,
    pdf_dir: Path,
    moved: Mapping[str, str] | None = None,
    fetch: Fetch | None = None,
) -> None:
    """PDF を pdf_dir/<sha256>.pdf に置き、シートの URL → sha256・名前・取得した URL・取得日時を pdf_dir/index.json に書く。

    fetch を渡すと、PDF をその関数で取得する。省くと fetch_plan(user_agent)。
    """
    fetch = fetch or fetch_plan(user_agent)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    urls = plan_urls(hosts, user_agent, moved)
    # シートから消えた URL は外す
    index = {url: entry for url, entry in read_index(pdf_dir).items() if url in urls}
    try:
        for url, title in urls.items():
            if url in index:
                index[url]["title"] = title
                continue
            fetched_url = relocate(url, moved)
            time.sleep(INTERVAL)
            partial = pdf_dir / "fetching.pdf"
            source = fetch(fetched_url, partial)
            if source is None:
                logger.info("plans: 取得できない: %s", fetched_url)
                continue
            with partial.open("rb") as f:
                is_pdf = f.read(4) == b"%PDF"
            if not is_pdf:
                partial.unlink()
                logger.info("plans: PDF ではない応答: %s", fetched_url)
                continue
            partial.replace(pdf_dir / f"{source['sha256']}.pdf")
            index[url] = {
                "sha256": source["sha256"],
                "title": title,
                "fetched_url": fetched_url,
                "fetched_at": source["retrieved_at"],
                "media_type": source["media_type"],
                "etag": source["etag"],
                "last_modified": source["last_modified"],
            }
    finally:
        # 途中で止まっても、取得済みの PDF を次の実行で取り直さない
        write_atomic(
            pdf_dir / "index.json",
            json.dumps(index, ensure_ascii=False, indent=1).encode(),
        )
    logger.info("plans: %d URL のうち %d 件の PDF", len(urls), len(index))


def build_plans_parquet(*, user_agent: str, pdf_dir: Path, parquet_dir: Path) -> None:
    cid_map = load_cid_map(user_agent)
    index = read_index(pdf_dir)
    documents, pages = [], {}
    for url, entry in index.items():
        digest = entry["sha256"]
        path = pdf_dir / f"{digest}.pdf"
        if digest not in pages:
            try:
                pages[digest] = pdf_pages(path.read_bytes(), cid_map)
            except (pymupdf.FileDataError, ValueError) as error:
                # 壊れた PDF・パスワードの要る PDF は開けない
                logger.info("plans: PDF を開けない: %s (%s)", url, error)
                pages[digest] = None
        if pages[digest] is None:
            continue
        documents.append(
            (
                url,
                digest,
                entry["title"],
                path.stat().st_size,
                len(pages[digest]),
                # fetched_url を書く前の index.json の行は、シートの URL から取得している
                entry.get("fetched_url", url),
                entry["fetched_at"],
            )
        )

    parquet_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE documents (url VARCHAR, sha256 VARCHAR, title VARCHAR, bytes BIGINT,"
        " page_count INTEGER, fetched_url VARCHAR, fetched_at TIMESTAMPTZ)"
    )
    con.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)", documents)
    con.execute(
        "CREATE TABLE pages (sha256 VARCHAR, page INTEGER, page_label VARCHAR, text VARCHAR,"
        " cid_repaired_chars INTEGER)"
    )
    con.executemany(
        "INSERT INTO pages VALUES (?, ?, ?, ?, ?)",
        [(digest, *page) for digest, ps in pages.items() for page in ps or ()],
    )
    # 取得したファイルの一覧。シートの複数の URL が同じ URL・同じファイルを指すことがあるので 1 行にまとめる。
    # ETag などを記録する前の index.json の行は、メディアタイプも NULL にする
    con.execute(
        "CREATE TABLE source_file (url VARCHAR, page_url VARCHAR, sha256 VARCHAR, bytes BIGINT,"
        " media_type VARCHAR, retrieved_at TIMESTAMPTZ, etag VARCHAR, last_modified VARCHAR)"
    )
    con.executemany(
        "INSERT INTO source_file VALUES (?, NULL, ?, ?, ?, ?, ?, ?)",
        sorted(
            {
                (
                    entry.get("fetched_url", url),
                    entry["sha256"],
                    (pdf_dir / f"{entry['sha256']}.pdf").stat().st_size,
                    entry.get("media_type"),
                    entry["fetched_at"],
                    entry.get("etag"),
                    entry.get("last_modified"),
                )
                for url, entry in index.items()
                if pages.get(entry["sha256"]) is not None
            },
            key=lambda row: row[:2],
        ),
    )
    for table in ("documents", "pages", "source_file"):
        destination = parquet_dir / f"plans_{table}.parquet"
        con.execute(
            f"COPY {table} TO '{destination}' (FORMAT parquet, COMPRESSION zstd)"
        )
    logger.info(
        "plans: %d 文書 %d ページ",
        len(documents),
        sum(len(ps or []) for ps in pages.values()),
    )
