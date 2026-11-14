"""PDF からページごとの本文を取り出す共通部品。PyMuPDF（pymupdf）を使う。

各データセットリポからは submodule の置き場所（shared）経由で読む:

    from shared.pdf_text import load_cid_map, pdf_pages

使うデータセットは pyproject.toml に pymupdf を追加する。

見出しや図の中の文字の一部は、PDF に文字コードの対応表（ToUnicode）が無いフォントで書かれていて、
PyMuPDF は文字コードの代わりに Adobe-Japan1 の CID を返す。そうしたフォント（ページの中で
その文字の 5% 超が普段の日本語の文書に出ない文字コードになるもの）では、U+3000 より前の文字を
CID とみなし、Adobe の Adobe-Japan1-UCS2 で文字に戻す。
"""

import re
import unicodedata
from collections import defaultdict
from typing import NamedTuple

import pymupdf
from shared.fetch import fetch

# Adobe-Japan1 の CID から Unicode への対応表（BSD-3-Clause、Adobe）。コミットを固定して取る
CID_MAP_URL = (
    "https://raw.githubusercontent.com/adobe-type-tools/mapping-resources-pdf/"
    "2dd5e53fb74a01718b9dfd448a0d1cce6fff2aa5/pdf2unicode/Adobe-Japan1-UCS2"
)

# 日本語の文書に普段出る文字コードの範囲。ASCII・Latin-1・記号類（U+2000〜U+2BFF）と U+3000 以降
PLAIN = ((0x20, 0x7F), (0xA0, 0x100), (0x2000, 0x2C00), (0x3000, 0x110000))
CID_FONT_SHARE = 0.05
HEX = re.compile(r"<([0-9a-fA-F]+)>")


class Page(NamedTuple):
    page: int  # PDF の先頭から 1 始まり
    label: str | None  # 紙面に印刷されたページ番号
    text: str  # NFKC で正規化した本文
    cid_repaired_chars: int  # CID から戻した文字の数


def parse_cid_map(text: str) -> dict[int, str]:
    """CMap（bfchar・bfrange）の本文を CID → 文字の辞書にする。"""
    table: dict[int, str] = {}
    section = None
    for line in text.splitlines():
        if line.endswith(("beginbfchar", "beginbfrange")):
            section = line.split()[-1]
            continue
        if line.startswith("end"):
            section = None
            continue
        values = HEX.findall(line)
        if section == "beginbfchar" and len(values) == 2:
            table[int(values[0], 16)] = bytes.fromhex(values[1]).decode("utf-16-be")
        elif section == "beginbfrange" and len(values) == 3:
            first, last = int(values[0], 16), int(values[1], 16)
            start = bytes.fromhex(values[2]).decode("utf-16-be")
            for offset in range(last - first + 1):
                table[first + offset] = start[:-1] + chr(ord(start[-1]) + offset)
    table.pop(0, None)
    return table


def load_cid_map(user_agent: str) -> dict[int, str]:
    return parse_cid_map(fetch(CID_MAP_URL, user_agent=user_agent).decode("ascii"))


def is_plain(code: int) -> bool:
    return code in (9, 10, 13) or any(low <= code < high for low, high in PLAIN)


def cid_fonts(lines: list[list[dict]]) -> set[str]:
    """CID を文字コードとして返しているフォントの名前。"""
    codes = defaultdict(list)
    for spans in lines:
        for span in spans:
            codes[span["font"]] += [ord(c["c"]) for c in span["chars"]]
    return {
        font
        for font, cs in codes.items()
        if sum(not is_plain(c) for c in cs) > CID_FONT_SHARE * len(cs)
    }


def page_text(page: pymupdf.Page, cid_map: dict[int, str]) -> tuple[str, int]:
    """ページの本文と、CID から戻した文字の数を返す。"""
    lines = [
        line["spans"]
        for block in page.get_text("rawdict")["blocks"]
        for line in block.get("lines", [])
    ]
    fonts = cid_fonts(lines)

    def is_cid(span: dict, c: dict) -> bool:
        return span["font"] in fonts and ord(c["c"]) < 0x3000

    def char(span: dict, c: dict) -> str:
        return cid_map.get(ord(c["c"]), "") if is_cid(span, c) else c["c"]

    text = "\n".join(
        "".join(char(span, c) for span in spans for c in span["chars"])
        for spans in lines
    )
    repaired = sum(
        is_cid(span, c) for spans in lines for span in spans for c in span["chars"]
    )
    return unicodedata.normalize("NFKC", text), repaired


def pdf_pages(data: bytes, cid_map: dict[int, str]) -> list[Page]:
    """PDF の本体からページごとの本文を取り出す。"""
    doc = pymupdf.open(stream=data, filetype="pdf")
    return [
        Page(number, page.get_label() or None, *page_text(page, cid_map))
        for number, page in enumerate(doc, 1)
    ]
