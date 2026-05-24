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
import sqlite3
import asyncio
import random
import argparse
from datetime import datetime
import io

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

# データベースのパス設定
DB_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DB_DIR, "kindle_monitor.db")
SESSION_FILE = os.path.join(DB_DIR, "session_start.txt")  # レジューム用セッションファイル


# ─── データベース処理 ────────────────────────────────────────────────────────

def init_db() -> None:
    """データベースと必要なテーブルを初期化する。"""
    os.makedirs(DB_DIR, exist_ok=True)
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # book_mappings テーブル (ASIN マッピング用)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS book_mappings (
                sample_asin TEXT PRIMARY KEY,
                paid_asin TEXT,
                title TEXT,
                created_at TEXT
            )
        """)
        
        # price_history テーブル (価格履歴用)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paid_asin TEXT,
                sell_price INTEGER,
                point_value INTEGER,
                actual_price INTEGER,
                campaign_text TEXT,
                timestamp TEXT,
                FOREIGN KEY (paid_asin) REFERENCES book_mappings(paid_asin)
            )
        """)
        
        # 既存DBへのカラム追加（エラーなら無視）
        try:
            cursor.execute("ALTER TABLE price_history ADD COLUMN is_unlimited INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
            
        conn.commit()


def get_paid_asin(sample_asin: str) -> str:
    """DB に保存済みの本編 ASIN を取得する。なければ None。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT paid_asin FROM book_mappings WHERE sample_asin = ?", (sample_asin,))
        row = cursor.fetchone()
        return row[0] if row and row[0] else None


def save_mapping(sample_asin: str, paid_asin: str, title: str) -> None:
    """サンプル ASIN と 本編 ASIN の対応を DB に保存する。"""
    now = datetime.now().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO book_mappings (sample_asin, paid_asin, title, created_at)
            VALUES (?, ?, ?, ?)
        """, (sample_asin, paid_asin, title, now))
        conn.commit()


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
        
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO price_history (paid_asin, sell_price, point_value, actual_price, campaign_text, timestamp, is_unlimited)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (data["asin"], sell_price, point_value, actual_price, campaign_text, now, is_unlimited))
        conn.commit()


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
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT paid_asin
            FROM price_history
            WHERE timestamp >= ?
        """, (session_start,))
        return {row[0] for row in cursor.fetchall()}


# ─── メインロジック ──────────────────────────────────────────────────────────

async def run_integration(xml_path: str = None, limit: int = None, is_test: bool = False) -> None:
    """統合フローの実行"""
    print("=" * 60)
    print("  Kindle システム統合処理開始")
    print("=" * 60)

    init_db()
    print("  [✓] データベース初期化完了")

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

    print(f"  [✓] サンプル本を {len(samples)} 件取得しました。")

    if limit and limit > 0:
        samples = samples[:limit]
        print(f"  [!] 処理件数を {limit} 件に制限して実行します。")

    print("-" * 60)
    
    # 各サンプルについて処理
    for i, book in enumerate(samples, 1):
        sample_asin = book["asin"]
        title = book["title"]
        
        print(f"\n[{i}/{len(samples)}] {title}")
        print(f"  Sample ASIN : {sample_asin}")

        # ── レジューム判定: 今回のセッションで既に処理済みか確認 ──
        paid_asin = get_paid_asin(sample_asin)
        if paid_asin and paid_asin in session_processed:
            print(f"  [Resume-Skip] 既に処理済みのためスキップします（ASIN: {paid_asin}）")
            continue

        # 2. DB から本編 ASIN を確認（キャッシュとして利用するが、スクレイピングは毎回実行）
        if paid_asin:
            print(f"  [✓] DB から本編 ASIN を取得しました: {paid_asin}")
        else:
            print("  [2/4] 本編 ASIN 解決中...")
            try:
                # resolver.py 呼び出し
                paid_asin = await resolve_sample_to_paid(sample_asin, headless=True)
                if paid_asin:
                    print(f"  [✓] 本編 ASIN 解決成功: {paid_asin}")
                    save_mapping(sample_asin, paid_asin, title)
                else:
                    print("  [✗] 本編 ASIN を解決できませんでした。スキップします。")
                    continue
            except Exception as e:
                print(f"  [Error] ASIN 解決中にエラー発生: {e}")
                continue
                
        # 3. 価格情報をクロール
        print(f"  [3/4] 価格情報をクロール中 (ASIN: {paid_asin})...")
        try:
            # crawler.py 呼び出し
            price_data = await crawl_price_info(paid_asin, headless=True)
            print(f"  [✓] クロール成功: 価格=¥{price_data.get('sell_price')}, ポイント={price_data.get('point_value')}pt")
            
            # 4. DB に保存
            save_price_history(price_data)
            print("  [4/4] データベースに保存しました。")
            
            # 5. レポートを即座に更新 (途中で強制停止されても最新状態を残すため)
            from src.reporter import generate_report
            generate_report()
            
        except Exception as e:
            print(f"  [Error] クロール中にエラー発生: {e}")
            
        # ディレイ (最後の要素以外)
        if i < len(samples):
            delay = random.uniform(2.0, 5.0)
            print(f"  [Sleep] Amazon アクセス回避のため {delay:.1f} 秒待機します...")
            await asyncio.sleep(delay)
    
    print("\n" + "=" * 60)
    print("  全処理が完了しました。")
    print("=" * 60)

    # 全処理完了: セッションファイルを削除し、次回は新規セッションとして実行されるようにする
    clear_session()
    print("  [✓] セッションをクリアしました。次回実行時は全件処理されます。")


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
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM book_mappings")
            mappings_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM price_history")
            history_count = cursor.fetchone()[0]
            
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
    parser.add_argument("--test", action="store_true", help="内蔵テストを実行する")
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
            
        asyncio.run(run_integration(xml_path=xml_path, limit=args.limit))
