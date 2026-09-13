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

from fastapi import HTTPException
from starlette.requests import Request

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


def _make_request(host: str = "localhost:8001", headers: dict | None = None) -> Request:
    """Starlette の Request を直接構築する（TestClient/httpx を導入しないため）。"""
    raw_headers = [(b"host", host.encode("latin-1"))]
    for key, value in (headers or {}).items():
        raw_headers.append((key.encode("latin-1"), value.encode("latin-1")))

    host_part, _, port_part = host.partition(":")
    server = (host_part, int(port_part) if port_part else 80)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/run",
        "raw_path": b"/api/run",
        "query_string": b"",
        "headers": raw_headers,
        "server": server,
        "scheme": "http",
        "client": ("testclient", 12345),
        "http_version": "1.1",
    }
    return Request(scope)


class VerifySameOriginTest(unittest.TestCase):
    """verify_same_origin の受入条件（auth-csrf-protection Phase）を検証する。"""

    def test_same_origin_header_passes(self):
        request = _make_request(headers={"origin": "http://localhost:8001"})
        asyncio.run(server.verify_same_origin(request))  # 例外が発生しないこと

    def test_missing_origin_and_referer_raises_403(self):
        request = _make_request(headers={})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.verify_same_origin(request))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_external_origin_raises_403(self):
        request = _make_request(headers={"origin": "http://evil.example"})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.verify_same_origin(request))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_external_referer_raises_403(self):
        request = _make_request(headers={"referer": "http://evil.example/page"})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.verify_same_origin(request))
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
