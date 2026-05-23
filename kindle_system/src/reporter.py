"""
reporter.py
-----------
Kindle監視システムのSQLiteデータベースから最新価格を取得し、
買い時の本が一目でわかるリッチな静的HTMLレポートを自動生成する。
"""

import sqlite3
import os
from datetime import datetime

# HTML出力パスとDBパス
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "kindle_monitor.db")
REPORT_PATH = os.path.join(BASE_DIR, "kindle_sales_report.html")

def generate_report():
    if not os.path.exists(DB_PATH):
        print("  [Report] データベースが存在しないためレポート生成をスキップします。")
        return

    # データの集計
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. 各ASINの最新価格を取得するクエリ
        query = """
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
            l.paid_asin, 
            l.sell_price, 
            l.point_value, 
            l.actual_price, 
            l.campaign_text, 
            l.timestamp,
            l.is_unlimited
        FROM book_mappings m
        JOIN latest_prices l ON m.paid_asin = l.paid_asin
        ORDER BY l.actual_price ASC
        """
        
        cursor.execute(query)
        rows = cursor.fetchall()

    total_books = len(rows)
    campaign_count = 0
    
    table_rows_html = ""
    
    for row in rows:
        title = row["title"] or "タイトル不明"
        asin = row["paid_asin"]
        sell_price = row["sell_price"]
        point_value = row["point_value"] or 0
        actual_price = row["actual_price"]
        
        # 不要な文言を除外
        campaign = row["campaign_text"] or ""
        campaign = campaign.replace("ご購入時にプロモーションが適用されます", "")
        campaign = " | ".join([c.strip() for c in campaign.split("|") if c.strip()])
        
        if sell_price is None:
            continue
            
        # 実質割引率（定価がないので販売価格ベース: ポイント還元率と同義）
        discount_rate = 0
        if sell_price > 0:
            discount_rate = (point_value / sell_price) * 100
            
        is_campaign = bool(campaign)
        if is_campaign:
            campaign_count += 1
            
        timestamp_str = row["timestamp"]
        try:
            ts_obj = datetime.fromisoformat(timestamp_str)
            updated_at = ts_obj.strftime("%m/%d %H:%M")
        except:
            updated_at = timestamp_str[:16].replace("T", " ") if timestamp_str else ""

        # CSSクラスやバッジの設定
        row_class = "campaign-target" if is_campaign else ""
        badge_html = '<span class="badge campaign">🎁 キャンペーン対象</span>' if is_campaign else ''
        discount_badge = f'<span class="badge discount">🔥 {int(discount_rate)}% OFF</span>' if discount_rate >= 20.0 else ''
        
        is_unlimited = row["is_unlimited"] or 0
        unlimited_badge = '<span class="badge unlimited">📖 Unlimited対象</span>' if is_unlimited == 1 else ''
        
        amazon_url = f"https://www.amazon.co.jp/dp/{asin}"
        ts_sort = timestamp_str if timestamp_str else "1970-01-01T00:00:00"
        is_campaign_val = 1 if is_campaign else 0
        
        table_rows_html += f'''
        <tr class="{row_class}" data-discount="{discount_rate}" data-updated="{ts_sort}" data-price="{actual_price}" data-unlimited="{is_unlimited}" data-campaign="{is_campaign_val}">
            <td class="col-title">
                <a href="{amazon_url}" target="_blank">{title}</a>
                {unlimited_badge} {badge_html} {discount_badge}
                <div class="campaign-text">
                    <span style="display:inline-block; margin-right:8px; color:var(--text-muted); opacity:0.8;">🕒 更新: {updated_at}</span>
                    {campaign}
                </div>
            </td>
            <td class="col-price">¥{sell_price:,}</td>
            <td class="col-point">{point_value:,} pt ({int(discount_rate)}%)</td>
            <td class="col-actual">¥{actual_price:,}</td>
        </tr>
        '''

    # HTMLテンプレートの組み立て (Glassmorphism & Dark Mode Aesthetic)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    html_content = f'''<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kindle Sales Report</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0f172a;
            --surface-color: rgba(30, 41, 59, 0.7);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --danger: #ef4444;
            --success: #10b981;
            --highlight: rgba(239, 68, 68, 0.15);
        }}
        
        body {{
            margin: 0;
            padding: 2rem;
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg-color);
            background-image: 
                radial-gradient(at 0% 0%, rgba(56, 189, 248, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(139, 92, 246, 0.15) 0px, transparent 50%);
            background-attachment: fixed;
            color: var(--text-main);
            min-height: 100vh;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
        }}

        h1 {{
            margin: 0;
            font-size: 2.5rem;
            font-weight: 800;
            background: linear-gradient(to right, #38bdf8, #818cf8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}

        .timestamp {{
            color: var(--text-muted);
            font-size: 0.9rem;
        }}

        /* Summary Cards */
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1.5rem;
            margin-bottom: 3rem;
        }}

        .card {{
            background: var(--surface-color);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 1rem;
            padding: 1.5rem;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            transition: transform 0.2s ease;
        }}

        .card:hover {{
            transform: translateY(-5px);
        }}

        .card-title {{
            color: var(--text-muted);
            font-size: 0.9rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.5rem;
        }}

        .card-value {{
            font-size: 2.5rem;
            font-weight: 800;
            color: var(--text-main);
        }}
        
        .card-value.highlight-red {{ color: var(--danger); }}
        .card-value.highlight-green {{ color: var(--success); }}

        /* Data Table */
        .table-container {{
            background: var(--surface-color);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 1rem;
            overflow: hidden;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}

        th, td {{
            padding: 1.25rem 1.5rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }}

        th {{
            background: rgba(0, 0, 0, 0.2);
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.85rem;
            letter-spacing: 0.05em;
        }}

        tr:last-child td {{
            border-bottom: none;
        }}

        tr:hover {{
            background: rgba(255, 255, 255, 0.03);
        }}

        tr.campaign-target td {{
            background: rgba(239, 68, 68, 0.05) !important;
        }}

        a {{
            color: var(--text-main);
            text-decoration: none;
            font-weight: 600;
            transition: color 0.2s;
        }}

        a:hover {{
            color: var(--accent);
        }}

        .col-price, .col-actual {{
            font-variant-numeric: tabular-nums;
            font-weight: 600;
        }}

        .col-actual {{
            color: var(--accent);
            font-size: 1.1rem;
        }}

        .campaign-text {{
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 0.5rem;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}

        .badge {{
            display: inline-block;
            padding: 0.25rem 0.5rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 800;
            margin-left: 0.5rem;
            vertical-align: middle;
        }}

        .badge.campaign {{
            background: rgba(239, 68, 68, 0.2);
            color: #fca5a5;
            border: 1px solid rgba(239, 68, 68, 0.3);
        }}
        
        .badge.unlimited {{
            background: rgba(16, 185, 129, 0.15);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }}

        .badge.discount {{
            background: rgba(239, 68, 68, 0.2);
            color: #fca5a5;
            border: 1px solid rgba(239, 68, 68, 0.3);
        }}
        
        /* Empty State */
        .empty-state {{
            padding: 4rem 2rem;
            text-align: center;
            color: var(--text-muted);
        }}

        /* Control Panel */
        .control-panel {{
            display: flex;
            align-items: flex-start;
            gap: 1rem;
            margin-bottom: 2rem;
            flex-wrap: wrap;
        }}
        .btn {{
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.75rem 1.5rem;
            border: none;
            border-radius: 0.75rem;
            font-family: inherit;
            font-size: 0.95rem;
            font-weight: 700;
            cursor: pointer;
            transition: opacity 0.2s, transform 0.15s;
        }}
        .btn:active {{ transform: scale(0.97); }}
        .btn:disabled {{ opacity: 0.45; cursor: not-allowed; }}
        .btn-sync {{ background: linear-gradient(135deg,#6366f1,#8b5cf6); color:#fff; }}
        .btn-run  {{ background: linear-gradient(135deg,#0ea5e9,#38bdf8); color:#0f172a; }}
        .server-hint {{
            font-size: 0.8rem;
            color: var(--text-muted);
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 0.5rem;
            padding: 0.6rem 1rem;
            display: none;
        }}
        .server-hint code {{
            background: rgba(255,255,255,0.1);
            border-radius: 0.25rem;
            padding: 0.1rem 0.4rem;
            font-size: 0.85em;
        }}
        /* Terminal Output */
        .terminal-wrap {{
            position: relative;
            display: none;
            margin-bottom: 2rem;
        }}
        .terminal-wrap.active {{ display: block; }}
        .terminal {{
            background: #020617;
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 0.75rem;
            padding: 1.2rem 1.5rem;
            font-family: 'Consolas','Courier New',monospace;
            font-size: 0.82rem;
            color: #94a3b8;
            height: 60vh;
            overflow-y: auto;
            scroll-behavior: smooth;
        }}
        /* カウンターバッジ */
        .term-counter {{
            position: absolute;
            top: 0.75rem;
            right: 1rem;
            background: rgba(56,189,248,0.15);
            border: 1px solid rgba(56,189,248,0.3);
            color: #38bdf8;
            font-family: 'Consolas','Courier New',monospace;
            font-size: 0.78rem;
            font-weight: 700;
            padding: 0.2rem 0.6rem;
            border-radius: 9999px;
            display: none;
        }}
        .term-counter.visible {{ display: block; }}
        /* ログ行の色分け */
        .terminal p {{ margin: 0; line-height: 1.65; white-space: pre-wrap; word-break: break-all; }}
        .log-book   {{ color: #e2e8f0; font-weight: 700; margin-top: 0.8rem; }}
        .log-ok     {{ color: #4ade80; }}
        .log-error  {{ color: #f87171; }}
        .log-url    {{ color: #475569; }}
        .log-sleep  {{ color: #6366f1; }}
        .log-step   {{ color: #7dd3fc; }}
        .log-done   {{ color: #4ade80; font-weight: 700; margin-top: 0.5rem; }}
        /* スピナー */
        .spinner {{
            display: inline-block;
            width: 14px; height: 14px;
            border: 2px solid rgba(255,255,255,0.3);
            border-top-color: #fff;
            border-radius: 50%;
            animation: spin 0.7s linear infinite;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        /* 停止ボタン */
        .btn-stop {{
            background: rgba(239,68,68,0.15);
            color: #fca5a5;
            border: 1px solid rgba(239,68,68,0.4);
            display: none;
        }}
        .btn-stop.visible {{ display: inline-flex; }}

        /* ソート・フィルタUI */
        .controls-wrapper {{
            display: flex;
            flex-wrap: wrap;
            gap: 1.5rem;
            margin-bottom: 1rem;
            align-items: center;
        }}
        .sort-panel, .filter-panel {{
            display: flex;
            gap: 0.8rem;
            align-items: center;
        }}
        .sort-label {{
            color: var(--text-muted);
            font-size: 0.9rem;
            font-weight: 600;
        }}
        .btn-sort {{
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            color: var(--text-main);
            padding: 0.4rem 0.8rem;
            border-radius: 0.5rem;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        .btn-sort:hover {{ background: rgba(255,255,255,0.1); }}
        .btn-sort.active {{
            background: rgba(56,189,248,0.2);
            border-color: #38bdf8;
            color: #38bdf8;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Kindle Pulse</h1>
            <div class="timestamp">最終更新: {now_str}</div>
        </header>

        <!-- コントロールパネル -->
        <div class="control-panel" id="controlPanel">
            <button class="btn btn-run" id="btnRun" onclick="apiAction('run')">
                ▶ 今すぐ更新（実行のみ）
            </button>
            <button class="btn btn-stop" id="btnStop" onclick="stopJob()">
                ⏹️ 停止
            </button>
            <div class="server-hint" id="serverHint">
                サーバーが起動していません。ターミナルで以下を実行してください:<br>
                <code>python C:\dev\kindle_system\src\server.py</code>
            </div>
        </div>

        <!-- ターミナル出力 -->
        <div class="terminal-wrap" id="terminalWrap">
            <div class="terminal" id="terminal"></div>
            <span class="term-counter" id="termCounter"></span>
        </div>

        <div class="summary-grid" style="grid-template-columns: repeat(2, 1fr);">
            <div class="card">
                <div class="card-title">総監視数</div>
                <div class="card-value">{total_books}</div>
            </div>
            <div class="card">
                <div class="card-title">キャンペーン対象</div>
                <div class="card-value highlight-red">{campaign_count}</div>
            </div>
        </div>

        <div class="controls-wrapper">
            <div class="sort-panel">
                <span class="sort-label">並び替え:</span>
                <button class="btn-sort" id="sortDiscount" onclick="sortTable('discount')">🔥 割引率順</button>
                <button class="btn-sort" id="sortUpdated" onclick="sortTable('updated')">🕒 更新順</button>
                <button class="btn-sort" id="sortUnlimited" onclick="sortTable('unlimited')">📖 Unlimited順</button>
                <button class="btn-sort active" id="sortPrice" onclick="sortTable('price')">💰 価格順</button>
            </div>
            <div class="filter-panel">
                <span class="sort-label">絞り込み:</span>
                <button class="btn-sort active" id="filterAll" onclick="filterTable('all')">すべて</button>
                <button class="btn-sort" id="filterCampaign" onclick="filterTable('campaign')">🎁 キャンペーン対象</button>
                <button class="btn-sort" id="filterUnlimited" onclick="filterTable('unlimited')">📖 Unlimited対象</button>
            </div>
        </div>

        <div class="table-container">
            <table id="booksTable">
                <thead>
                    <tr>
                        <th>書籍名 / キャンペーン</th>
                        <th>販売価格</th>
                        <th>還元ポイント</th>
                        <th>実質価格</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows_html if total_books > 0 else '<tr><td colspan="4"><div class="empty-state">データがありません。監視システムを実行してください。</div></td></tr>'}
                </tbody>
            </table>
        </div>
    </div>

<script>
const SERVER    = 'http://localhost:8765';
const termWrap  = document.getElementById('terminalWrap');
const term      = document.getElementById('terminal');
const btnRun    = document.getElementById('btnRun');
const btnStop   = document.getElementById('btnStop');
const hint      = document.getElementById('serverHint');
const counter   = document.getElementById('termCounter');
let sse = null;

// ログ行の内容からCSSクラスを判定
function classifyLine(text) {{
    if (/^\[\d+\/\d+\]/.test(text))     return 'log-book';   // 本タイトル
    if (text.includes('[✓]') || text.includes('[Report]')) return 'log-ok';
    if (text.includes('[Error]') || text.includes('[タイムアウト]') || text.includes('[停止]')) return 'log-error';
    if (text.startsWith('アクセス中:'))  return 'log-url';
    if (text.includes('[Sleep]'))        return 'log-sleep';
    if (/^  \[\d\/\d\]/.test(text) || text.startsWith('  ->')) return 'log-step';
    return '';
}}

// カウンター更新 [X/Y]
function updateCounter(text) {{
    const m = text.match(/^\[(\d+)\/(\d+)\]/);
    if (m) {{
        counter.textContent = `${{m[1]}} / ${{m[2]}}`;
        counter.classList.add('visible');
    }}
}}

function appendLine(text, extraCls='') {{
    const p = document.createElement('p');
    const cls = extraCls || classifyLine(text);
    if (cls) p.className = cls;
    p.textContent = text;
    term.appendChild(p);
    term.scrollTop = term.scrollHeight;
    updateCounter(text);
}}

function setRunning(yes) {{
    btnRun.disabled = yes;
    btnStop.classList.toggle('visible', yes);
    termWrap.classList.toggle('active', yes);
    if (yes) {{
        term.innerHTML = '';
        counter.classList.remove('visible');
        btnRun.innerHTML  = '<span class="spinner"></span> 実行中...';
    }} else {{
        btnRun.innerHTML  = '▶ 今すぐ更新（実行のみ）';
    }}
}}

function startSSE() {{
    if (sse) sse.close();
    sse = new EventSource(SERVER + '/api/events');
    sse.onmessage = e => appendLine(e.data);
    sse.addEventListener('done', () => {{
        appendLine('✓ 完了しました。ページをリロードすると最新データが表示されます。', 'log-done');
        setRunning(false);
        sse.close(); sse = null;
    }});
    sse.onerror = () => {{ setRunning(false); if(sse){{ sse.close(); sse=null; }} }};
}}

async function apiAction(endpoint) {{
    try {{
        const res  = await fetch(SERVER + '/api/' + endpoint, {{method:'POST'}});
        const data = await res.json();
        if (data.ok) {{ setRunning(true); startSSE(); }}
        else alert(data.message);
    }} catch(e) {{
        hint.style.display = 'block';
    }}
}}

async function stopJob() {{
    try {{
        const res  = await fetch(SERVER + '/api/stop', {{method:'POST'}});
        const data = await res.json();
        if (!data.ok) alert(data.message);
    }} catch(e) {{
        alert('サーバーに接続できません。');
    }}
}}

// ─── テーブルの動的ソート・フィルタ機能 ───
function sortTable(mode) {{
    const tbody = document.querySelector('#booksTable tbody');
    const rows = Array.from(tbody.querySelectorAll('tr[data-price]'));
    if (rows.length === 0) return;

    // ソートボタンのアクティブ状態を更新
    document.querySelectorAll('.sort-panel .btn-sort').forEach(b => b.classList.remove('active'));
    
    if (mode === 'discount') {{
        document.getElementById('sortDiscount').classList.add('active');
        rows.sort((a, b) => parseFloat(b.dataset.discount) - parseFloat(a.dataset.discount));
    }} else if (mode === 'updated') {{
        document.getElementById('sortUpdated').classList.add('active');
        rows.sort((a, b) => b.dataset.updated.localeCompare(a.dataset.updated));
    }} else if (mode === 'unlimited') {{
        document.getElementById('sortUnlimited').classList.add('active');
        rows.sort((a, b) => parseInt(b.dataset.unlimited) - parseInt(a.dataset.unlimited));
    }} else if (mode === 'price') {{
        document.getElementById('sortPrice').classList.add('active');
        rows.sort((a, b) => parseFloat(a.dataset.price) - parseFloat(b.dataset.price));
    }}

    // 行を再配置
    tbody.innerHTML = '';
    rows.forEach(row => tbody.appendChild(row));
}}

function filterTable(mode) {{
    const tbody = document.querySelector('#booksTable tbody');
    const rows = Array.from(tbody.querySelectorAll('tr[data-price]'));
    if (rows.length === 0) return;

    // フィルタボタンのアクティブ状態を更新
    document.querySelectorAll('.filter-panel .btn-sort').forEach(b => b.classList.remove('active'));
    
    if (mode === 'all') {{
        document.getElementById('filterAll').classList.add('active');
        rows.forEach(row => row.style.display = '');
    }} else if (mode === 'campaign') {{
        document.getElementById('filterCampaign').classList.add('active');
        rows.forEach(row => {{
            row.style.display = (row.dataset.campaign === '1') ? '' : 'none';
        }});
    }} else if (mode === 'unlimited') {{
        document.getElementById('filterUnlimited').classList.add('active');
        rows.forEach(row => {{
            row.style.display = (row.dataset.unlimited === '1') ? '' : 'none';
        }});
    }}
}}

// 起動時: サーバー接続チェック
fetch(SERVER + '/api/status')
    .then(r => r.json())
    .then(d => {{
        if (d.running) {{ setRunning(true); startSSE(); }}
    }})
    .catch(() => {{
        hint.style.display = 'block';
    }});
</script>
</body>
</html>'''

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print(f"  [Report] レポートを生成しました: {REPORT_PATH}")

if __name__ == "__main__":
    generate_report()
