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
from src.anti_ban import BanCoordinator, RequestPacer, get_profile
from src.repository import (
    init_db,
    get_paid_asin,
    get_purchased_asins,
    save_mapping,
    save_price_history,
)
from src.database import get_session
from src.models import BookMapping, PriceHistory

# データベースのパス設定
DB_DIR = os.path.join(BASE_DIR, "data")
SESSION_FILE = os.path.join(DB_DIR, "session_start.txt")  # レジューム用セッションファイル


# ─── データベース処理 ────────────────────────────────────────────────────────
# save_price_history / save_mapping / get_paid_asin / get_purchased_asins / init_db は
# bookmeter 系の新規 Phase からも再利用するため src/repository.py へ移動した。
# ここでは src.repository からの import をそのまま使う。


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


# ─── ログ整形（1 冊単位の結果行・集計行） ────────────────────────────────────
# 並列ワーカーの出力は行が混ざるため、1 冊ごとに必ず 1 行の結果行を出し、
# 全ワーカー終了後に集計行を出す。scraping-hub 側のパーサがこの形式に依存するため、
# 文言（「結果: 」「成功 / 失敗 / スキップ」「集計: 」）を変えるときは両側を揃えること。

RESULT_SUCCESS = "成功"
RESULT_FAILURE = "失敗"
RESULT_SKIP = "スキップ"


def format_price_summary(price_data: dict) -> str:
    """クロール結果を `価格=¥2,695 ポイント=81pt` 形式にする。"""
    sell_price = price_data.get("sell_price")
    price_text = f"¥{sell_price:,}" if sell_price is not None else "取得不可"
    return f"価格={price_text} ポイント={price_data.get('point_value') or 0}pt"


def classify_crawl_result(price_data: dict) -> tuple:
    """
    クロール結果から (結果種別, 詳細) を返す。
    crawl_price_info はページアクセスエラーや BAN 検知時に例外を投げず、
    sell_price=None のまま返すため、価格が取れていないものは失敗として扱う。
    """
    if price_data.get("sell_price") is None:
        return RESULT_FAILURE, "価格を取得できませんでした（ページアクセスエラーまたは BAN 検知の可能性）"
    return RESULT_SUCCESS, format_price_summary(price_data)


def format_result_line(worker_id: int, index: int, total: int, status: str, detail: str = "") -> str:
    """1 冊の結果行 `[Worker-N][i/total] 結果: 成功 ...` を組み立てる。"""
    line = f"[Worker-{worker_id}][{index}/{total}] 結果: {status}"
    return f"{line} {detail}" if detail else line


