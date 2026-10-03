"""
run.py
------
ローカルサーバー常駐（src/server.py）を廃止し、CLIバッチのみで運用するための
エントリポイント。sync / want / purchase / import-marks / recommend のサブコマンドを提供する。

使い方:
    python run.py sync [--workers N] [--limit N] [--start N]
    python run.py want <asin> (--on|--off)
    python run.py purchase <asin> (--on|--off)
    python run.py import-marks [<書き出したファイル>] [--publish]
    python run.py recommend [--kind manga|book|all] [--count N] [--new N] [--model NAME] [--dry-run]
"""

import argparse
import asyncio
import glob
import json
import os
import re
import sys
import subprocess
import io
from typing import Optional

# Windows CP932 環境での文字化け防止（main.py / report.py と同じ対処）
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from sqlalchemy.exc import SQLAlchemyError

import report
from report import _load_env_file
import main as main_module
from src import recommender
from src.book_kind import KIND_BOOK, KIND_MANGA
from src.bookmeter_sync import sync_bookmeter_wishlist
from src.repository import get_book_marks, get_books, import_marks, init_db, set_wanted, set_purchased

# 欲しい本の画面（book-highlights の web/core/wishlist.js の marksFile）の「見た・評価を書き出す」が作るファイル
MARKS_FILE_FORMAT = "kindle-marks"
MARKS_FILE_GLOB = "kindle-marks-*.json"

# report.main() が PUBLIC_SITE_DIR に書き出し、publish() が公開するファイル。
# 公開先は book-highlights の web/wishlist-site/。この 2 つ以外は commit しない（他の作業中の変更を巻き込まない）。
PUBLISHED_FILES = ["wishlist.json", "feed.xml"]

# GitHub Pages は main への push でだけ公開される（book-highlights の .github/workflows/pages.yml）
PUBLISH_BRANCH = "main"

# これがリポジトリの git フォルダにあれば、誰かが rebase / merge / cherry-pick の途中
_IN_PROGRESS_MARKERS = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD")


def _git_output(args: list, cwd: str, git_env: dict) -> Optional[str]:
    """git の標準出力（末尾の改行を除く。status --porcelain の行頭の空白は意味を持つので先頭は削らない）。失敗したら None。"""
    result = subprocess.run(
        ["git"] + args, cwd=cwd, env=git_env, shell=False, capture_output=True, text=True
    )
    return result.stdout.rstrip() if result.returncode == 0 else None


