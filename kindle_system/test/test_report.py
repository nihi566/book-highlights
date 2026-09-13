"""
test_report.py
---------------
report.py の HTML 生成関数（build_html）の単体テスト。

実行:
    python -m unittest test.test_report -v
"""

import os
import sys
import shutil
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import report


class BuildHtmlTest(unittest.TestCase):
    def test_includes_title_and_price_for_each_book(self):
        books = [
            {
                "title": "読みたい本1",
                "asin": "B0WANT001",
                "sell_price": 1000,
                "point_value": 100,
                "actual_price": 900,
                "campaign_text": "",
                "timestamp": "2026-01-01T00:00:00",
                "is_unlimited": 0,
                "is_wanted": 1,
                "is_purchased": 0,
            }
        ]
        html = report.build_html(books)
        self.assertIn("読みたい本1", html)
        self.assertIn("900", html)

    def test_escapes_html_special_characters_in_title(self):
        """R2: タイトルに <script> 等が含まれていても HTML インジェクションにならないこと。"""
        books = [
            {
                "title": "<script>alert('xss')</script>",
                "asin": "B0XSS001",
                "sell_price": None,
                "point_value": 0,
                "actual_price": None,
                "campaign_text": "",
                "timestamp": None,
                "is_unlimited": 0,
                "is_wanted": 0,
                "is_purchased": 0,
            }
        ]
        html = report.build_html(books)
        self.assertNotIn("<script>alert('xss')</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_shows_placeholder_when_price_is_missing(self):
        books = [
            {
                "title": "価格未取得本",
                "asin": "B0NOPRICE001",
                "sell_price": None,
                "point_value": 0,
                "actual_price": None,
                "campaign_text": "",
                "timestamp": None,
                "is_unlimited": 0,
                "is_wanted": 1,
                "is_purchased": 0,
            }
        ]
        html = report.build_html(books)
        self.assertIn("価格未取得本", html)
        self.assertIn("価格情報なし", html)

    def test_shows_ku_label_instead_of_zero_yen_for_unlimited_books(self):
        """
        src/crawler.pyのKU安全弁はis_unlimited本のsell_price/actual_priceを常に0で
        保存する（本来の価格はcampaign_textへ退避）。¥0とそのまま表示すると
        「無料で買える本」という誤解を生むため、専用ラベルで表示すること。
        """
        books = [
            {
                "title": "Unlimited対象本",
                "asin": "B0KU001",
                "sell_price": 0,
                "point_value": 0,
                "actual_price": 0,
                "campaign_text": "通常価格: ¥1,200",
                "timestamp": "2026-01-01T00:00:00",
                "is_unlimited": 1,
                "is_wanted": 1,
                "is_purchased": 1,
            }
        ]
        html = report.build_html(books)
        self.assertIn("Unlimited対象本", html)
        self.assertNotIn("¥0", html)
        self.assertIn("Kindle Unlimited 対象", html)

    def test_renders_valid_html_when_no_books(self):
        html = report.build_html([])
        self.assertIn("<html", html)
        self.assertIn("</html>", html)
        # 0件時は空状態メッセージが実際に出ること（title タグの文言では代用しない）
        self.assertIn("読みたい本はまだ登録されていません。", html)

    def test_escapes_asin(self):
        books = [
            {
                "title": "普通の本",
                "asin": "B0<script>",
                "sell_price": 500,
                "point_value": 0,
                "actual_price": 500,
                "campaign_text": "",
                "timestamp": "2026-01-01T00:00:00",
                "is_unlimited": 0,
                "is_wanted": 0,
                "is_purchased": 1,
            }
        ]
        html = report.build_html(books)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


class CategorySectionsTest(unittest.TestCase):
    """report.build_html() の want済み/購入済み/全部 3カテゴリ分けのテスト。"""

    def _book(self, title, asin, is_wanted, is_purchased):
        return {
            "title": title,
            "asin": asin,
            "sell_price": None,
            "point_value": 0,
            "actual_price": None,
            "campaign_text": "",
            "timestamp": None,
            "is_unlimited": 0,
            "is_wanted": is_wanted,
            "is_purchased": is_purchased,
        }

    def test_three_sections_present_with_expected_ids(self):
        html = report.build_html([])
        self.assertIn('id="section-all"', html)
        self.assertIn('id="section-wanted"', html)
        self.assertIn('id="section-purchased"', html)

    def test_wanted_only_book_appears_in_all_and_wanted_sections_only(self):
        books = [self._book("欲しい本X", "B0CATW1", is_wanted=1, is_purchased=0)]
        html = report.build_html(books)
        self.assertEqual(html.count("欲しい本X"), 2)  # 全部 + 読みたい

    def test_purchased_only_book_appears_in_all_and_purchased_sections_only(self):
        books = [self._book("買った本Y", "B0CATP1", is_wanted=0, is_purchased=1)]
        html = report.build_html(books)
        self.assertEqual(html.count("買った本Y"), 2)  # 全部 + 購入済み

    def test_book_without_flags_appears_only_in_all_section(self):
        books = [self._book("フラグなし本Z", "B0CATN1", is_wanted=0, is_purchased=0)]
        html = report.build_html(books)
        self.assertEqual(html.count("フラグなし本Z"), 1)  # 全部のみ


class BuildPriceHistorySvgTest(unittest.TestCase):
    """report._build_price_history_svg() のテスト（R1/R3/R4対策）。"""

    def test_returns_polyline_for_two_or_more_valid_points(self):
        history = [
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 800, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertIn("<svg", svg)
        self.assertIn("<polyline", svg)

    def test_excludes_unlimited_points_from_polyline(self):
        """R4: is_unlimited=1の点(価格0で保存される)は折れ線の座標計算から除外すること。

        座標の個数を検証する（除外フィルタが働かず3点分の座標が出ていないこと）。
        """
        history = [
            {"actual_price": 0, "is_unlimited": 1, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
            {"actual_price": 800, "is_unlimited": 0, "timestamp": "2026-01-03T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertIn("<polyline", svg)
        self.assertNotIn("価格履歴データなし", svg)
        import re

        points = re.search(r'points="([^"]*)"', svg).group(1).split()
        self.assertEqual(len(points), 2)  # 有効点(KU除外後)は2点のみ

    def test_higher_price_maps_to_smaller_y_coordinate(self):
        """価格が高いほどグラフ上で上（y座標が小さい）に描画されること。"""
        history = [
            {"actual_price": 500, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        import re

        points = re.search(r'points="([^"]*)"', svg).group(1).split()
        y0 = float(points[0].split(",")[1])
        y1 = float(points[1].split(",")[1])
        self.assertLess(y1, y0)  # 1000円の点のほうが500円の点よりyが小さい(上にある)

    def test_shows_placeholder_when_fewer_than_two_valid_points(self):
        """R3: 有効な価格点が2点未満の場合はプレースホルダーを返すこと。"""
        history = [
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertIn("価格履歴データなし", svg)
        self.assertNotIn("<polyline", svg)

    def test_shows_placeholder_when_history_is_empty(self):
        svg = report._build_price_history_svg([])
        self.assertIn("価格履歴データなし", svg)

    def test_shows_placeholder_when_all_points_are_unlimited(self):
        history = [
            {"actual_price": 0, "is_unlimited": 1, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 0, "is_unlimited": 1, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertIn("価格履歴データなし", svg)

    def test_treats_missing_actual_price_as_invalid_point(self):
        history = [
            {"actual_price": None, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertIn("価格履歴データなし", svg)

    def test_polyline_points_are_numeric_only(self):
        """R1: 座標は数値のみで構成され、自由文字列(ユーザー由来の文字列)を含まないこと。"""
        import re

        history = [
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 800, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        match = re.search(r'points="([^"]*)"', svg)
        self.assertIsNotNone(match)
        self.assertRegex(match.group(1), r'^[\d.,\s\-]+$')


class LoadEnvFileTest(unittest.TestCase):
    """_load_env_file()（.env 読み込み）のテスト。

    P0対策: このリポジトリには dotenv ローダーが存在せず、.env に値を書いても
    読まれない状態だったため追加した最小実装のテスト。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="report_env_test_")
        self.env_path = os.path.join(self.tmpdir, ".env")
        self._saved_env = {}

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _isolate_env_var(self, key):
        """テスト前後で当該環境変数を退避・復元する。"""
        self._saved_env[key] = os.environ.get(key)
        os.environ.pop(key, None)

    def test_loads_values_from_env_file(self):
        self._isolate_env_var("REPORT_TEST_VAR_A")
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("REPORT_TEST_VAR_A=hello\n")

        report._load_env_file(self.env_path)

        self.assertEqual(os.environ.get("REPORT_TEST_VAR_A"), "hello")

    def test_does_not_override_existing_env_var(self):
        self._isolate_env_var("REPORT_TEST_VAR_B")
        os.environ["REPORT_TEST_VAR_B"] = "existing"
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("REPORT_TEST_VAR_B=from_file\n")

        report._load_env_file(self.env_path)

        self.assertEqual(os.environ.get("REPORT_TEST_VAR_B"), "existing")

    def test_ignores_comments_and_blank_lines(self):
        self._isolate_env_var("REPORT_TEST_VAR_C")
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("# comment\n\nREPORT_TEST_VAR_C=ok\n")

        report._load_env_file(self.env_path)

        self.assertEqual(os.environ.get("REPORT_TEST_VAR_C"), "ok")

    def test_missing_file_is_noop(self):
        # 例外が出ないことを確認する
        report._load_env_file(os.path.join(self.tmpdir, "does_not_exist.env"))


if __name__ == "__main__":
    unittest.main()
