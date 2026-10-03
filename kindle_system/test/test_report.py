"""
test_report.py
---------------
report.py（欲しい本のデータ wishlist.json の書き出し）の単体テスト。

実行:
    python -m unittest discover -s test -p test_report.py -v
"""

import json
import os
import sys
import shutil
import tempfile
import unittest
import unittest.mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import report



class BuildWishlistTest(unittest.TestCase):
    """build_wishlist() が book-highlights アプリ向けの欲しい本データ（kindle-wishlist v1）を組み立てること。"""

    def _book(self, **overrides):
        book = {
            "title": "欲しい本",
            "asin": "B0WISH001",
            "actual_price": 900,
            "timestamp": "2026-01-02T03:04:05",
            "is_unlimited": 0,
            "is_wanted": 1,
            "is_purchased": 0,
            "from_kindle_sample": 0,
            "from_bookmeter": 1,
        }
        book.update(overrides)
        return book

    def test_top_level_has_format_version_last_scraped_and_books(self):
        data = report.build_wishlist([self._book(), self._book(asin="B0WISH002", timestamp="2026-02-01T00:00:00")])
        self.assertEqual(data["format"], "kindle-wishlist")
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["last_scraped"], "2026-02-01T00:00:00")
        self.assertEqual([b["asin"] for b in data["books"]], ["B0WISH001", "B0WISH002"])

    def test_does_not_include_generation_time(self):
        """生成時刻を載せると自動公開のたびに差分が出て、データが同じでもコミットが増える。"""
        data = report.build_wishlist([self._book()])
        self.assertEqual(set(data), {"format", "version", "last_scraped", "books"})

    def test_last_scraped_is_none_when_no_price_fetched(self):
        data = report.build_wishlist([self._book(timestamp=None)])
        self.assertIsNone(data["last_scraped"])

    def test_book_fields(self):
        book = report.build_wishlist([self._book()])["books"][0]
        self.assertEqual(
            book,
            {
                "asin": "B0WISH001",
                "title": "欲しい本",
                "price": 900,
                "ku": False,
                "wanted": True,
                "purchased": False,
                "sources": ["bookmeter"],
                "kind": report.classify_kind("欲しい本"),
                "tag": "",
                "rating": None,
                "scraped_at": "2026-01-02T03:04:05",
                "price_prev": None,
                "price_changed_at": None,
                "price_low": None,
                "price_history": [],
                "price_reason": None,
                "bookmeter_id": None,
            },
        )

    def test_sources_list_where_the_book_came_from(self):
        """Kindle（サンプル）と読書メーター（読みたい本）のどちらから来た本か。両方なら両方、決まった順で載せる。"""
        sources = lambda **flags: report.build_wishlist([self._book(**flags)])["books"][0]["sources"]
        self.assertEqual(sources(from_kindle_sample=1, from_bookmeter=0), ["kindle"])
        self.assertEqual(sources(from_kindle_sample=0, from_bookmeter=1), ["bookmeter"])
        self.assertEqual(sources(from_kindle_sample=1, from_bookmeter=1), ["kindle", "bookmeter"])
        self.assertEqual(sources(from_kindle_sample=0, from_bookmeter=0), [])

    def test_price_history_is_published_as_is(self):
        history = [{"at": "2026-01-01T00:00:00", "price": 1000, "ku": False}]
        book = report.build_wishlist([self._book(price_history=history)])["books"][0]
        self.assertEqual(book["price_history"], history)

    def test_price_trend_is_published_for_paid_book(self):
        trend = {"prev": 1000, "changed_at": "2026-01-02T03:04:05", "low": 800}
        book = report.build_wishlist([self._book(price_trend=trend)])["books"][0]
        self.assertEqual((book["price_prev"], book["price_changed_at"], book["price_low"]), (1000, "2026-01-02T03:04:05", 800))

    def test_price_trend_is_dropped_when_book_has_no_price(self):
        """KU・価格未取得の本は今の価格が無いので、前回価格・最安値と比べられない。"""
        trend = {"prev": 1000, "changed_at": "2026-01-02T03:04:05", "low": 800}
        for book in (self._book(is_unlimited=1, actual_price=0, price_trend=trend), self._book(actual_price=None, price_trend=trend)):
            data = report.build_wishlist([book])["books"][0]
            self.assertEqual((data["price_prev"], data["price_changed_at"], data["price_low"]), (None, None, None))

    def test_unlimited_book_has_no_price(self):
        """KU の本は価格が 0 で保存されるため、0 円と誤表示しないよう price を null にする。"""
        book = report.build_wishlist([self._book(is_unlimited=1, actual_price=0)])["books"][0]
        self.assertIsNone(book["price"])
        self.assertTrue(book["ku"])

    def test_missing_price_is_null(self):
        book = report.build_wishlist([self._book(actual_price=None)])["books"][0]
        self.assertIsNone(book["price"])

    def test_missing_title_falls_back_to_unknown_title(self):
        book = report.build_wishlist([self._book(title=None)])["books"][0]
        self.assertEqual(book["title"], report.UNKNOWN_TITLE)

    def test_mark_is_resolved_like_the_page(self):
        book = report.build_wishlist([self._book(mark={"tag": "seen", "rating": 4, "kind": "manga"})])["books"][0]
        self.assertEqual((book["kind"], book["tag"], book["rating"]), ("manga", "seen", 4))

    def test_rating_is_dropped_unless_tag_is_seen(self):
        book = report.build_wishlist([self._book(mark={"tag": "wanted", "rating": 4})])["books"][0]
        self.assertEqual((book["tag"], book["rating"]), ("wanted", None))

    def test_unknown_tag_kind_and_rating_are_dropped(self):
        book = report.build_wishlist(
            [self._book(title="普通の本", mark={"tag": "<script>", "rating": 9, "kind": "evil"})]
        )["books"][0]
        self.assertEqual(book["tag"], "")
        self.assertIsNone(book["rating"])
        self.assertEqual(book["kind"], report.classify_kind("普通の本"))


