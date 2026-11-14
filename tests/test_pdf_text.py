"""pdf_text.py のテスト。PyMuPDF で作った PDF を読んで確かめる。

実行: uv run --no-project --python 3.13 --with pymupdf python -m unittest tests.test_pdf_text
"""

import sys
import tempfile
import unittest
from pathlib import Path

# データセットリポと同じく shared パッケージとして読む
ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = Path(tempfile.mkdtemp())
(PACKAGE_DIR / "shared").symlink_to(ROOT)
sys.path.insert(0, str(PACKAGE_DIR))

import pymupdf
from shared import pdf_text

CMAP = """\
2 beginbfchar
<0001> <0020>
<0010> <4e00>
endbfchar
1 beginbfrange
<0020> <0022> <3042>
endbfrange
"""


class ParseCidMapTest(unittest.TestCase):
    def test_bfchar_and_bfrange(self):
        self.assertEqual(
            pdf_text.parse_cid_map(CMAP),
            {1: " ", 0x10: "一", 0x20: "あ", 0x21: "ぃ", 0x22: "い"},
        )


class IsPlainTest(unittest.TestCase):
    def test_ranges(self):
        self.assertTrue(pdf_text.is_plain(ord("A")))
        self.assertTrue(pdf_text.is_plain(ord("あ")))
        self.assertTrue(pdf_text.is_plain(ord("※")))
        self.assertFalse(pdf_text.is_plain(0x0627))  # アラビア文字
        self.assertFalse(pdf_text.is_plain(5))


class CidFontsTest(unittest.TestCase):
    def test_share(self):
        span = lambda font, s: {"font": font, "chars": [{"c": c} for c in s]}
        lines = [[span("ok", "国土交通白書"), span("cid", "ابab")]]
        self.assertEqual(pdf_text.cid_fonts(lines), {"cid"})


class PdfPagesTest(unittest.TestCase):
    def test_pages(self):
        doc = pymupdf.open()
        for text in ("Page one", "Page two"):
            doc.new_page().insert_text((72, 72), text)
        pages = pdf_text.pdf_pages(doc.tobytes(), {})
        self.assertEqual(
            [(p.page, p.text, p.cid_repaired_chars) for p in pages],
            [(1, "Page one", 0), (2, "Page two", 0)],
        )


if __name__ == "__main__":
    unittest.main()
