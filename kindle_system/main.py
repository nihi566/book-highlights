"""
main.py
-------
Kindle サンプル本の抽出から価格情報のクロールまでを統合し、
SQLite データベースに結果を保存するシステム司令塔。

使い方:
    python main.py
    python main.py --limit 3    (先頭から最大3件まで処理)
    python main.py --test       (ダミーXMLを用いてテスト実行)
"""

import os
import sys
import asyncio
import random
import argparse
from datetime import datetime
import io
from sqlmodel import select

# Windows CP932 環境での文字化け防止
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# モジュールのインポート設定
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import importlib.util
spec = importlib.util.spec_from_file_location("kindle_sample_extractor", os.path.join(BASE_DIR, "test", "kindle_sample_extractor.py"))
kindle_sample_extractor = importlib.util.module_from_spec(spec)
sys.modules["kindle_sample_extractor"] = kindle_sample_extractor
spec.loader.exec_module(kindle_sample_extractor)

extract_samples = kindle_sample_extractor.extract_samples
MOCK_XML = kindle_sample_extractor.MOCK_XML

from src.resolver import resolve_sample_to_paid
from src.crawler import crawl_price_info
from src.anti_ban import BanCoordinator, get_profile
from src.database import init_db_orm, get_session
from src.models import BookMapping, PriceHistory

# データベースのパス設定
DB_DIR = os.path.join(BASE_DIR, "data")
SESSION_FILE = os.path.join(DB_DIR, "session_start.txt")  # レジューム用セッションファイル


# ─── データベース処理 ────────────────────────────────────────────────────────

def init_db() -> None:
    """データベースと必要なテーブルを初期化する。"""
    init_db_orm()


def get_paid_asin(sample_asin: str) -> str:
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
    """サンプル ASIN と 本編 ASIN の対応を DB に保存する。"""
    now = datetime.now().isoformat()
    with get_session() as session:
        statement = select(BookMapping).where(BookMapping.sample_asin == sample_asin)
        book = session.exec(statement).first()
        if book:
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
                is_purchased=0
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
            is_unlimited=is_unlimited
        )
        session.add(new_history)
        session.commit()


# ─── セッション管理（レジューム機能） ────────────────────────────────────────

def get_or_create_session_start() -> str:
    """
    セッション開始時刻を返す。
    - セッションファイルが存在する場合: 前回クラッシュした実行を再開しているため、
      そのタイムスタンプを再利用する（= 以降に処理された本はスキップ対象）。
    - 存在しない場合: 新規セッションとしてファイルを作成する。
    """
    os.makedirs(DB_DIR, exist_ok=True)
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            session_start = f.read().strip()
        print(f"  [Resume] セッションファイルを検出しました。")
        print(f"  [Resume] セッション開始時刻: {session_start}")
        return session_start
    else:
        session_start = datetime.now().isoformat()
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            f.write(session_start)
        return session_start


def clear_session() -> None:
    """全処理完了後にセッションファイルを削除する。次回は新規セッションとして実行される。"""
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)


def get_session_processed_asins(session_start: str) -> set:
    """
    セッション開始時刻以降に price_history へ記録された paid_asin のセットを返す。
    = 今回のセッションで既に処理が完了した本のリスト。
    """
    with get_session() as session:
        statement = select(PriceHistory.paid_asin).where(PriceHistory.timestamp >= session_start).distinct()
        results = session.exec(statement).all()
        return set(results)


# ─── メインロジック ──────────────────────────────────────────────────────────

