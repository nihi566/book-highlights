"""
server.py
---------
Kindle Pulse ローカル管理サーバー（FastAPI + SQLModel版）。
ブラウザから http://localhost:8765 でレポート閲覧 + ボタン操作が可能になる。

起動方法:
    python C:\\dev\\kindle_system\\src\\server.py
"""

import os
import sys
import time
import asyncio
import subprocess
from fastapi import FastAPI, Response, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select, text
import uvicorn

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
PORT     = 8765
MAIN_PY  = os.path.join(BASE_DIR, "main.py")
REPORT   = os.path.join(BASE_DIR, "kindle_sales_report.html")
XML_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"),
    "Amazon", "Kindle", "Cache", "KindleSyncMetadataCache.xml"
)
KINDLE_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA",""), "Amazon","Kindle","application","Kindle.exe"),
    r"C:\Program Files\Amazon\Kindle\Kindle.exe",
    r"C:\Program Files (x86)\Amazon\Kindle\Kindle.exe",
    os.path.join(os.environ.get("LOCALAPPDATA",""), "Amazon","Kindle","Kindle.exe"),
]

# SQLModel の依存関係
from src.database import get_session
from src.models import BookMapping, PriceHistory

# ─── ジョブ管理（非同期版） ──────────────────────────────────────────────────

class JobManager:
    def __init__(self):
        self.running = False
        self.lines: list[str] = []
        self._subs: list[asyncio.Queue] = []
        self.current_proc = None   # 実行中の非同期サブプロセス
        self.current_task = None   # バックグラウンド実行中の asyncio.Task
        self.stopped = False       # 停止フラグ

    def start(self):
        self.running = True
        self.stopped = False
        self.lines = []
        self._subs = []

    def emit(self, line: str):
        self.lines.append(line)
        for q in self._subs:
            q.put_nowait(line)

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        for l in self.lines:
            q.put_nowait(l)
        if not self.running:
            q.put_nowait(None)
        else:
            self._subs.append(q)
        return q

    def stop(self):
        """実行中の処理とサブプロセスを強制終了する。"""
        self.stopped = True
        
        # サブプロセスの強制終了
        proc = self.current_proc
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        
        # バックグラウンド非同期タスクのキャンセル
        task = self.current_task
        if task and not task.done():
            task.cancel()
            
        self.emit("[停止] ユーザーによって処理が中断されました。")

    def finish(self):
        self.running = False
        self.current_proc = None
        self.current_task = None
        for q in self._subs:
            q.put_nowait(None)
        self._subs = []

job = JobManager()

# ─── ジョブ処理（非同期） ────────────────────────────────────────────────────

