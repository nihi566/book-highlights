"""
src/repository.py
------------------
book_mappings / price_history へのデータアクセスを集約するモジュール。

main.py に直書きされていた DB アクセス関数（save_price_history / save_mapping /
get_paid_asin / get_purchased_asins）をここへ移し、bookmeter 系の新規 Phase からも
再利用できるようにする。

あわせて、book_mappings の旧スキーマ（sample_asin 主キー・source 列なし）を
新スキーマ（id 主キー・sample_asin nullable・source 列あり）へ移行する
マイグレーション関数と、paid_asin 一致による dedup ヘルパーを提供する。
"""

import os
import sqlite3
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from src.database import DB_PATH, get_session, init_db_orm
from src.models import BookMapping, PriceHistory


def backup_database(db_path: str = DB_PATH) -> Optional[str]:
    """
    タイムスタンプ付きバックアップを作成する。

    sqlite3 の backup API を使うため、WAL モードで未チェックポイントのデータも
    含めて複製できる（単純な shutil.copyfile では WAL 分が欠落する恐れがある）。
    命名規則: <db_path>.bak-<YYYYMMDDHHMMSS>

    db_path が存在しない場合（新規 DB）は何もせず None を返す。
    """
    if not os.path.exists(db_path):
        return None

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = f"{db_path}.bak-{timestamp}"

    source = sqlite3.connect(db_path, timeout=30)
    try:
        dest = sqlite3.connect(backup_path, timeout=30)
        try:
            source.backup(dest)
        finally:
            dest.close()
    except Exception:
        # 途中失敗した不完全なバックアップファイルを残さない
        # （最新の .bak-* を無条件に正として復元されると壊れたデータを掴む）
        if os.path.exists(backup_path):
            os.remove(backup_path)
        raise
    finally:
        source.close()

    return backup_path


