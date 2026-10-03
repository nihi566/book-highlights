"""
test_bookmeter.py
------------------
src/bookmeter.py の単体テスト（標準ライブラリ unittest）。

固定HTML fixture（実際の読書メーター「読みたい本」一覧ページの構造に基づく）を用い、
実ネットワークへは一切接続しない。ネットワークアクセスは requests.Session.get を
モック化して差し替える。

使い方:
    python3 -m unittest test.test_bookmeter
    python3 test/test_bookmeter.py
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

import requests

# プロジェクトルートを sys.path に追加し、直接実行でも `src` パッケージを解決できるようにする
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src.bookmeter import fetch_wish_books, parse_books, get_next_page_url


# ─── fixture: 1ページ目相当（次ページあり・2冊） ──────────────────────────────────
FIXTURE_PAGE_1 = """
<ul class="book-list__group">
  <li class="group__book">
    <div class="book__detail">
      <div class="detail__title"><a href="/books/15472989">紛争でしたら八田まで(1) (モーニングKC)</a></div>
      <ul class="detail__authors"><li><a href="/search?author=a">田 素弘</a></li></ul>
    </div>
  </li>
  <li class="group__book">
    <div class="book__detail">
      <div class="detail__title"><a href="/books/9837512">消費社会の神話と構造 新装版</a></div>
      <ul class="detail__authors"><li><a href="/search?author=b">ジャン ボードリヤール</a></li></ul>
    </div>
  </li>
</ul>
<ul class="bm-pagination">
  <li class="active"><a class="bm-pagination__link" href="/users/1770332/books/wish">1</a></li>
  <li><a class="bm-pagination__link" rel="next" href="/users/1770332/books/wish?page=2">2</a></li>
  <li><a rel="next" class="bm-pagination__link" href="/users/1770332/books/wish?page=2">次</a></li>
</ul>
"""

# ─── fixture: 最終ページ相当（次ページなし・共著1冊） ─────────────────────────────
# 実サイトでは最終ページの「次」は <a> ではなく disable な <div> になる（rel="next" が存在しない）
FIXTURE_PAGE_LAST = """
<ul class="book-list__group">
  <li class="group__book">
    <div class="book__detail">
      <div class="detail__title"><a href="/books/2001">共著の本</a></div>
      <ul class="detail__authors">
        <li><a href="/search?author=c">著者A</a></li>
        <li><a href="/search?author=d">著者B</a></li>
      </ul>
    </div>
  </li>
</ul>
<ul class="bm-pagination">
  <li class="disable"><div class="bm-pagination__link">次</div></li>
