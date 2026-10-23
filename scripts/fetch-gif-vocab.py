# /// script
# requires-python = ">=3.11"
# dependencies = ["openpyxl"]
# ///
"""GIF のコアデータモデル（DMD）を CSV にする。

    uv run scripts/fetch-gif-vocab.py            # vocab/gif_core.csv を作り直す
    uv run scripts/fetch-gif-vocab.py v2.5       # 版を上げる

取り込むのはデジタル庁が GitHub に置いている 438_コアデータモデル_DMD.xlsx だけ。
版はタグで固定し、出力の各行に入れる。
"""

import csv
import re
import io
import sys
import urllib.parse
import urllib.request
from functools import reduce
from pathlib import Path

import openpyxl

REPO = "digital-go-jp/GIF"
DMD_PATH = "430_コアデータモデル/438_コアデータモデル_DMD.xlsx"
OUT = Path(__file__).resolve().parent.parent / "vocab" / "gif_core.csv"

# DMD の見出しのうち、取り出す列（シートごとに列位置が違うので見出しで引く）
HEADERS = {
    "No.": "no",
    "項目名": "term_ja",
    "項目名（英語）": "term_en",
    "説明": "description",
    "形式": "format",
    "記入例": "example",
    "名称・形式等の参考元": "reference",
}
FIELDS = ["version", "class", "section", "no", "term_ja", "term_en", "description", "format", "example", "reference"]


def fetch(ref: str) -> bytes:
    url = f"https://raw.githubusercontent.com/{REPO}/{ref}/{urllib.parse.quote(DMD_PATH)}"
    with urllib.request.urlopen(url) as res:
        return res.read()


def clean(value) -> str:
    # 項目名には「事業所住所 (*1)」のような注記の印が付くことがある
    return re.sub(r"\s*\(\*\d+\)$", "", "" if value is None else str(value).strip())


def section_of(first) -> str | None:
    # 表の前に「基礎項目」「拡張項目（…）」という見出し行が 1 行ある
    if isinstance(first, str) and first.startswith("基礎項目"):
        return "base"
    if isinstance(first, str) and first.startswith("拡張項目"):
        return "extension"
    return None


def sheet_rows(version: str, sheet) -> list[dict]:
    def step(state, row):
        columns, section, out = state
        first = row[0] if row else None
        if first == "No.":
            return {h: i for i, h in enumerate(row) if h in HEADERS}, section, out
        if section_of(first):
            return columns, section_of(first), out
        # 拡張項目の行には No. が無い
        is_term = isinstance(first, int) or (first is None and section == "extension")
        if is_term and columns and row[columns["項目名"]]:
            values = {HEADERS[h]: clean(row[i]) for h, i in columns.items()}
            return columns, section, [*out, {"version": version, "class": sheet.title, "section": section, **values}]
        return state

    rows = [tuple(r) for r in sheet.iter_rows(values_only=True)]
    return reduce(step, rows, ({}, "base", []))[2]


def main(ref: str) -> None:
    book = openpyxl.load_workbook(io.BytesIO(fetch(ref)), read_only=True)
    rows = [r for sheet in book.worksheets for r in sheet_rows(ref, sheet)]
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{OUT.name}: {len(rows)} rows from {REPO}@{ref}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "v2.4")
