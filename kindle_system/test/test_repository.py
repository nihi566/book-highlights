"""
test_repository.py
-------------------
src/repository.py の単体テスト（マイグレーション冪等性・データ保持・dedup）。

実行:
    python -m unittest test.test_repository -v
"""

import os
import sys
import shutil
import sqlite3
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src import repository


def _create_old_schema_db(db_path: str) -> None:
    """旧スキーマ（sample_asin PK, source 列なし）の book_mappings を再現する。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE book_mappings (
                sample_asin VARCHAR NOT NULL PRIMARY KEY,
                paid_asin VARCHAR,
                title VARCHAR,
                created_at VARCHAR,
                is_purchased INTEGER NOT NULL DEFAULT 0,
                is_wanted INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX ix_book_mappings_paid_asin ON book_mappings (paid_asin)")
        conn.executemany(
            "INSERT INTO book_mappings "
            "(sample_asin, paid_asin, title, created_at, is_purchased, is_wanted) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("B0SAMPLE001", "B0PAID001", "サンプル本1", "2026-01-01T00:00:00", 1, 0),
                ("B0SAMPLE002", "B0PAID002", "サンプル本2", "2026-01-02T00:00:00", 0, 1),
                ("B0SAMPLE003", None, "サンプル本3", None, 0, 0),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _table_columns(db_path: str, table: str) -> set:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def _fetch_all_rows(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM book_mappings ORDER BY sample_asin")
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


class MigrateBookMappingsSchemaTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_schema_test_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        _create_old_schema_db(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_migration_preserves_row_count(self):
        before_rows = _fetch_all_rows(self.db_path)
        repository.migrate_book_mappings_schema(self.db_path)
        after_rows = _fetch_all_rows(self.db_path)
        self.assertEqual(len(before_rows), len(after_rows))

    def test_migration_preserves_column_values(self):
        repository.migrate_book_mappings_schema(self.db_path)
        rows = _fetch_all_rows(self.db_path)
        by_sample_asin = {r["sample_asin"]: r for r in rows}
        self.assertEqual(by_sample_asin["B0SAMPLE001"]["paid_asin"], "B0PAID001")
        self.assertEqual(by_sample_asin["B0SAMPLE001"]["title"], "サンプル本1")
        self.assertEqual(by_sample_asin["B0SAMPLE001"]["is_purchased"], 1)
        self.assertEqual(by_sample_asin["B0SAMPLE002"]["is_wanted"], 1)
        self.assertIsNone(by_sample_asin["B0SAMPLE003"]["paid_asin"])

    def test_migration_adds_id_and_backfills_source(self):
        repository.migrate_book_mappings_schema(self.db_path)
        columns = _table_columns(self.db_path, "book_mappings")
        self.assertIn("id", columns)
        self.assertIn("source", columns)
        rows = _fetch_all_rows(self.db_path)
        self.assertTrue(all(r["source"] == "kindle_sample" for r in rows))

    def test_migration_assigns_unique_ids(self):
        repository.migrate_book_mappings_schema(self.db_path)
        rows = _fetch_all_rows(self.db_path)
        ids = [r["id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(isinstance(i, int) and i > 0 for i in ids))

    def test_migration_is_idempotent(self):
        repository.migrate_book_mappings_schema(self.db_path)
        rows_after_first = _fetch_all_rows(self.db_path)
        repository.migrate_book_mappings_schema(self.db_path)
        rows_after_second = _fetch_all_rows(self.db_path)
        self.assertEqual(rows_after_first, rows_after_second)

    def test_migration_creates_backup_file(self):
        repository.migrate_book_mappings_schema(self.db_path)
        backups = [f for f in os.listdir(self.tmpdir) if f.startswith("test.db.bak-")]
        self.assertEqual(len(backups), 1)

    def test_migration_succeeds_when_db_file_itself_is_read_only(self):
        """
        実データ data/kindle_monitor.db は root 所有・-rw-r--r-- で実行ユーザーからは
        書き込み不可（設計方針参照）。移行は db_path へ直接書き込まず、書き込み可能な
        一時ファイルを os.replace() で原子的に差し替える方式のため、ファイル自体が
        読み取り専用でも成功する（ディレクトリ自体の書き込み権限は必要）。
        """
        os.chmod(self.db_path, 0o444)
        try:
            repository.migrate_book_mappings_schema(self.db_path)
        finally:
            os.chmod(self.db_path, 0o644)  # tearDown の shutil.rmtree のため復元

        columns = _table_columns(self.db_path, "book_mappings")
        self.assertIn("id", columns)
        self.assertIn("source", columns)
        rows = _fetch_all_rows(self.db_path)
        self.assertEqual(len(rows), 3)

    def test_migration_keeps_sample_asin_unique_but_allows_multiple_null(self):
        """
        旧スキーマは sample_asin が PRIMARY KEY で一意性が保証されていた。
        新スキーマでも非NULL値の一意性は維持し、bookmeter 由来行（NULL）は
        複数存在できることを確認する。
        """
        repository.migrate_book_mappings_schema(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            # 複数の NULL sample_asin は許容される
            conn.execute(
                "INSERT INTO book_mappings (sample_asin, paid_asin, source) VALUES (NULL, 'B0X1', 'bookmeter')"
            )
            conn.execute(
                "INSERT INTO book_mappings (sample_asin, paid_asin, source) VALUES (NULL, 'B0X2', 'bookmeter')"
            )
            conn.commit()

            # 既存の非NULL sample_asin と重複する INSERT は拒否される
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO book_mappings (sample_asin, paid_asin, source) "
                    "VALUES ('B0SAMPLE001', 'B0DUP', 'kindle_sample')"
                )
        finally:
            conn.close()


class GetOrCreateByPaidAsinTest(unittest.TestCase):
    """get_or_create_by_paid_asin（paid_asin一致によるdedupヘルパー）のテスト。

    この関数は呼び出し側から session を受け取り、commit は呼び出し側の責務
    （src/repository.py の docstring 参照）なので、各テストは明示的に commit する。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_dedup_test_")
        db_path = os.path.join(self.tmpdir, "dedup.db")
        from sqlmodel import create_engine, SQLModel
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_new_row_with_null_sample_asin(self):
        from sqlmodel import Session
        with Session(self.engine) as session:
            book = repository.get_or_create_by_paid_asin(
                session, "B0NEW001", title="新刊", source="bookmeter", is_wanted=1
            )
            session.commit()
            self.assertIsNone(book.sample_asin)
            self.assertEqual(book.paid_asin, "B0NEW001")
            self.assertEqual(book.source, "bookmeter")
            self.assertEqual(book.is_wanted, 1)

    def test_second_call_updates_is_wanted_without_creating_new_row(self):
        from sqlmodel import Session, select
        from src.models import BookMapping
        with Session(self.engine) as session:
            repository.get_or_create_by_paid_asin(
                session, "B0NEW002", title="既刊", source="bookmeter", is_wanted=1
            )
            repository.get_or_create_by_paid_asin(session, "B0NEW002", is_wanted=0)
            session.commit()

            statement = select(BookMapping).where(BookMapping.paid_asin == "B0NEW002")
            rows = session.exec(statement).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].is_wanted, 0)

    def test_unknown_paid_asin_creates_new_row(self):
        from sqlmodel import Session, select
        from src.models import BookMapping
        with Session(self.engine) as session:
            repository.get_or_create_by_paid_asin(session, "B0AAA", is_wanted=1)
            repository.get_or_create_by_paid_asin(session, "B0BBB", is_wanted=1)
            session.commit()

            rows = session.exec(select(BookMapping)).all()
            self.assertEqual(len(rows), 2)
            paid_asins = {r.paid_asin for r in rows}
            self.assertEqual(paid_asins, {"B0AAA", "B0BBB"})

    def test_empty_paid_asin_raises_value_error(self):
        from sqlmodel import Session
        with Session(self.engine) as session:
            with self.assertRaises(ValueError):
                repository.get_or_create_by_paid_asin(session, "", is_wanted=1)
            with self.assertRaises(ValueError):
                repository.get_or_create_by_paid_asin(session, None, is_wanted=1)


