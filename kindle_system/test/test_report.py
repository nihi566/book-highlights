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
            }
        ]
        html = report.build_html(books)
        self.assertIn("価格未取得本", html)
        self.assertIn("価格情報なし", html)

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
            }
        ]
        html = report.build_html(books)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


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
