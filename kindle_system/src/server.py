"""
server.py
---------
Kindle Pulse ローカル管理サーバー。
ブラウザから http://localhost:8765 でレポート閲覧 + ボタン操作が可能になる。

起動方法:
    python C:\\dev\\kindle_system\\src\\server.py
"""

import os, sys, json, time, threading, subprocess, queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

# ─── ジョブ管理 ──────────────────────────────────────────────────────────────

class JobManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.running   = False
        self.lines: list[str] = []
        self._subs: list[queue.Queue] = []
        self.current_proc = None   # 実行中のサブプロセス
        self.stopped = False       # 停止フラグ

    def start(self):
        with self._lock:
            self.running = True
            self.stopped = False
            self.lines   = []
            self._subs   = []

    def emit(self, line: str):
        with self._lock:
            self.lines.append(line)
            for q in self._subs:
                q.put(line)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            for l in self.lines:
                q.put(l)
            if not self.running:
                q.put(None)
            else:
                self._subs.append(q)
        return q

    def stop(self):
        """実行中のサブプロセスを強制終了する。"""
        with self._lock:
            self.stopped = True
            proc = self.current_proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        self.emit("[停止] ユーザーによって処理が中断されました。")

    def finish(self):
        with self._lock:
            self.running = False
            self.current_proc = None
            for q in self._subs:
                q.put(None)
            self._subs = []

job = JobManager()

# ─── ジョブ処理 ──────────────────────────────────────────────────────────────

def _run_proc(cmd: list):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [cmd[0], "-u"] + cmd[1:],   # -u = 標準出力バッファなし
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        env=env,
    )
    with job._lock:
        job.current_proc = proc
    for line in proc.stdout:
        if job.stopped:
            break
        job.emit(line.rstrip())
    proc.wait()
    with job._lock:
        job.current_proc = None
    if not job.stopped:
        job.emit(f"[完了] 終了コード: {proc.returncode}")

def _regen_report():
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "reporter", os.path.join(BASE_DIR, "src", "reporter.py"))
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.generate_report()
        job.emit("[Report] レポートを更新しました。ページをリロードしてください。")
    except Exception as e:
        job.emit(f"[Report] エラー: {e}")

def do_run_only(start_val=None):
    job.start()
    try:
        job.emit("=== 今すぐ更新 ===")
        cmd = [sys.executable, "-X", "utf8", MAIN_PY]
        if start_val:
            cmd += ["--start", str(start_val)]
        _run_proc(cmd)
        _regen_report()
    finally:
        job.finish()

def do_sync_and_run(start_val=None):
    job.start()
    try:
        kindle = next((p for p in KINDLE_CANDIDATES if os.path.exists(p)), None)
        if not kindle:
            job.emit("[エラー] Kindle for PC が見つかりません。")
            job.emit(f"  検索パス: {KINDLE_CANDIDATES}")
            job.emit("  手動で同期後、「今すぐ更新」を使ってください。")
            return

        initial_mtime = os.path.getmtime(XML_PATH) if os.path.exists(XML_PATH) else 0
        job.emit("=== Kindle同期モード ===")
        job.emit(f"[✓] Kindleを起動します: {kindle}")

        # Kindle をバックグラウンドで起動（すでに起動中でも問題ない）
        subprocess.Popen([kindle], creationflags=subprocess.DETACHED_PROCESS)

        job.emit("XMLファイルの更新を待機中... (最大5分)")
        job.emit("  Kindleが起動したら、ライブラリの同期が完了するまでお待ちください。")

        start   = time.time()
        detected = False
        last_log = 0

        while time.time() - start < 300:
            if job.stopped:
                break
            time.sleep(2)
            mtime = os.path.getmtime(XML_PATH) if os.path.exists(XML_PATH) else 0
            if mtime > initial_mtime:
                job.emit("[✓] XMLファイルの更新を検知しました！同期完了。")
                detected = True
                break
            elapsed = int(time.time() - start)
            if elapsed - last_log >= 15:
                last_log = elapsed
                job.emit(f"  待機中... ({elapsed}秒経過)")

        if job.stopped:
            return

        if not detected:
            job.emit("[タイムアウト] 5分以内にXMLの更新が検知されませんでした。")
            return

        job.emit("")
        job.emit("=== main.py を実行します ===")
        cmd = [sys.executable, "-X", "utf8", MAIN_PY]
        if start_val:
            cmd += ["--start", str(start_val)]
        _run_proc(cmd)
        _regen_report()
    finally:
        job.finish()

# ─── HTTPハンドラー ───────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass  # アクセスログ無効

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            if os.path.exists(REPORT):
                with open(REPORT, "r", encoding="utf-8") as f:
                    body = f.read().encode("utf-8")
            else:
                body = "<p>reporter.py を実行してレポートを生成してください。</p>".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/status":
            self.send_json({"running": job.running, "count": len(job.lines)})

        elif path == "/api/events":
            q = job.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control",  "no-cache")
            self.send_header("Connection",      "keep-alive")
            self._cors()
            self.end_headers()
            try:
                while True:
                    try:
                        line = q.get(timeout=25)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    if line is None:
                        self.wfile.write(b"event: done\ndata: done\n\n")
                        self.wfile.flush()
                        break
                    escaped = line.replace("\n", "\\n").replace("\r", "")
                    self.wfile.write(f"data: {escaped}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        
        # クエリパラメータから start を取得
        from urllib.parse import parse_qs
        query_params = parse_qs(parsed_url.query)
        start_val = query_params.get("start", [None])[0]

        # 停止は実行中でなくてもOK（エラーにしない）
        if path == "/api/stop":
            if job.running:
                job.stop()
                self.send_json({"ok": True})
            else:
                self.send_json({"ok": False, "message": "実行中のジョブがありません。"})
            return

        if job.running:
            self.send_json({"ok": False, "message": "すでに実行中です。"}, 409)
            return
        if path == "/api/run":
            threading.Thread(target=do_run_only, args=(start_val,), daemon=True).start()
            self.send_json({"ok": True})
        elif path == "/api/sync-run":
            threading.Thread(target=do_sync_and_run, args=(start_val,), daemon=True).start()
            self.send_json({"ok": True})
        else:
            self.send_response(404); self.end_headers()

# ─── エントリポイント ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    from http.server import ThreadingHTTPServer
    print("=" * 50)
    print(f"  Kindle Pulse サーバー起動")
    print(f"  → http://localhost:{PORT}")
    print(f"  停止: Ctrl+C")
    print("=" * 50)
    try:
        ThreadingHTTPServer(("localhost", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")
