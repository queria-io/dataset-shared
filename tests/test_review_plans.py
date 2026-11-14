"""review_plans.py のテスト。ネットワークを使わない部分だけを確かめる。

実行: uv run --no-project --python 3.13 --with duckdb --with pymupdf python -m unittest tests.test_review_plans
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# データセットリポと同じく shared パッケージとして読む
ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = Path(tempfile.mkdtemp())
(PACKAGE_DIR / "shared").symlink_to(ROOT)
sys.path.insert(0, str(PACKAGE_DIR))

from shared import review_plans

HOSTS = {"www.mlit.go.jp"}


def row(url: str, title: str) -> dict:
    return {"plan_url": url, "plan_title": title}


class IsTargetTest(unittest.TestCase):
    def test_host_and_pdf(self):
        self.assertTrue(review_plans.is_target(" https://www.mlit.go.jp/a.PDF ", HOSTS))
        self.assertFalse(review_plans.is_target("https://www.mlit.go.jp/a.html", HOSTS))
        self.assertFalse(review_plans.is_target("https://www.mhlw.go.jp/a.pdf", HOSTS))
        self.assertFalse(review_plans.is_target("", HOSTS))


MOVED = {"https://www.kantei.go.jp/jp/singi/": "https://www.cas.go.jp/jp/seisakukaigi/"}


class RelocateTest(unittest.TestCase):
    def test_prefix(self):
        self.assertEqual(
            review_plans.relocate("https://www.kantei.go.jp/jp/singi/a/b.pdf", MOVED),
            "https://www.cas.go.jp/jp/seisakukaigi/a/b.pdf",
        )
        self.assertEqual(
            review_plans.relocate("https://www.kantei.go.jp/jp/headline/c.pdf", MOVED),
            "https://www.kantei.go.jp/jp/headline/c.pdf",
        )
        self.assertEqual(review_plans.relocate("https://x/a.pdf"), "https://x/a.pdf")


class YearTitlesTest(unittest.TestCase):
    def test_moved(self):
        rows = [
            row("https://www.kantei.go.jp/jp/singi/a.pdf", "会議の決定"),
            row("https://www.kantei.go.jp/jp/headline/c.pdf", "官邸"),
            row("https://www.cas.go.jp/d.pdf", "内閣官房"),
        ]
        self.assertEqual(
            review_plans.year_titles(rows, {"www.cas.go.jp"}, MOVED),
            {
                "https://www.kantei.go.jp/jp/singi/a.pdf": "会議の決定",
                "https://www.cas.go.jp/d.pdf": "内閣官房",
            },
        )

    def test_most_common_title(self):
        rows = [
            row("https://www.mlit.go.jp/a.pdf", "計画 P10"),
            row("https://www.mlit.go.jp/a.pdf", "計画"),
            row("https://www.mlit.go.jp/a.pdf", "計画"),
            row("https://www.mlit.go.jp/b .pdf", "通知"),
            row("https://www.mhlw.go.jp/c.pdf", "他省"),
        ]
        self.assertEqual(
            review_plans.year_titles(rows, HOSTS),
            {
                "https://www.mlit.go.jp/a.pdf": "計画",
                "https://www.mlit.go.jp/b.pdf": "通知",
            },
        )


class PlanUrlsTest(unittest.TestCase):
    def test_later_year_wins(self):
        years = {
            2025: [row("https://www.mlit.go.jp/a.pdf", "新しい名前")],
            2024: [
                row("https://www.mlit.go.jp/a.pdf", "古い名前"),
                row("https://www.mlit.go.jp/b.pdf", "通知"),
            ],
        }
        with mock.patch.object(review_plans, "review_rows", return_value=years):
            self.assertEqual(
                review_plans.plan_urls(HOSTS, "test"),
                {
                    "https://www.mlit.go.jp/a.pdf": "新しい名前",
                    "https://www.mlit.go.jp/b.pdf": "通知",
                },
            )

    def test_no_rows(self):
        with (
            mock.patch.object(review_plans, "review_rows", return_value={}),
            self.assertRaises(SystemExit),
        ):
            review_plans.plan_urls(HOSTS, "test")


if __name__ == "__main__":
    unittest.main()