class InitDbEngineStalenessTest(unittest.TestCase):
    """
    init_db()（init_db_orm() → migrate_book_mappings_schema()）を通しで実行した後、
    同じ SQLAlchemy engine 経由で書き込みができることを確認する。

    migrate_book_mappings_schema() は db_path を os.replace() で新しい inode へ
    差し替えるため、init_db_orm() が先に開いた接続プールが古い（削除済みの）
    inode を掴んだままだと、以後の書き込みが "no such column" 等で失敗する
    （実際に再現した不具合。修正: migrate 側で engine.dispose() を呼ぶ）。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_engine_staleness_test_")
        self.db_path = os.path.join(self.tmpdir, "old.db")
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE book_mappings (
                sample_asin VARCHAR NOT NULL PRIMARY KEY,
                paid_asin VARCHAR, title VARCHAR, created_at VARCHAR,
                is_purchased INTEGER NOT NULL DEFAULT 0, is_wanted INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO book_mappings VALUES ('B0OLD','B0PAID','old book',NULL,0,0)"
        )
        conn.commit()
        conn.close()

        import src.database as database_module
        from sqlmodel import create_engine
        self._database_module = database_module
        self._original_db_path = database_module.DB_PATH
        self._original_engine = database_module.engine
        database_module.DB_PATH = self.db_path
        database_module.engine = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False}
        )

    def tearDown(self):
        self._database_module.engine.dispose()
        self._database_module.DB_PATH = self._original_db_path
        self._database_module.engine = self._original_engine
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_succeeds_after_init_db_migrates_existing_old_schema(self):
        from sqlmodel import Session
        from src.models import BookMapping

        repository.init_db()

        with self._database_module.get_session() as session:
            session.add(
                BookMapping(
                    sample_asin="B0NEWAFTER", paid_asin="B0X", title="t",
                    source="kindle_sample",
                )
            )
            session.commit()  # ここで例外が出なければ修正が効いている


class SaveMappingCrossSourceDedupTest(unittest.TestCase):
    """
    save_mapping が bookmeter 経由（sample_asin=None）で既に登録済みの paid_asin と
    重複行を作らないことを確認する（Phase の目的: 双方のソースから登録されても
    行が重複しないこと）。

    save_mapping は src.database.get_session() 経由でモジュールグローバルな engine
    を使うため、テスト用の一時DBへ差し替えてから呼び出す。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_savemapping_test_")
        db_path = os.path.join(self.tmpdir, "savemapping.db")
        from sqlmodel import create_engine, SQLModel
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

        import src.database as database_module
        self._original_engine = database_module.engine
        database_module.engine = self.engine

    def tearDown(self):
        import src.database as database_module
        database_module.engine = self._original_engine
        self.engine.dispose()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_mapping_merges_into_existing_bookmeter_row(self):
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            repository.get_or_create_by_paid_asin(
                session, "B0SHARED", title="共有本", source="bookmeter", is_wanted=1
            )
            session.commit()

        repository.save_mapping("B0SAMPLE999", "B0SHARED", "共有本(kindle_sample側タイトル)")

        with Session(self.engine) as session:
            rows = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0SHARED")
            ).all()
            self.assertEqual(
                len(rows), 1, "bookmeter経由の既存行とsave_mappingが別行を作ってはならない"
            )
            self.assertEqual(rows[0].sample_asin, "B0SAMPLE999")
            self.assertEqual(rows[0].paid_asin, "B0SHARED")

    def test_save_mapping_still_creates_new_row_when_no_match(self):
        from sqlmodel import Session, select
        from src.models import BookMapping

        repository.save_mapping("B0SAMPLE_NEW", "B0PAID_NEW", "新規本")

        with Session(self.engine) as session:
            rows = session.exec(select(BookMapping)).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].sample_asin, "B0SAMPLE_NEW")
            self.assertEqual(rows[0].source, "kindle_sample")


if __name__ == "__main__":
    unittest.main()
