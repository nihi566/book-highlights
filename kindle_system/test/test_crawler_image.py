"""
test_crawler_image.py
---------------------
crawler.extract_cover_image_url（読み込み済みのページから表紙画像の URL を読む）の単体テスト。

scraping-hub の実行画面は `[Worker-N] Image URL : <https の URL>` の行から表紙を表示するため、
https の URL だけを返し、取れないときは空文字を返す（例外で 1 冊の処理を止めない）。
実ブラウザは使わず、page.evaluate の戻り値だけを差し替える。

使い方:
    python -m unittest discover -s test -p "test_crawler_image.py" -v
"""

import asyncio
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src import crawler


class FakePage:
    def __init__(self, value=None, error=None):
        self._value = value
        self._error = error

    async def evaluate(self, script):
        if self._error:
            raise self._error
        return self._value


def run(coro):
    return asyncio.run(coro)


class ExtractCoverImageUrlTest(unittest.TestCase):
    def test_returns_https_url(self):
        url = "https://m.media-amazon.com/images/I/81abc.jpg"
        self.assertEqual(run(crawler.extract_cover_image_url(FakePage(url))), url)

    def test_ignores_non_https_values(self):
        for value in ["data:image/gif;base64,R0lGOD", "http://example.com/a.jpg", "", None, 123]:
            with self.subTest(value=value):
                self.assertEqual(run(crawler.extract_cover_image_url(FakePage(value))), "")

    def test_rejects_urls_with_whitespace(self):
        # 1 行のログに出すので、空白・改行を含むものは使わない
        self.assertEqual(run(crawler.extract_cover_image_url(FakePage("https://a.example/x y.jpg"))), "")

    def test_returns_empty_when_page_fails(self):
        self.assertEqual(run(crawler.extract_cover_image_url(FakePage(error=RuntimeError("boom")))), "")

    def test_image_line_format(self):
        self.assertEqual(
            crawler.format_image_url_line("[Worker-2]", "https://m.media-amazon.com/images/I/a.jpg"),
            "  [Worker-2] Image URL   : https://m.media-amazon.com/images/I/a.jpg",
        )

    def test_image_line_without_worker_prefix(self):
        self.assertEqual(
            crawler.format_image_url_line("", "https://m.media-amazon.com/images/I/a.jpg"),
            "  Image URL   : https://m.media-amazon.com/images/I/a.jpg",
        )


if __name__ == "__main__":
    unittest.main()