</ul>
"""

WISH_URL = "https://bookmeter.com/users/1770332/books/wish"
PAGE_2_URL = "https://bookmeter.com/users/1770332/books/wish?page=2"


def _mock_response(text, status_ok=True):
    resp = MagicMock()
    resp.text = text
    if status_ok:
        resp.raise_for_status = MagicMock()
    else:
        resp.raise_for_status = MagicMock(side_effect=requests.exceptions.HTTPError("500"))
    return resp


class ParseBooksTest(unittest.TestCase):
    def test_extracts_title_and_author(self):
        books = parse_books(FIXTURE_PAGE_1)
        self.assertEqual(
            books,
            [
                {"title": "紛争でしたら八田まで(1) (モーニングKC)", "author": "田 素弘", "bookmeter_id": "15472989"},
                {"title": "消費社会の神話と構造 新装版", "author": "ジャン ボードリヤール", "bookmeter_id": "9837512"},
            ],
        )

    def test_joins_multiple_authors(self):
        books = parse_books(FIXTURE_PAGE_LAST)
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["title"], "共著の本")
        self.assertEqual(books[0]["author"], "著者A、著者B")

    def test_empty_html_returns_empty_list(self):
        self.assertEqual(parse_books("<html><body></body></html>"), [])

    def test_missing_author_returns_empty_author_string_and_continues(self):
        # 著者要素が無い書籍でも例外にせず、空文字の著者として継続する
        html = """
        <ul class="book-list__group">
          <li class="group__book">
            <div class="book__detail">
              <div class="detail__title"><a href="/books/1">著者不明の本</a></div>
              <ul class="detail__authors"></ul>
            </div>
          </li>
        </ul>
        """
        books = parse_books(html)
        self.assertEqual(books, [{"title": "著者不明の本", "author": "", "bookmeter_id": "1"}])


class ParseBooksFullTitleTest(unittest.TestCase):
    """一覧の書名リンクは長い書名を「…」で切るので、表紙画像の alt（切れていない書名）を使う。"""

    @staticmethod
    def _item(title_text, img=""):
        return f"""
        <ul class="book-list__group">
          <li class="group__book">
            <div class="book__thumbnail"><div class="thumbnail__cover"><a href="/books/1">{img}</a></div></div>
            <div class="book__detail">
              <div class="detail__title"><a href="/books/1">{title_text}</a></div>
              <ul class="detail__authors"><li><a href="/search?author=a">著者</a></li></ul>
            </div>
          </li>
        </ul>
        """

    def test_uses_cover_alt_when_link_text_is_truncated(self):
        html = self._item(
            "消費者行動の知識 （日経文庫） (日経文庫 …",
            '<img alt="消費者行動の知識 （日経文庫） (日経文庫 1415)" class="cover__image" src="x.jpg">',
        )
        self.assertEqual(parse_books(html)[0]["title"], "消費者行動の知識 （日経文庫） (日経文庫 1415)")

    def test_keeps_link_text_without_cover_alt(self):
        self.assertEqual(parse_books(self._item("短い書名 (…"))[0]["title"], "短い書名 (…")
        html = self._item("短い書名 (…", '<img alt="" class="cover__image" src="x.jpg">')
        self.assertEqual(parse_books(html)[0]["title"], "短い書名 (…")

    def test_ignores_cover_alt_of_a_different_title(self):
        # alt が書名の続きでなければ（別の文言なら）使わない
        html = self._item("ある本 (…", '<img alt="表紙画像" class="cover__image" src="x.jpg">')
        self.assertEqual(parse_books(html)[0]["title"], "ある本 (…")


class GetNextPageUrlTest(unittest.TestCase):
    def test_returns_next_url_when_present(self):
        url = get_next_page_url(FIXTURE_PAGE_1, WISH_URL)
        self.assertEqual(url, PAGE_2_URL)

    def test_returns_none_when_absent(self):
        self.assertIsNone(get_next_page_url(FIXTURE_PAGE_LAST, PAGE_2_URL))

    def test_detects_next_url_with_multi_value_rel_attribute(self):
        # rel は複数値属性になりうる(例: rel="next nofollow")。完全一致セレクタ
        # (a[rel="next"])だと検出できず1ページ目で正常終了したように見えてしまう
        html = (
            '<a class="bm-pagination__link" rel="next nofollow" '
            'href="/users/1770332/books/wish?page=2">次</a>'
        )
        self.assertEqual(get_next_page_url(html, WISH_URL), PAGE_2_URL)


class FetchWishBooksTest(unittest.TestCase):
    @patch("src.bookmeter.time.sleep", return_value=None)
    @patch("src.bookmeter.requests.Session.get")
    def test_paginates_until_last_page(self, mock_get, mock_sleep):
        mock_get.side_effect = [
            _mock_response(FIXTURE_PAGE_1),
            _mock_response(FIXTURE_PAGE_LAST),
        ]

        books = fetch_wish_books()

        self.assertEqual(len(books), 3)
        self.assertEqual(books[0]["title"], "紛争でしたら八田まで(1) (モーニングKC)")
        self.assertEqual(books[2]["author"], "著者A、著者B")
        self.assertEqual(mock_get.call_count, 2)
        called_urls = [call.args[0] for call in mock_get.call_args_list]
        self.assertEqual(called_urls, [WISH_URL, PAGE_2_URL])
        # リクエスト間の待機を入れているが、テストでは time.sleep をモック化しているため実待機しない
        self.assertTrue(mock_sleep.called)

    @patch("src.bookmeter.time.sleep", return_value=None)
    @patch("src.bookmeter.requests.Session.get")
    def test_network_error_returns_empty_list_without_raising(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.exceptions.ConnectionError("boom")

        books = fetch_wish_books()

        self.assertEqual(books, [])

    @patch("src.bookmeter.time.sleep", return_value=None)
    @patch("src.bookmeter.requests.Session.get")
    def test_mid_pagination_failure_keeps_already_fetched_books(self, mock_get, mock_sleep):
        # 1ページ目は成功、2ページ目で一時的なネットワークエラーが発生するケース。
        # 1ページ目で取得済みの分まで捨ててはならない（全破棄すると呼び出し元は
        # 「読みたい本が無い」と「取得に失敗した」を区別できなくなる）
        mock_get.side_effect = [
            _mock_response(FIXTURE_PAGE_1),
            requests.exceptions.ConnectionError("boom"),
        ]

        books = fetch_wish_books()

        self.assertEqual(len(books), 2)
        self.assertEqual(books[0]["title"], "紛争でしたら八田まで(1) (モーニングKC)")

    @patch("src.bookmeter.time.sleep", return_value=None)
    @patch("src.bookmeter.requests.Session.get")
    def test_http_error_status_returns_empty_list_without_raising(self, mock_get, mock_sleep):
        mock_get.return_value = _mock_response(FIXTURE_PAGE_1, status_ok=False)

        books = fetch_wish_books()

        self.assertEqual(books, [])

    @patch("src.bookmeter.time.sleep", return_value=None)
    @patch("src.bookmeter.requests.Session.get")
    def test_safety_page_limit_stops_infinite_pagination(self, mock_get, mock_sleep):
        # 呼び出しのたびに「次」リンクの参照先を進め、正常にページが進み続ける状況を再現する。
        # それでも安全上限（max_pages）で必ず停止することを確認する
        def ever_advancing_page(url, timeout=None):
            page_no = int(url.split("page=")[1]) if "page=" in url else 1
            html = f"""
            <ul class="book-list__group">
              <li class="group__book">
                <div class="book__detail">
                  <div class="detail__title"><a href="/books/{page_no}">書籍{page_no}</a></div>
                  <ul class="detail__authors"><li><a href="/search?author=x">著者{page_no}</a></li></ul>
                </div>
              </li>
            </ul>
            <ul class="bm-pagination">
              <li><a rel="next" class="bm-pagination__link"
                 href="/users/1770332/books/wish?page={page_no + 1}">次</a></li>
            </ul>
            """
            return _mock_response(html)

        mock_get.side_effect = ever_advancing_page

        books = fetch_wish_books(max_pages=3)

        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(len(books), 3)  # 1冊 x 3ページ

    @patch("src.bookmeter.time.sleep", return_value=None)
    @patch("src.bookmeter.requests.Session.get")
    def test_corrects_encoding_when_response_misdetected_as_latin1(self, mock_get, mock_sleep):
        # Content-Type に charset が無い応答で response.encoding が誤って
        # "ISO-8859-1" のままだと、日本語タイトルが無音で文字化けする。
        # MagicMockではなく実際のrequests.Responseを使い、バイト列からの
        # デコード結果そのもの（response.textの中身）で検証する
        resp = requests.Response()
        resp.status_code = 200
        resp._content = FIXTURE_PAGE_LAST.encode("utf-8")
        resp.headers["Content-Type"] = "text/html"
        resp.encoding = "ISO-8859-1"  # 誤判定された状態を模擬
        mock_get.return_value = resp

        books = fetch_wish_books()

        self.assertEqual(books[0]["title"], "共著の本")
        self.assertEqual(books[0]["author"], "著者A、著者B")

    @patch("src.bookmeter.time.sleep", return_value=None)
    @patch("src.bookmeter.requests.Session.get")
    def test_encoding_correction_falls_back_to_utf8_when_apparent_encoding_unknown(
        self, mock_get, mock_sleep
    ):
        # apparent_encoding 自体がNoneを返す極端なケースでも、少なくとも例外で
        # 落ちずにutf-8へフォールバックすることを確認する
        resp = _mock_response(FIXTURE_PAGE_LAST)
        resp.encoding = None
        resp.apparent_encoding = None
        mock_get.return_value = resp

        books = fetch_wish_books()

        self.assertEqual(resp.encoding, "utf-8")
        self.assertEqual(books[0]["title"], "共著の本")


if __name__ == "__main__":
    unittest.main()
