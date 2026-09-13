"""
test_repository.py
-------------------
src/repository.py の単体テスト（マイグレーション冪等性・データ保持・dedup）。

実行:
    python -m unittest test.test_repository -v
"""

import logging
import os
import sys
import shutil
import sqlite3
import tempfile
import unittest
import unittest.mock

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


class BookMappingModelSourceFlagsTest(unittest.TestCase):
    """BookMapping モデルに from_kindle_sample/from_bookmeter 列が追加され、
    既定値が False であることを検証する。"""

    def test_new_instance_has_default_false_flags(self):
        from src.models import BookMapping

        book = BookMapping(sample_asin="B0FLAGDEFAULT", paid_asin="B0FLAGDEFAULT")
        self.assertFalse(book.from_kindle_sample)
        self.assertFalse(book.from_bookmeter)

    def test_flags_can_be_set_independently(self):
        from src.models import BookMapping

        book = BookMapping(
            sample_asin="B0FLAGSET",
            paid_asin="B0FLAGSET",
            from_kindle_sample=True,
        )
        self.assertTrue(book.from_kindle_sample)
        self.assertFalse(book.from_bookmeter)


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

    def test_migration_adds_source_flag_columns(self):
        repository.migrate_book_mappings_schema(self.db_path)
        columns = _table_columns(self.db_path, "book_mappings")
        self.assertIn("from_kindle_sample", columns)
        self.assertIn("from_bookmeter", columns)

    def test_migration_backfills_from_kindle_sample_for_kindle_sample_source(self):
        """真の旧スキーマ(sourceなし)からの移行は、source同様 kindle_sample 一択として扱う。"""
        repository.migrate_book_mappings_schema(self.db_path)
        rows = _fetch_all_rows(self.db_path)
        self.assertTrue(all(r["from_kindle_sample"] == 1 for r in rows))
        self.assertTrue(all(r["from_bookmeter"] == 0 for r in rows))

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


