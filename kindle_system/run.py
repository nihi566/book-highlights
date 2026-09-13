"""
run.py
------
ローカルサーバー常駐（src/server.py）を廃止し、CLIバッチのみで運用するための
エントリポイント。sync / want / purchase の3サブコマンドを提供する。

使い方:
    python run.py sync [--workers N] [--limit N] [--start N]
    python run.py want <asin> (--on|--off)
    python run.py purchase <asin> (--on|--off)
"""

import os
import sys
import subprocess
import io

# Windows CP932 環境での文字化け防止（main.py / report.py と同じ対処）
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import report
from report import _load_env_file


def publish() -> None:
    """
    「読みたい本」を GitHub Pages 公開用リポジトリへ公開する。

    src/server.py の do_publish() と同じ判定順序（report生成 → git add →
    git diff --cached --quiet による差分判定 → 差分ありのみ git commit →
    push は常に試行）を、asyncio 非依存の subprocess.run で同期的に実装する
    （run.py はサーバー無しの単発バッチ実行のため、do_publish() の非同期
    サブプロセス実装をそのまま呼び出せない。server.py 自体は変更しない）。

    PUBLIC_SITE_DIR / PUBLIC_SITE_URL が未設定の場合は明示エラーを表示して
    終了する（report.main() の _require_env() は同じ検証を行うが、ここで
    先に確認することで git コマンドが一切呼ばれないことを保証する）。
    """
    _load_env_file(os.path.join(BASE_DIR, ".env"))

    public_site_dir = os.environ.get("PUBLIC_SITE_DIR")
    public_site_url = os.environ.get("PUBLIC_SITE_URL")
    if not public_site_dir or not public_site_url:
        print(
            "エラー: 環境変数 PUBLIC_SITE_DIR / PUBLIC_SITE_URL が設定されていません。"
            ".env.example を参考に .env に設定してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    report.main()