class SummarizePriceChangesTest(unittest.TestCase):
    """summarize_price_changes() が有料価格の記録（本ごと・時刻順）から、前回価格・変わった日時・最安値を出すこと。"""

    def _points(self, asin, prices):
        return [
            {"paid_asin": asin, "actual_price": price, "timestamp": f"2026-01-0{i + 1}T00:00:00"}
            for i, price in enumerate(prices)
        ]

    def test_unchanged_price_has_no_previous_price(self):
        self.assertEqual(
            report.summarize_price_changes(self._points("B0AAAAAAA1", [1000, 1000])),
            {"B0AAAAAAA1": {"prev": None, "changed_at": None, "low": 1000}},
        )

    def test_drop_records_previous_price_and_when_it_changed(self):
        self.assertEqual(
            report.summarize_price_changes(self._points("B0AAAAAAA1", [1000, 1000, 800])),
            {"B0AAAAAAA1": {"prev": 1000, "changed_at": "2026-01-03T00:00:00", "low": 800}},
        )

    def test_latest_change_wins_and_lowest_is_kept(self):
        self.assertEqual(
            report.summarize_price_changes(self._points("B0AAAAAAA1", [1000, 800, 1200])),
            {"B0AAAAAAA1": {"prev": 800, "changed_at": "2026-01-03T00:00:00", "low": 800}},
        )

    def test_books_are_summarized_separately(self):
        points = self._points("B0AAAAAAA1", [1000, 900]) + self._points("B0BBBBBBB2", [500])
        result = report.summarize_price_changes(points)
        self.assertEqual(result["B0AAAAAAA1"]["prev"], 1000)
        self.assertEqual(result["B0BBBBBBB2"], {"prev": None, "changed_at": None, "low": 500})