def _create_new_schema_db_without_flags(db_path: str) -> None:
    """id/source は既にある(1回目の移行は済んでいる)が、from_kindle_sample/from_bookmeter
    列がまだ無い状態を再現する(本Phase以前にbookmeter-sync等でsourceが書かれた実データ相当)。"""
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
                source VARCHAR NOT NULL DEFAULT 'kindle_sample'
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX ix_book_mappings_sample_asin_unique "
            "ON book_mappings (sample_asin) WHERE sample_asin IS NOT NULL"
        )
        conn.execute("CREATE INDEX ix_book_mappings_paid_asin ON book_mappings (paid_asin)")
        conn.executemany(
            "INSERT INTO book_mappings "
            "(sample_asin, paid_asin, title, created_at, is_purchased, is_wanted, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("B0KS001", "B0KSPAID001", "kindle_sample本", "2026-01-01T00:00:00", 1, 0, "kindle_sample"),
                (None, "B0BM001", "bookmeter本", "2026-01-02T00:00:00", 0, 1, "bookmeter"),
                # sample_asin が NULL かつ source も想定外の値。sample_asin は
                # save_mapping(kindle_sample経由)しか書かないため、これが NULL の
                # 場合は kindle_sample 由来と判定する手がかりが無い、真に
                # 判定不能なケースを表す(R2)。
                (None, "B0UNKPAID001", "想定外source本", "2026-01-03T00:00:00", 0, 0, "unknown_source"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


class MigrateBookMappingsSchemaBackfillFlagsTest(unittest.TestCase):
    """新スキーマ(id/source あり)だが from_kindle_sample/from_bookmeter が無い DB からの
    バックフィルを検証する(R2: 想定外source値は両フラグ0のまま許容)。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_backfill_test_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        _create_new_schema_db_without_flags(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_adds_flag_columns_without_touching_row_count(self):
        before_rows = _fetch_all_rows(self.db_path)
        repository.migrate_book_mappings_schema(self.db_path)
        after_rows = _fetch_all_rows(self.db_path)
        self.assertEqual(len(before_rows), len(after_rows))
        columns = _table_columns(self.db_path, "book_mappings")
        self.assertIn("from_kindle_sample", columns)
        self.assertIn("from_bookmeter", columns)

    def test_backfills_from_kindle_sample_for_kindle_sample_source_rows(self):
        repository.migrate_book_mappings_schema(self.db_path)
        rows = _fetch_all_rows(self.db_path)
        by_sample_asin = {r["sample_asin"]: r for r in rows}
        self.assertEqual(by_sample_asin["B0KS001"]["from_kindle_sample"], 1)
        self.assertEqual(by_sample_asin["B0KS001"]["from_bookmeter"], 0)

    def test_backfills_from_bookmeter_for_bookmeter_source_rows(self):
        repository.migrate_book_mappings_schema(self.db_path)
        rows = _fetch_all_rows(self.db_path)
        by_paid_asin = {r["paid_asin"]: r for r in rows}
        self.assertEqual(by_paid_asin["B0BM001"]["from_bookmeter"], 1)
        self.assertEqual(by_paid_asin["B0BM001"]["from_kindle_sample"], 0)

    def test_unrecognized_source_value_leaves_both_flags_unset(self):
        repository.migrate_book_mappings_schema(self.db_path)
        rows = _fetch_all_rows(self.db_path)
        by_paid_asin = {r["paid_asin"]: r for r in rows}
        self.assertEqual(by_paid_asin["B0UNKPAID001"]["from_kindle_sample"], 0)
        self.assertEqual(by_paid_asin["B0UNKPAID001"]["from_bookmeter"], 0)

    def test_sample_asin_present_sets_from_kindle_sample_even_with_unrecognized_source(self):
        """sample_asinはsave_mapping(kindle_sample経由)しか書かないため、sourceの値が
        想定外でもsample_asinが設定済みならkindle_sample由来と判定できる。"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO book_mappings (sample_asin, paid_asin, source) "
                "VALUES ('B0MYSTERY', 'B0MYSTERYPAID', 'unknown_source')"
            )
            conn.commit()
        finally:
            conn.close()

        repository.migrate_book_mappings_schema(self.db_path)
        rows = _fetch_all_rows(self.db_path)
        by_sample_asin = {r["sample_asin"]: r for r in rows}
        self.assertEqual(by_sample_asin["B0MYSTERY"]["from_kindle_sample"], 1)
        self.assertEqual(by_sample_asin["B0MYSTERY"]["from_bookmeter"], 0)

    def test_is_idempotent_when_flag_columns_already_present(self):
        repository.migrate_book_mappings_schema(self.db_path)
        rows_after_first = _fetch_all_rows(self.db_path)
        repository.migrate_book_mappings_schema(self.db_path)
        rows_after_second = _fetch_all_rows(self.db_path)
        self.assertEqual(rows_after_first, rows_after_second)

    def test_falls_back_to_staging_when_db_file_itself_is_read_only(self):
        """id/sourceがある新スキーマでflags列だけ無い場合、in-place ALTERが
        db_pathへ書き込めない(読み取り専用)ときは複製→置換パターンへ
        フォールバックし、バックフィルが成功すること。"""
        os.chmod(self.db_path, 0o444)
        try:
            repository.migrate_book_mappings_schema(self.db_path)
        finally:
            os.chmod(self.db_path, 0o644)

        columns = _table_columns(self.db_path, "book_mappings")
        self.assertIn("from_kindle_sample", columns)
        self.assertIn("from_bookmeter", columns)
        rows = _fetch_all_rows(self.db_path)
        self.assertEqual(len(rows), 3)


class MigrateBookMappingsSchemaLockContentionTest(unittest.TestCase):
    """migrate_book_mappings_schema（実体は _backfill_source_flags_in_place）が、
    他プロセスが実際に BEGIN IMMEDIATE でロックを保持している状態で呼び出されても、
    例外を伝播させず「見送り」として静かに終了することを検証する（この防御コードが
    退行しても検知できない、という既存の穴を塞ぐ）。

    本番コードは timeout=30 で接続するため、実ロック競合をそのまま再現すると
    最大30秒かかる。ここでは unittest.mock.patch で src.repository.sqlite3.connect
    のみを差し替え、timeout引数だけを短縮して高速化する（本番コードの timeout=30
    自体は変更しない）。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_lockcontention_test_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        _create_new_schema_db_without_flags(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_migration_skips_silently_when_lock_is_actually_held(self):
        real_connect = sqlite3.connect

        def _fast_timeout_connect(*args, **kwargs):
            kwargs["timeout"] = 0.2
            return real_connect(*args, **kwargs)

        # 別接続で実際に BEGIN IMMEDIATE を保持し、書き込みロックを取得した状態を再現する。
        locker_conn = real_connect(self.db_path)
        try:
            locker_conn.execute("BEGIN IMMEDIATE")

            with unittest.mock.patch(
                "src.repository.sqlite3.connect", side_effect=_fast_timeout_connect
            ):
                try:
                    repository.migrate_book_mappings_schema(self.db_path)
                except Exception as e:
                    self.fail(f"ロック競合時に例外が伝播した(見送りにならなかった): {e}")
        finally:
            locker_conn.rollback()
            locker_conn.close()

        # 見送られたため、flags列はまだ追加されていない。
        columns = _table_columns(self.db_path, "book_mappings")
        self.assertNotIn("from_kindle_sample", columns)

        # ロック解放後（=次回起動時の再試行相当）に呼び出せば正常に完了する。
        repository.migrate_book_mappings_schema(self.db_path)
        columns_after_retry = _table_columns(self.db_path, "book_mappings")
        self.assertIn("from_kindle_sample", columns_after_retry)


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

    def test_new_row_has_from_bookmeter_flag_set(self):
        from sqlmodel import Session
        with Session(self.engine) as session:
            book = repository.get_or_create_by_paid_asin(
                session, "B0FLAGNEW", title="新刊", source="bookmeter", is_wanted=1
            )
            session.commit()
            self.assertTrue(book.from_bookmeter)
            self.assertFalse(book.from_kindle_sample)

    def test_merge_sets_from_bookmeter_without_clearing_from_kindle_sample(self):
        """paid_asin一致でkindle_sample経由の既存行にマージする際、from_bookmeterを
        立てつつ、相手側のfrom_kindle_sampleは変更しない(既存値を保持する)ことを検証する。"""
        from sqlmodel import Session
        from src.models import BookMapping
        with Session(self.engine) as session:
            session.add(
                BookMapping(
                    sample_asin="B0KSFLAG",
                    paid_asin="B0MERGEBM",
                    title="サンプル本",
                    source="kindle_sample",
                    from_kindle_sample=True,
                )
            )
            session.commit()

            book = repository.get_or_create_by_paid_asin(
                session, "B0MERGEBM", is_wanted=1
            )
            session.commit()
            self.assertTrue(book.from_bookmeter)
            self.assertTrue(
                book.from_kindle_sample,
                "既存のfrom_kindle_sampleがget_or_create_by_paid_asinのマージで消えてはならない",
            )


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

    def test_save_mapping_sets_from_kindle_sample_on_new_row(self):
        from sqlmodel import Session, select
        from src.models import BookMapping

        repository.save_mapping("B0SAMPLE_FLAG", "B0PAID_FLAG", "新規本")

        with Session(self.engine) as session:
            row = session.exec(
                select(BookMapping).where(BookMapping.sample_asin == "B0SAMPLE_FLAG")
            ).first()
            self.assertTrue(row.from_kindle_sample)
            self.assertFalse(row.from_bookmeter)

    def test_save_mapping_sets_from_kindle_sample_on_merge_without_clearing_from_bookmeter(self):
        """paid_asin一致でbookmeter経由の既存行にマージする際、from_kindle_sampleを
        立てつつ、相手側のfrom_bookmeterは変更しない(既存値を保持する)ことを検証する。"""
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            session.add(
                BookMapping(
                    paid_asin="B0MERGEFLAG",
                    title="共有本",
                    source="bookmeter",
                    is_wanted=1,
                    from_bookmeter=True,
                )
            )
            session.commit()

        repository.save_mapping("B0SAMPLE_MERGEFLAG", "B0MERGEFLAG", "共有本")

        with Session(self.engine) as session:
            row = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0MERGEFLAG")
            ).first()
            self.assertTrue(row.from_kindle_sample)
            self.assertTrue(
                row.from_bookmeter, "既存のfrom_bookmeterがsave_mappingのマージで消えてはならない"
            )

    def test_save_mapping_logs_when_overwriting_existing_sample_asin(self):
        """paid_asin一致で既存行にマージする際、既存のsample_asinが別値へ上書きされる
        場合はログ(旧値→新値)を出力する。上書き自体は禁止しない(YAGNI)が、旧値との
        対応関係がDB上のどこにも残らなくなるため、後から追跡できるようにする
        (P0: データ整合性)。"""
        from sqlmodel import Session, select
        from src.models import BookMapping

        repository.save_mapping("B0OLDSAMPLE", "B0OVERWRITE", "本(1回目)")

        # level="WARNING"で固定する(=INFOでは検知できるがWARNINGでは検知できない、
        # というlevel="INFO"指定だと、本番で実際には出力されないINFOへ退行しても
        # このテストは緑のまま気づけない。実際に出力される水準そのものを検証する)。
        with self.assertLogs("src.repository", level="WARNING") as cm:
            repository.save_mapping("B0NEWSAMPLE", "B0OVERWRITE", "本(2回目)")

        self.assertEqual(cm.records[0].levelno, logging.WARNING)
        self.assertTrue(
            any("B0OLDSAMPLE" in msg and "B0NEWSAMPLE" in msg for msg in cm.output),
            f"旧sample_asinと新sample_asinの両方を含むログが出力されていない: {cm.output}",
        )

        # ログだけでなく、上書き後のDB最終状態も検証する(行が重複しない・値が
        # 正しく更新されていることを確認しないと、ログの存在だけでは実処理の
        # 正しさを保証できない)。
        with Session(self.engine) as session:
            rows = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0OVERWRITE")
            ).all()
            self.assertEqual(len(rows), 1, "paid_asin一致マージで行が重複してはならない")
            self.assertEqual(rows[0].sample_asin, "B0NEWSAMPLE")
            self.assertTrue(rows[0].from_kindle_sample)

    def test_save_mapping_does_not_log_when_sample_asin_is_first_set(self):
        """bookmeter経由(sample_asin=None)の既存行への初回マージはsample_asinが
        新規に設定されるだけで上書きではないため、ログを出さない(正常系のノイズ防止)。"""
        from sqlmodel import Session

        with Session(self.engine) as session:
            repository.get_or_create_by_paid_asin(
                session, "B0NOLOGSHARED", title="共有本", source="bookmeter", is_wanted=1
            )
            session.commit()

        with self.assertNoLogs("src.repository", level="INFO"):
            repository.save_mapping("B0NOLOGSAMPLE", "B0NOLOGSHARED", "共有本")


class DualSourceRegistrationFlagsIntegrationTest(unittest.TestCase):
    """save_mapping と get_or_create_by_paid_asin を両順序(kindle_sample→bookmeter /
    bookmeter→kindle_sample)で呼び出した場合、最終的に両フラグが1になることを検証する
    (受入条件(2)の直接検証)。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_dualsource_test_")
        db_path = os.path.join(self.tmpdir, "dualsource.db")
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

    def test_kindle_sample_then_bookmeter_sets_both_flags(self):
        from sqlmodel import Session, select
        from src.models import BookMapping

        repository.save_mapping("B0ORDER_A", "B0PAID_ORDER_A", "本A")
        with Session(self.engine) as session:
            repository.get_or_create_by_paid_asin(session, "B0PAID_ORDER_A", is_wanted=1)
            session.commit()

        with Session(self.engine) as session:
            rows = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0PAID_ORDER_A")
            ).all()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0].from_kindle_sample)
            self.assertTrue(rows[0].from_bookmeter)

    def test_bookmeter_then_kindle_sample_sets_both_flags(self):
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            repository.get_or_create_by_paid_asin(
                session, "B0PAID_ORDER_B", title="本B", source="bookmeter", is_wanted=1
            )
            session.commit()
        repository.save_mapping("B0ORDER_B", "B0PAID_ORDER_B", "本B")

        with Session(self.engine) as session:
            rows = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0PAID_ORDER_B")
            ).all()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0].from_kindle_sample)
            self.assertTrue(rows[0].from_bookmeter)


