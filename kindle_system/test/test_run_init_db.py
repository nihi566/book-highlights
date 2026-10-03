"""
test_run_init_db.py
--------------------
run.py のサブコマンドが、処理の前に init_db()（テーブル作成 + book_mappings の列追加）を通すことの再現テスト。

#52 で book_mappings に bookmeter_id 列を足したが、列を足す migrate_book_mappings_schema は
init_db() 経由でしか呼ばれず、init_db() を呼ぶのは main.py の Kindle クロールだけだった。
そのため `run.py sync --target bookmeter` では列が足されないまま ORM が新しい列を読み、
登録が全件 `no such column: book_mappings.bookmeter_id` で失敗していた。

実行:
    python -m unittest discover -s test -p test_run_init_db.py -v
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import run
import src.database as database_module


def _create_db_without_bookmeter_id(db_path):
    """#52 より前の本番 DB の形（from_* フラグあり・bookmeter_id なし）。"""
    conn = sqlite3.connect(db_path)
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
        conn.execute(
            "INSERT INTO book_mappings (paid_asin, title, created_at, is_wanted, source, from_bookmeter) "
            "VALUES ('B0EXIST001', '前からある本', '2026-01-01T00:00:00', 1, 'bookmeter', 1)"
        )
        conn.commit()
    finally:
        conn.close()


class RunSyncInitDbTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="run_init_db_test_")
        self.db_path = os.path.join(self.tmpdir, "kindle_monitor.db")
        _create_db_without_bookmeter_id(self.db_path)
        from sqlmodel import create_engine
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        self._saved = (database_module.engine, database_module.DB_PATH, database_module.DB_DIR)
        database_module.engine = self.engine
        database_module.DB_PATH = self.db_path
        database_module.DB_DIR = self.tmpdir

    def tearDown(self):
        database_module.engine, database_module.DB_PATH, database_module.DB_DIR = self._saved
        self.engine.dispose()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _columns(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return {row[1] for row in conn.execute("PRAGMA table_info(book_mappings)")}
        finally:
            conn.close()

    @patch("run.publish")
    @patch("src.bookmeter_sync.save_price_history")
    @patch("src.bookmeter_sync.crawl_price_info", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.resolve_title_to_paid_asin", new_callable=AsyncMock)
    @patch("src.bookmeter_sync.fetch_wish_books")
    def test_sync_bookmeter_adds_column_and_registers_books(
        self, mock_fetch, mock_resolve, mock_crawl, mock_save_price, mock_publish
    ):
        mock_fetch.return_value = [{"title": "新しい本", "author": "著者", "bookmeter_id": "123"}]
        mock_resolve.return_value = "B0NEWBOOK1"
        mock_crawl.return_value = {"asin": "B0NEWBOOK1", "sell_price": 1000, "point_value": 0}

        with patch.object(sys, "argv", ["run.py", "sync", "--target", "bookmeter"]):
            run.main()

        self.assertIn("bookmeter_id", self._columns())
        conn = sqlite3.connect(self.db_path)
        try:
            rows = dict(conn.execute("SELECT paid_asin, bookmeter_id FROM book_mappings").fetchall())
        finally:
            conn.close()
        self.assertEqual(rows, {"B0EXIST001": None, "B0NEWBOOK1": "123"}, "既存の行は残り、新しい本が登録される")
        mock_save_price.assert_called_once()
        mock_publish.assert_called_once()

    def test_want_adds_column_before_reading_with_orm(self):
        """sync 以外のサブコマンド（ORM で BookMapping を読む want 等）も同じく列を足してから動く。"""
        with patch.object(sys, "argv", ["run.py", "want", "B0EXIST001", "--off"]):
            run.main()
        self.assertIn("bookmeter_id", self._columns())


if __name__ == "__main__":
    unittest.main()
