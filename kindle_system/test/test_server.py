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


def _make_request(host: str = "localhost:8001", headers: dict | None = None, path: str = "/api/run") -> Request:
    """Starlette の Request を直接構築する（TestClient/httpx を導入しないため）。"""
    raw_headers = [(b"host", host.encode("latin-1"))]
    for key, value in (headers or {}).items():
        raw_headers.append((key.encode("latin-1"), value.encode("latin-1")))

    host_part, _, port_part = host.partition(":")
    server = (host_part, int(port_part) if port_part else 80)

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode("latin-1"),
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

    def test_referer_fallback_passes_when_origin_missing(self):
        """Originが無くRefererのみで自オリジンと一致する場合は通過する
        （優先順位: Origin → Referer フォールバック の正常系）。"""
        request = _make_request(headers={"referer": "http://localhost:8001/index.html"})
        asyncio.run(server.verify_same_origin(request))  # 例外が発生しないこと

    def test_same_host_different_port_raises_403(self):
        """ホスト名は一致するがポートが異なる場合は拒否する
        （ローカルの別アプリからのクロスオリジンPOSTを想定）。"""
        request = _make_request(host="localhost:8001", headers={"origin": "http://localhost:9999"})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.verify_same_origin(request))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_dns_rebinding_same_external_hostname_raises_403(self):
        """DNSリバインディング対策: Host/Originヘッダーの両方が同一の
        外部ドメイン文字列（evil.example）であっても、そのホスト名自体が
        ループバック/プライベートIP相当でなければ拒否する。単純な
        Host/Origin一致比較だけでは通過してしまう経路を締め出す。"""
        request = _make_request(host="evil.example:8001", headers={"origin": "http://evil.example:8001"})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.verify_same_origin(request))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_lan_private_ip_same_origin_passes(self):
        """LAN内プライベートIP経由のアクセスは、Host/Originが一致していれば
        引き続き通過する（0.0.0.0待受・LANアクセスのサポートを維持）。"""
        request = _make_request(host="192.168.1.42:8001", headers={"origin": "http://192.168.1.42:8001"})
        asyncio.run(server.verify_same_origin(request))  # 例外が発生しないこと

    def test_public_ip_same_host_raises_403(self):
        """Host/Originが一致していても、そのIPアドレスが公開（グローバル）
        IPであればループバック/プライベートIPではないため拒否する
        （_is_local_host の is_private/is_loopback 判定分岐を通す）。"""
        request = _make_request(host="8.8.8.8:8001", headers={"origin": "http://8.8.8.8:8001"})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.verify_same_origin(request))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_malformed_port_in_origin_raises_403_not_500(self):
        """Originヘッダーのポート番号が範囲外（例: 99999）の場合、
        urlparseの遅延パースがValueErrorを送出しうるが、未処理の500では
        なく403として一律拒否する。"""
        request = _make_request(headers={"origin": "http://localhost:99999"})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.verify_same_origin(request))
        self.assertEqual(ctx.exception.status_code, 403)


class PostEndpointsRequireSameOriginCheckTest(unittest.TestCase):
    """全POSTルートに Depends(verify_same_origin) が適用されていることを
    機械的に検証する回帰テスト。エンドポイント追加時の付け忘れ・
    意図しない除外漏れを検出する（テスト十分性レビュー指摘への対応）。
    """

    # Phase auth-csrf-protection の「やらないこと」で明示的にスコープ外と
    # した唯一のエンドポイント。停止操作はデータ変更を伴わず実害が小さい
    # ため対象外（phases/auth-csrf-protection.md リスク表 R4）。
    EXEMPT_PATHS = {"/api/stop"}

    def test_all_post_routes_require_verify_same_origin_except_exempted(self):
        missing = []
        checked = []
        for route in server.app.routes:
            methods = getattr(route, "methods", None)
            if not methods or "POST" not in methods:
                continue
            if route.path in self.EXEMPT_PATHS:
                continue
            checked.append(route.path)
            dependency_calls = {dep.call for dep in route.dependant.dependencies}
            if server.verify_same_origin not in dependency_calls:
                missing.append(route.path)

        # POSTルートを1件も収集できなかった場合（FastAPIの内部APIが変わった、
        # importに失敗している等）に空振りで緑にならないようにする。
        self.assertGreaterEqual(
            len(checked), 5,
            f"対象外(EXEMPT_PATHS)以外のPOSTルートが5件未満しか収集できていません: {checked}",
        )
        self.assertEqual(
            missing, [],
            f"Depends(verify_same_origin) が適用されていないPOSTルート: {missing}",
        )


if __name__ == "__main__":
    unittest.main()
