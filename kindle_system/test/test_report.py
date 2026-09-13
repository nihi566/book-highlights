"""
test_report.py
---------------
report.py の HTML 生成関数（build_html）の単体テスト。

実行:
    python -m unittest test.test_report -v
"""

import os
import re
import sys
import shutil
import tempfile
import unittest
import unittest.mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import report


def _extract_section_html(html: str, section_id: str) -> str:
    """指定id の <section> 内側のHTMLだけを取り出す（他セクションとの取り違え防止用）。"""
    match = re.search(rf'<section id="{re.escape(section_id)}"[^>]*>(.*?)</section>', html, re.S)
    assert match is not None, f"section {section_id!r} が見つかりません"
    return match.group(1)


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
        # 0件時、初期表示される「全部」セクションに空状態メッセージが実際に出ること
        # （section-wanted 等 hidden 側の文言では代用しない。section-all に限定して検証する）
        all_section = _extract_section_html(html, "section-all")
        self.assertIn("本はまだ登録されていません。", all_section)

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
        # フィルタUI用の信頼済み<script>タグ自体は許容しつつ、asin由来の
        # 未エスケープペイロード("B0<script>")のみが混入していないことを確認する。
        self.assertNotIn("B0<script>", html)
        # is_purchased=1のため「全部」「購入済み」の2セクションに表示される
        # （カウントを固定することでエスケープ漏れの検出力低下を防ぐ）。
        self.assertEqual(html.count("&lt;script&gt;"), 2)


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
        self.assertIn("欲しい本X", _extract_section_html(html, "section-all"))
        self.assertIn("欲しい本X", _extract_section_html(html, "section-wanted"))
        self.assertNotIn("欲しい本X", _extract_section_html(html, "section-purchased"))

    def test_purchased_only_book_appears_in_all_and_purchased_sections_only(self):
        books = [self._book("買った本Y", "B0CATP1", is_wanted=0, is_purchased=1)]
        html = report.build_html(books)
        self.assertIn("買った本Y", _extract_section_html(html, "section-all"))
        self.assertNotIn("買った本Y", _extract_section_html(html, "section-wanted"))
        self.assertIn("買った本Y", _extract_section_html(html, "section-purchased"))

    def test_book_without_flags_appears_only_in_all_section(self):
        books = [self._book("フラグなし本Z", "B0CATN1", is_wanted=0, is_purchased=0)]
        html = report.build_html(books)
        self.assertIn("フラグなし本Z", _extract_section_html(html, "section-all"))
        self.assertNotIn("フラグなし本Z", _extract_section_html(html, "section-wanted"))
        self.assertNotIn("フラグなし本Z", _extract_section_html(html, "section-purchased"))

    def test_price_history_key_on_book_is_rendered_as_polyline(self):
        """book['price_history'] が build_html まで正しく伝播し、<polyline> として
        描画されること（main() が付与するキー名との契約を固定する）。"""
        book = self._book("価格履歴あり本", "B0CATH1", is_wanted=0, is_purchased=0)
        book["price_history"] = [
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 800, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        html = report.build_html([book])
        self.assertIn("<polyline", _extract_section_html(html, "section-all"))

    def test_missing_price_history_key_shows_placeholder(self):
        """price_history キーが無い本(未取得データ等)ではプレースホルダーになること。"""
        book = self._book("価格履歴なし本", "B0CATH2", is_wanted=0, is_purchased=0)
        html = report.build_html([book])
        self.assertIn("価格履歴データなし", _extract_section_html(html, "section-all"))


class FilterUiTest(unittest.TestCase):
    """report.build_html() のフィルタ切り替えUI（3カテゴリボタン + 初期表示）のテスト。"""

    def test_filter_buttons_exist_for_each_category(self):
        html = report.build_html([])
        self.assertIn('data-target="section-all"', html)
        self.assertIn('data-target="section-wanted"', html)
        self.assertIn('data-target="section-purchased"', html)

    def test_initial_display_shows_only_all_section(self):
        """初期表示は「全部」セクションのみで、他の2セクションはhiddenクラスを持つこと。"""
        html = report.build_html([])
        self.assertIn('id="section-all" class="book-section"', html)
        self.assertIn('id="section-wanted" class="book-section hidden"', html)
        self.assertIn('id="section-purchased" class="book-section hidden"', html)

    def test_every_filter_button_target_has_matching_section_id(self):
        """フィルタボタンのdata-targetが実在するセクションidを指していること
        （ID不一致はJS実行時に全セクション非表示のまま復旧不能になるため）。"""
        import re

        html = report.build_html([])
        targets = re.findall(r'data-target="([^"]+)"', html)
        self.assertTrue(targets)
        for target in targets:
            self.assertIn(f'id="{target}"', html)

    def test_includes_toggle_script_without_page_navigation(self):
        """ページ遷移(XHR/location遷移)を発生させない素のJSでトグルすること。"""
        html = report.build_html([])
        self.assertIn("<script>", html)
        self.assertIn("classList", html)
        self.assertNotIn("location.href", html)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("XMLHttpRequest", html)


class MainIntegrationTest(unittest.TestCase):
    """main() が get_books/get_price_history の結果をbuild_htmlへ正しく配線することのテスト。

    実DBには接続せず、report.get_books / report.get_price_history をモックする。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="report_main_test_")
        os.makedirs(os.path.join(self.tmpdir, ".git"))
        self._saved_env = {}
        for key in ("PUBLIC_SITE_DIR", "PUBLIC_SITE_URL"):
            self._saved_env[key] = os.environ.get(key)
        os.environ["PUBLIC_SITE_DIR"] = self.tmpdir
        os.environ["PUBLIC_SITE_URL"] = "https://example.invalid/"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_main_wires_get_books_and_price_history_into_output(self):
        fake_book = {
            "title": "結合テスト本",
            "asin": "B0INTEG1",
            "sell_price": 1000,
            "point_value": 0,
            "actual_price": 1000,
            "campaign_text": "",
            "timestamp": "2026-01-02T00:00:00",
            "is_unlimited": 0,
            "is_wanted": 1,
            "is_purchased": 0,
        }
        fake_history = [
            {"actual_price": 1200, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        with unittest.mock.patch.object(
            report, "get_books", return_value=[dict(fake_book)]
        ) as mock_get_books, unittest.mock.patch.object(
            report, "get_price_history", return_value=fake_history
        ) as mock_get_history:
            report.main()

        mock_get_books.assert_called_once_with(filter="all")
        mock_get_history.assert_called_once_with("B0INTEG1")

        output_path = os.path.join(self.tmpdir, "index.html")
        with open(output_path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("結合テスト本", html)
        self.assertIn("<polyline", html)  # price_history が build_html まで伝播していること


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

    def test_flat_line_when_all_valid_prices_are_equal(self):
        """全有効点が同価格のとき、ゼロ除算(max_price == min_price)を起こさず
        水平な折れ線(全点同じy座標)を返すこと。"""
        history = [
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertIn("<polyline", svg)
        points = re.search(r'points="([^"]*)"', svg).group(1).split()
        y0 = float(points[0].split(",")[1])
        y1 = float(points[1].split(",")[1])
        self.assertEqual(y0, y1)

    def test_polyline_points_are_numeric_only(self):
        """R1: 座標は数値のみで構成され、自由文字列(ユーザー由来の文字列)を含まないこと。"""

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