def format_summary_line(counts: dict, resumed: int = 0) -> str:
    """全体の集計行 `集計: 成功 N 件 / 失敗 N 件 / スキップ N 件` を組み立てる。"""
    line = (
        f"集計: 成功 {counts.get(RESULT_SUCCESS, 0)} 件"
        f" / 失敗 {counts.get(RESULT_FAILURE, 0)} 件"
        f" / スキップ {counts.get(RESULT_SKIP, 0)} 件"
    )
    if resumed:
        line += f"（前回処理済み {resumed} 件）"
    return line


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
    # アクセス間隔調整（並列数が2以上の場合に有効。ワーカー数を増やしても合計アクセス頻度が増えないようにする）
    request_pacer = RequestPacer() if workers > 1 else None

    # ── スキップを事前適用してワークリストを作成 ─────────────────────
    work_items = []  # (i, book) のリスト
    resumed_count = 0  # Resume-Skip で前回処理済みとして飛ばした件数（集計行用）
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
            resumed_count += 1
            continue

        work_items.append((i, book))

    print(f"  [OK] 実処理対象: {len(work_items)} 件（並列数: {workers}）")
    print("-" * 60)

    # ── キューの作成とタスク投入 ─────────────────────────────────────
    queue = asyncio.Queue()
    for item in work_items:
        queue.put_nowait(item)

    # 1 冊ごとの結果件数（集計行用）。ワーカーは同一イベントループ上で動くためロック不要
    result_counts = {RESULT_SUCCESS: 0, RESULT_FAILURE: 0, RESULT_SKIP: 0}

    async def process_book(i: int, book: dict, worker_id: int) -> tuple:
        """
        1冊分の処理関数（セマフォ不要）。
        戻り値は (結果種別, 詳細)。結果行の出力は worker 側で 1 冊につき必ず 1 回行う。
        """
        sample_asin = book["asin"]
        title       = book["title"]
        profile     = get_profile(worker_id)
        wp          = f"[Worker-{worker_id}]"

        print(f"\n{wp}[{i}/{len(samples)}] {title}")
        print(f"  {wp} Sample ASIN : {sample_asin}")
        print(f"  {wp} Profile     : {profile['id']}")

        # BAN 発生中は解除を待つ
        if ban_coordinator:
            await ban_coordinator.wait_if_banned(worker_id=worker_id)

        # 2. 本編 ASIN の解決
        paid_asin = get_paid_asin(sample_asin)
        if paid_asin:
            print(f"  {wp}[OK] DB から本編 ASIN を取得しました: {paid_asin}")
        else:
            print(f"  {wp}[2/4] 本編 ASIN 解決中...")
            try:
                paid_asin = await resolve_sample_to_paid(
                    sample_asin,
                    headless=True,
                    browser_profile=profile,
                    worker_id=worker_id,
                    ban_coordinator=ban_coordinator,
                    request_pacer=request_pacer,
                )
                if paid_asin:
                    print(f"  {wp}[OK] 本編 ASIN 解決成功: {paid_asin}")
                    save_mapping(sample_asin, paid_asin, title)
                else:
                    print(f"  {wp}[NG] 本編 ASIN を解決できませんでした。スキップします。")
                    return RESULT_SKIP, "本編 ASIN を解決できませんでした"
            except Exception as e:
                print(f"  {wp}[Error] ASIN 解決中にエラー発生: {e}")
                return RESULT_FAILURE, f"ASIN 解決中にエラー: {e}"

        # BAN 待機チェック（resolve 後）
        if ban_coordinator:
            await ban_coordinator.wait_if_banned(worker_id=worker_id)

        # 3. 価格情報をクロール
        print(f"  {wp}[3/4] 価格情報をクロール中 (ASIN: {paid_asin})...")
        stage = "クロール中"
        try:
            price_data = await crawl_price_info(
                paid_asin,
                headless=True,
                browser_profile=profile,
                worker_id=worker_id,
                ban_coordinator=ban_coordinator,
                request_pacer=request_pacer,
            )
            outcome = classify_crawl_result(price_data)
            if outcome[0] == RESULT_SUCCESS:
                print(f"  {wp}[OK] クロール成功: {format_price_summary(price_data)}")
            else:
                print(f"  {wp}[NG] クロールは終了しましたが価格を取得できませんでした: {format_price_summary(price_data)}")

            # 4. DB に保存
            stage = "DB 保存中"
            save_price_history(price_data)
            print(f"  {wp}[4/4] データベースに保存しました。")

        except Exception as e:
            print(f"  {wp}[Error] {stage}にエラー発生: {e}")
            outcome = (RESULT_FAILURE, f"{stage}にエラー: {e}")

        # ループ再開までの小休止（Amazon向けの間隔調整は RequestPacer が担当）
        delay = random.uniform(0.5, 1.5)
        print(f"  {wp}[Sleep] {delay:.1f} 秒待機します...")
        await asyncio.sleep(delay)
        return outcome

    async def worker(worker_id: int) -> None:
        """常駐ワーカータスク"""
        # 初回の起動ズレ（同時ブラウザ起動の負荷軽減）
        if workers > 1:
            initial_delay = 2.5 * (worker_id - 1)
            print(f"  [Worker-{worker_id}] 起動遅延として {initial_delay:.1f} 秒待機します...")
            await asyncio.sleep(initial_delay)

        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            
            i, book = item
            try:
                status, detail = await process_book(i, book, worker_id)
            except Exception as e:
                print(f"  [Worker-{worker_id}][Error] 処理エラーが発生しました: {e}")
                status, detail = RESULT_FAILURE, f"処理エラー: {e}"
            finally:
                queue.task_done()
            result_counts[status] += 1
            print(format_result_line(worker_id, i, len(samples), status, detail))

    # ── 全ワーカーを並列起動 ─────────────────────────────────────────
    worker_tasks = [
        asyncio.create_task(worker(w_id))
        for w_id in range(1, workers + 1)
    ]
    await asyncio.gather(*worker_tasks)

    print("\n" + "=" * 60)
    print("  全処理が完了しました。")
    print(format_summary_line(result_counts, resumed=resumed_count))
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