async def run_integration(xml_path: str = None, limit: int = None, is_test: bool = False, start: int = None, workers: int = 1) -> None:
    """統合フローの実行"""
    print("=" * 60)
    print(f"  Kindle システム統合処理開始（並列数: {workers}）")
    print("=" * 60)

    init_db()
    print("  [OK] データベース初期化完了")

    # ── セッション管理: レジューム判定 ──────────────────────────────
    session_start = get_or_create_session_start()
    session_processed = get_session_processed_asins(session_start)
    if session_processed:
        print(f"  [Resume] このセッションで処理済みの本: {len(session_processed)} 件 → スキップします。")
    else:
        print(f"  [新規セッション] 全件フルスクレイピングを開始します。")

    # 1. XML からサンプル本を取得
    print("  [1/4] XML パース中...")
    try:
        samples = extract_samples(xml_path)
    except Exception as e:
        print(f"  [Error] XML パースに失敗しました: {e}")
        return

    print(f"  [OK] サンプル本を {len(samples)} 件取得しました。")

    if limit and limit > 0:
        samples = samples[:limit]
        print(f"  [!] 処理件数を {limit} 件に制限して実行します。")

    # 起動時のユーザー入力プロンプト (引数 start が指定されている場合はそれを優先しプロンプトをスキップ)
    manual_start = start
    if manual_start is None:
        try:
            print(f"[Start] 開始するインデックス番号を入力してください (1 ~ {len(samples)}) [Enterで通常開始]: ", end="", flush=True)
            user_input = input().strip()
            if user_input:
                manual_start = int(user_input)
                if manual_start < 1 or manual_start > len(samples):
                    manual_start = None
        except Exception:
            # 数値以外の入力や EOFError 等は安全に通常開始へフォールバック
            manual_start = None

    print("-" * 60)

    # 購入済み ASIN をループ前に一括取得
    purchased_asins = get_purchased_asins()
    if purchased_asins:
        print(f"  [購入済み] {len(purchased_asins)} 件は購入済みのためクロールをスキップします。")

    # BAN コーディネーター（並列数が2以上の場合に有効）
    ban_coordinator = BanCoordinator() if workers > 1 else None

    # ── スキップを事前適用してワークリストを作成 ─────────────────────
    work_items = []  # (i, book) のリスト
    for i, book in enumerate(samples, 1):
        sample_asin = book["asin"]

        # 手動開始位置スキップ
        if manual_start and i < manual_start:
            print(f"  [Manual-Skip] 指定位置（{manual_start}冊目）より前の処理をスキップします")
            continue

        # 購入済みスキップ
        paid_asin = get_paid_asin(sample_asin)
        if paid_asin and paid_asin in purchased_asins:
            print(f"  [Purchased-Skip] 購入済みのため確認をスキップします（ASIN: {paid_asin}）")
            continue

        # レジューム判定スキップ
        if paid_asin and paid_asin in session_processed:
            print(f"  [Resume-Skip] 既に処理済みのためスキップします（ASIN: {paid_asin}）")
            continue

        work_items.append((i, book))

    print(f"  [OK] 実処理対象: {len(work_items)} 件（並列数: {workers}）")
    print("-" * 60)

    # ── 並列処理セマフォ ─────────────────────────────────────────────
    semaphore = asyncio.Semaphore(workers)

    async def process_book(i: int, book: dict, worker_id: int) -> None:
        """1冊分の処理ワーカー関数"""
        sample_asin = book["asin"]
        title       = book["title"]
        profile     = get_profile(worker_id)

        # ワーカー起動オフセット（同時アクセスの波を平滑化）
        if workers > 1:
            offset = random.uniform(0.0, 2.0 * (worker_id % workers))
            await asyncio.sleep(offset)

        async with semaphore:
            print(f"\n[Worker-{worker_id}][{i}/{len(samples)}] {title}")
            print(f"  Sample ASIN : {sample_asin}")
            print(f"  Profile     : {profile['id']}")

            # BAN 発生中は解除を待つ
            if ban_coordinator:
                await ban_coordinator.wait_if_banned(worker_id=worker_id)

            # 2. 本編 ASIN の解決
            paid_asin = get_paid_asin(sample_asin)
            if paid_asin:
                print(f"  [OK] DB から本編 ASIN を取得しました: {paid_asin}")
            else:
                print(f"  [2/4] 本編 ASIN 解決中...")
                try:
                    paid_asin = await resolve_sample_to_paid(
                        sample_asin,
                        headless=True,
                        browser_profile=profile,
                        worker_id=worker_id,
                        ban_coordinator=ban_coordinator,
                    )
                    if paid_asin:
                        print(f"  [OK] 本編 ASIN 解決成功: {paid_asin}")
                        save_mapping(sample_asin, paid_asin, title)
                    else:
                        print("  [✗] 本編 ASIN を解決できませんでした。スキップします。")
                        return
                except Exception as e:
                    print(f"  [Error] ASIN 解決中にエラー発生: {e}")
                    return

            # BAN 待機チェック（resolve 後）
            if ban_coordinator:
                await ban_coordinator.wait_if_banned(worker_id=worker_id)

            # 3. 価格情報をクロール
            print(f"  [3/4] 価格情報をクロール中 (ASIN: {paid_asin})...")
            try:
                price_data = await crawl_price_info(
                    paid_asin,
                    headless=True,
                    browser_profile=profile,
                    worker_id=worker_id,
                    ban_coordinator=ban_coordinator,
                )
                print(f"  [OK] クロール成功: 価格=¥{price_data.get('sell_price')}, ポイント={price_data.get('point_value')}pt")

                # 4. DB に保存
                save_price_history(price_data)
                print("  [4/4] データベースに保存しました。")

            except Exception as e:
                print(f"  [Error] クロール中にエラー発生: {e}")

            # ワーカー間ディレイ（Amazon アクセス回避）
            delay = random.uniform(1.5, 3.5) if workers > 1 else random.uniform(2.0, 5.0)
            print(f"  [Sleep] アクセス回避のため {delay:.1f} 秒待機します...")
            await asyncio.sleep(delay)

    # ── 全ワーカーを並列起動 ─────────────────────────────────────────
    tasks = [
        process_book(i, book, worker_id=(idx % workers) + 1)
        for idx, (i, book) in enumerate(work_items)
    ]
    await asyncio.gather(*tasks)

    print("\n" + "=" * 60)
    print("  全処理が完了しました。")
    print("=" * 60)

    # 全処理完了: セッションファイルを削除し、次回は新規セッションとして実行されるようにする
    clear_session()
    print("  [OK] セッションをクリアしました。次回実行時は全件処理されます。")




