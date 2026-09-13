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

    git_env = os.environ.copy()
    # 認証切れの git コマンドが対話プロンプト待ちで無限にハングしないようにする
    # （do_publish() と同じ対処。無人バッチ実行では標準入力を操作する手段が無い）。
    git_env["GIT_TERMINAL_PROMPT"] = "0"

    def _run_git(args: list) -> int:
        result = subprocess.run(
            ["git"] + args, cwd=public_site_dir, env=git_env, shell=False
        )
        return result.returncode

    if _run_git(["add", "index.html"]) != 0:
        print("エラー: git add に失敗しました。公開を中断しました。", file=sys.stderr)
        sys.exit(1)

    # git diff --cached --quiet の終了コードは「差分なし=0 / 差分あり=1」で、
    # 他の分岐と意味が逆になる（0 が異常ではなく「commit 不要」を意味する）。
    diff_returncode = _run_git(["diff", "--cached", "--quiet", "--", "index.html"])
    if diff_returncode == 0:
        print("差分なし（前回から内容が同じ）。")
    else:
        commit_returncode = _run_git(
            ["commit", "-m", "chore: update wishlist", "-q", "--", "index.html"]
        )
        if commit_returncode != 0:
            print("エラー: git commit に失敗しました。公開を中断しました。", file=sys.stderr)
            sys.exit(1)

    # 差分が無い場合も push は必ず試みる。前回の公開で push だけが失敗し
    # commit だけがローカルに残っていた場合、diff の判定だけでは検出できず
    # 「差分なし」のまま永久に push されない状態になってしまうため
    # （push 自体は送るものが無ければ no-op で成功する）。
    if _run_git(["push", "-q"]) != 0:
        print(
            "エラー: git push に失敗しました。コミットはローカルに残っています。"
            "通信状況や認証情報を確認し、もう一度実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"[完了] 公開しました: {public_site_url}")
