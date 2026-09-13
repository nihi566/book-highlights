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

import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select, text

from src import database as database_module
from src.database import DB_PATH, get_session, init_db_orm
from src.models import BookMapping, PriceHistory

logger = logging.getLogger(__name__)


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


def _apply_source_flag_backfill(cur: sqlite3.Cursor, columns: set) -> None:
    """from_kindle_sample/from_bookmeter 列を(無ければ)追加し、既存の source
    値から best-effort で backfill する。呼び出し側が開いたトランザクション内で
    実行される前提で、commit/rollback はしない。"""
    if "from_kindle_sample" not in columns:
        cur.execute(
            "ALTER TABLE book_mappings ADD COLUMN from_kindle_sample "
            "INTEGER NOT NULL DEFAULT 0"
        )
    if "from_bookmeter" not in columns:
        cur.execute(
            "ALTER TABLE book_mappings ADD COLUMN from_bookmeter "
            "INTEGER NOT NULL DEFAULT 0"
        )
    cur.execute(
        "UPDATE book_mappings SET from_kindle_sample = 1 "
        "WHERE source = 'kindle_sample' OR sample_asin IS NOT NULL"
    )
    cur.execute("UPDATE book_mappings SET from_bookmeter = 1 WHERE source = 'bookmeter'")


def _backfill_source_flags_in_place(db_path: str) -> str:
    """_apply_source_flag_backfill を db_path へ直接・1トランザクションで適用する。

    ALTER TABLE ADD COLUMN + UPDATE は元ファイルへの追記のみで完結するため、
    migrate_book_mappings_schema 本体が使う「全ファイル複製→os.replace()」は
    使わない。全ファイル複製〜置換の間は他プロセスの同時書き込み
    （crawler 側の price_history 追記等）を巻き込んで破棄しうるため、
    列追加だけで済むこの経路ではその窓を作らないほうが安全。

    列の存在チェックは BEGIN IMMEDIATE でロックを取ってから読み直す（呼び出し前に
    読んだ列集合のままだと、ロック待ちの間に他プロセスが先に列を追加していた場合
    "duplicate column name" で失敗しうるため）。

    戻り値:
      "done"     — 成功。呼び出し側はここで終了してよい。
      "skip"     — 他プロセスが db_path をロック中（一時的な競合）。全ファイル複製→
                   置換は同時書き込みを巻き込みかねないため降格せず、今回は何もせず
                   見送る（次回起動時に再試行される）。
      "fallback" — db_path へ書き込めない（実データファイルがオーナー相違で読み取り
                   専用等）。呼び出し側の複製→置換フォールバックに委ねる。
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            return "skip" if _is_lock_contention(e) else "fallback"

        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(book_mappings)")
            columns = {row[1] for row in cur.fetchall()}
            _apply_source_flag_backfill(cur, columns)
            conn.commit()
            return "done"
        except sqlite3.OperationalError as e:
            conn.rollback()
            return "skip" if _is_lock_contention(e) else "fallback"
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def _is_lock_contention(error: sqlite3.OperationalError) -> bool:
    """一時的なロック競合（database is locked / database is busy）かどうかを
    エラーメッセージから判定する。書き込み権限が無い場合の "attempt to write a
    readonly database" 等とは区別し、前者は複製→置換へ降格させない。"""
    message = str(error).lower()
    return "locked" in message or "busy" in message


