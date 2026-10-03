"""
report.py
---------
蔵書一覧（読みたい本 / 購入済み本 / 全部）を、GitHub Pages 公開用リポジトリ（kindle-wishlist-site）に
データだけの wishlist.json として書き出すバッチスクリプト。画面は持たない。

欲しい本の画面は book-highlights アプリ（https://nihi566.github.io/book-highlights/#/wishlist）にあり、
同じオリジンからこの wishlist.json を fetch して表示する。見た目・操作を変えるときは book-highlights を直す。
公開リポジトリの index.html はその画面へ移動する静的ページで、ここでは作らない（上書きしない）。

画面ではタグ（読みたい / 購入済み / 読んだ。旧画面の「読みたくない」は廃止）・「見た」本の★評価・種別
（マンガ / 本）をブラウザに保存でき、「見た・評価を書き出す」で JSON にして
`run.py import-marks` で DB に取り込める（ローカル LLM のおすすめ `run.py recommend` に使う）。
取り込んだタグ・★を wishlist.json に載せるのは PUBLISH_MARKS=1 のときだけ（種別の上書きは常に載せる）。

使い方:
    python report.py [--allow-shrink]

本が 0 冊、または公開中の wishlist.json の半分未満に減ったときは、DB の不調とみなして書き出さずに止める
（自動公開がそのまま空の一覧を公開しないように）。本当に減らしたときだけ --allow-shrink で書き出す。

環境変数（.env.example 参照。いずれも必須。未設定なら明示エラーで停止する）:
    PUBLIC_SITE_DIR: GitHub Pages 公開用リポジトリのローカルクローン先絶対パス
    PUBLIC_SITE_URL: 公開後にアクセスする GitHub Pages の URL
"""

import argparse
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
from src.repository import MARK_TAGS, UNKNOWN_TITLE, get_all_price_points, get_book_marks, get_books, get_paid_price_points

# book-highlights アプリが読む欲しい本のデータ（wishlist.json）の形式名と版
WISHLIST_FILE_FORMAT = "kindle-wishlist"
WISHLIST_FILE_VERSION = 1
# 1 冊あたりに載せるスクレイピングの履歴の上限（新しい方から）。毎日取得しても wishlist.json が際限なく大きくならないように
MAX_PRICE_HISTORY_PER_BOOK = 50


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


def summarize_price_changes(points: list) -> dict:
    """
    有料価格の記録（repository.get_paid_price_points() の戻り値。本ごと・時刻順）から、本ごとに
    {"prev": 直前の価格, "changed_at": 今の価格に変わった日時, "low": 記録上の最安値} を返す。
    一度も変わっていなければ prev / changed_at は None。
    """
    result = {}
    for point in points:
        asin, price = point["paid_asin"], point["actual_price"]
        summary = result.get(asin)
        if summary is None:
            result[asin] = {"prev": None, "changed_at": None, "low": price, "current": price}
            continue
        if price != summary["current"]:
            summary.update(prev=summary["current"], changed_at=str(point["timestamp"]), current=price)
        summary["low"] = min(summary["low"], price)
    return {asin: {k: v for k, v in s.items() if k != "current"} for asin, s in result.items()}


def summarize_price_history(points: list, limit: int = MAX_PRICE_HISTORY_PER_BOOK) -> dict:
    """
    価格の記録（repository.get_all_price_points() の戻り値。本ごと・時刻順）から、本ごとの
    スクレイピングの履歴 [{"at": 取得日時, "price": 価格（KU・取得失敗は None）, "ku": KU か}] を返す。
    古い順で、新しい方から limit 件だけ残す。
    """
    result = {}
    for point in points:
        is_ku = bool(point["is_unlimited"])
        price = None if is_ku else point["actual_price"]
        result.setdefault(point["paid_asin"], []).append({"at": str(point["timestamp"]), "price": price, "ku": is_ku})
    return {asin: rows[-limit:] for asin, rows in result.items()}


