"""
report.py
---------
蔵書一覧（読みたい本 / 購入済み本 / 全部）を、GitHub Pages 公開用リポジトリ（kindle-wishlist-site）に
データだけの wishlist.json として書き出すバッチスクリプト。画面は持たない。

欲しい本の画面は book-highlights アプリ（https://nihi566.github.io/book-highlights/#/wishlist）にあり、
同じオリジンからこの wishlist.json を fetch して表示する。見た目・操作を変えるときは book-highlights を直す。
公開リポジトリの index.html はその画面へ移動する静的ページで、ここでは作らない（上書きしない）。

画面ではタグ（読みたい / 読みたくない / 購入済み / 見た）・「見た」本の★評価・種別
（マンガ / 本）をブラウザに保存でき、「見た・評価を書き出す」で JSON にして
`run.py import-marks` で DB に取り込める（ローカル LLM のおすすめ `run.py recommend` に使う）。
取り込んだタグ・★を wishlist.json に載せるのは PUBLISH_MARKS=1 のときだけ（種別の上書きは常に載せる）。

使い方:
    python report.py

環境変数（.env.example 参照。いずれも必須。未設定なら明示エラーで停止する）:
    PUBLIC_SITE_DIR: GitHub Pages 公開用リポジトリのローカルクローン先絶対パス
    PUBLIC_SITE_URL: 公開後にアクセスする GitHub Pages の URL
"""

import os
import sys
import io
import json

# Windows CP932 環境での文字化け防止（main.py と同じ対処）
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from src.book_kind import KINDS, classify_kind
from src.repository import MARK_TAGS, UNKNOWN_TITLE, get_book_marks, get_books

# book-highlights アプリが読む欲しい本のデータ（wishlist.json）の形式名と版
WISHLIST_FILE_FORMAT = "kindle-wishlist"
WISHLIST_FILE_VERSION = 1


def _resolve_mark(book: dict) -> tuple:
    """
    取り込み済みの状態（book["mark"] = repository.get_book_marks() の1件）から、
    公開してよい (種別, タグ, ★評価) を決める。index.html と wishlist.json で同じ判定にする。

    固定の集合に入る値だけを返し、ユーザー由来の自由文字列は通さない（R2）。
    種別は取り込み済みの上書きが無ければ書名から自動判定し、★評価はタグが「見た」のときだけ残す。
    """
    mark = book.get("mark") or {}
    kind = mark.get("kind") if mark.get("kind") in KINDS else classify_kind(book.get("title") or "")
    tag = mark.get("tag") if mark.get("tag") in MARK_TAGS else ""
    rating = mark.get("rating")
    is_valid_rating = isinstance(rating, int) and not isinstance(rating, bool) and 1 <= rating <= 5
    return kind, tag, rating if tag == "seen" and is_valid_rating else None


def build_wishlist(books: list) -> dict:
    """
    欲しい本のデータ（book-highlights アプリが同じオリジンから fetch する wishlist.json）を組み立てる。

    画面は持たずデータだけを渡す。価格は index.html と同じく KU の本（価格が 0 で保存される）と
    未取得の本を null にする。生成時刻は載せない（自動公開のたびに差分が出て、データが同じでも
    コミットが増えるため）。最終取得日時は index.html と同じく各本の最新価格の timestamp の最大値。
    """
    timestamps = [str(book["timestamp"]) for book in books if book.get("timestamp")]
    items = []
    for book in books:
        is_ku = bool(book.get("is_unlimited"))
        actual_price = book.get("actual_price")
        kind, tag, rating = _resolve_mark(book)
        items.append(
            {
                "asin": book.get("asin") or "",
                "title": book.get("title") or UNKNOWN_TITLE,
                "price": None if is_ku or actual_price is None else actual_price,
                "ku": is_ku,
                "wanted": bool(book.get("is_wanted")),
                "purchased": bool(book.get("is_purchased")),
                "kind": kind,
                "tag": tag,
                "rating": rating,
                "scraped_at": str(book["timestamp"]) if book.get("timestamp") else None,
            }
        )
    return {
        "format": WISHLIST_FILE_FORMAT,
        "version": WISHLIST_FILE_VERSION,
        "last_scraped": max(timestamps) if timestamps else None,
        "books": items,
    }



def _load_env_file(env_path: str) -> None:
    """
    .env ファイルがあれば読み込み、未設定の環境変数にのみ反映する
    （既に設定済みの環境変数は上書きしない）。

    このリポジトリには python-dotenv 等のローダーが存在せず（main.py / src/server.py も
    os.environ を直接参照するのみ）、.env.example の「.env にコピーして編集してください」
    という案内どおりにしても値が読み込まれない状態だった。PUBLIC_SITE_DIR / PUBLIC_SITE_URL
    は（既存の KINDLE_XML_PATH 等と異なり）未設定だと起動できない必須項目のため、
    ここで最小限のローダーを追加する（新規依存を増やさないため自前実装）。
    """
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"エラー: 環境変数 {name} が設定されていません。.env.example を参考に .env に設定してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def main() -> None:
    """
    R1/R6 対策: PUBLIC_SITE_DIR / PUBLIC_SITE_URL の存在確認と、書き込み先が
    git リポジトリの作業ツリーであることの確認を行ってから書き出す。

    repository.get_books(filter="all") で全件を1回取得し、取り込み済みのタグ・★・種別を付けて
    wishlist.json にする（価格履歴は画面が無くなったので問い合わせない）。
    """
    _load_env_file(os.path.join(BASE_DIR, ".env"))

    public_site_dir = _require_env("PUBLIC_SITE_DIR")
    _require_env("PUBLIC_SITE_URL")  # report.py 自体は開かないが、起動時に設定漏れとして検出する

    if not os.path.isdir(public_site_dir):
        print(
            f"エラー: PUBLIC_SITE_DIR（{public_site_dir}）が存在しません。パスを確認してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    if not os.path.isdir(os.path.join(public_site_dir, ".git")):
        print(
            f"エラー: PUBLIC_SITE_DIR（{public_site_dir}）は git リポジトリの作業ツリーではありません。",
            file=sys.stderr,
        )
        sys.exit(1)

    books = get_books(filter="all")
    # run.py import-marks で取り込んだ内容を、画面の初期状態として各本に持たせる。
    # 種別（マンガ/本）の上書きは常に載せるが、「見た」・★評価・読みたくない等のタグは読書記録
    # なので、PUBLISH_MARKS=1 のときだけ公開する（既定では DB とローカル LLM だけで使う）。
    marks = get_book_marks()
    publish_marks = os.environ.get("PUBLISH_MARKS", "").strip().lower() in ("1", "true", "yes")
    for book in books:
        mark = marks.get(book["asin"])
        if mark and not publish_marks:
            mark = {"kind": mark.get("kind")}
        book["mark"] = mark
    # 日本語をエスケープしないのは、公開リポジトリの差分を人が読めるようにするため
    wishlist_path = os.path.join(public_site_dir, "wishlist.json")
    _write_replacing(wishlist_path, json.dumps(build_wishlist(books), ensure_ascii=False, indent=1) + "\n")

    print(f"生成しました: {wishlist_path}（{len(books)} 冊）")


def _write_replacing(path: str, content: str) -> None:
    # 途中中断で壊れたファイルを公開リポジトリに残さないよう、一時ファイルへ
    # 書き出してから置換する（src/repository.py の DB マイグレーションと同じ方式）。
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, path)


if __name__ == "__main__":
    main()