def ensure_safe_to_publish(public_site_dir: str, git_env: dict) -> None:
    """
    公開先は人や他のセッションが作業する book-highlights の作業ツリーの中なので、
    pull・commit・push で他の作業を壊したり巻き込んだりしないことを先に確かめる。
    main 以外のブランチ / rebase・merge の途中 / 公開する 2 ファイル以外の（追跡中の）変更があれば、
    git を何も変えずに終了コード 1 で止める（未追跡のファイルは pull にも commit にも関わらないので許す）。
    """
    problems = []
    branch = _git_output(["rev-parse", "--abbrev-ref", "HEAD"], public_site_dir, git_env)
    if branch != PUBLISH_BRANCH:
        problems.append(f"ブランチが {PUBLISH_BRANCH} ではありません（今: {branch}）")

    git_dir = _git_output(["rev-parse", "--absolute-git-dir"], public_site_dir, git_env)
    if git_dir and any(os.path.exists(os.path.join(git_dir, m)) for m in _IN_PROGRESS_MARKERS):
        problems.append("rebase / merge / cherry-pick の途中です")

    prefix = _git_output(["rev-parse", "--show-prefix"], public_site_dir, git_env) or ""
    allowed = {prefix + name for name in PUBLISHED_FILES}
    status = _git_output(
        ["-c", "core.quotePath=false", "status", "--porcelain", "--untracked-files=no"],
        public_site_dir, git_env,
    )
    if status is None:
        problems.append("git status を読めませんでした")
    else:
        others = [line[3:] for line in status.splitlines() if line[3:] not in allowed]
        if others:
            problems.append("公開するファイル以外に未コミットの変更があります: " + ", ".join(others[:5]))

    if problems:
        print(
            f"エラー: 公開先（{public_site_dir}）のリポジトリが公開できる状態ではないので、何も変えずに中断しました。\n  - "
            + "\n  - ".join(problems)
            + f"\nそのリポジトリで {PUBLISH_BRANCH} に戻し、作業中の変更を commit するか worktree に移してから、もう一度実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)


def publish() -> None:
    """
    「読みたい本」を GitHub Pages 公開用リポジトリへ公開する。

    書き出す前に git pull --rebase で公開用クローンを origin の最新に合わせる（失敗したら中断）。
    以降は src/server.py の do_publish() と同じ判定順序（report生成 → git add →
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

    report.require_public_site_repo(public_site_dir)

    git_env = os.environ.copy()
    # 認証切れの git コマンドが対話プロンプト待ちで無限にハングしないようにする
    # （do_publish() と同じ対処。無人バッチ実行では標準入力を操作する手段が無い）。
    git_env["GIT_TERMINAL_PROMPT"] = "0"

    # 下の rebase --abort や autostash が他の人の作業を壊さないよう、git を変える前に確かめる
    # （これを通れば、autostash が退避するのは公開する 2 ファイルだけになる）
    ensure_safe_to_publish(public_site_dir, git_env)

    def _run_git(args: list) -> int:
        result = subprocess.run(
            ["git"] + args, cwd=public_site_dir, env=git_env, shell=False
        )
        return result.returncode

    # 書き出す前に公開用クローンを origin の最新へ合わせる。index.html 等を GitHub 側で
    # 変えた後にクローンが古いままだと、push が毎回 non-fast-forward で拒否され続ける。
    # 前回 push だけ失敗して残ったコミットは rebase で origin の上に積み直す。
    if _run_git(["pull", "--rebase", "--autostash", "-q"]) != 0:
        # 衝突で止まった rebase を残すと次回以降もずっと失敗するので取り消す（rebase 中でなければ何もしない）
        subprocess.run(
            ["git", "rebase", "--abort"], cwd=public_site_dir, env=git_env, shell=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print(
            f"エラー: 公開用リポジトリ（{public_site_dir}）を origin の最新に合わせられませんでした（git pull 失敗）。"
            "通信状況を確認し、そのフォルダで git status を見て手元の変更を整理してから、もう一度実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    report.main()

    if _run_git(["add"] + PUBLISHED_FILES) != 0:
        print("エラー: git add に失敗しました。公開を中断しました。", file=sys.stderr)
        sys.exit(1)

    # git diff --cached --quiet の終了コードは「差分なし=0 / 差分あり=1」で、
    # 他の分岐と意味が逆になる（0 が異常ではなく「commit 不要」を意味する）。
    diff_returncode = _run_git(["diff", "--cached", "--quiet", "--"] + PUBLISHED_FILES)
    if diff_returncode == 0:
        print("差分なし（前回から内容が同じ）。")
    else:
        commit_returncode = _run_git(
            ["commit", "-m", "chore: update wishlist", "-q", "--"] + PUBLISHED_FILES
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


async def _run_sync(
    xml_path: str, limit: int, start: int, workers: int, target: str, only_asins: set = None
) -> None:
    """
    target に応じて Kindle クロール / 読書メーター同期を実行し、公開は常に1回行う。

    - target="kindle":    Kindle クロールのみ
    - target="bookmeter": 読書メーター同期のみ
    - target="both"（既定）: 従来どおり両方を順に実行（後方互換）

    sync_bookmeter_wishlist() は progress_cb 省略時に一切出力せず、失敗した
    本は戻り値の failed_titles にしか載らない契約（src/bookmeter_sync.py の
    docstring参照）。CLIバッチにはSSE/画面が無く標準出力が唯一の通知経路
    のため、src/server.py の do_bookmeter_sync() と同様に progress_cb=print
    を接続し、スキップ一覧を明示する（無音のまま公開してしまうことを防ぐ）。
    """
    if target in ("kindle", "both"):
        await main_module.run_integration(
            xml_path=xml_path, limit=limit, start=start, workers=workers, only_asins=only_asins
        )
    if target in ("bookmeter", "both"):
        result = await sync_bookmeter_wishlist(progress_cb=print)
        if result["failed_titles"]:
            print(f"[スキップ一覧] {', '.join(result['failed_titles'])}")
    publish()


_ASIN_RE = re.compile(r"[A-Z0-9]{10}")


def _parse_asin_list(value: str) -> set:
    """`--asins` の値（カンマ区切りの ASIN）を集合にする。形式が違えば argparse のエラーにする。"""
    asins = value.split(",")
    if not asins or not all(_ASIN_RE.fullmatch(asin) for asin in asins):
        raise argparse.ArgumentTypeError("ASIN は英大文字・数字 10 文字をカンマ区切りで指定してください。")
    return set(asins)


def cmd_sync(args: argparse.Namespace) -> None:
    """
    `run.py sync` のエントリ。xml_path のデフォルト解決（main.py と同じ
    kindle_sample_extractor.DEFAULT_CACHE_PATH）、--workers のクランプ（1〜5）は
    main.py の挙動をそのまま踏襲する（--xml オプションはスコープ外のため無い）。

    --target=bookmeter のときは Kindle クロールを行わないため、Kindle の
    キャッシュ XML が存在しなくても実行できる（存在チェックをスキップする）。
    """
    target = args.target
    # 指定した本だけの再実行（scraping-hub で失敗した本だけを取り直す）は Kindle クロールだけを行う。
    only_asins = getattr(args, "asins", None)
    if only_asins:
        if target == "bookmeter":
            print("エラー: --asins は Kindle クロール用のため --target bookmeter とは併用できません。", file=sys.stderr)
            sys.exit(2)
        target = "kindle"
    xml_path = main_module.kindle_sample_extractor.DEFAULT_CACHE_PATH
    if target in ("kindle", "both") and not os.path.exists(xml_path):
        print(f"エラー: XML ファイルが見つかりません: {xml_path}", file=sys.stderr)
        sys.exit(1)

    workers = max(1, min(args.workers, 5))
    if workers != args.workers:
        print(f"[注意] --workers は 1、5 の範囲にクランプされました: {args.workers} → {workers}")

    asyncio.run(
        _run_sync(
            xml_path=xml_path,
            limit=args.limit,
            start=args.start,
            workers=workers,
            target=target,
            only_asins=only_asins,
        )
    )


def cmd_want(args: argparse.Namespace) -> None:
    status = 1 if args.on else 0
    if not set_wanted(args.asin, status):
        print(f"エラー: 対象が見つかりませんでした（ASIN: {args.asin}）。", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] want フラグを更新しました（ASIN: {args.asin}, status: {status}）。")


def cmd_purchase(args: argparse.Namespace) -> None:
    status = 1 if args.on else 0
    if not set_purchased(args.asin, status):
        print(f"エラー: 対象が見つかりませんでした（ASIN: {args.asin}）。", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] purchase フラグを更新しました（ASIN: {args.asin}, status: {status}）。")


def _find_latest_marks_file(directory: str):
    """directory 直下の書き出しファイル（kindle-marks-*.json）のうち最も新しいものを返す。無ければ None。"""
    files = glob.glob(os.path.join(directory, MARKS_FILE_GLOB))
    return max(files, key=os.path.getmtime) if files else None


def cmd_import_marks(args: argparse.Namespace) -> None:
    """
    公開ページで書き出したタグ・★評価・種別を book_marks に取り込む。

    ファイルを省略した場合は MARKS_DOWNLOAD_DIR（未設定ならホームの Downloads）から
    最新の kindle-marks-*.json を使う（PC のブラウザで書き出した直後にそのまま取り込めるように）。
    --publish を付けると取り込み後に publish()（report 生成 → git push）まで行い、
    別の端末で開いても取り込んだ状態が初期表示されるようにする。
    """
    _load_env_file(os.path.join(BASE_DIR, ".env"))

    path = args.file
    if not path:
        directory = os.environ.get("MARKS_DOWNLOAD_DIR") or os.path.join(os.path.expanduser("~"), "Downloads")
        path = _find_latest_marks_file(directory)
        if not path:
            print(
                f"エラー: 書き出しファイルが見つかりません（{directory} に {MARKS_FILE_GLOB} がありません）。"
                "ファイルのパスを指定してください。",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[情報] 最新の書き出しファイルを使います: {path}")

    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except OSError as e:
        print(f"エラー: ファイルを開けません: {path}（{e}）", file=sys.stderr)
        sys.exit(1)
    except ValueError:
        print(f"エラー: JSON として読めません: {path}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict) or data.get("format") != MARKS_FILE_FORMAT or not isinstance(data.get("items"), list):
        print(
            "エラー: 公開ページの「見た・評価を書き出す」で作ったファイルではありません。",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = import_marks(data["items"])
    except SQLAlchemyError as e:
        # DB ファイルが書き込み不可（所有者違い等）やロック中のときに、トレースバックではなく原因を示す
        print(f"エラー: DB への書き込みに失敗しました。取り込みを中断しました（{e}）", file=sys.stderr)
        sys.exit(1)
    for reason in result["skipped"]:
        print(f"[スキップ] {reason}")
    print(
        f"[OK] 取り込みました（反映 {result['updated']} 件 / 解除 {result['deleted']} 件 / "
        f"スキップ {len(result['skipped'])} 件）。"
    )
    summary = recommender.summarize_seen(get_books(filter="all"), get_book_marks())
    print(
        "[情報] 見た: "
        + " / ".join(
            f"{recommender.KIND_LABELS[kind]} {summary[kind]['seen']}件（★評価 {summary[kind]['rated']}件）"
            for kind in (KIND_MANGA, KIND_BOOK)
        )
    )

    if args.publish:
        publish()
    else:
        print("[情報] 公開ページにも反映するには --publish を付けて実行するか、python run.py sync を実行してください。")


def cmd_recommend(args: argparse.Namespace) -> None:
    """
    取り込み済みの「見た」作品と★評価から、ローカル LLM にマンガ・本それぞれのおすすめを出してもらう。

    --kind all（既定）はマンガ・本を別々に問い合わせる。「見た」が1件も無い種別は飛ばす。
    --dry-run は LLM に送るプロンプトを表示するだけで問い合わせない（LLM を用意する前の確認用）。
    """
    _load_env_file(os.path.join(BASE_DIR, ".env"))
    try:
        settings = recommender.load_llm_settings(os.environ, model=args.model, timeout=args.timeout)
    except recommender.LocalLlmError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    books = get_books(filter="all")
    marks = get_book_marks()
    kinds = [KIND_MANGA, KIND_BOOK] if args.kind == "all" else [args.kind]
    model = None
    for kind in kinds:
        label = recommender.KIND_LABELS[kind]
        inputs = recommender.collect_inputs(books, marks, kind, max_candidates=args.max_candidates)
        if not inputs["seen"]:
            print(
                f"[{label}] 「見た」{label}がまだ無いため、おすすめを出せません"
                "（公開ページで「見た」と★を付けて書き出し、python run.py import-marks で取り込んでください）。"
            )
            continue
        messages = recommender.build_messages(kind, inputs, count=args.count, new_count=args.new)
        if args.dry_run:
            print(f"===== {label}: ローカル LLM に送るプロンプト =====")
            for message in messages:
                print(f"[{message['role']}]\n{message['content']}\n")
            continue
        try:
            if model is None:
                model = recommender.resolve_model(settings)
            print(f"[{label}] ローカル LLM（{model}）に問い合わせています…（数十秒〜数分かかることがあります）", flush=True)
            text = recommender.request_recommendations(messages, settings, model)
        except recommender.LocalLlmError as e:
            print(f"エラー: {e}", file=sys.stderr)
            sys.exit(1)
        result = recommender.parse_recommendations(text, inputs, count=args.count, new_count=args.new)
        print(recommender.format_recommendations(kind, result, model=model, inputs=inputs))
        print()


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("0 以上の整数を指定してください")
    return number


def _add_on_off_group(subparser: argparse.ArgumentParser) -> None:
    group = subparser.add_mutually_exclusive_group(required=True)
    group.add_argument("--on", action="store_true", help="フラグをONにする")
    group.add_argument("--off", action="store_true", help="フラグをOFFにする")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kindle システム CLI バッチ運用エントリポイント"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser(
        "sync", help="クロール→読書メーター同期→レポート生成→公開を順に実行する"
    )
    sync_parser.add_argument(
        "--workers", type=int, default=1, help="並列ブラウザ数（デフォルト: 1、推奨: 2〜3）"
    )
    sync_parser.add_argument("--limit", type=int, default=None, help="処理する最大件数")
    sync_parser.add_argument(
        "--start", type=int, default=None, help="開始するインデックス番号"
    )
    sync_parser.add_argument(
        "--target",
        choices=["kindle", "bookmeter", "both"],
        default="both",
        help="実行対象（kindle: Kindleクロールのみ / bookmeter: 読書メーター同期のみ / both: 両方。デフォルト: both）",
    )
    sync_parser.add_argument(
        "--asins",
        type=_parse_asin_list,
        default=None,
        help="カンマ区切りの Sample ASIN。指定した本だけを Kindle クロールし直す（前回処理済みでも処理する。"
        "読書メーター同期は行わない）",
    )
    sync_parser.set_defaults(func=cmd_sync)

    want_parser = subparsers.add_parser("want", help="「欲しい本」フラグを更新する")
    want_parser.add_argument("asin", help="対象の paid_asin")
    _add_on_off_group(want_parser)
    want_parser.set_defaults(func=cmd_want)

    purchase_parser = subparsers.add_parser("purchase", help="購入済みフラグを更新する")
    purchase_parser.add_argument("asin", help="対象の paid_asin")
    _add_on_off_group(purchase_parser)
    purchase_parser.set_defaults(func=cmd_purchase)

    import_parser = subparsers.add_parser(
        "import-marks", help="公開ページで書き出した「見た」・★評価・種別（JSON）を DB に取り込む"
    )
    import_parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="書き出したファイル（省略時は MARKS_DOWNLOAD_DIR か ~/Downloads の最新の kindle-marks-*.json）",
    )
    import_parser.add_argument(
        "--publish", action="store_true", help="取り込んだあと公開ページを作り直して公開する"
    )
    import_parser.set_defaults(func=cmd_import_marks)

    recommend_parser = subparsers.add_parser(
        "recommend", help="「見た」作品と★評価から、ローカル LLM にマンガ・本のおすすめを出してもらう"
    )
    recommend_parser.add_argument(
        "--kind",
        choices=[KIND_MANGA, KIND_BOOK, "all"],
        default="all",
        help="対象（manga: マンガ / book: 本 / all: 両方を別々に。デフォルト: all）",
    )
    recommend_parser.add_argument(
        "--count", type=_non_negative_int, default=5, help="登録済みの本から選ぶ件数（デフォルト: 5）"
    )
    recommend_parser.add_argument(
        "--new", type=_non_negative_int, default=3, help="登録済み以外から挙げる件数（デフォルト: 3、0 で出さない）"
    )
    recommend_parser.add_argument(
        "--max-candidates",
        type=_non_negative_int,
        default=recommender.DEFAULT_MAX_CANDIDATES,
        help=f"LLM に見せる候補の上限（デフォルト: {recommender.DEFAULT_MAX_CANDIDATES}）",
    )
    recommend_parser.add_argument(
        "--model", default=None, help="使うモデル名（省略時は LOCAL_LLM_MODEL、無ければ入っている最初のモデル）"
    )
    recommend_parser.add_argument(
        "--timeout", type=int, default=None, help=f"応答待ちの秒数（デフォルト: {recommender.DEFAULT_TIMEOUT_SECONDS}）"
    )
    recommend_parser.add_argument(
        "--dry-run", action="store_true", help="LLM に送るプロンプトを表示するだけで問い合わせない"
    )
    recommend_parser.set_defaults(func=cmd_recommend)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    # どのサブコマンドも DB（BookMapping 等）を読み書きするので、先にテーブル作成と book_mappings の
    # 列追加（バックアップ付き）を済ませる。main.py の Kindle クロールだけが init_db() を呼んでいたため、
    # sync --target bookmeter 等では新しい列（bookmeter_id）が足されないまま ORM が読みに行き、登録が全件失敗した
    init_db()
    args.func(args)


if __name__ == "__main__":
    main()
