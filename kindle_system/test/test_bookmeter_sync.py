"""
test_bookmeter_sync.py
-----------------------
src/bookmeter_sync.py の単体テスト（標準ライブラリ unittest）。

外部依存（読書メーター取得・ASIN解決・価格クロール・dedup登録）はすべて
モック化し、実ネットワーク・実ブラウザ・実DBへは一切接続しない。

使い方:
    python3 -m unittest test.test_bookmeter_sync -v
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src.bookmeter_sync import sync_bookmeter_wishlist


def _make_session_mock():
    """get_session() の `with get_session() as session:` を模倣するモックを返す。"""
    session = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx, session


class SyncBookmeterWishlistNormalTest(unittest.TestCase):
    """(a) 全冊がASIN解決成功→登録→クロール成功する正常系。"""

    @patch("src.bookmeter_sync.save_price_history")
    @patch("src.bookmeter_sync.crawl_price_info", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.get_or_create_by_paid_asin")
    @patch("src.bookmeter_sync.get_session")
    @patch("src.bookmeter_sync.resolve_title_to_paid_asin", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.fetch_wish_books")
    def test_all_books_registered_and_crawled(
        self, mock_fetch, mock_resolve, mock_get_session, mock_dedup, mock_crawl, mock_save_price
    ):
        mock_fetch.return_value = [
            {"title": "本A", "author": "著者A"},
            {"title": "本B", "author": "著者B"},
        ]
        mock_resolve.side_effect = ["B0AAAAAAAA", "B0BBBBBBBB"]
        ctx, session = _make_session_mock()
        mock_get_session.return_value = ctx
        mock_crawl.side_effect = [
            {"asin": "B0AAAAAAAA", "sell_price": 1000, "point_value": 10},
            {"asin": "B0BBBBBBBB", "sell_price": 2000, "point_value": 20},
        ]

        lines = []
        result = asyncio.run(sync_bookmeter_wishlist(progress_cb=lines.append))

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["registered"], 2)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["failed_titles"], [])
        self.assertEqual(mock_dedup.call_count, 2)
        self.assertEqual(mock_crawl.call_count, 2)
        self.assertEqual(mock_save_price.call_count, 2)
        # 登録が実際にDBへ反映されることを確認する（commitを消してもテストが
        # 通ってしまい「登録したのに保存されない」ことに気づけない事故を防ぐ）
        self.assertEqual(session.commit.call_count, 2)
        mock_dedup.assert_any_call(
            session, "B0AAAAAAAA", title="本A", source="bookmeter", is_wanted=1
        )
        mock_dedup.assert_any_call(
            session, "B0BBBBBBBB", title="本B", source="bookmeter", is_wanted=1
        )
        self.assertTrue(any("開始" in l for l in lines))
        self.assertTrue(any("完了" in l for l in lines))


class SyncBookmeterWishlistAsinFailureTest(unittest.TestCase):
    """(b) 一部の本でASIN解決が失敗する場合にスキップしログ記録した上で残りを継続する。"""

    @patch("src.bookmeter_sync.save_price_history")
    @patch("src.bookmeter_sync.crawl_price_info", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.get_or_create_by_paid_asin")
    @patch("src.bookmeter_sync.get_session")
    @patch("src.bookmeter_sync.resolve_title_to_paid_asin", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.fetch_wish_books")
    def test_asin_resolution_failure_is_skipped_and_continues(
        self, mock_fetch, mock_resolve, mock_get_session, mock_dedup, mock_crawl, mock_save_price
    ):
        mock_fetch.return_value = [
            {"title": "紙のみの本", "author": "著者C"},
            {"title": "本D", "author": "著者D"},
        ]
        # 1冊目は解決できず None、2冊目は成功
        mock_resolve.side_effect = [None, "B0DDDDDDDD"]
        ctx, session = _make_session_mock()
        mock_get_session.return_value = ctx
        mock_crawl.side_effect = [
            {"asin": "B0DDDDDDDD", "sell_price": 1500, "point_value": 0},
        ]

        lines = []
        result = asyncio.run(sync_bookmeter_wishlist(progress_cb=lines.append))

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["registered"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed_titles"], ["紙のみの本"])
        # ASIN解決に失敗した本は登録・クロールされない
        self.assertEqual(mock_dedup.call_count, 1)
        self.assertEqual(mock_crawl.call_count, 1)
        self.assertTrue(any("紙のみの本" in l for l in lines))


class SyncBookmeterWishlistCrawlFailureTest(unittest.TestCase):
    """
    (c) 価格クロールが失敗した場合もスキップして次の本へ進む。

    crawl_price_info は BAN検知・ページ取得失敗を例外にせず、
    sell_price=None の既定 dict を返す契約（src/crawler.py 参照）。
    そのため失敗の再現は例外の送出ではなく sell_price=None の返却で行う
    （例外を模した場合、実際には到達しない except 節だけを検証してしまい、
    本来の失敗経路である sell_price=None の判定漏れを検出できない）。
    予期しない例外（ブラウザ起動失敗等）に対する防御的な except 節も
    別途検証する。
    """

    @patch("src.bookmeter_sync.save_price_history")
    @patch("src.bookmeter_sync.crawl_price_info", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.get_or_create_by_paid_asin")
    @patch("src.bookmeter_sync.get_session")
    @patch("src.bookmeter_sync.resolve_title_to_paid_asin", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.fetch_wish_books")
    def test_crawl_returning_null_price_is_skipped_and_continues(
        self, mock_fetch, mock_resolve, mock_get_session, mock_dedup, mock_crawl, mock_save_price
    ):
        mock_fetch.return_value = [
            {"title": "クロール失敗本", "author": "著者E"},
            {"title": "本F", "author": "著者F"},
        ]
        mock_resolve.side_effect = ["B0EEEEEEEE", "B0FFFFFFFF"]
        ctx, session = _make_session_mock()
        mock_get_session.return_value = ctx
        mock_crawl.side_effect = [
            # BAN検知・ページ取得失敗時の実際の戻り値（crawler.py の既定dict）
            {"asin": "B0EEEEEEEE", "sell_price": None, "point_value": 0, "campaign_text": "", "is_unlimited": 0},
            {"asin": "B0FFFFFFFF", "sell_price": 3000, "point_value": 0},
        ]

        lines = []
        result = asyncio.run(sync_bookmeter_wishlist(progress_cb=lines.append))

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["registered"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed_titles"], ["クロール失敗本"])
        # 両方ともASIN解決・dedup登録は行われる（登録自体はクロール前に完了するため）
        self.assertEqual(mock_dedup.call_count, 2)
        self.assertEqual(mock_crawl.call_count, 2)
        # 価格取得不可の本は price_history へ保存されない（NULL価格を保存しない）
        self.assertEqual(mock_save_price.call_count, 1)
        self.assertTrue(any("クロール失敗本" in l for l in lines))

    @patch("src.bookmeter_sync.save_price_history")
    @patch("src.bookmeter_sync.crawl_price_info", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.get_or_create_by_paid_asin")
    @patch("src.bookmeter_sync.get_session")
    @patch("src.bookmeter_sync.resolve_title_to_paid_asin", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.fetch_wish_books")
    def test_crawl_unexpected_exception_is_skipped_and_continues(
        self, mock_fetch, mock_resolve, mock_get_session, mock_dedup, mock_crawl, mock_save_price
    ):
        """crawl_price_info が想定外の例外を送出した場合の防御的経路。"""
        mock_fetch.return_value = [{"title": "本G", "author": "著者G"}]
        mock_resolve.side_effect = ["B0GGGGGGGG"]
        ctx, session = _make_session_mock()
        mock_get_session.return_value = ctx
        mock_crawl.side_effect = [RuntimeError("ブラウザ起動に失敗しました")]

        lines = []
        result = asyncio.run(sync_bookmeter_wishlist(progress_cb=lines.append))

        self.assertEqual(result["registered"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed_titles"], ["本G"])
        mock_save_price.assert_not_called()


if __name__ == "__main__":
    unittest.main()
