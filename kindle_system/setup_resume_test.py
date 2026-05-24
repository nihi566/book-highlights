"""
setup_resume_test.py  - テスト用セットアップスクリプト（使い捨て）
2冊目・3冊目の最新DBレコードを削除し、session_start.txt を
「1冊目の直前」に設定することで「1冊目だけが処理済み」の状態を作る。
"""
import sqlite3
import os

DB_PATH      = r'c:\dev\kindle_system\data\kindle_monitor.db'
SESSION_FILE = r'c:\dev\kindle_system\data\session_start.txt'

ASIN_1 = 'B0GGY819NL'  # 1冊目: 処理済みに見せる
ASIN_2 = 'B07J9XG8DL'  # 2冊目: 未処理（DBから最新レコードを削除）
ASIN_3 = 'B00I0JV58Y'  # 3冊目: 未処理（DBから最新レコードを削除）

with sqlite3.connect(DB_PATH) as conn:
    cur = conn.cursor()

    # ── 2・3冊目の最新レコードを1件ずつ削除 ──
    for asin in [ASIN_2, ASIN_3]:
        cur.execute(
            "SELECT id, timestamp FROM price_history WHERE paid_asin=? ORDER BY timestamp DESC LIMIT 1",
            (asin,)
        )
        row = cur.fetchone()
        if row:
            rec_id, ts = row
            cur.execute("DELETE FROM price_history WHERE id=?", (rec_id,))
            print(f"  削除: {asin} id={rec_id} ts={ts}")
        else:
            print(f"  スキップ: {asin} (レコードなし)")

    conn.commit()

    # ── 1冊目の最新タイムスタンプを確認 ──
    cur.execute(
        "SELECT timestamp FROM price_history WHERE paid_asin=? ORDER BY timestamp DESC LIMIT 1",
        (ASIN_1,)
    )
    row = cur.fetchone()
    ts_book1 = row[0] if row else None
    print(f"\n1冊目の最新timestamp: {ts_book1}")

# ── session_start を「1冊目の 1秒前」に設定 ──
from datetime import datetime, timedelta
if ts_book1:
    session_start = (datetime.fromisoformat(ts_book1) - timedelta(seconds=1)).isoformat()
else:
    session_start = datetime.now().isoformat()

with open(SESSION_FILE, 'w', encoding='utf-8') as f:
    f.write(session_start)

print(f"session_start.txt = {session_start}")
print()
print("期待動作:")
print(f"  {ASIN_1} ({ts_book1}) >= session_start → [Resume-Skip]")
print(f"  {ASIN_2} (レコード削除済み)              → Playwright クロール実行")
print(f"  {ASIN_3} (レコード削除済み)              → Playwright クロール実行")