def migrate_book_mappings_schema(db_path: str = DB_PATH) -> None:
    """
    book_mappings を新スキーマ（id 主キー・source 列・sample_asin nullable）へ移行する。

    冪等: 既に id / source 列が存在する場合は何もしない。
    db_path が存在しない、または book_mappings テーブルが未作成の場合も何もしない
    （SQLModel.metadata.create_all が新スキーマで作成するため）。

    移行手順（1トランザクション）:
      1. バックアップを作成する
      2. 新スキーマの一時テーブルを作成する
      3. 旧テーブルから全行を INSERT SELECT する（source は全件 'kindle_sample' を補完）
      4. 件数が一致することを確認する（不一致なら ROLLBACK して例外を投げる）
      5. 旧テーブルを DROP し、一時テーブルを book_mappings へ RENAME する
    """
    if not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path, timeout=30)
    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='book_mappings'"
        )
        if cur.fetchone() is None:
            return

        # 冪等チェックは BEGIN IMMEDIATE（排他ロック取得）の内側で行う。
        # BEGIN の外でチェックすると、main.py と server.py が同時起動した場合に
        # 両プロセスとも「旧スキーマ」と判定し、後発が既に移行済みのテーブルを
        # 再度コピーし直してしまう（TOCTOU。source が全件 'kindle_sample' に
        # 上書きされ、id も振り直される）。
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur.execute("PRAGMA table_info(book_mappings)")
            columns = {row[1] for row in cur.fetchall()}
            if "id" in columns and "source" in columns:
                conn.rollback()
                return

            backup_path = backup_database(db_path)

            cur.execute("""
                CREATE TABLE book_mappings_new (
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
            cur.execute("""
                INSERT INTO book_mappings_new
                    (sample_asin, paid_asin, title, created_at, is_purchased, is_wanted, source)
                SELECT sample_asin, paid_asin, title, created_at, is_purchased, is_wanted, 'kindle_sample'
                FROM book_mappings
            """)

            cur.execute("SELECT COUNT(*) FROM book_mappings")
            old_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM book_mappings_new")
            new_count = cur.fetchone()[0]
            if old_count != new_count:
                conn.rollback()
                raise RuntimeError(
                    "book_mappings migration failed: row count mismatch "
                    f"(old={old_count}, new={new_count}). "
                    f"元のテーブルは無傷です。バックアップ: {backup_path}"
                )

            cur.execute("DROP TABLE book_mappings")
            cur.execute("ALTER TABLE book_mappings_new RENAME TO book_mappings")
            # sample_asin は主キーではなくなったが、非NULL値の一意性（旧スキーマでは
            # PRIMARY KEY により保証されていた）は部分UNIQUEインデックスで維持する。
            # bookmeter 由来行（sample_asin=NULL）はこの制約の対象外。
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_book_mappings_sample_asin_unique "
                "ON book_mappings (sample_asin) WHERE sample_asin IS NOT NULL"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS ix_book_mappings_paid_asin "
                "ON book_mappings (paid_asin)"
            )
            conn.commit()
            print(
                f"  [Migration] book_mappings を新スキーマへ移行しました"
                f"（{new_count} 行、バックアップ: {backup_path}）"
            )
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def init_db() -> None:
    """データベースのテーブルを作成し、既存 book_mappings があれば新スキーマへ移行する。"""
    init_db_orm()
    migrate_book_mappings_schema()


def get_paid_asin(sample_asin: str) -> Optional[str]:
    """DB に保存済みの本編 ASIN を取得する。なければ None。"""
    with get_session() as session:
        statement = select(BookMapping).where(BookMapping.sample_asin == sample_asin)
        book = session.exec(statement).first()
        return book.paid_asin if book and book.paid_asin else None


def get_purchased_asins() -> set:
    """購入済み（is_purchased=1）の paid_asin の集合を返す。"""
    with get_session() as session:
        statement = select(BookMapping).where(BookMapping.is_purchased == 1)
        books = session.exec(statement).all()
        return {b.paid_asin for b in books if b.paid_asin}


def save_mapping(sample_asin: str, paid_asin: str, title: str) -> None:
    """
    サンプル ASIN と本編 ASIN の対応を DB に保存する（kindle_sample 経由）。

    sample_asin 一致の既存行を優先して探すが、見つからない場合は paid_asin 一致
    （bookmeter 経由で先に登録された行等）も確認し、同一書籍の重複行を作らない
    （Phase の目的: 双方のソースから登録されても行が重複しないこと）。
    """
    now = datetime.now().isoformat()
    with get_session() as session:
        statement = select(BookMapping).where(BookMapping.sample_asin == sample_asin)
        book = session.exec(statement).first()
        if not book and paid_asin:
            statement = select(BookMapping).where(BookMapping.paid_asin == paid_asin)
            book = session.exec(statement).first()
        if book:
            book.sample_asin = sample_asin
            book.paid_asin = paid_asin
            book.title = title
            book.created_at = now
            session.add(book)
        else:
            new_book = BookMapping(
                sample_asin=sample_asin,
                paid_asin=paid_asin,
                title=title,
                created_at=now,
                is_purchased=0,
                source="kindle_sample",
            )
            session.add(new_book)
        session.commit()


def save_price_history(data: dict) -> None:
    """クロールした価格情報を DB に保存する。"""
    now = datetime.now().isoformat()
    sell_price = data.get("sell_price")
    point_value = data.get("point_value", 0)
    campaign_text = data.get("campaign_text", "")
    is_unlimited = data.get("is_unlimited", 0)

    # 実質価格を計算
    actual_price = None
    if sell_price is not None:
        actual_price = sell_price - point_value

    with get_session() as session:
        new_history = PriceHistory(
            paid_asin=data["asin"],
            sell_price=sell_price,
            point_value=point_value,
            actual_price=actual_price,
            campaign_text=campaign_text,
            timestamp=now,
            is_unlimited=is_unlimited,
        )
        session.add(new_history)
        session.commit()


def get_or_create_by_paid_asin(
    session: Session,
    paid_asin: str,
    title: Optional[str] = None,
    source: str = "bookmeter",
    is_wanted: int = 1,
) -> BookMapping:
    """
    paid_asin 一致による dedup ヘルパー。

    既存行（sample_asin 経由 / bookmeter 経由いずれでも）があれば新規行を作らず
    is_wanted のみ更新して返す。無ければ新規作成する（sample_asin は None のまま）。

    他の関数と異なり session を呼び出し側から受け取る（複数冊をまとめて 1 トランザクション
    で処理したい呼び出し元のため）。**commit は呼び出し側の責務**。この関数は
    flush のみ行い、返す BookMapping には自動採番済みの id が反映される。

    paid_asin が空/None の場合は例外を送出する（`WHERE paid_asin IS NULL` に化けて
    無関係な既存行を誤って更新するのを防ぐ）。
    """
    if not paid_asin:
        raise ValueError("paid_asin must be a non-empty string")

    statement = select(BookMapping).where(BookMapping.paid_asin == paid_asin)
    book = session.exec(statement).first()
    if book:
        book.is_wanted = is_wanted
        session.add(book)
        session.flush()
        session.refresh(book)
        return book

    now = datetime.now().isoformat()
    new_book = BookMapping(
        paid_asin=paid_asin,
        title=title,
        created_at=now,
        is_purchased=0,
        is_wanted=is_wanted,
        source=source,
    )
    session.add(new_book)
    session.flush()
    session.refresh(new_book)
    return new_book
