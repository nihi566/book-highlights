"""
test_feed.py
-------------
report.build_feed()（値下がり・読み放題入りを知らせる Atom フィード feed.xml）の単体テスト。

実行:
    python -m unittest discover -s test -p test_feed.py -v
"""

import os
import sys
import unittest
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import report

ATOM = "{http://www.w3.org/2005/Atom}"
SITE = "https://example.invalid/kindle-wishlist-site/"


def _book(**overrides):
    book = {
        "asin": "B0FEED0001",
        "title": "欲しい本",
        "price": 900,
        "ku": False,
        "wanted": True,
        "purchased": False,
        "scraped_at": "2026-10-03T09:00:00",
        "price_prev": None,
        "price_changed_at": None,
        "price_low": None,
        "price_history": [],
    }
    book.update(overrides)
    return book


def _wishlist(*books):
    return {"format": "kindle-wishlist", "version": 1, "last_scraped": "2026-10-03T09:00:00", "books": list(books)}


def _entries(xml_text):
    root = ET.fromstring(xml_text)
    return [
        {
            "id": e.findtext(f"{ATOM}id"),
            "title": e.findtext(f"{ATOM}title"),
            "updated": e.findtext(f"{ATOM}updated"),
            "link": e.find(f"{ATOM}link").get("href"),
        }
        for e in root.findall(f"{ATOM}entry")
    ]


class BuildFeedTest(unittest.TestCase):
    def test_price_drop_becomes_an_entry(self):
        xml_text = report.build_feed(_wishlist(_book(price=700, price_prev=1000, price_changed_at="2026-10-02T08:00:00")), SITE)
        [entry] = _entries(xml_text)
        self.assertIn("値下がり", entry["title"])
        self.assertIn("¥1,000 → ¥700", entry["title"])
        self.assertIn("欲しい本", entry["title"])
        self.assertEqual(entry["link"], "https://www.amazon.co.jp/dp/B0FEED0001")
        self.assertTrue(entry["updated"].startswith("2026-10-02T08:00:00"))
        self.assertRegex(entry["updated"], r"[+-]\d\d:\d\d$|Z$", "Atom の日時はタイムゾーン付き")
        self.assertIn("B0FEED0001", entry["id"])

    def test_price_rise_purchased_and_unchanged_books_are_not_entries(self):
        books = [
            _book(asin="B0FEED0002", price=1200, price_prev=1000, price_changed_at="2026-10-02T08:00:00"),
            _book(asin="B0FEED0003", price=700, price_prev=1000, price_changed_at="2026-10-02T08:00:00", purchased=True),
            _book(asin="B0FEED0004"),
        ]
        self.assertEqual(_entries(report.build_feed(_wishlist(*books), SITE)), [])

    def test_joining_kindle_unlimited_becomes_an_entry(self):
        history = [
            {"at": "2026-09-30T09:00:00", "price": 1000, "ku": False},
            {"at": "2026-10-01T09:00:00", "price": None, "ku": False},
            {"at": "2026-10-02T09:00:00", "price": None, "ku": True},
            {"at": "2026-10-03T09:00:00", "price": None, "ku": True},
        ]
        [entry] = _entries(report.build_feed(_wishlist(_book(price=None, ku=True, price_history=history)), SITE))
        self.assertIn("読み放題", entry["title"])
        self.assertTrue(entry["updated"].startswith("2026-10-02T09:00:00"), "取得に失敗した回（価格なし・KU でない）は飛ばして、入った回の時刻")

    def test_book_that_was_always_ku_or_left_ku_is_not_an_entry(self):
        always = [{"at": "2026-10-01T09:00:00", "price": None, "ku": True}, {"at": "2026-10-02T09:00:00", "price": None, "ku": True}]
        left = [{"at": "2026-10-01T09:00:00", "price": 1000, "ku": False}, {"at": "2026-10-02T09:00:00", "price": None, "ku": True}, {"at": "2026-10-03T09:00:00", "price": 1000, "ku": False}]
        books = [_book(asin="B0FEED0005", price=None, ku=True, price_history=always), _book(asin="B0FEED0006", price=1000, price_history=left)]
        self.assertEqual(_entries(report.build_feed(_wishlist(*books), SITE)), [])

    def test_titles_are_escaped_and_bad_asin_links_to_the_site(self):
        book = _book(asin="", title="A & B <C>", price=700, price_prev=1000, price_changed_at="2026-10-02T08:00:00")
        xml_text = report.build_feed(_wishlist(book), SITE)
        [entry] = _entries(xml_text)
        self.assertIn("A & B <C>", entry["title"])
        self.assertNotIn("<C>", xml_text)
        self.assertEqual(entry["link"], SITE)

    def test_newest_first_and_capped(self):
        books = [
            _book(asin=f"B0FEED{i:04d}", price=500, price_prev=1000, price_changed_at=f"2026-0{1 + i // 28}-{1 + i % 28:02d}T00:00:00")
            for i in range(report.MAX_FEED_ENTRIES + 5)
        ]
        entries = _entries(report.build_feed(_wishlist(*books), SITE))
        self.assertEqual(len(entries), report.MAX_FEED_ENTRIES)
        self.assertEqual(entries, sorted(entries, key=lambda e: e["updated"], reverse=True))

    def test_feed_header_and_output_is_stable(self):
        wl = _wishlist(_book(price=700, price_prev=1000, price_changed_at="2026-10-02T08:00:00"))
        xml_text = report.build_feed(wl, SITE)
        self.assertEqual(xml_text, report.build_feed(wl, SITE), "同じデータなら同じ内容（自動公開で無駄な差分を出さない）")
        root = ET.fromstring(xml_text)
        self.assertEqual(root.tag, f"{ATOM}feed")
        self.assertEqual(root.find(f"{ATOM}link[@rel='self']").get("href"), SITE + "feed.xml")
        self.assertTrue(root.findtext(f"{ATOM}updated").startswith("2026-10-02T08:00:00"))

    def test_empty_feed_is_valid(self):
        root = ET.fromstring(report.build_feed(_wishlist(), SITE))
        self.assertEqual(root.findall(f"{ATOM}entry"), [])
        self.assertTrue(root.findtext(f"{ATOM}updated"))


if __name__ == "__main__":
    unittest.main()