def run_tests():
    """内蔵のダミー XML を用いたテスト実行"""
    import tempfile
    
    print("\n>>> テストモードで実行します <<<")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".xml", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(MOCK_XML)
        tmp_path = tmp.name
        
    try:
        # B0SAMPLE001 などは Amazon に実在しないため、resolver が失敗します。
        # 代わりに、手動でマッピングを登録してからクロールするか、
        # 解決できない場合のフローだけテストします。
        
        # ダミーではなく、実際に Amazon に存在するダミー XML を生成してテストする
        REAL_MOCK_XML = """<?xml version="1.0" encoding="UTF-8"?>
        <response><add_update_list>
          <meta_data>
            <ASIN>B0GGY819NL</ASIN>
            <title>マンガの裏技 (サンプル版)</title>
            <cde_contenttype>EBSP</cde_contenttype>
            <origins><origin><type>Sample</type></origin></origins>
          </meta_data>
        </add_update_list></response>
        """
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(REAL_MOCK_XML)
            
        asyncio.run(run_integration(xml_path=tmp_path, is_test=True))
        
        # DB が正しく作成され、データが入っているか検証
        from sqlmodel import select, func
        with get_session() as session:
            mappings_count = session.exec(select(func.count()).select_from(BookMapping)).one()
            history_count = session.exec(select(func.count()).select_from(PriceHistory)).one()
            
            print(f"\n[検証結果] マッピング件数: {mappings_count}, 履歴件数: {history_count}")
            if mappings_count > 0 and history_count > 0:
                print(">>> テスト PASSED <<<")
            else:
                print(">>> テスト FAILED <<<")
                
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kindle モニターシステム")
    parser.add_argument("--xml", help="対象の XML ファイルパス (省略時はデフォルト)", default=None)
    parser.add_argument("--limit", type=int, help="処理する最大件数", default=None)
    parser.add_argument("--start", type=int, help="開始するインデックス番号", default=None)
    parser.add_argument("--test", action="store_true", help="内蔵テストを実行する")
    parser.add_argument("--workers", type=int, default=1,
                        help="並列ブラウザ数（デフォルト: 1、推奨: 2〜3）")
    args = parser.parse_args()
    
    if args.test:
        run_tests()
    else:
        # デフォルト XML のパスは extractor のものを利用
        DEFAULT_CACHE_PATH = kindle_sample_extractor.DEFAULT_CACHE_PATH
        xml_path = args.xml or DEFAULT_CACHE_PATH
        if not os.path.exists(xml_path):
            print(f"エラー: XML ファイルが見つかりません: {xml_path}")
            sys.exit(1)

        workers = max(1, min(args.workers, 5))  # 1以上5以下にクランプ
        if workers != args.workers:
            print(f"[注意] --workers は 1、5 の範囲にクランプされました: {args.workers} → {workers}")

        asyncio.run(run_integration(xml_path=xml_path, limit=args.limit, start=args.start, workers=workers))