class GetWantedBooksTest(unittest.TestCase):
    """get_wanted_books()（is_wanted=1 の本を最新価格とあわせて取得する）のテスト。

    is_wanted=0 の本を含めないこと、価格情報が未取得の本も LEFT JOIN により
    欠落しないことを中心に検証する（Phase の設計方針: R5 対策）。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_wanted_test_")
        db_path = os.path.join(self.tmpdir, "wanted.db")
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

    def _insert_mapping(self, session, paid_asin, title, is_wanted):
        from src.models import BookMapping
        book = BookMapping(
            paid_asin=paid_asin,
            title=title,
            created_at="2026-01-01T00:00:00",
            is_purchased=0,
            is_wanted=is_wanted,
            source="bookmeter",
        )
        session.add(book)

    def _insert_price(self, session, paid_asin, sell_price, timestamp):
        from src.models import PriceHistory
        session.add(
            PriceHistory(
                paid_asin=paid_asin,
                sell_price=sell_price,
                point_value=0,
                actual_price=sell_price,
                campaign_text="",
                timestamp=timestamp,
                is_unlimited=0,
            )
        )

    def test_only_is_wanted_books_are_returned(self):
        from sqlmodel import Session
        with Session(self.engine) as session:
            self._insert_mapping(session, "B0WANT001", "読みたい本1", is_wanted=1)
            self._insert_mapping(session, "B0NOTWANT001", "読みたくない本1", is_wanted=0)
            session.commit()

        books = repository.get_wanted_books()
        asins = {b["asin"] for b in books}
        self.assertIn("B0WANT001", asins)
        self.assertNotIn("B0NOTWANT001", asins)

    def test_book_without_price_history_is_included(self):
        """R5: 価格未取得（登録直後）の本が LEFT JOIN で一覧から消えないこと。"""
        from sqlmodel import Session
        with Session(self.engine) as session:
            self._insert_mapping(session, "B0NOPRICE001", "価格未取得本", is_wanted=1)
            session.commit()

        books = repository.get_wanted_books()
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["title"], "価格未取得本")
        self.assertIsNone(books[0]["sell_price"])
        self.assertIsNone(books[0]["actual_price"])

    def test_latest_price_is_selected_when_multiple_history_rows_exist(self):
        from sqlmodel import Session
        with Session(self.engine) as session:
            self._insert_mapping(session, "B0MULTI001", "複数履歴本", is_wanted=1)
            self._insert_price(session, "B0MULTI001", sell_price=1000, timestamp="2026-01-01T00:00:00")
            self._insert_price(session, "B0MULTI001", sell_price=800, timestamp="2026-02-01T00:00:00")
            session.commit()

        books = repository.get_wanted_books()
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["sell_price"], 800)

    def test_returns_empty_list_when_no_wanted_books(self):
        from sqlmodel import Session
        with Session(self.engine) as session:
            self._insert_mapping(session, "B0NOTWANT002", "読みたくない本2", is_wanted=0)
            session.commit()

        books = repository.get_wanted_books()
        self.assertEqual(books, [])

    def test_returned_field_types(self):
        from sqlmodel import Session
        with Session(self.engine) as session:
            self._insert_mapping(session, "B0TYPE001", "型検証本", is_wanted=1)
            self._insert_price(session, "B0TYPE001", sell_price=1234, timestamp="2026-01-01T00:00:00")
            session.commit()

        books = repository.get_wanted_books()
        self.assertEqual(len(books), 1)
        book = books[0]
        self.assertIsInstance(book["title"], str)
        self.assertIsInstance(book["asin"], str)
        self.assertIsInstance(book["sell_price"], int)


class SetWantedTest(unittest.TestCase):
    """repository.set_wanted(paid_asin, status)（src/server.py の set_wanted 相当を
    repository.py へ移植したもの）のテスト。挙動を server.py と完全一致させる
    （対象無し→False、対象有り→True かつ is_wanted 更新、複数行一致時は全行更新）。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_setwanted_test_")
        db_path = os.path.join(self.tmpdir, "setwanted.db")
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

    def test_returns_false_when_paid_asin_not_found(self):
        result = repository.set_wanted("B0NOTFOUND", 1)
        self.assertFalse(result)

    def test_returns_true_and_updates_is_wanted_when_found(self):
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            session.add(BookMapping(paid_asin="B0SETWANTED", title="本", is_wanted=0, is_purchased=1))
            session.commit()

        result = repository.set_wanted("B0SETWANTED", 1)
        self.assertTrue(result)

        with Session(self.engine) as session:
            row = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0SETWANTED")
            ).first()
            self.assertEqual(row.is_wanted, 1)
            self.assertEqual(row.is_purchased, 1, "set_wantedはis_purchasedを巻き込んで書き換えてはならない")

    def test_sets_is_wanted_to_zero(self):
        """status引数がそのまま反映されること（is_wanted=1へのハードコードを検出する）。"""
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            session.add(BookMapping(paid_asin="B0SETOFF", title="本", is_wanted=1))
            session.commit()

        result = repository.set_wanted("B0SETOFF", 0)
        self.assertTrue(result)

        with Session(self.engine) as session:
            row = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0SETOFF")
            ).first()
            self.assertEqual(row.is_wanted, 0)

    def test_does_not_touch_other_paid_asin_rows(self):
        """WHERE条件の欠落（対象外行までの一括更新）を検出する。"""
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            session.add(BookMapping(paid_asin="B0TARGET", title="対象本", is_wanted=0))
            session.add(BookMapping(paid_asin="B0OTHER", title="別の本", is_wanted=0))
            session.commit()

        repository.set_wanted("B0TARGET", 1)

        with Session(self.engine) as session:
            other = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0OTHER")
            ).first()
            self.assertEqual(other.is_wanted, 0)

    def test_returns_false_and_does_not_touch_null_paid_asin_rows_when_paid_asin_is_empty(self):
        """paid_asin が空/None の場合、`WHERE paid_asin IS NULL` に化けて
        paid_asin 未設定の既存行を一括更新しないことを確認する。"""
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            session.add(
                BookMapping(sample_asin="B0NULLPAID", paid_asin=None, title="paid_asin未設定本", is_wanted=0)
            )
            session.commit()

        self.assertFalse(repository.set_wanted(None, 1))
        self.assertFalse(repository.set_wanted("", 1))

        with Session(self.engine) as session:
            row = session.exec(
                select(BookMapping).where(BookMapping.sample_asin == "B0NULLPAID")
            ).first()
            self.assertEqual(row.is_wanted, 0)

    def test_updates_all_rows_when_multiple_rows_share_paid_asin(self):
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            session.add(BookMapping(sample_asin="B0S1", paid_asin="B0DUP", title="本1", is_wanted=0))
            session.add(BookMapping(sample_asin="B0S2", paid_asin="B0DUP", title="本2", is_wanted=0))
            session.commit()

        result = repository.set_wanted("B0DUP", 1)
        self.assertTrue(result)

        with Session(self.engine) as session:
            rows = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0DUP")
            ).all()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r.is_wanted == 1 for r in rows))