class SummarizePriceHistoryTest(unittest.TestCase):
    """summarize_price_history() が価格の記録（本ごと・時刻順）を、本ごとのスクレイピングの履歴にすること。"""

    def test_each_scrape_becomes_one_row_with_ku_and_failures(self):
        points = [
            {"paid_asin": "B0AAAAAAA1", "actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"paid_asin": "B0AAAAAAA1", "actual_price": 0, "is_unlimited": 1, "timestamp": "2026-01-02T00:00:00"},
            {"paid_asin": "B0AAAAAAA1", "actual_price": None, "is_unlimited": 0, "timestamp": "2026-01-03T00:00:00"},
            {"paid_asin": "B0BBBBBBB2", "actual_price": 500, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
        ]
        self.assertEqual(
            report.summarize_price_history(points),
            {
                "B0AAAAAAA1": [
                    {"at": "2026-01-01T00:00:00", "price": 1000, "ku": False},
                    {"at": "2026-01-02T00:00:00", "price": None, "ku": True},
                    {"at": "2026-01-03T00:00:00", "price": None, "ku": False},
                ],
                "B0BBBBBBB2": [{"at": "2026-01-01T00:00:00", "price": 500, "ku": False}],
            },
        )

    def test_keeps_only_latest_rows_per_book(self):
        """毎日スクレイピングしても公開データが際限なく大きくならないよう、新しい方から上限件数だけ残す。"""
        points = [
            {"paid_asin": "B0AAAAAAA1", "actual_price": 100 + i, "is_unlimited": 0, "timestamp": f"2026-01-{i + 1:02d}T00:00:00"}
            for i in range(5)
        ]
        rows = report.summarize_price_history(points, limit=3)["B0AAAAAAA1"]
        self.assertEqual([r["price"] for r in rows], [102, 103, 104])


class MainIntegrationTest(unittest.TestCase):
    """main() が get_books / get_book_marks の結果を wishlist.json へ正しく書き出すことのテスト。

    実DBには接続せず、report.get_books / report.get_book_marks をモックする。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="report_main_test_")
        os.makedirs(os.path.join(self.tmpdir, ".git"))
        self._saved_env = {}
        for key in ("PUBLIC_SITE_DIR", "PUBLIC_SITE_URL"):
            self._saved_env[key] = os.environ.get(key)
        os.environ["PUBLIC_SITE_DIR"] = self.tmpdir
        os.environ["PUBLIC_SITE_URL"] = "https://example.invalid/"
        self._points = unittest.mock.patch.object(report, "get_paid_price_points", return_value=[])
        self.mock_points = self._points.start()
        self._all_points = unittest.mock.patch.object(report, "get_all_price_points", return_value=[])
        self.mock_all_points = self._all_points.start()

    def tearDown(self):
        self._points.stop()
        self._all_points.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _run_main_with_marks(self, env):
        fake_book = {"title": "結合テスト本", "asin": "B0INTEG1", "actual_price": 1000, "is_unlimited": 0}
        marks = {"B0INTEG1": {"tag": "seen", "rating": 5, "kind": "manga"}}
        with unittest.mock.patch.dict(os.environ, env), unittest.mock.patch.object(
            report, "get_books", return_value=[fake_book]
        ), unittest.mock.patch.object(report, "get_book_marks", return_value=marks):
            report.main()

    def _read_wishlist_text(self):
        with open(os.path.join(self.tmpdir, "wishlist.json"), encoding="utf-8") as f:
            return f.read()

    def test_main_writes_wishlist_json_next_to_index_html(self):
        os.environ.pop("PUBLISH_MARKS", None)
        self._run_main_with_marks({})
        data = json.loads(self._read_wishlist_text())
        self.assertEqual(data["format"], "kindle-wishlist")
        self.assertEqual([b["asin"] for b in data["books"]], ["B0INTEG1"])
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "wishlist.json.tmp")))

    def test_main_writes_feed_xml_next_to_wishlist_json(self):
        """値下がり・読み放題入りのフィードも一緒に書き出す（自分自身へのリンクは PUBLIC_SITE_URL）。"""
        self._run_main_with_marks({})
        with open(os.path.join(self.tmpdir, "feed.xml"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('href="https://example.invalid/feed.xml"', text)
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "feed.xml.tmp")))

    def test_main_wishlist_publishes_only_kind_override_by_default(self):
        os.environ.pop("PUBLISH_MARKS", None)
        self._run_main_with_marks({})
        book = json.loads(self._read_wishlist_text())["books"][0]
        self.assertEqual((book["kind"], book["tag"], book["rating"]), ("manga", "", None))

    def test_main_wishlist_publishes_tags_and_ratings_when_opted_in(self):
        self._run_main_with_marks({"PUBLISH_MARKS": "1"})
        book = json.loads(self._read_wishlist_text())["books"][0]
        self.assertEqual((book["kind"], book["tag"], book["rating"]), ("manga", "seen", 5))

    def test_main_wishlist_keeps_japanese_readable(self):
        """差分を人が読めるよう、日本語をエスケープせずそのまま書く。"""
        self._run_main_with_marks({})
        self.assertIn("結合テスト本", self._read_wishlist_text())

    def test_main_does_not_write_index_html(self):
        """画面は book-highlights に移したので、公開ページ（index.html）は作らない。
        公開リポジトリの index.html は欲しい本の画面へ移動する静的ページで、ここから上書きしてはいけない。"""
        self._run_main_with_marks({})
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "index.html")))

    def test_main_publishes_price_trend_from_paid_price_points(self):
        self.mock_points.return_value = [
            {"paid_asin": "B0INTEG1", "actual_price": 1200, "timestamp": "2026-01-01T00:00:00"},
            {"paid_asin": "B0INTEG1", "actual_price": 1000, "timestamp": "2026-01-05T00:00:00"},
        ]
        self._run_main_with_marks({})
        book = json.loads(self._read_wishlist_text())["books"][0]
        self.assertEqual((book["price_prev"], book["price_changed_at"], book["price_low"]), (1200, "2026-01-05T00:00:00", 1000))

    def test_main_publishes_price_history_from_all_price_points(self):
        self.mock_all_points.return_value = [
            {"paid_asin": "B0INTEG1", "actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
        ]
        self._run_main_with_marks({})
        book = json.loads(self._read_wishlist_text())["books"][0]
        self.assertEqual(book["price_history"], [{"at": "2026-01-01T00:00:00", "price": 1000, "ku": False}])

    def test_main_reads_books_marks_and_price_points_once_not_per_book(self):
        """価格の記録は全冊分を 1 回で取る（1 冊ごとに履歴を問い合わせない）。"""
        fake_book = {"title": "結合テスト本", "asin": "B0INTEG1", "actual_price": 1000, "is_unlimited": 0, "is_wanted": 1}
        with unittest.mock.patch.object(report, "get_books", return_value=[fake_book]) as mock_get_books, unittest.mock.patch.object(
            report, "get_book_marks", return_value={}
        ) as mock_get_marks:
            report.main()
        mock_get_books.assert_called_once_with(filter="all")
        mock_get_marks.assert_called_once_with()
        self.mock_points.assert_called_once_with()
        self.assertFalse(hasattr(report, "get_price_history"))
        book = json.loads(self._read_wishlist_text())["books"][0]
        self.assertEqual((book["title"], book["wanted"], book["price"]), ("結合テスト本", True, 1000))


class ShrinkGuardTest(unittest.TestCase):
    """DB の不調で本が 0 冊（または急減）になったとき、公開中の wishlist.json を書き換えずに止めること。

    止めないと run.py の publish() がそのまま commit・push し、欲しい本の一覧が空のまま公開される。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="report_shrink_test_")
        os.makedirs(os.path.join(self.tmpdir, ".git"))
        self._env = unittest.mock.patch.dict(
            os.environ, {"PUBLIC_SITE_DIR": self.tmpdir, "PUBLIC_SITE_URL": "https://example.invalid/"}
        )
        self._env.start()
        self._points = unittest.mock.patch.object(report, "get_paid_price_points", return_value=[])
        self._points.start()
        self._all_points = unittest.mock.patch.object(report, "get_all_price_points", return_value=[])
        self._all_points.start()
        self.path = os.path.join(self.tmpdir, "wishlist.json")

    def tearDown(self):
        self._points.stop()
        self._all_points.stop()
        self._env.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _publish_existing(self, count):
        books = [{"asin": f"B0OLD{i:05d}"} for i in range(count)]
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"format": "kindle-wishlist", "version": 1, "books": books}, f)

    def _books(self, count):
        return [{"title": f"本{i}", "asin": f"B0NEW{i:05d}", "actual_price": 100} for i in range(count)]

    def _run_main(self, count, **kwargs):
        with unittest.mock.patch.object(report, "get_books", return_value=self._books(count)), unittest.mock.patch.object(
            report, "get_book_marks", return_value={}
        ):
            report.main(**kwargs)

    def _published_count(self):
        with open(self.path, encoding="utf-8") as f:
            return len(json.load(f)["books"])

    def test_zero_books_stops_without_overwriting(self):
        self._publish_existing(10)
        with self.assertRaises(SystemExit) as cm:
            self._run_main(0)
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(self._published_count(), 10)

    def test_zero_books_stops_even_on_first_publish(self):
        with self.assertRaises(SystemExit):
            self._run_main(0)
        self.assertFalse(os.path.exists(self.path))

    def test_less_than_half_stops_without_overwriting(self):
        self._publish_existing(10)
        with self.assertRaises(SystemExit) as cm:
            self._run_main(4)
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(self._published_count(), 10)

    def test_half_or_more_is_written(self):
        self._publish_existing(10)
        self._run_main(5)
        self.assertEqual(self._published_count(), 5)

    def test_allow_shrink_writes_even_when_sharply_decreased(self):
        """本当に減らしたとき（読書メーターで整理した等）は --allow-shrink で書き出せる。"""
        self._publish_existing(10)
        self._run_main(1, allow_shrink=True)
        self.assertEqual(self._published_count(), 1)

    def test_unreadable_existing_file_compares_with_nothing(self):
        """公開中のファイルが壊れていても書き出しは止めない（0 冊だけは止める）。"""
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{broken")
        self._run_main(1)
        self.assertEqual(self._published_count(), 1)

    def test_cli_accepts_allow_shrink(self):
        self.assertTrue(report.parse_args(["--allow-shrink"]).allow_shrink)
        self.assertFalse(report.parse_args([]).allow_shrink)


class HtmlGenerationRemovedTest(unittest.TestCase):
    """HTML を作る処理は book-highlights の JS に一本化したので、report.py に残さない（直す場所を 1 か所にする）。"""

    def test_no_html_builders_remain(self):
        for name in ("build_html", "_PAGE_STYLE", "_PAGE_HEADER_HTML", "_PAGE_SCRIPT", "_build_book_row", "_build_price_history_svg"):
            self.assertFalse(hasattr(report, name), name)


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
