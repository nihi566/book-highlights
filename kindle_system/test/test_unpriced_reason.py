"""
test_unpriced_reason.py
------------------------
価格が取れなかった理由（販売終了の可能性か、取り直しが要る失敗か）を記録し、
wishlist.json の price_reason に載せることの単体テスト。

実行:
    python -m unittest discover -s test -p test_unpriced_reason.py -v
"""

import os
import shutil
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import report
from src import repository
from src.crawler import classify_unpriced


class ClassifyUnpricedTest(unittest.TestCase):
    """ページを開いた後に価格が無かったときの理由（crawler.classify_unpriced）。"""

    def test_price_found_has_no_reason(self):
        self.assertIsNone(classify_unpriced(sell_price=1200, http_status=200))
        self.assertIsNone(classify_unpriced(sell_price=0, http_status=200), "KU は 0 円で保存される（価格あり扱い）")

    def test_missing_product_page_is_not_found(self):
        self.assertEqual(classify_unpriced(sell_price=None, http_status=404), "not_found")

    def test_page_opened_without_price_is_no_price(self):
        self.assertEqual(classify_unpriced(sell_price=None, http_status=200), "no_price")
        self.assertEqual(classify_unpriced(sell_price=None, http_status=None), "no_price")

    def test_server_error_needs_retry(self):
        self.assertEqual(classify_unpriced(sell_price=None, http_status=503), "page_error")


class UnpricedReasonStorageTest(unittest.TestCase):
    """save_price_history が理由を価格の記録と同じ時刻で残し、get_unpriced_reasons で読めること。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="unpriced_reason_test_")
        from sqlmodel import SQLModel, create_engine
        self.engine = create_engine(f"sqlite:///{os.path.join(self.tmpdir, 'u.db')}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)
        import src.database as database_module
        self._original_engine = database_module.engine
        database_module.engine = self.engine

    def tearDown(self):
        import src.database as database_module
        database_module.engine = self._original_engine
        self.engine.dispose()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _latest_timestamp(self, asin):
        return repository.get_price_history(asin)[-1]["timestamp"]

    def test_reason_is_saved_with_the_same_timestamp_as_the_price_row(self):
        repository.save_price_history({"asin": "B0NOPRICE1", "sell_price": None, "unpriced_reason": "not_found"})
        reasons = repository.get_unpriced_reasons()
        self.assertEqual(reasons["B0NOPRICE1"]["reason"], "not_found")
        self.assertEqual(reasons["B0NOPRICE1"]["at"], self._latest_timestamp("B0NOPRICE1"))

    def test_latest_failure_overwrites_and_price_rows_store_nothing(self):
        repository.save_price_history({"asin": "B0RETRY001", "sell_price": None, "unpriced_reason": "blocked"})
        repository.save_price_history({"asin": "B0RETRY001", "sell_price": None, "unpriced_reason": "no_price"})
        repository.save_price_history({"asin": "B0PRICED01", "sell_price": 800, "unpriced_reason": None})
        reasons = repository.get_unpriced_reasons()
        self.assertEqual(reasons["B0RETRY001"]["reason"], "no_price")
        self.assertNotIn("B0PRICED01", reasons)

    def test_unknown_reason_is_not_saved(self):
        repository.save_price_history({"asin": "B0BADREAS1", "sell_price": None, "unpriced_reason": "<script>"})
        self.assertNotIn("B0BADREAS1", repository.get_unpriced_reasons())

    def test_missing_table_returns_empty(self):
        from sqlmodel import text
        with repository.get_session() as session:
            session.exec(text("DROP TABLE unpriced_reasons"))
            session.commit()
        self.assertEqual(repository.get_unpriced_reasons(), {})


class PriceReasonInWishlistTest(unittest.TestCase):
    """wishlist.json の price_reason（価格が null の本だけ。理由は最新の取得と同じ時刻のものだけ使う）。"""

    def _book(self, **overrides):
        book = {"title": "本", "asin": "B0WISH001", "actual_price": None, "timestamp": "2026-10-01T00:00:00", "is_unlimited": 0, "is_wanted": 1, "is_purchased": 0}
        book.update(overrides)
        return report.build_wishlist([book])["books"][0]["price_reason"]

    def test_priced_book_has_no_reason(self):
        self.assertIsNone(self._book(actual_price=900))

    def test_ku_and_not_scraped(self):
        self.assertEqual(self._book(is_unlimited=1, actual_price=0), "ku")
        self.assertEqual(self._book(timestamp=None), "not_scraped")

    def test_recorded_reason_of_the_latest_scrape(self):
        for reason in ("not_found", "no_price", "blocked", "page_error"):
            self.assertEqual(self._book(unpriced_reason={"reason": reason, "at": "2026-10-01T00:00:00"}), reason)

    def test_stale_or_missing_reason_is_unknown(self):
        """理由を残す前の取得（古いデータ）や、別の回の理由は使わない。"""
        self.assertEqual(self._book(), "unknown")
        self.assertEqual(self._book(unpriced_reason={"reason": "not_found", "at": "2026-09-01T00:00:00"}), "unknown")


if __name__ == "__main__":
    unittest.main()