class SetPurchasedTest(unittest.TestCase):
    """repository.set_purchased(paid_asin, status)（src/server.py:272-286 の set_purchased
    相当を repository.py へ移植したもの）のテスト。挙動を server.py と完全一致させる。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bookmeter_setpurchased_test_")
        db_path = os.path.join(self.tmpdir, "setpurchased.db")
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

    def test_returns_false_when_paid_asin_not_found(self):
        result = repository.set_purchased("B0NOTFOUND", 1)
        self.assertFalse(result)

    def test_returns_true_and_updates_is_purchased_when_found(self):
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            session.add(BookMapping(paid_asin="B0SETPURCHASED", title="本", is_purchased=0, is_wanted=1))
            session.commit()

        result = repository.set_purchased("B0SETPURCHASED", 1)
        self.assertTrue(result)

        with Session(self.engine) as session:
            row = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0SETPURCHASED")
            ).first()
            self.assertEqual(row.is_purchased, 1)
            self.assertEqual(row.is_wanted, 1, "set_purchasedはis_wantedを巻き込んで書き換えてはならない")

    def test_sets_is_purchased_to_zero(self):
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            session.add(BookMapping(paid_asin="B0PURCHASEDOFF", title="本", is_purchased=1))
            session.commit()

        result = repository.set_purchased("B0PURCHASEDOFF", 0)
        self.assertTrue(result)

        with Session(self.engine) as session:
            row = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0PURCHASEDOFF")
            ).first()
            self.assertEqual(row.is_purchased, 0)

    def test_updates_all_rows_when_multiple_rows_share_paid_asin(self):
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            session.add(BookMapping(sample_asin="B0PS1", paid_asin="B0PDUP", title="本1", is_purchased=0))
            session.add(BookMapping(sample_asin="B0PS2", paid_asin="B0PDUP", title="本2", is_purchased=0))
            session.commit()

        result = repository.set_purchased("B0PDUP", 1)
        self.assertTrue(result)

        with Session(self.engine) as session:
            rows = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0PDUP")
            ).all()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r.is_purchased == 1 for r in rows))

    def test_does_not_touch_other_paid_asin_rows(self):
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            session.add(BookMapping(paid_asin="B0PTARGET", title="対象本", is_purchased=0))
            session.add(BookMapping(paid_asin="B0POTHER", title="別の本", is_purchased=0))
            session.commit()

        repository.set_purchased("B0PTARGET", 1)

        with Session(self.engine) as session:
            other = session.exec(
                select(BookMapping).where(BookMapping.paid_asin == "B0POTHER")
            ).first()
            self.assertEqual(other.is_purchased, 0)

    def test_returns_false_and_does_not_touch_null_paid_asin_rows_when_paid_asin_is_empty(self):
        """paid_asin が空/None の場合、`WHERE paid_asin IS NULL` に化けて
        paid_asin 未設定の既存行を一括更新しないことを確認する（set_wanted と同種の欠陥防止）。"""
        from sqlmodel import Session, select
        from src.models import BookMapping

        with Session(self.engine) as session:
            session.add(
                BookMapping(sample_asin="B0PNULLPAID", paid_asin=None, title="paid_asin未設定本", is_purchased=0)
            )
            session.commit()

        self.assertFalse(repository.set_purchased(None, 1))
        self.assertFalse(repository.set_purchased("", 1))

        with Session(self.engine) as session:
            row = session.exec(
                select(BookMapping).where(BookMapping.sample_asin == "B0PNULLPAID")
            ).first()
            self.assertEqual(row.is_purchased, 0)


if __name__ == "__main__":
    unittest.main()
