"""
server.py
---------
Kindle Pulse ローカル管理サーバー（FastAPI + SQLModel版）。
ブラウザから http://localhost:8001 でレポート閲覧 + ボタン操作が可能になる。

起動方法:
    python C:\\dev\\kindle_system\\src\\server.py
"""

import os
import sys
import time
import asyncio
import subprocess
from urllib.parse import urlparse
from fastapi import FastAPI, Response, Query, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select, text
import uvicorn

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
PORT     = int(os.environ.get("CRAWLER_PORT", "8001"))
MAIN_PY  = os.path.join(BASE_DIR, "main.py")
REPORT   = os.path.join(BASE_DIR, "kindle_sales_report.html")
# 環境変数 KINDLE_XML_PATH が設定されている場合はそれを優先する（Docker環境向け）
# 設定されていない場合は Windows のデフォルトパスを使用する
XML_PATH = os.environ.get(
    "KINDLE_XML_PATH",
    os.path.join(
        os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"),
        "Amazon", "Kindle", "Cache", "KindleSyncMetadataCache.xml"
    )
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
from src.repository import init_db
from src.bookmeter_sync import sync_bookmeter_wishlist
from report import _load_env_file

REPORT_PY = os.path.join(BASE_DIR, "report.py")

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

async def _run_proc(cmd: list, cwd: str = None):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # 認証切れの git コマンドが対話プロンプト待ちで無限にハングしないようにする
    # （stdin は継承されるがサーバーの標準入力を操作する手段が無いため）。
    env["GIT_TERMINAL_PROMPT"] = "0"

    # 非同期サブプロセス起動
    proc = await asyncio.create_subprocess_exec(
        cmd[0], *cmd[1:],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        cwd=cwd,
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

    return proc.returncode


async def do_run_only(start_val=None):
    job.start()
    try:
        job.emit("=== 今すぐ更新 ===")
        cmd = [sys.executable, "-u", "-X", "utf8", MAIN_PY, "--workers", "3"]
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


async def do_bookmeter_sync():
    job.start()
    try:
        result = await sync_bookmeter_wishlist(progress_cb=job.emit)
        if result["failed_titles"]:
            job.emit(f"[スキップ一覧] {', '.join(result['failed_titles'])}")
    except asyncio.CancelledError:
        # sync_bookmeter_wishlist は do_run_only のようなサブプロセスを持たず、
        # キャンセルは次の await 到達時まで反映されない（fetch_wish_books の
        # スレッド実行中は特に遅延しうる）。ここに到達した時点で finish() が
        # ジョブ状態を初期化する。
        pass
    except Exception as e:
        job.emit(f"[エラー] {e}")
    finally:
        job.finish()


async def do_publish():
    """
    「読みたい本」を GitHub Pages 公開用リポジトリへ公開する。

    R2対策: report.py の _require_env() は未設定時に sys.exit(1) するため、
    in-process では絶対に呼ばない。ここでは _load_env_file()（sys.exit しない）
    のみ import し、PUBLIC_SITE_DIR / PUBLIC_SITE_URL は自前で検証する。
    report.py 本体はこれまでどおりサブプロセスとして実行し、本体側の検証
    （git 作業ツリー確認等）はそのまま活かす。
    """
    job.start()
    try:
        job.emit("=== 公開 ===")
        _load_env_file(os.path.join(BASE_DIR, ".env"))

        public_site_dir = os.environ.get("PUBLIC_SITE_DIR")
        public_site_url = os.environ.get("PUBLIC_SITE_URL")
        if not public_site_dir or not public_site_url:
            job.emit(
                "[エラー] 環境変数 PUBLIC_SITE_DIR / PUBLIC_SITE_URL が設定されていません。"
                ".env.example を参考に .env に設定してください。"
            )
            return

        report_returncode = await _run_proc([sys.executable, "-u", "-X", "utf8", REPORT_PY])
        if report_returncode != 0:
            job.emit("[エラー] レポート生成に失敗しました。公開を中断しました。")
            return

        add_returncode = await _run_proc(
            ["git", "add", "index.html"], cwd=public_site_dir
        )
        if add_returncode != 0:
            job.emit("[エラー] git add に失敗しました。公開を中断しました。")
            return

        # git diff --cached --quiet の終了コードは「差分なし=0 / 差分あり=1」で、
        # 他の分岐と意味が逆になる（0 が異常ではなく「commit 不要」を意味する）。
        diff_returncode = await _run_proc(
            ["git", "diff", "--cached", "--quiet", "--", "index.html"], cwd=public_site_dir
        )
        if diff_returncode == 0:
            job.emit("差分なし（前回から内容が同じ）。")
        else:
            commit_returncode = await _run_proc(
                ["git", "commit", "-m", "chore: update wishlist", "-q", "--", "index.html"],
                cwd=public_site_dir,
            )
            if commit_returncode != 0:
                job.emit("[エラー] git commit に失敗しました。公開を中断しました。")
                return

        # 差分が無い場合も push は必ず試みる。前回の公開で push だけが失敗し
        # commit だけがローカルに残っていた場合、diff の判定だけでは検出できず
        # 「差分なし」のまま永久に push されない状態になってしまうため
        # （push 自体は送るものが無ければ no-op で成功する）。
        push_returncode = await _run_proc(["git", "push", "-q"], cwd=public_site_dir)
        if push_returncode != 0:
            job.emit(
                "[エラー] git push に失敗しました。コミットはローカルに残っています。"
                "通信状況や認証情報を確認し、もう一度「公開」を実行してください。"
            )
            return

        job.emit(f"[完了] 公開しました: {public_site_url}")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        job.emit(f"[エラー] {e}")
    finally:
        job.finish()


# ─── 購入済み / 欲しい本 DB操作 ──────────────────────────────────────────────

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


def set_wanted(paid_asin: str, status: int) -> bool:
    """欲しい本フラグをDBUPDATE。対象が見つからない場合は False。"""
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

# ─── CSRF対策 ────────────────────────────────────────────────────────────────

async def verify_same_origin(request: Request) -> None:
    """
    Origin（無ければReferer）ヘッダーのホスト・ポートがリクエスト自身のHost
    （request.url.hostname / request.url.port）と一致することを検証する。
    一致しない場合（ヘッダー欠落を含む）は403を送出する。

    外部サイトからの無認証POST（CSRF）を拒否するための最小対策。自オリジンの
    基準は固定値ではなくリクエスト自身のHostから動的に導出するため、
    localhost/127.0.0.1/LAN IP等アクセス経路の違いを問わず機能する。
    """
    header_value = request.headers.get("origin") or request.headers.get("referer")
    if not header_value:
        raise HTTPException(status_code=403, detail="Origin/Referer ヘッダーが必要です。")

    parsed = urlparse(header_value)
    if parsed.hostname != request.url.hostname or parsed.port != request.url.port:
        raise HTTPException(status_code=403, detail="許可されていないオリジンからのリクエストです。")


# ─── FastAPI アプリケーション ────────────────────────────────────────────────

app = FastAPI(title="Kindle Pulse Server")

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
        COALESCE(m.is_purchased, 0) as is_purchased,
        COALESCE(m.is_wanted,   0) as is_wanted,
        m.source,
        m.from_kindle_sample,
        m.from_bookmeter
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



@app.get("/api/books/{asin}/history")
async def get_book_history(asin: str):
    """指定 ASIN の価格履歴を全件取得して返す"""
    query = """
    SELECT sell_price, point_value, actual_price, campaign_text, timestamp, is_unlimited
    FROM price_history
    WHERE paid_asin = :asin
    ORDER BY timestamp ASC
    """
    def fetch():
        with get_session() as session:
            result = session.exec(text(query), params={"asin": asin}).mappings().all()
            return [dict(row) for row in result]

    history = await asyncio.to_thread(fetch)
    return history


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


@app.post("/api/bookmeter/sync")
async def run_bookmeter_sync():
    """読書メーター「読みたい本」の一気通貫同期処理を開始する"""
    if job.running:
        raise HTTPException(status_code=409, detail="すでに実行中です。")

    # 既存 job（JobManager）インスタンスを共有するため、通常のクロール実行
    # （/api/run）と本エンドポイントは互いに対して409で多重起動を防ぐ
    job.current_task = asyncio.create_task(do_bookmeter_sync())
    return {"ok": True}




@app.post("/api/publish")
async def publish():
    """読みたい本を GitHub Pages へ公開する"""
    if job.running:
        raise HTTPException(status_code=409, detail="すでに実行中です。")

    job.current_task = asyncio.create_task(do_publish())
    return {"ok": True}


@app.post("/api/stop")
async def stop_job():
    """実行中のジョブを停止する"""
    if job.running:
        job.stop()
        return {"ok": True}
    else:
        return {"ok": False, "message": "実行中のジョブがありません。"}


@app.post("/api/want")
async def want(asin: str = Query(...), status: int = Query(1)):
    """書籍の「欲しい」ステータスをトグルする"""
    if not asin:
        raise HTTPException(status_code=400, detail="asin パラメータが必要です。")
    ok = await asyncio.to_thread(set_wanted, asin, status)
    return {"ok": ok, "asin": asin, "is_wanted": status}


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
    
    @app.get("/favicon.png")
    async def get_favicon():
        favicon_path = os.path.join(DIST_DIR, "favicon.png")
        if os.path.exists(favicon_path):
            return FileResponse(favicon_path)
        raise HTTPException(status_code=404)
    
    @app.get("/favicon.svg")
    async def get_favicon_svg():
        favicon_path = os.path.join(DIST_DIR, "favicon.svg")
        if os.path.exists(favicon_path):
            return FileResponse(favicon_path, media_type="image/svg+xml")
        raise HTTPException(status_code=404)
    
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

def ensure_frontend_built() -> None:
    """
    dist/index.html が無い、または frontend/src 配下に dist より新しいファイルが
    あれば npm run build を実行する。python src/server.py を起動するだけで画面が
    古いビルドのままにならないようにするための対策。
    """
    frontend_dir = os.path.join(BASE_DIR, "frontend")
    src_dir = os.path.join(frontend_dir, "src")
    dist_index = os.path.join(DIST_DIR, "index.html")

    if not os.path.isdir(src_dir):
        return

    dist_mtime = os.path.getmtime(dist_index) if os.path.exists(dist_index) else 0
    needs_build = dist_mtime == 0
    if not needs_build:
        for root, _, files in os.walk(src_dir):
            for name in files:
                if os.path.getmtime(os.path.join(root, name)) > dist_mtime:
                    needs_build = True
                    break
            if needs_build:
                break

    if not needs_build:
        return

    print("  [Build] frontend が最新ソースより古いため npm run build を実行します...")
    try:
        subprocess.run(
            ["npm", "run", "build"],
            cwd=frontend_dir,
            shell=(os.name == "nt"),
            check=True,
        )
        print("  [OK] frontend のビルドが完了しました。")
    except Exception as e:
        print(f"  [警告] frontend の自動ビルドに失敗しました（既存の dist で続行します）: {e}")


# ─── エントリポイント ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print(f"  Kindle Pulse サーバー起動 (FastAPI + SQLModel)")
    print(f"  → http://localhost:{PORT}")
    print(f"  停止: Ctrl+C")
    print("=" * 50)
    try:
        ensure_frontend_built()
        # DBテーブルが未作成の場合に備え、起動時に作成しておく（main.py と同様）。
        # 既存DBに旧スキーマ（sample_asin主キー）が残っている場合は、ここで新スキーマへ
        # 自動移行される（src.repository.init_db が create_all + migration をまとめて行う）。
        init_db()
        # uvicornで起動
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
    except KeyboardInterrupt:
        print("\n停止しました。")
