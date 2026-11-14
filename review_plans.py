"""行政事業レビューのシートが「関係する計画・通知等」に挙げる PDF を、配布元のサイトごとにページの本文にする共通部品。

各データセットリポからは submodule の置き場所（shared）経由で読む:

    from shared.review_plans import build_plans_parquet, download_plans

    HOSTS = {"www.mlit.go.jp"}
    download_plans(HOSTS, user_agent=USER_AGENT, pdf_dir=Path("data/plans"))
    build_plans_parquet(user_agent=USER_AGENT, pdf_dir=Path("data/plans"), parquet_dir=Path("data/parquet"))

1. 行政事業レビュー見える化サイト（https://rssystem.go.jp）の CSV（1-3 政策・施策、法令等、2024 年度のシートから）
   から、計画・通知等の URL のうち hosts のサイトの PDF を集める
2. PDF を 1 秒に 1 回の頻度で取得して sha256 を付ける。手元では取得済みの URL を pdf_dir/index.json で覚えて取り直さない
3. ページごとに文字を取り出し（shared/pdf_text.py）、Parquet にする
   （<parquet_dir>/plans_documents.parquet: 1 行が 1 URL、plans_pages.parquet: 1 行が 1 ページ）

使うデータセットは pyproject.toml に pymupdf と duckdb を追加する。
"""

import csv
import datetime
import hashlib
import io
import json
import logging
import re
import time
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from http.client import InvalidURL
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit

import duckdb
import pymupdf
from shared.fetch import fetch, write_atomic
from shared.pdf_text import load_cid_map, pdf_pages

logger = logging.getLogger("pipelines")

RS_SITE = "https://rssystem.go.jp"
RS_CSV = "1-3_RS_{year}_基本情報_政策・施策、法令等.zip"
FIRST_YEAR = 2024
INTERVAL = 1.0


def review_zip(year: int, user_agent: str) -> bytes | None:
    url = f"{RS_SITE}/files/{year}/rs/{quote(RS_CSV.format(year=year))}"
    body = fetch(url, user_agent=user_agent, not_found_ok=True)
    # ZIP の無い年度にはサイトの画面の HTML が 200 で返る
    return body if body is not None and body.startswith(b"PK") else None


def review_rows(body: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        (member,) = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        text = archive.read(member).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def is_target(url: str, hosts: Iterable[str]) -> bool:
    parts = urlsplit(url.strip())
    return parts.hostname in set(hosts) and parts.path.lower().endswith(".pdf")


def year_titles(rows: Iterable[dict], hosts: Iterable[str]) -> dict[str, str]:
    """1 年度のシートの行から、hosts の PDF の URL → 名前。

    同じ URL に複数の名前があるときは、最も多く書かれている名前を使う（同数なら先に出てきた名前）。
    """
    names: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        # シートの URL には途中に空白が入ったものがある（keizai- shimon など）ので、空白を除く
        url = re.sub(r"\s+", "", row.get("計画通知等URL") or "")
        if is_target(url, hosts):
            names[url][(row.get("計画通知名") or "").strip()] += 1
    return {url: counts.most_common(1)[0][0] for url, counts in names.items()}


def plan_urls(hosts: Iterable[str], user_agent: str) -> dict[str, str]:
    """計画・通知等の URL → 名前。同じ URL は後の年度のシートの名前を使う。"""
    years = range(FIRST_YEAR, datetime.date.today().year + 2)
    bodies = [b for b in (review_zip(y, user_agent) for y in years) if b is not None]
    if not bodies:
        raise SystemExit("plans: 行政事業レビューの CSV が取れない")
    titles: dict[str, str] = {}
    for body in bodies:
        titles |= year_titles(review_rows(body), hosts)
    return titles


def read_index(pdf_dir: Path) -> dict[str, dict]:
    path = pdf_dir / "index.json"
    return json.loads(path.read_text()) if path.exists() else {}


def download_plans(hosts: Iterable[str], *, user_agent: str, pdf_dir: Path) -> None:
    """PDF を pdf_dir/<sha256>.pdf に置き、URL → sha256・名前・取得日時を pdf_dir/index.json に書く。"""
    pdf_dir.mkdir(parents=True, exist_ok=True)
    urls = plan_urls(hosts, user_agent)
    # シートから消えた URL は外す
    index = {url: entry for url, entry in read_index(pdf_dir).items() if url in urls}
    try:
        for url, title in urls.items():
            if url in index:
                index[url]["title"] = title
                continue
            time.sleep(INTERVAL)
            try:
                body = fetch(url, user_agent=user_agent, not_found_ok=True)
            except HTTPError as error:
                # 404 以外のクライアントエラー（403・410 など）もリンク切れとして扱う。
                # 5xx は再試行しても失敗したときにそのまま止める
                if error.code >= 500:
                    raise
                body = None
            except InvalidURL:
                # シートの URL に空白などが入っていて、要求を送れない
                body = None
            if body is None:
                logger.info("plans: 取得できない: %s", url)
                continue
            if not body.startswith(b"%PDF"):
                logger.info("plans: PDF ではない応答: %s", url)
                continue
            digest = hashlib.sha256(body).hexdigest()
            write_atomic(pdf_dir / f"{digest}.pdf", body)
            fetched_at = datetime.datetime.now(datetime.UTC).isoformat()
            index[url] = {"sha256": digest, "title": title, "fetched_at": fetched_at}
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
                entry["fetched_at"],
            )
        )

    parquet_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE documents (url VARCHAR, sha256 VARCHAR, title VARCHAR, bytes BIGINT,"
        " page_count INTEGER, fetched_at TIMESTAMPTZ)"
    )
    con.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)", documents)
    con.execute(
        "CREATE TABLE pages (sha256 VARCHAR, page INTEGER, page_label VARCHAR, text VARCHAR,"
        " cid_repaired_chars INTEGER)"
    )
    con.executemany(
        "INSERT INTO pages VALUES (?, ?, ?, ?, ?)",
        [(digest, *page) for digest, ps in pages.items() for page in ps or ()],
    )
    for table in ("documents", "pages"):
        destination = parquet_dir / f"plans_{table}.parquet"
        con.execute(
            f"COPY {table} TO '{destination}' (FORMAT parquet, COMPRESSION zstd)"
        )
    logger.info(
        "plans: %d 文書 %d ページ",
        len(documents),
        sum(len(ps or []) for ps in pages.values()),
    )
