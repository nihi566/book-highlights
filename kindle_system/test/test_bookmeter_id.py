"""
test_bookmeter_id.py
---------------------
読書メーターの本 ID（一覧の書名リンク /books/<数字> から取る）を book_mappings.bookmeter_id に保存し、
wishlist.json の bookmeter_id に載せることの単体テスト。

実行:
    python -m unittest discover -s test -p test_bookmeter_id.py -v
"""

import asyncio
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import report
from src import repository
from src.bookmeter import parse_books
from src.bookmeter_sync import sync_bookmeter_wishlist


def _columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return [row[1] for row in conn.execute("PRAGMA table_info(book_mappings)")]
    finally:
        conn.close()


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM book_mappings ORDER BY id")]
    finally:
        conn.close()


class MigrationAddsBookmeterIdTest(unittest.TestCase):
    """既存の DB（bookmeter_id 列が無い、今の形の book_mappings）を開くと、列を足すだけで行・値は変えない。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_id_migration_test_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE book_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sample_asin VARCHAR,
                    paid_asin VARCHAR,
                    title VARCHAR,
                    created_at VARCHAR,
                    is_purchased INTEGER NOT NULL DEFAULT 0,
                    is_wanted INTEGER NOT NULL DEFAULT 0,
                    source VARCHAR NOT NULL DEFAULT 'kindle_sample',
                    from_kindle_sample INTEGER NOT NULL DEFAULT 0,
                    from_bookmeter INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("CREATE TABLE price_history (id INTEGER PRIMARY KEY, paid_asin VARCHAR, sell_price INTEGER, timestamp VARCHAR)")
            conn.executemany(
                "INSERT INTO book_mappings (sample_asin, paid_asin, title, created_at, is_purchased, is_wanted, source, from_kindle_sample, from_bookmeter) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("B0KS001", "B0KSPAID01", "キンドルの本", "2026-01-01T00:00:00", 1, 0, "kindle_sample", 1, 0),
                    # フラグをあえて source と食い違わせておく（列の追加でフラグを付け直さないことを確かめる）
                    (None, "B0BMPAID01", "読書メーターの本", "2026-01-02T00:00:00", 0, 1, "bookmeter", 0, 0),
                ],
            )
            conn.execute("INSERT INTO price_history (paid_asin, sell_price, timestamp) VALUES ('B0BMPAID01', 900, '2026-01-03T00:00:00')")
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_adds_bookmeter_id_column_and_keeps_rows_as_is(self):
        before = _rows(self.db_path)
        repository.migrate_book_mappings_schema(self.db_path)
        self.assertIn("bookmeter_id", _columns(self.db_path))
        after = _rows(self.db_path)
        self.assertEqual([{k: v for k, v in r.items() if k != "bookmeter_id"} for r in after], before)
        self.assertEqual([r["bookmeter_id"] for r in after], [None, None])
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0], 1)
        finally:
            conn.close()

    def test_second_run_changes_nothing(self):
        repository.migrate_book_mappings_schema(self.db_path)
        once = _rows(self.db_path)
        repository.migrate_book_mappings_schema(self.db_path)
        self.assertEqual(_rows(self.db_path), once)


class ParseBookmeterIdTest(unittest.TestCase):
    def _html(self, href):
        return f"""
        <li class="group__book"><div class="book__detail">
          <div class="detail__title"><a href="{href}">本</a></div>
          <ul class="detail__authors"><li><a href="/search?author=a">著者</a></li></ul>
        </div></li>
        """

    def test_takes_id_from_title_link(self):
        self.assertEqual(parse_books(self._html("/books/22690039"))[0]["bookmeter_id"], "22690039")
        self.assertEqual(parse_books(self._html("https://bookmeter.com/books/15472989"))[0]["bookmeter_id"], "15472989")

    def test_unexpected_link_has_empty_id(self):
        for href in ("/users/1/books", "/books/12a", "https://evil.example/books/123", "javascript:alert(1)"):
            self.assertEqual(parse_books(self._html(href))[0]["bookmeter_id"], "", href)


class _DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_id_db_test_")
        from sqlmodel import SQLModel, create_engine
        self.engine = create_engine(f"sqlite:///{os.path.join(self.tmpdir, 'b.db')}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)
        import src.database as database_module
        self._original_engine = database_module.engine
        database_module.engine = self.engine

    def tearDown(self):
        import src.database as database_module
        database_module.engine = self._original_engine
        self.engine.dispose()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class SaveBookmeterIdTest(_DbTestCase):
    def test_get_or_create_saves_valid_id_and_ignores_invalid(self):
        from sqlmodel import Session
        with Session(self.engine) as session:
            book = repository.get_or_create_by_paid_asin(session, "B0NEWID001", title="本", bookmeter_id="123456")
            self.assertEqual(book.bookmeter_id, "123456")
            book = repository.get_or_create_by_paid_asin(session, "B0NEWID001", bookmeter_id="999")
            self.assertEqual(book.bookmeter_id, "999", "読書メーター側で変われば新しい ID にする")
            book = repository.get_or_create_by_paid_asin(session, "B0NEWID001", bookmeter_id="../x")
            self.assertEqual(book.bookmeter_id, "999", "形の合わない ID は保存しない")
            book = repository.get_or_create_by_paid_asin(session, "B0NEWID002", title="別の本", bookmeter_id="javascript:1")
            self.assertIsNone(book.bookmeter_id)

    def test_attach_ids_to_existing_rows_by_exact_unique_title(self):
        """ASIN 解決に失敗して get_or_create されない行にも、一覧の書名が 1 つに一致すれば ID を付ける。"""
        from sqlmodel import Session, select
        from src.models import BookMapping
        with Session(self.engine) as session:
            for asin, title, source, bid in [
                ("B0ATTACH01", "一致する本", "bookmeter", None),
                ("B0ATTACH02", "同じ書名の本", "bookmeter", None),
                ("B0ATTACH03", "キンドルの本", "kindle_sample", None),
                ("B0ATTACH04", "付いている本", "bookmeter", "111"),
            ]:
                session.add(BookMapping(paid_asin=asin, title=title, source=source, is_wanted=1, bookmeter_id=bid))
            session.commit()
        attached = repository.attach_bookmeter_ids([
            {"title": "一致する本", "bookmeter_id": "100"},
            {"title": "同じ書名の本", "bookmeter_id": "200"},
            {"title": "同じ書名の本", "bookmeter_id": "201"},
            {"title": "キンドルの本", "bookmeter_id": "300"},
            {"title": "付いている本", "bookmeter_id": "400"},
            {"title": "形が違う", "bookmeter_id": "x"},
        ])
        self.assertEqual(attached, 1)
        with Session(self.engine) as session:
            ids = {b.paid_asin: b.bookmeter_id for b in session.exec(select(BookMapping)).all()}
        self.assertEqual(ids, {"B0ATTACH01": "100", "B0ATTACH02": None, "B0ATTACH03": None, "B0ATTACH04": "111"})

    def test_get_books_returns_bookmeter_id(self):
        from sqlmodel import Session
        from src.models import BookMapping
        with Session(self.engine) as session:
            session.add(BookMapping(paid_asin="B0GETBOOK1", title="本", source="bookmeter", is_wanted=1, bookmeter_id="555"))
            session.commit()
        self.assertEqual(repository.get_books()[0]["bookmeter_id"], "555")


class SyncPassesBookmeterIdTest(unittest.TestCase):
    @patch("src.bookmeter_sync.save_price_history")
    @patch("src.bookmeter_sync.crawl_price_info", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.get_or_create_by_paid_asin")
    @patch("src.bookmeter_sync.get_session")
    @patch("src.bookmeter_sync.resolve_title_to_paid_asin", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.attach_bookmeter_ids")
    @patch("src.bookmeter_sync.fix_truncated_bookmeter_titles")
    @patch("src.bookmeter_sync.fetch_wish_books")
    def test_id_is_saved_with_the_book_and_attached_before_resolving(
        self, mock_fetch, mock_fix, mock_attach, mock_resolve, mock_get_session, mock_dedup, mock_crawl, mock_save
    ):
        books = [{"title": "本A", "author": "a", "bookmeter_id": "123"}]
        mock_fetch.return_value = books
        mock_fix.return_value = 0
        mock_attach.return_value = 0
        mock_resolve.return_value = "B0AAAAAAAA"
        session = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_get_session.return_value = ctx
        mock_crawl.return_value = {"asin": "B0AAAAAAAA", "sell_price": 1000, "point_value": 0}

        asyncio.run(sync_bookmeter_wishlist())

        mock_attach.assert_called_once_with(books)
        mock_dedup.assert_called_once_with(session, "B0AAAAAAAA", title="本A", source="bookmeter", is_wanted=1, bookmeter_id="123")


class WishlistBookmeterIdTest(unittest.TestCase):
    def _published(self, value):
        book = {"title": "本", "asin": "B0WISH001", "actual_price": 900, "timestamp": "2026-01-01T00:00:00", "is_unlimited": 0, "is_wanted": 1, "is_purchased": 0, "bookmeter_id": value}
        return report.build_wishlist([book])["books"][0]["bookmeter_id"]

    def test_valid_id_is_published_and_others_are_null(self):
        self.assertEqual(self._published("22690039"), "22690039")
        for value in (None, "", "12a", "../1", 123):
            self.assertIsNone(self._published(value), value)


if __name__ == "__main__":
    unittest.main()