async def _run_proc(cmd: list):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    
    # 非同期サブプロセス起動
    proc = await asyncio.create_subprocess_exec(
        cmd[0], *cmd[1:],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    job.current_proc = proc
    
    try:
        # stdout から非同期で1行ずつ読み込む
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            if job.stopped:
                break
            job.emit(line)
    except asyncio.CancelledError:
        # タスクがキャンセルされた場合にサブプロセスを確実に kill する
        if proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        raise
    finally:
        await proc.wait()
        job.current_proc = None

    if not job.stopped:
        job.emit(f"[完了] 終了コード: {proc.returncode}")


async def do_run_only(start_val=None):
    job.start()
    try:
        job.emit("=== 今すぐ更新 ===")
        cmd = [sys.executable, "-u", "-X", "utf8", MAIN_PY, "--workers", "2"]
        if start_val:
            cmd += ["--start", str(start_val)]
        await _run_proc(cmd)
    except asyncio.CancelledError:
        # キャンセル時は finish で適切に処理される
        pass
    except Exception as e:
        job.emit(f"[エラー] {e}")
    finally:
        job.finish()




# ─── 購入済みDB操作 ────────────────────────────────────────────────

def set_purchased(paid_asin: str, status: int) -> bool:
    """購入済みDBUPDATE。対象が見つからない場合は False。"""
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

# ─── FastAPI アプリケーション ────────────────────────────────────────────────

app = FastAPI(title="Kindle Pulse Server")

# CORS 設定（既存のフロントエンドが別オリジンからアクセスする可能性を考慮）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/books")
async def get_books():
    """最新の書籍リストと価格履歴を取得して返却する"""
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
        l.paid_asin as asin, 
        l.sell_price, 
        l.point_value, 
        l.actual_price, 
        l.campaign_text, 
        l.timestamp,
        l.is_unlimited,
        COALESCE(m.is_purchased, 0) as is_purchased
    FROM book_mappings m
    JOIN latest_prices l ON m.paid_asin = l.paid_asin
    ORDER BY l.actual_price ASC
    """
    
    def fetch_data():
        with get_session() as session:
            result = session.exec(text(query)).mappings().all()
            return [dict(row) for row in result]
            
    books = await asyncio.to_thread(fetch_data)
    return books


@app.get("/api/status")
async def get_status():
    """現在のジョブ状態を取得する"""
    return {"running": job.running, "count": len(job.lines)}


@app.get("/api/events")
async def get_events():
    """SSE (Server-Sent Events) で進捗ログをストリーミング配信する"""
    q = job.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    # 25秒タイムアウトで待機し、タイムアウト時は ping を送信
                    line = await asyncio.wait_for(q.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue

                if line is None:
                    # 完了通知
                    yield "event: done\ndata: done\n\n"
                    break

                escaped = line.replace("\n", "\\n").replace("\r", "")
                yield f"data: {escaped}\n\n"
        except asyncio.CancelledError:
            # クライアント切断時
            pass
        finally:
            # 切断または終了時に購読を解除
            if q in job._subs:
                job._subs.remove(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/api/run")
async def run_job(start: str = Query(None)):
    """クロール処理のみを開始する"""
    if job.running:
        raise HTTPException(status_code=409, detail="すでに実行中です。")
    
    # asyncio.create_task を使用してバックグラウンドタスクとして非同期に回す
    job.current_task = asyncio.create_task(do_run_only(start))
    return {"ok": True}




@app.post("/api/stop")
async def stop_job():
    """実行中のジョブを停止する"""
    if job.running:
        job.stop()
        return {"ok": True}
    else:
        return {"ok": False, "message": "実行中のジョブがありません。"}


@app.post("/api/purchase")
async def purchase(asin: str = Query(...), status: int = Query(1)):
    """書籍の購入ステータスをトグルする"""
    if not asin:
        raise HTTPException(status_code=400, detail="asin パラメータが必要です。")
    
    # スレッドプール上で同期DB操作を実行
    ok = await asyncio.to_thread(set_purchased, asin, status)
    return {"ok": ok, "asin": asin, "is_purchased": status}


# ─── React フロントエンドのマウントと配信 ─────────────────────────────────────

DIST_DIR = os.path.join(BASE_DIR, "frontend", "dist")

if os.path.exists(DIST_DIR):
    # React ビルド成果物のマウント
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST_DIR, "assets")), name="assets")
    
    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    async def get_index():
        with open(os.path.join(DIST_DIR, "index.html"), "r", encoding="utf-8") as f:
            body = f.read()
        return HTMLResponse(content=body)
else:
    # React がまだビルドされていない場合は、従来の kindle_sales_report.html を表示する
    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    async def get_index():
        if os.path.exists(REPORT):
            with open(REPORT, "r", encoding="utf-8") as f:
                body = f.read()
            return HTMLResponse(content=body)
        else:
            return HTMLResponse(content="<p>React のビルドまたは reporter.py の実行を行ってください。</p>", status_code=200)

# ─── エントリポイント ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print(f"  Kindle Pulse サーバー起動 (FastAPI + SQLModel)")
    print(f"  → http://localhost:{PORT}")
    print(f"  停止: Ctrl+C")
    print("=" * 50)
    try:
        # uvicornで起動
        uvicorn.run(app, host="localhost", port=PORT, log_level="info")
    except KeyboardInterrupt:
        print("\n停止しました。")