def build_wishlist(books: list) -> dict:
    """
    欲しい本のデータ（book-highlights アプリが同じオリジンから fetch する wishlist.json）を組み立てる。

    画面は持たずデータだけを渡す。価格は index.html と同じく KU の本（価格が 0 で保存される）と
    未取得の本を null にする。生成時刻は載せない（自動公開のたびに差分が出て、データが同じでも
    コミットが増えるため）。最終取得日時は index.html と同じく各本の最新価格の timestamp の最大値。
    値動き（book["price_trend"] = summarize_price_changes の 1 件）は、今の価格がある本にだけ載せる。
    スクレイピングの履歴（book["price_history"] = summarize_price_history の 1 件）は全冊に載せる（無ければ空）。
    """
    timestamps = [str(book["timestamp"]) for book in books if book.get("timestamp")]
    items = []
    for book in books:
        is_ku = bool(book.get("is_unlimited"))
        actual_price = book.get("actual_price")
        kind, tag, rating = _resolve_mark(book)
        price = None if is_ku or actual_price is None else actual_price
        trend = (book.get("price_trend") or {}) if price is not None else {}
        items.append(
            {
                "asin": book.get("asin") or "",
                "title": book.get("title") or UNKNOWN_TITLE,
                "price": price,
                "ku": is_ku,
                "wanted": bool(book.get("is_wanted")),
                "purchased": bool(book.get("is_purchased")),
                "kind": kind,
                "tag": tag,
                "rating": rating,
                "scraped_at": str(book["timestamp"]) if book.get("timestamp") else None,
                "price_prev": trend.get("prev"),
                "price_changed_at": trend.get("changed_at"),
                "price_low": trend.get("low"),
                "price_history": book.get("price_history") or [],
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


def require_public_site_repo(public_site_dir: str) -> None:
    """書き込み先が存在する git リポジトリの作業ツリーでなければ、設定の誤りとして明示エラーで止める
    （run.py の publish() も git pull の前に呼ぶ。先に git を走らせると原因の分からない例外になるため）。"""
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


def _published_book_count(path: str):
    """公開中の wishlist.json の冊数。ファイルが無い・読めないときは None（比べる相手が無い）。"""
    try:
        with open(path, encoding="utf-8") as f:
            books = json.load(f).get("books")
    except (OSError, ValueError, AttributeError):
        return None
    return len(books) if isinstance(books, list) else None


def _shrink_error(new_count: int, old_count) -> str:
    """書き出してはいけないほど減っていれば理由を返す（0 冊、または公開中の半分未満）。"""
    if new_count == 0:
        return "本が 0 冊です"
    if old_count and new_count * 2 < old_count:
        return f"本の数が公開中の {old_count} 冊から {new_count} 冊に減りました（半分未満）"
    return ""


def main(allow_shrink: bool = False) -> None:
    """
    R1/R6 対策: PUBLIC_SITE_DIR / PUBLIC_SITE_URL の存在確認と、書き込み先が
    git リポジトリの作業ツリーであることの確認を行ってから書き出す。

    repository.get_books(filter="all") で全件を1回取得し、取り込み済みのタグ・★・種別を付けて
    wishlist.json にする。値動き（前回価格・最安値）用の価格の記録も全冊分を 1 回で取る（1 冊ごとに問い合わせない）。
    0 冊・急減のときは書き出さずに SystemExit(1) する（allow_shrink=True なら書き出す）。
    """
    _load_env_file(os.path.join(BASE_DIR, ".env"))

    public_site_dir = _require_env("PUBLIC_SITE_DIR")
    _require_env("PUBLIC_SITE_URL")  # report.py 自体は開かないが、起動時に設定漏れとして検出する

    require_public_site_repo(public_site_dir)

    books = get_books(filter="all")
    # run.py import-marks で取り込んだ内容を、画面の初期状態として各本に持たせる。
    # 種別（マンガ/本）の上書きは常に載せるが、「見た」・★評価・読みたくない等のタグは読書記録
    # なので、PUBLISH_MARKS=1 のときだけ公開する（既定では DB とローカル LLM だけで使う）。
    marks = get_book_marks()
    trends = summarize_price_changes(get_paid_price_points())
    histories = summarize_price_history(get_all_price_points())
    publish_marks = os.environ.get("PUBLISH_MARKS", "").strip().lower() in ("1", "true", "yes")
    for book in books:
        mark = marks.get(book["asin"])
        if mark and not publish_marks:
            mark = {"kind": mark.get("kind")}
        book["mark"] = mark
        book["price_trend"] = trends.get(book["asin"])
        book["price_history"] = histories.get(book["asin"], [])
    wishlist_path = os.path.join(public_site_dir, "wishlist.json")
    reason = _shrink_error(len(books), _published_book_count(wishlist_path))
    if reason and not allow_shrink:
        print(
            f"エラー: {reason}。DB の不調の可能性があるため、wishlist.json を書き換えずに中断しました。"
            "本当に減らした場合は python report.py --allow-shrink で書き出してから、"
            "もう一度 python run.py sync を実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    # 日本語をエスケープしないのは、公開リポジトリの差分を人が読めるようにするため
    _write_replacing(wishlist_path, json.dumps(build_wishlist(books), ensure_ascii=False, indent=1) + "\n")

    print(f"生成しました: {wishlist_path}（{len(books)} 冊）")


def _write_replacing(path: str, content: str) -> None:
    # 途中中断で壊れたファイルを公開リポジトリに残さないよう、一時ファイルへ
    # 書き出してから置換する（src/repository.py の DB マイグレーションと同じ方式）。
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, path)


def parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="欲しい本のデータ wishlist.json を公開用リポジトリに書き出す")
    parser.add_argument(
        "--allow-shrink",
        action="store_true",
        help="本が 0 冊・公開中の半分未満に減っていても書き出す（本当に減らしたときだけ使う）",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    main(allow_shrink=parse_args(sys.argv[1:]).allow_shrink)