def migrate_book_mappings_schema(db_path: str = DB_PATH) -> None:
    """
    book_mappings を新スキーマ（id 主キー・source 列・sample_asin nullable・
    from_kindle_sample/from_bookmeter 列）へ移行する。

    冪等: 既に id / source / from_kindle_sample / from_bookmeter の 4 列が
    揃っている場合は何もしない。
    db_path が存在しない、または book_mappings テーブルが未作成の場合も何もしない
    （SQLModel.metadata.create_all が新スキーマで作成するため）。

    移行は 2 パターンある:
      (a) 真の旧スキーマ（sample_asin PK・source/flags 列なし）からの全面移行。
          source 列自体が無かった時期のデータは全て kindle_sample 経由とみなす。
      (b) 新スキーマ（id/source あり）だが from_kindle_sample/from_bookmeter が
          無いだけ。ALTER TABLE ADD COLUMN + UPDATE のみで完結するため、
          まず `_backfill_source_flags_in_place` で db_path へ直接適用する。
          他プロセスが db_path をロック中の一時的な競合（"database is locked" 等）
          の場合は、複製→置換へ降格せず何もせず見送る（次回起動時に再試行される。
          複製→置換は同時書き込みを巻き込みうるため、一時的な競合ではそちらを
          使わないほうが安全なため）。

    実データファイルはオーナーが異なり書き込み不可な場合がある
    （実測: `-rw-r--r-- root:root`。実行ユーザーからは書き込み不可）。
    (b) が書き込み不可（読み取り専用等。ロック競合ではない）で失敗した場合、
    および (a) は常に、SQLite の CREATE/DROP/RENAME がファイル自体への
    書き込み権限を要するため、db_path を直接書き換えず以下のフォールバックを使う:
      1. バックアップを作成する（sqlite3 backup API。恒久的な保全用）
      2. 書き込み可能な一時ファイル（同じ backup API で全体複製）へ移行作業を行う
         （複製の読み取りは world-readable なファイルであれば所有者に関わらず可能）
      3. 一時ファイル上でスキーマ移行を 1 トランザクションで行う
         （(a) は CREATE new → INSERT SELECT → 件数検証 → DROP old → RENAME →
         インデックス作成。(b) のフォールバックは ALTER TABLE ADD COLUMN → UPDATE のみ）
      4. 検証済みの一時ファイルを `os.replace()` で db_path へ原子的に差し替える
         （`data/` ディレクトリへの書き込み権限があれば、対象ファイル自体の
         所有者・パーミッションに関わらず置換できる。実測済み。置換直前に
         book_mappings に加え price_history の行数も複製時から変化していないか
         確認し、変化していれば他プロセスの同時書き込みを上書きしないよう中止する）
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
        cur.execute("PRAGMA table_info(book_mappings)")
        columns = {row[1] for row in cur.fetchall()}
        has_id_source = "id" in columns and "source" in columns
        has_source_flags = "from_kindle_sample" in columns and "from_bookmeter" in columns
        if has_id_source and has_source_flags:
            return
    finally:
        conn.close()

    # 既存の複製→置換パターンと同様、実ファイルへ書き込む前に必ずバックアップを
    # 作る（in-place 経路が成功する場合も含む。恒久的な保全用）。
    backup_path = backup_database(db_path)

    if has_id_source:
        outcome = _backfill_source_flags_in_place(db_path)
        if outcome in ("done", "skip"):
            # "skip" は他プロセスによる一時的なロック競合。複製→置換は同時書き込みを
            # 巻き込みかねないため降格せず、次回起動時の再試行に委ねる。
            return
        # outcome == "fallback"（書き込み不可）のときのみ、以下の複製→置換へ進む。

    staging_path = f"{db_path}.migrating-{os.getpid()}"
    if os.path.exists(staging_path):
        os.remove(staging_path)

    try:
        source = sqlite3.connect(db_path, timeout=30)
        try:
            staging_conn = sqlite3.connect(staging_path, timeout=30)
            try:
                source.backup(staging_conn)
            finally:
                staging_conn.close()
        finally:
            source.close()

        new_count = None
        conn = sqlite3.connect(staging_path, timeout=30)
        try:
            cur = conn.cursor()

            # 複製後の一時ファイル上で再度冪等チェック（他プロセスが db_path の
            # 複製〜置換の間に先に移行を完了させていた場合はここで検知できる。
            # 先に完了した側の os.replace() は既に db_path へ反映済みのため、
            # この staging を破棄するだけで安全に収束する）。
            cur.execute("PRAGMA table_info(book_mappings)")
            columns = {row[1] for row in cur.fetchall()}
            has_id_source = "id" in columns and "source" in columns
            has_source_flags = "from_kindle_sample" in columns and "from_bookmeter" in columns
            if has_id_source and has_source_flags:
                return

            # book_mappings 以外にも同時書き込みが起こりうるテーブル（crawler が
            # 追記する price_history）の複製時点の行数を控えておく。置換直前の
            # 再確認でここと食い違えば、他プロセスの書き込みを上書きしないよう
            # 置換を中止する（book_mappings の行数比較だけでは検知できないため）。
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='price_history'"
            )
            has_price_history = cur.fetchone() is not None
            staged_price_history_count = None
            if has_price_history:
                cur.execute("SELECT COUNT(*) FROM price_history")
                staged_price_history_count = cur.fetchone()[0]

            conn.execute("BEGIN IMMEDIATE")
            try:
                if not has_id_source:
                    # 真の旧スキーマ（sample_asin PK・source/flags列なし）からの
                    # 全面移行。source列自体が無かった時期のデータは全て
                    # kindle_sample 経由だったとみなす。
                    cur.execute("""
                        CREATE TABLE book_mappings_new (
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
                    cur.execute("""
                        INSERT INTO book_mappings_new
                            (sample_asin, paid_asin, title, created_at, is_purchased, is_wanted,
                             source, from_kindle_sample, from_bookmeter)
                        SELECT sample_asin, paid_asin, title, created_at, is_purchased, is_wanted,
                               'kindle_sample', 1, 0
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
                            f"元のファイルには一切書き込んでいないため無傷です。バックアップ: {backup_path}"
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
                else:
                    # 新スキーマ（id/source あり）だが from_kindle_sample/from_bookmeter が
                    # まだ無い。_backfill_source_flags_in_place が db_path へ直接書き込め
                    # なかった場合のフォールバックのみここを通る（想定外の source 値の
                    # 行は両フラグ 0 のまま許容する）。
                    cur.execute("SELECT COUNT(*) FROM book_mappings")
                    old_count = cur.fetchone()[0]
                    new_count = old_count
                    _apply_source_flag_backfill(cur, columns)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

        # 複製〜ここまでの間に db_path 自体が他プロセスに書き換えられていないか
        # 最終確認する（真の排他ロックではないが、無言の上書きより安全に失敗させる）。
        recheck_conn = sqlite3.connect(db_path, timeout=30)
        try:
            recheck_cur = recheck_conn.cursor()
            recheck_cur.execute("PRAGMA table_info(book_mappings)")
            recheck_columns = {row[1] for row in recheck_cur.fetchall()}
            if (
                "id" in recheck_columns
                and "source" in recheck_columns
                and "from_kindle_sample" in recheck_columns
                and "from_bookmeter" in recheck_columns
            ):
                # 他プロセスが先に置換済み。この staging は不要になった。
                return
            recheck_cur.execute("SELECT COUNT(*) FROM book_mappings")
            current_count = recheck_cur.fetchone()[0]
            current_price_history_count = None
            if has_price_history:
                recheck_cur.execute("SELECT COUNT(*) FROM price_history")
                current_price_history_count = recheck_cur.fetchone()[0]
        finally:
            recheck_conn.close()
        if current_count != old_count:
            raise RuntimeError(
                "book_mappings migration aborted: 複製後に元ファイルの行数が変化しました "
                f"(複製時={old_count}, 置換直前={current_count})。他プロセスの書き込みを"
                f"上書きしないよう置換を中止しました。バックアップ: {backup_path}"
            )
        if has_price_history and current_price_history_count != staged_price_history_count:
            raise RuntimeError(
                "book_mappings migration aborted: 複製後に price_history の行数が変化しました "
                f"(複製時={staged_price_history_count}, 置換直前={current_price_history_count})。"
                f"他プロセスの書き込みを上書きしないよう置換を中止しました。バックアップ: {backup_path}"
            )

        # 元ファイルの WAL/journal サイドカーが残っていると、置換後に古い
        # 差分が新ファイルへ誤って再生されうるため、置換前に取り除く。
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = db_path + suffix
            if os.path.exists(sidecar):
                os.remove(sidecar)

        # ここに到達した時点で staging_path は検証済みの新スキーマ。
        # 元ファイルには一度も書き込んでいないため、失敗時は無傷のまま残る。
        os.replace(staging_path, db_path)

        # SQLAlchemy の接続プールが置換前（旧 inode）の接続を保持したままだと、
        # 以後の書き込みが「削除済みだが参照され続けているファイル」に対して
        # 行われてしまい、db_path から見える内容と食い違う（実測で再現・確認済み）。
        # プールを破棄し、以後の接続が新ファイルを開き直すようにする。
        database_module.engine.dispose()

        print(
            f"  [Migration] book_mappings を新スキーマへ移行しました"
            f"（{new_count} 行、バックアップ: {backup_path}）"
        )
    finally:
        if os.path.exists(staging_path):
            os.remove(staging_path)


def init_db() -> None:
    """データベースのテーブルを作成し、既存 book_mappings があれば新スキーマへ移行する。

    スキーマ移行が失敗しても起動そのものは止めない（警告を出し、既存スキーマの
    まま続行する）。crawler と server の両プロセスがこの関数を起動時に呼ぶため、
    一方が book_mappings/price_history へ書き込み中の一時的な競合で移行が中断
    しうる。その場合に例外を伝播させると起動自体が失敗してしまう（移行の遅延は
    次回起動時のリトライで解消でき、データ自体は無傷のため、起動を止めてまで
    今すぐ解決する必要はない）。
    """
    init_db_orm()
    # database_module.DB_PATH を都度参照する（migrate_book_mappings_schema の
    # デフォルト引数 DB_PATH はモジュール import 時点の値に固定されるため、
    # テスト等で src.database.DB_PATH を差し替えても追従しない）。
    try:
        migrate_book_mappings_schema(database_module.DB_PATH)
    except Exception as e:
        print(
            f"  [Migration] book_mappings のスキーマ移行に失敗しました。"
            f"既存のスキーマのまま起動を継続します（次回起動時に再試行されます）: {e}"
        )


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
            old_sample_asin = book.sample_asin
            if old_sample_asin and old_sample_asin != sample_asin:
                # paid_asin一致でマージする際、既存のsample_asinを別値で上書きする。
                # 上書き自体は禁止しない(仕様判断が要るためYAGNI)が、旧値との対応関係が
                # DB上のどこにも残らなくなるため、追跡できるようログに残す。
                # このプロジェクトにはlogging設定(basicConfig等)が無く、root loggerの
                # 既定レベルはWARNINGのため、INFOでは本番実行経路で出力されない
                # (lastResortハンドラもWARNING以上のみ)。src/bookmeter.pyの既存規約も
                # warning/errorのみを使っており、それに合わせる。
                logger.warning(
                    "save_mapping: paid_asin=%s の既存sample_asinを上書きします "
                    "(旧: %s -> 新: %s)",
                    paid_asin,
                    old_sample_asin,
                    sample_asin,
                )
            book.sample_asin = sample_asin
            book.paid_asin = paid_asin
            book.title = title
            book.created_at = now
            book.from_kindle_sample = True
            session.add(book)
        else:
            new_book = BookMapping(
                sample_asin=sample_asin,
                paid_asin=paid_asin,
                title=title,
                created_at=now,
                is_purchased=0,
                source="kindle_sample",
                from_kindle_sample=True,
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


def set_wanted(paid_asin: str, status: int) -> bool:
    """欲しい本フラグをDBUPDATE。対象が見つからない場合は False。

    戻り値の契約（成功 True / 対象なし・例外時 False。例外握り潰しを含む）は
    src/server.py の set_wanted を踏襲する。ただし paid_asin が空/None の場合は
    移植元と異なり早期 return する（意図的な差分。移植元のままだと WHERE 句が
    `paid_asin IS NULL` に化け、sample_asin/bookmeter 由来で paid_asin が未設定の
    既存行を一括更新してしまうため。get_or_create_by_paid_asin と同じ理由）。
    """
    if not paid_asin:
        return False
    try:
        with get_session() as session:
            statement = select(BookMapping).where(BookMapping.paid_asin == paid_asin)
            books = session.exec(statement).all()
            if not books:
                return False
            for book in books:
                book.is_wanted = status
                session.add(book)
            session.commit()
            return True
    except Exception:
        return False


def set_purchased(paid_asin: str, status: int) -> bool:
    """購入済みDBUPDATE。対象が見つからない場合は False。

    戻り値の契約（成功 True / 対象なし・例外時 False。例外握り潰しを含む）は
    src/server.py の set_purchased を踏襲する。ただし paid_asin が空/None の場合は
    移植元と異なり早期 return する（set_wanted と同じ理由。移植元のままだと
    WHERE 句が `paid_asin IS NULL` に化け、無関係な既存行を一括更新してしまうため）。
    """
    if not paid_asin:
        return False
    try:
        with get_session() as session:
            statement = select(BookMapping).where(BookMapping.paid_asin == paid_asin)
            books = session.exec(statement).all()
            if not books:
                return False
            for book in books:
                book.is_purchased = status
                session.add(book)
            session.commit()
            return True
    except Exception:
        return False


def get_wanted_books() -> list:
    """
    is_wanted=1 の本を、最新の価格情報とあわせて取得する（wishlist-site-report 用）。

    src/server.py の get_books() と同じ「price_history を timestamp 最新1件に絞る」
    SQL パターンを使うが、INNER JOIN ではなく LEFT JOIN にする。読みたい本は登録直後で
    価格情報が未取得のことがあり、INNER JOIN のままだとそのような本が理由もなく
    一覧から消えてしまうため（設計方針 R5 対策）。
    """
    query = text("""
        WITH latest_prices AS (
            SELECT p1.paid_asin, p1.sell_price, p1.point_value, p1.actual_price, p1.campaign_text, p1.timestamp, p1.is_unlimited
            FROM price_history p1
            INNER JOIN (
                SELECT paid_asin, MAX(timestamp) as max_ts
                FROM price_history
                GROUP BY paid_asin
            ) p2 ON p1.paid_asin = p2.paid_asin AND p1.timestamp = p2.max_ts
        )
        SELECT
            m.title,
            m.paid_asin as asin,
            l.sell_price,
            l.point_value,
            l.actual_price,
            l.campaign_text,
            l.timestamp,
            l.is_unlimited
        FROM book_mappings m
        LEFT JOIN latest_prices l ON m.paid_asin = l.paid_asin
        WHERE m.is_wanted = 1
        ORDER BY m.title ASC
    """)
    with get_session() as session:
        result = session.exec(query).mappings().all()
        return [dict(row) for row in result]


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
    is_wanted と from_bookmeter（相手側の from_kindle_sample は変更しない）を
    更新して返す。無ければ新規作成する（sample_asin は None のまま）。

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
        book.from_bookmeter = True
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
        from_bookmeter=True,
    )
    session.add(new_book)
    session.flush()
    session.refresh(new_book)
    return new_book
