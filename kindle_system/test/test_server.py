"""
test_server.py
----------------
src/server.py の GET /api/books の単体テスト。

使い方:
    python3 -m unittest test.test_server -v
"""

import asyncio
import os
import sys
import shutil
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src import server


class GetBooksSourceFlagsTest(unittest.TestCase):
    """GET /api/books のレスポンスに from_kindle_sample/from_bookmeter が
    含まれることを検証する（book-source-flags Phase の受入条件）。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_server_test_")
        db_path = os.path.join(self.tmpdir, "server.db")
        from sqlmodel import create_engine, SQLModel
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(self.engine)

        import src.database as database_module
        self._original_engine = database_module.engine
        database_module.engine = self.engine

    def tearDown(self):
        import src.database as database_module
        database_module.engine = self._original_engine
        self.engine.dispose()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _insert_book_with_price(self, session, **kwargs):
        from src.models import BookMapping, PriceHistory
        defaults = dict(
            paid_asin="B0DEFAULT",
            title="タイトル",
            is_purchased=0,
            is_wanted=0,
            source="kindle_sample",
            from_kindle_sample=False,
            from_bookmeter=False,
        )
        defaults.update(kwargs)
        session.add(BookMapping(**defaults))
        session.add(
            PriceHistory(
                paid_asin=defaults["paid_asin"],
                sell_price=1000,
                point_value=0,
                actual_price=1000,
                campaign_text="",
                timestamp="2026-01-01T00:00:00",
                is_unlimited=0,
            )
        )

    def test_response_includes_source_flags(self):
        from sqlmodel import Session
        with Session(self.engine) as session:
            self._insert_book_with_price(
                session,
                paid_asin="B0BOTH",
                from_kindle_sample=True,
                from_bookmeter=True,
            )
            session.commit()

        books = asyncio.run(server.get_books())
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["from_kindle_sample"], 1)
        self.assertEqual(books[0]["from_bookmeter"], 1)

    def test_response_reflects_independent_flag_values(self):
        from sqlmodel import Session
        with Session(self.engine) as session:
            self._insert_book_with_price(
                session,
                paid_asin="B0KSONLY",
                from_kindle_sample=True,
                from_bookmeter=False,
            )
            session.commit()

        books = asyncio.run(server.get_books())
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["from_kindle_sample"], 1)
        self.assertEqual(books[0]["from_bookmeter"], 0)


if __name__ == "__main__":
    unittest.main()
