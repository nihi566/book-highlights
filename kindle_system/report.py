"""
report.py
---------
蔵書一覧（読みたい本 / 購入済み本 / 全部）と価格履歴を GitHub Pages 公開用の静的 HTML として
書き出すバッチスクリプト。filmarks_scraper の reporter.py 相当（単一 HTML・軽量 JS のみ・
React 不要）。あわせて同じ一覧をデータだけの wishlist.json として書き出す
（book-highlights アプリが同じオリジンから fetch して欲しい本の画面を出す）。

ページではタグ（読みたい / 読みたくない / 購入済み / 見た）・「見た」本の★評価・種別
（マンガ / 本）をブラウザに保存でき、「見た・評価を書き出す」で JSON にして
`run.py import-marks` で DB に取り込める（ローカル LLM のおすすめ `run.py recommend` に使う）。
取り込んだタグ・★を公開ページにも載せるのは PUBLISH_MARKS=1 のときだけ（種別の上書きは常に載せる）。

使い方:
    python report.py

環境変数（.env.example 参照。いずれも必須。未設定なら明示エラーで停止する）:
    PUBLIC_SITE_DIR: GitHub Pages 公開用リポジトリのローカルクローン先絶対パス
    PUBLIC_SITE_URL: 公開後にアクセスする GitHub Pages の URL
"""

import os
import sys
import html
import io
import json
from datetime import datetime

# Windows CP932 環境での文字化け防止（main.py と同じ対処）
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from src.book_kind import KINDS, classify_kind
from src.repository import MARK_TAGS, UNKNOWN_TITLE, get_book_marks, get_books, get_price_history

# book-highlights アプリが読む欲しい本のデータ（wishlist.json）の形式名と版
WISHLIST_FILE_FORMAT = "kindle-wishlist"
WISHLIST_FILE_VERSION = 1


def _format_price(book: dict) -> str:
    """
    actual_price があればそれを整形して返し、無ければ「価格情報なし」を返す（R5 対策）。

    is_unlimited の本は src/crawler.py の KU 安全弁により sell_price/actual_price が
    常に 0 で保存される（本来の価格は campaign_text に退避されている）ため、
    ここで ¥0 と誤表示しないよう先に判定する。
    """
    if book.get("is_unlimited"):
        return "Kindle Unlimited 対象"
    actual_price = book.get("actual_price")
    if actual_price is None:
        return "価格情報なし"
    return f"¥{actual_price:,}"


_PRICE_HISTORY_SVG_WIDTH = 200
_PRICE_HISTORY_SVG_HEIGHT = 50


def _build_price_history_svg(history: list) -> str:
    """
    価格履歴（repository.get_price_history() の返り値形式）を価格履歴欄の中身に変換する。

    is_unlimited=1 の点は src/crawler.py の KU 安全弁により価格が常に 0 で保存される
    ため、有効な価格点から除外する（R4）。有効な価格点が2点未満なら「データなし」、
    全点が同じ価格なら「変動なし」の文言を返す（カード表示の狭い欄で横線だけの
    グラフを出さないため）。価格が変動している場合だけインライン SVG の折れ線を返す
    （外部グラフライブラリ不使用。R1/R3）。座標は数値のみで構成し、ユーザー由来の
    自由文字列を一切埋め込まない（R1: エスケープ対象自体が存在しない設計）。
    """
    valid_points = [
        point
        for point in history
        if not point.get("is_unlimited") and point.get("actual_price") is not None
    ]
    if len(valid_points) < 2:
        return "データなし"

    prices = [point["actual_price"] for point in valid_points]
    min_price = min(prices)
    max_price = max(prices)
    if min_price == max_price:
        return "変動なし"
    n = len(valid_points)

    coords = []
    for i, price in enumerate(prices):
        x = i * (_PRICE_HISTORY_SVG_WIDTH / (n - 1))
        y = _PRICE_HISTORY_SVG_HEIGHT - (
            (price - min_price) / (max_price - min_price) * _PRICE_HISTORY_SVG_HEIGHT
        )
        coords.append(f"{x:.1f},{y:.1f}")
    points_attr = " ".join(coords)

    # 折れ線は視覚情報のみのため、スクリーンリーダー向けに要約テキストを role="img" +
    # aria-label で持たせる（数値のみで構成しユーザー由来の自由文字列は含めない）。
    summary = f"価格履歴: 最低¥{min_price:,}〜最高¥{max_price:,}、直近¥{prices[-1]:,}（{n}件）"
    aria_label = html.escape(summary)

    return (
        f'<svg class="price-history-svg" role="img" aria-label="{aria_label}" '
        f'viewBox="0 0 {_PRICE_HISTORY_SVG_WIDTH} {_PRICE_HISTORY_SVG_HEIGHT}" '
        f'width="{_PRICE_HISTORY_SVG_WIDTH}" height="{_PRICE_HISTORY_SVG_HEIGHT}">'
        f'<polyline points="{points_attr}" fill="none" stroke="#0074d9" stroke-width="2"/>'
        f"</svg>"
    )


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


def _build_book_row(book: dict, index: int) -> str:
    """
    1冊分の表の <tr> を組み立てる（R2対策: title/asin/価格表示は html.escape() を通す）。

    book["price_history"] は repository.get_price_history() と同じ形式の list を
    想定する（main() が呼び出し前に付与する。キーが無い/Noneの場合は履歴なし扱い）。

    data-index は「登録順」への並べ替え復元専用（JS側でsortMode==='default'のとき
    この値で再ソートする。並べ替え後にDOM順が入れ替わっても元の順序へ戻せるようにする）。

    表紙・タグ欄・Amazon への遷移は、この行の ASIN を読んでページ側の JS が付け足す
    （_PAGE_SCRIPT。行そのものには <a> も表紙の <img> も出さない）。
    """
    title = html.escape(book.get("title") or UNKNOWN_TITLE)
    asin = html.escape(book.get("asin") or "")
    price_text = html.escape(_format_price(book))
    history_html = _build_price_history_svg(book.get("price_history") or [])

    # data-price / data-ku はクライアント側の検索・並べ替え・フィルタ（JS）専用の数値/真偽値
    # 属性で、title/asin のようなユーザー由来の自由文字列を複製しない（R2 のエスケープ件数を
    # 変えないため）。KU本はsell_price/actual_priceが常に0で保存される（R4と同じ理由）ため、
    # data-price は空にして価格ソート・価格帯フィルタの対象から除外する。
    is_ku = bool(book.get("is_unlimited"))
    actual_price = book.get("actual_price")
    data_price = "" if is_ku or actual_price is None else str(actual_price)
    data_ku = "1" if is_ku else "0"

    # data-saved-* は取り込み済みの状態（book["mark"] = repository.get_book_marks() の1件）と
    # 種別の自動判定で、ページの JS はブラウザに保存した状態が無いときにこれを使う。
    saved_kind, saved_tag, rating = _resolve_mark(book)
    saved_rating = "" if rating is None else str(rating)

    return (
        f'<tr class="book" data-price="{data_price}" data-ku="{data_ku}" data-index="{index}" '
        f'data-saved-kind="{saved_kind}" data-saved-tag="{saved_tag}" data-saved-rating="{saved_rating}">'
        f'<td class="col-title">{title}</td>'
        f'<td class="col-asin">{asin}</td>'
        f'<td class="col-price">{price_text}</td>'
        f'<td class="col-history">{history_html}</td>'
        f"</tr>"
    )


def _build_category_section(section_id: str, heading: str, books: list, empty_message: str, visible: bool) -> str:
    """
    section_id / heading は呼び出し元(build_html)が固定リテラルのみを渡す前提
    （ここではエスケープしない。呼び出し元でユーザー由来の値を渡さないこと）。
    heading と empty_message は html.escape() を通す。行の内容は _build_book_row 側でエスケープ済み。

    見出しは上部の分類ボタンと重複するため画面上は隠し、スクリーンリーダー向けにだけ残す。
    タグ欄の <td> はページ側の JS が行ごとに付け足すため、見出し行にだけ「タグ」列がある。
    """
    escaped_heading = html.escape(heading)
    if books:
        rows_html = "".join(_build_book_row(book, i) for i, book in enumerate(books))
        list_html = (
            f'<p class="result-count" id="count-{section_id}" aria-live="polite"></p>'
            f'<table class="book-table">'
            f'<caption class="visually-hidden">蔵書一覧（{escaped_heading}）</caption>'
            f"<thead><tr>"
            f'<th scope="col">タイトル</th>'
            f'<th scope="col">ASIN</th>'
            f'<th scope="col">価格</th>'
            f'<th scope="col">価格履歴</th>'
            f'<th scope="col">タグ</th>'
            f"</tr></thead>"
            f"<tbody>{rows_html}</tbody>"
            f"</table>"
            f'<p class="no-results hidden" role="status">'
            f"条件に一致する本がありません。検索語・種別・タグ・価格の条件を見直してください。</p>"
        )
    else:
        list_html = f'<p class="empty">{html.escape(empty_message)}</p>'
    section_class = "book-section" if visible else "book-section hidden"
    return (
        f'<section id="{section_id}" class="{section_class}" data-category="{section_id}">'
        f'<h2 class="visually-hidden">{escaped_heading}</h2>'
        f"{list_html}"
        f"</section>"
    )


# ページの CSS / 見出し・操作欄 / JS。値はすべて固定リテラルで、ユーザー由来の値は含まない。
# 公開ページ（PUBLIC_SITE_DIR の index.html）はこの report.py の生成物なので、見た目や操作を
# 変えるときはここを直してから生成し直す（index.html を直接編集すると次の自動公開で消える）。
_PAGE_STYLE = r"""
:root {
  --color-bg: #f7f7f9;
  --color-surface: #fff;
  --color-text: #222;
  --color-muted: #595959;
  --color-accent: #0074d9;
  --color-price: #d1401f;
  --color-border: #ccc;
  --color-border-light: #e3e3e8;
  --color-accent-hover: #0063b8;
  --space-xs: 0.25rem;
  --space-sm: 0.4rem;
  --space-md: 0.6rem;
  --space-lg: 0.75rem;
  --space-xl: 1rem;
  --space-2xl: 1.25rem;
  --radius-sm: 0.4rem;
  --radius-md: 0.6rem;
  --radius-pill: 999px;
  --font-size-sm: 0.8rem;
  --shadow-sm: 0 1px 3px rgba(0,0,0,0.08);
  --color-tag-wanted: var(--color-accent);
  --color-tag-unwanted: #767676;
  --color-tag-purchased: #2e8b57;
  --color-tag-seen: #7b3fa0;
  --color-star: #b8860b;
  --color-kind-manga: #b3261e;
}
*, *::before, *::after { box-sizing: border-box; }
body { font-family: -apple-system, "Hiragino Sans", "Yu Gothic", sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 var(--space-xl); background: var(--color-bg); color: var(--color-text); }
h1 { margin-bottom: var(--space-xs); }
.last-scraped { color: var(--color-muted); font-size: var(--font-size-sm); margin: 0 0 var(--space-xl); }
.visually-hidden { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
.result-count { color: var(--color-muted); font-size: var(--font-size-sm); margin: 0 0 var(--space-sm); }
.book-table { width: 100%; border-collapse: collapse; background: var(--color-surface); border-radius: var(--radius-md); overflow: hidden; box-shadow: var(--shadow-sm); }
.book-table caption { text-align: left; }
.book-table th, .book-table td { padding: var(--space-md) var(--space-lg); text-align: left; border-bottom: 1px solid var(--color-border); vertical-align: middle; }
.book-table th { font-size: var(--font-size-sm); color: var(--color-muted); font-weight: 600; }
.book-table tbody tr:last-child td { border-bottom: none; }
.book-table tbody tr:hover { background: #f0f4f9; }
.book-table tbody tr.book { cursor: pointer; }
.col-title { font-weight: 600; overflow-wrap: anywhere; }
.col-asin { color: var(--color-muted); font-size: var(--font-size-sm); white-space: nowrap; }
.col-price { font-weight: bold; white-space: nowrap; color: var(--color-price); }
.col-history { color: var(--color-muted); font-size: var(--font-size-sm); white-space: nowrap; }
.price-history-svg { display: block; max-width: 100%; height: auto; }
.col-tag { white-space: nowrap; }
.tag-group { display: flex; flex-wrap: wrap; gap: var(--space-xs); }
.tag-btn { padding: 0.2rem 0.55rem; font-size: 0.75rem; cursor: pointer; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-surface); color: var(--color-muted); white-space: nowrap; }
.tag-btn:hover { background: var(--color-bg); }
.tag-btn[aria-pressed="true"] { color: #fff; border-color: transparent; font-weight: 600; }
.tag-btn-wanted[aria-pressed="true"] { background: var(--color-tag-wanted); }
.tag-btn-unwanted[aria-pressed="true"] { background: var(--color-tag-unwanted); }
.tag-btn-purchased[aria-pressed="true"] { background: var(--color-tag-purchased); }
.tag-btn-seen[aria-pressed="true"] { background: var(--color-tag-seen); }
.kind-btn { display: inline-block; margin-bottom: var(--space-xs); padding: 0 0.4rem; font-size: 0.7rem; line-height: 1.4; cursor: pointer; border: 1px solid currentColor; border-radius: var(--radius-sm); background: var(--color-surface); color: var(--color-muted); }
.kind-btn[data-kind="manga"] { color: var(--color-kind-manga); }
.rating-group { display: flex; max-width: 10rem; margin-top: var(--space-xs); }
.star-btn { flex: 1 1 0; min-width: 0; min-height: 1.75rem; padding: 0; cursor: pointer; border: none; background: none; color: var(--color-muted); font-size: 1.1rem; line-height: 1; }
.star-btn.is-on { color: var(--color-star); }
.kind-bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-xs); margin-bottom: var(--space-lg); }
.kind-bar-label { color: var(--color-muted); font-size: var(--font-size-sm); margin-right: var(--space-xs); }
.kind-filter-btn { padding: 0.25rem 0.8rem; font-size: var(--font-size-sm); cursor: pointer; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-surface); }
.kind-filter-btn[aria-pressed="true"] { font-weight: bold; color: #fff; background: var(--color-text); border-color: var(--color-text); }
.marks-bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--space-sm); margin-bottom: var(--space-lg); padding: var(--space-sm) var(--space-lg); background: var(--color-surface); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); }
.marks-summary { margin: 0; color: var(--color-muted); font-size: var(--font-size-sm); }
.export-btn { padding: var(--space-sm) 0.8rem; cursor: pointer; border: 1px solid var(--color-accent); border-radius: var(--radius-sm); background: var(--color-surface); color: var(--color-accent); font-weight: 600; }
.export-btn:hover { background: var(--color-bg); }
.export-status { flex-basis: 100%; margin: 0; font-size: var(--font-size-sm); overflow-wrap: anywhere; }
.empty { color: var(--color-muted); }
.no-results { color: var(--color-muted); padding: var(--space-lg); text-align: center; }
.hidden { display: none; }
.filter-bar { display: flex; flex-wrap: wrap; gap: var(--space-sm); margin-bottom: var(--space-lg); }
.filter-btn { padding: var(--space-sm) 0.8rem; cursor: pointer; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-surface); }
.filter-btn[aria-pressed="true"] { font-weight: bold; color: #fff; background: var(--color-accent); border-color: var(--color-accent); }
.controls-bar { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); align-items: center; gap: var(--space-sm) var(--space-md); margin-bottom: var(--space-2xl); padding: var(--space-lg); background: var(--color-surface); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); box-shadow: var(--shadow-sm); }
.controls-bar > .ku-filter, .controls-bar > .tag-filter-select, .controls-bar > .price-range, .controls-bar > .price-range-error, .controls-bar > .reset-btn { grid-column: 1 / -1; }
.search-input { width: 100%; min-width: 0; padding: var(--space-sm) var(--space-md) var(--space-sm) 2rem; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23595959' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat 0.6rem center / 1rem; }
.sort-select, .tag-filter-select { width: 100%; min-width: 0; padding: var(--space-sm) var(--space-md); border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-surface); }
.ku-filter { display: flex; align-items: center; gap: 0.3rem; font-size: var(--font-size-sm); }
.price-range { display: flex; align-items: center; gap: var(--space-sm); }
.price-input { flex: 1; min-width: 0; padding: var(--space-sm) var(--space-md); border: 1px solid var(--color-border); border-radius: var(--radius-sm); }
.reset-btn { width: 100%; padding: var(--space-sm) 0.8rem; cursor: pointer; border: 1px solid var(--color-accent); border-radius: var(--radius-sm); background: var(--color-accent); color: #fff; font-weight: 600; }
.reset-btn:hover { background: var(--color-accent-hover); }
.price-range-error { color: var(--color-price); font-size: var(--font-size-sm); width: 100%; margin: 0; }
.active-filter-notice { color: var(--color-text); font-size: var(--font-size-sm); margin: 0 0 var(--space-sm); }
.section-toolbar { display: flex; align-items: center; justify-content: space-between; gap: var(--space-sm); margin: 0 0 var(--space-sm); }
.section-toolbar .result-count { margin: 0; }
.view-toggle { display: flex; gap: var(--space-xs); }
.view-toggle-btn { display: inline-flex; align-items: center; justify-content: center; width: 2.25rem; height: 2.25rem; padding: 0; cursor: pointer; border: 1px solid var(--color-border-light); border-radius: var(--radius-sm); background: var(--color-surface); color: var(--color-muted); }
.view-toggle-btn[aria-pressed="true"] { background: var(--color-accent); border-color: var(--color-accent); color: #fff; }
.view-toggle-btn svg { width: 1rem; height: 1rem; }
.col-cover img { display: block; max-width: 100%; max-height: 100%; object-fit: contain; }
.view-list .book-table td.col-cover { display: none; }
.view-grid .book-table { display: block; background: none; border-radius: 0; overflow: visible; box-shadow: none; }
.view-grid .book-table thead { display: none; }
.view-grid .book-table tbody { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.35rem; }
.view-grid .book-table tr.book { display: flex; flex-direction: column; gap: 0.15rem; min-width: 0; padding: 0.35rem; background: var(--color-surface); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); box-shadow: var(--shadow-sm); }
.view-grid .book-table tr.book:hover { background: #f0f4f9; }
.view-grid .book-table tr.book.hidden,
.view-grid .book-table.hidden { display: none; }
.view-grid .book-table td { display: block; padding: 0; border: none; }
.view-grid .book-table td.col-cover { display: flex; align-items: center; justify-content: center; height: 4.75rem; margin-bottom: var(--space-xs); }
.view-grid .col-cover:empty { background: var(--color-bg); border-radius: var(--radius-sm); }
.view-grid .book-table td.col-title { font-size: var(--font-size-sm); line-height: 1.35; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.view-grid .col-asin, .view-grid .col-history { font-size: 0.625rem; white-space: normal; overflow-wrap: anywhere; }
.view-grid .col-asin::before { content: "ASIN: "; }
.view-grid .col-history::before { content: "価格履歴: "; }
.view-grid .col-price { font-size: 1rem; white-space: normal; overflow-wrap: anywhere; }
.view-grid .col-tag { margin-top: auto; padding-top: var(--space-xs); white-space: normal; }
.view-grid .tag-btn { min-height: 1.5rem; padding: 0.1rem 0.35rem; font-size: 0.65rem; }
.view-grid .kind-btn { font-size: 0.6rem; }
.view-grid .star-btn { min-height: 1.5rem; font-size: 1rem; }
@media (min-width: 641px) {
  .view-grid .book-table tbody { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
}
@media (max-width: 640px) {
  body { margin: 1rem auto; padding: 0 var(--space-lg); }
  h1 { font-size: 1.4rem; margin: 0 0 var(--space-xs); }
  .last-scraped { margin-bottom: var(--space-lg); }
  .filter-btn, .reset-btn, .kind-filter-btn, .export-btn { min-height: 2.75rem; display: inline-flex; align-items: center; justify-content: center; }
  .search-input, .sort-select, .tag-filter-select, .price-input { min-height: 2.75rem; }
  .view-list .book-table thead { display: none; }
  .view-list .book-table, .view-list .book-table tbody, .view-list .book-table tr, .view-list .book-table td { display: block; width: 100%; }
  .view-list .book-table tr { border-bottom: 1px solid var(--color-border); padding: var(--space-md) 0; }
  .view-list .book-table td { border-bottom: none; padding: var(--space-xs) var(--space-sm); }
  .view-list .col-asin::before { content: "ASIN: "; }
  .view-list .col-history::before { content: "価格履歴: "; }
  .view-list .col-tag { margin-top: var(--space-xs); }
  .view-list .book-table .hidden,
  .view-list .book-table.hidden { display: none; }
}

"""

_PAGE_HEADER_HTML = r"""<div class="filter-bar" role="group" aria-label="表示する分類">
<button type="button" class="filter-btn" data-target="section-all" aria-pressed="true">全部</button>
<button type="button" class="filter-btn" data-target="section-wanted" aria-pressed="false">読みたい</button>
<button type="button" class="filter-btn" data-target="section-purchased" aria-pressed="false">購入済み</button>
</div>
<div class="kind-bar" role="group" aria-label="種別で絞り込み">
<span class="kind-bar-label" aria-hidden="true">種別</span>
<button type="button" class="kind-filter-btn" data-kind-filter="all" aria-pressed="true">すべて</button>
<button type="button" class="kind-filter-btn" data-kind-filter="manga" aria-pressed="false">マンガ</button>
<button type="button" class="kind-filter-btn" data-kind-filter="book" aria-pressed="false">本</button>
</div>
<div class="controls-bar">
<label class="visually-hidden" for="search-input">タイトル・ASINで検索</label>
<input type="search" id="search-input" class="search-input" placeholder="タイトル・ASINで検索" aria-label="タイトル・ASINで検索">
<label class="visually-hidden" for="sort-select">並べ替え</label>
<select id="sort-select" class="sort-select" aria-label="並べ替え">
<option value="default">登録順</option>
<option value="price-asc">価格が安い順</option>
<option value="price-desc">価格が高い順</option>
<option value="title-asc">タイトル順</option>
<option value="rating-desc">評価が高い順</option>
</select>
<label class="ku-filter"><input type="checkbox" id="ku-only-checkbox"> Kindle Unlimited対象のみ</label>
<label class="visually-hidden" for="tag-filter-select">タグで絞り込み</label>
<select id="tag-filter-select" class="tag-filter-select" aria-label="タグで絞り込み">
<option value="all">タグ: すべて</option>
<option value="hide-unwanted">タグ: 「読みたくない」を隠す</option>
<option value="wanted">タグ: 読みたいのみ</option>
<option value="unwanted">タグ: 読みたくないのみ</option>
<option value="purchased">タグ: 購入済みのみ</option>
<option value="seen">タグ: 見たのみ</option>
<option value="untagged">タグ: タグなしのみ</option>
</select>
<div class="price-range">
<label class="visually-hidden" for="price-min">価格下限</label>
<input type="number" id="price-min" class="price-input" placeholder="下限" min="0" inputmode="numeric" aria-label="価格下限">
<span aria-hidden="true">〜</span>
<label class="visually-hidden" for="price-max">価格上限</label>
<input type="number" id="price-max" class="price-input" placeholder="上限" min="0" inputmode="numeric" aria-label="価格上限">
</div>
<p id="price-range-error" class="price-range-error hidden" role="alert">価格の下限は上限以下にしてください（価格条件は一時的に無視されます）。</p>
<button type="button" id="reset-controls-button" class="reset-btn">条件をクリア</button>
</div>
<p id="active-filter-notice" class="active-filter-notice hidden" role="status">検索・種別・タグ・価格の条件を適用中です（すべてのタブに共通で適用されます）。</p>
<div class="marks-bar">
<p id="marks-summary" class="marks-summary" aria-live="polite"></p>
<button type="button" id="export-marks-button" class="export-btn">見た・評価を書き出す</button>
<p id="export-marks-status" class="export-status hidden" role="status"></p>
</div>"""

_PAGE_SCRIPT = r"""
function applyControls() {
  var searchInput = document.getElementById('search-input');
  var sortSelect = document.getElementById('sort-select');
  var kuOnlyCheckbox = document.getElementById('ku-only-checkbox');
  var priceMinInput = document.getElementById('price-min');
  var priceMaxInput = document.getElementById('price-max');
  var tagFilterSelect = document.getElementById('tag-filter-select');

  var searchTerm = searchInput.value.trim().toLowerCase();
  var searchWords = searchTerm.split(/[\s　]+/).filter(function (w) {
    return w.length > 0;
  });
  var sortMode = sortSelect.value;
  var kuOnly = kuOnlyCheckbox.checked;
  var priceMin = parseFloat(priceMinInput.value);
  var priceMax = parseFloat(priceMaxInput.value);
  var tagFilter = tagFilterSelect ? tagFilterSelect.value : 'all';
  var kindFilter = getKindFilter();

  var priceRangeError = document.getElementById('price-range-error');
  var priceRangeInvalid = !isNaN(priceMin) && !isNaN(priceMax) && priceMin > priceMax;
  if (priceRangeError) {
    priceRangeError.classList.toggle('hidden', !priceRangeInvalid);
  }
  var hasPriceFilter = !priceRangeInvalid && (!isNaN(priceMin) || !isNaN(priceMax));
  if (priceRangeInvalid) {
    priceMin = NaN;
    priceMax = NaN;
  }

  var activeFilterNotice = document.getElementById('active-filter-notice');
  if (activeFilterNotice) {
    var hasActiveFilter =
      searchWords.length > 0 || kuOnly || hasPriceFilter || tagFilter !== 'all' || kindFilter !== 'all';
    activeFilterNotice.classList.toggle('hidden', !hasActiveFilter);
  }

  document.querySelectorAll('.book-section table').forEach(function (table) {
    var tbody = table.querySelector('tbody');
    if (!tbody) {
      return;
    }
    var items = Array.prototype.slice.call(tbody.querySelectorAll('.book'));
    var visibleCount = 0;

    items.forEach(function (tr) {
      var titleEl = tr.querySelector('.col-title');
      var asinEl = tr.querySelector('.col-asin');
      var title = titleEl ? titleEl.textContent.toLowerCase() : '';
      var asin = asinEl ? asinEl.textContent.toLowerCase() : '';
      var price = tr.dataset.price === '' ? null : parseFloat(tr.dataset.price);
      var isKu = tr.dataset.ku === '1';

      var visible = true;
      var matchesAllWords = searchWords.every(function (word) {
        return title.indexOf(word) !== -1 || asin.indexOf(word) !== -1;
      });
      if (searchWords.length > 0 && !matchesAllWords) {
        visible = false;
      }
      if (kuOnly && !isKu) {
        visible = false;
      }
      if (!matchesTagFilter(tr.dataset.tag || '', tagFilter)) {
        visible = false;
      }
      if (kindFilter !== 'all' && (tr.dataset.kind || tr.dataset.savedKind) !== kindFilter) {
        visible = false;
      }
      if (!isNaN(priceMin) && (price === null || price < priceMin)) {
        visible = false;
      }
      if (!isNaN(priceMax) && (price === null || price > priceMax)) {
        visible = false;
      }
      tr.classList.toggle('hidden', !visible);
      if (visible) {
        visibleCount++;
      }
    });

    var sorted = items.slice().sort(function (a, b) {
      if (sortMode === 'default') {
        return parseInt(a.dataset.index, 10) - parseInt(b.dataset.index, 10);
      }
      if (sortMode === 'title-asc') {
        var titleA = a.querySelector('.col-title');
        var titleB = b.querySelector('.col-title');
        return (titleA ? titleA.textContent : '').localeCompare(titleB ? titleB.textContent : '', 'ja');
      }
      if (sortMode === 'rating-desc') {
        var diff = ratingSortValue(b) - ratingSortValue(a);
        return diff !== 0 ? diff : parseInt(a.dataset.index, 10) - parseInt(b.dataset.index, 10);
      }
      var pa = a.dataset.price === '' ? null : parseFloat(a.dataset.price);
      var pb = b.dataset.price === '' ? null : parseFloat(b.dataset.price);
      if (pa === null && pb === null) {
        return 0;
      }
      if (pa === null) {
        return 1;
      }
      if (pb === null) {
        return -1;
      }
      return sortMode === 'price-asc' ? pa - pb : pb - pa;
    });
    sorted.forEach(function (tr) {
      tbody.appendChild(tr);
    });

    var section = table.closest('.book-section');
    var countEl = section ? section.querySelector('.result-count') : null;
    if (countEl) {
      countEl.textContent = visibleCount + '件 / 全' + items.length + '件を表示';
    }
    var noResultsEl = section ? section.querySelector('.no-results') : null;
    if (noResultsEl) {
      noResultsEl.classList.toggle('hidden', visibleCount !== 0);
    }
    table.classList.toggle('hidden', visibleCount === 0 && items.length > 0);
  });
}

function matchesTagFilter(tag, filter) {
  if (filter === 'hide-unwanted') {
    return tag !== 'unwanted';
  }
  if (filter === 'untagged') {
    return tag === '';
  }
  if (filter === 'wanted' || filter === 'unwanted' || filter === 'purchased' || filter === 'seen') {
    return tag === filter;
  }
  return true;
}

// 評価が高い順: ★の数、「見た」だけで★なしは★の付いた本の後、「見た」以外はさらに後
function ratingSortValue(tr) {
  if (tr.dataset.tag !== 'seen') {
    return 0;
  }
  return parseInt(tr.dataset.rating, 10) || 0.5;
}

var KIND_FILTER_STORAGE_KEY = 'book-kind-filter';

function getKindFilter() {
  var pressed = document.querySelector('.kind-filter-btn[aria-pressed="true"]');
  return pressed ? pressed.dataset.kindFilter : 'all';
}

function setKindFilter(filter) {
  document.querySelectorAll('.kind-filter-btn').forEach(function (btn) {
    btn.setAttribute('aria-pressed', btn.dataset.kindFilter === filter ? 'true' : 'false');
  });
  try {
    if (filter === 'manga' || filter === 'book') {
      localStorage.setItem(KIND_FILTER_STORAGE_KEY, filter);
    } else {
      localStorage.removeItem(KIND_FILTER_STORAGE_KEY);
    }
  } catch (e) {}
}

document.querySelectorAll('.kind-filter-btn').forEach(function (btn) {
  btn.addEventListener('click', function () {
    setKindFilter(btn.dataset.kindFilter);
    applyControls();
  });
});

(function restoreKindFilter() {
  var stored = null;
  try {
    stored = localStorage.getItem(KIND_FILTER_STORAGE_KEY);
  } catch (e) {}
  if (stored === 'manga' || stored === 'book') {
    setKindFilter(stored);
  }
})();

document.querySelectorAll('.filter-btn').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var target = document.getElementById(btn.dataset.target);
    if (!target) {
      return;
    }
    document.querySelectorAll('.book-section').forEach(function (sec) {
      sec.classList.add('hidden');
    });
    target.classList.remove('hidden');
    document.querySelectorAll('.filter-btn').forEach(function (b) {
      b.setAttribute('aria-pressed', 'false');
    });
    btn.setAttribute('aria-pressed', 'true');
  });
});

['search-input', 'price-min', 'price-max'].forEach(function (id) {
  var el = document.getElementById(id);
  if (el) {
    el.addEventListener('input', applyControls);
  }
});
['sort-select', 'ku-only-checkbox'].forEach(function (id) {
  var el = document.getElementById(id);
  if (el) {
    el.addEventListener('change', applyControls);
  }
});

var resetControlsButton = document.getElementById('reset-controls-button');
if (resetControlsButton) {
  resetControlsButton.addEventListener('click', function () {
    document.getElementById('search-input').value = '';
    document.getElementById('sort-select').value = 'default';
    document.getElementById('ku-only-checkbox').checked = false;
    document.getElementById('price-min').value = '';
    document.getElementById('price-max').value = '';
    var tagFilterSelect = document.getElementById('tag-filter-select');
    if (tagFilterSelect) {
      tagFilterSelect.value = 'all';
    }
    setStoredTagFilter('all');
    setKindFilter('all');
    applyControls();
  });
}

applyControls();

var TAG_LABELS = { wanted: '読みたい', unwanted: '読みたくない', purchased: '購入済み', seen: '見た' };
var KIND_LABELS = { manga: 'マンガ', book: '本' };
// タグ・★評価・種別はブラウザに ASIN ごとに保存する（タグのキーは従来どおり book-tag:ASIN）
var MARK_STORAGE_PREFIXES = { tag: 'book-tag:', rating: 'book-rating:', kind: 'book-kind:' };
var MARK_FIELDS = ['tag', 'rating', 'kind'];
var TAG_FILTER_STORAGE_KEY = 'book-tag-filter';
// 同じ本は「全部」と「読みたい」等の複数タブに行があるため、タグの変更は同じ ASIN の全行へ反映する
var rowsByAsin = {};

function getStoredTagFilter() {
  try {
    return localStorage.getItem(TAG_FILTER_STORAGE_KEY) || 'all';
  } catch (e) {
    return 'all';
  }
}

function setStoredTagFilter(filter) {
  try {
    if (filter && filter !== 'all') {
      localStorage.setItem(TAG_FILTER_STORAGE_KEY, filter);
    } else {
      localStorage.removeItem(TAG_FILTER_STORAGE_KEY);
    }
  } catch (e) {}
}

// 最後に押した時刻（ASIN ごと）と、最後に書き出した時刻
var MARK_TIME_PREFIX = 'book-mark-at:';
var MARKS_EXPORTED_AT_KEY = 'book-marks-exported-at';
// 書き出し・取り込みの単位。★は「見た」に付くのでタグと一緒に扱う
var MARK_GROUPS = { tag: ['tag', 'rating'], kind: ['kind'] };
// 各行の公開ページ上の状態（data-saved-*）
var savedByAsin = {};

function readStorage(key) {
  try {
    return localStorage.getItem(key);
  } catch (e) {
    return null;
  }
}

function writeStorage(key, value) {
  try {
    if (value === null) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, value);
    }
  } catch (e) {}
}

// サイトデータがブロックされている等で保存できないブラウザでは、画面上の状態と公開ページの状態の差を書き出す
var canStoreMarks = (function () {
  try {
    localStorage.setItem('book-marks-probe', '1');
    localStorage.removeItem('book-marks-probe');
    return true;
  } catch (e) {
    return false;
  }
})();

function readStoredMark(asin, field) {
  return readStorage(MARK_STORAGE_PREFIXES[field] + asin);
}

// 押した値は、公開ページ（data-saved-*）と同じ値に戻したときも保存する。
// 取り込んでからページを作り直すまでの間は、DB の方がページより新しいことがあるため。
function storeMarks(asin, marks, group) {
  MARK_GROUPS[group].forEach(function (field) {
    writeStorage(MARK_STORAGE_PREFIXES[field] + asin, marks[field]);
  });
  writeStorage(MARK_TIME_PREFIX + asin, String(Date.now()));
}

function clearStoredMarks(asin) {
  MARK_FIELDS.forEach(function (field) {
    writeStorage(MARK_STORAGE_PREFIXES[field] + asin, null);
  });
  writeStorage(MARK_TIME_PREFIX + asin, null);
}

function hasStoredMarks(asin) {
  return MARK_FIELDS.some(function (field) {
    return readStoredMark(asin, field) !== null;
  });
}

function storedAt(asin) {
  return parseInt(readStorage(MARK_TIME_PREFIX + asin), 10) || 0;
}

function lastExportedAt() {
  return parseInt(readStorage(MARKS_EXPORTED_AT_KEY), 10) || 0;
}

function sameMarks(a, b) {
  return a.tag === b.tag && a.rating === b.rating && a.kind === b.kind;
}

function getSavedMarks(tr) {
  var tag = tr.dataset.savedTag || '';
  var rating = tr.dataset.savedRating || '';
  return {
    tag: TAG_LABELS.hasOwnProperty(tag) ? tag : '',
    rating: /^[1-5]$/.test(rating) ? rating : '',
    kind: tr.dataset.savedKind === 'manga' ? 'manga' : 'book'
  };
}

function loadMarks(asin, saved) {
  var marks = { tag: saved.tag, rating: saved.rating, kind: saved.kind };
  if (asin) {
    var tag = readStoredMark(asin, 'tag');
    var rating = readStoredMark(asin, 'rating');
    var kind = readStoredMark(asin, 'kind');
    if (tag !== null && (tag === '' || TAG_LABELS.hasOwnProperty(tag))) {
      marks.tag = tag;
    }
    if (rating !== null && /^[1-5]?$/.test(rating)) {
      marks.rating = rating;
    }
    if (kind === 'manga' || kind === 'book') {
      marks.kind = kind;
    }
  }
  // ★は「見た」の本だけに付く
  if (marks.tag !== 'seen') {
    marks.rating = '';
  }
  return marks;
}

function applyMarks(tr, marks) {
  tr.dataset.tag = marks.tag;
  tr.dataset.rating = marks.rating;
  tr.dataset.kind = marks.kind;
  tr.querySelectorAll('.tag-btn').forEach(function (btn) {
    btn.setAttribute('aria-pressed', btn.dataset.tag === marks.tag ? 'true' : 'false');
  });
  var kindBtn = tr.querySelector('.kind-btn');
  if (kindBtn) {
    var otherKind = marks.kind === 'manga' ? 'book' : 'manga';
    kindBtn.dataset.kind = marks.kind;
    kindBtn.textContent = KIND_LABELS[marks.kind];
    kindBtn.setAttribute('aria-label', '種別: ' + KIND_LABELS[marks.kind] + '（押すと' + KIND_LABELS[otherKind] + 'に切り替え）');
  }
  var ratingGroup = tr.querySelector('.rating-group');
  if (ratingGroup) {
    ratingGroup.classList.toggle('hidden', marks.tag !== 'seen');
    var value = parseInt(marks.rating, 10) || 0;
    ratingGroup.querySelectorAll('.star-btn').forEach(function (star) {
      var n = parseInt(star.dataset.rating, 10);
      star.classList.toggle('is-on', n <= value);
      star.textContent = n <= value ? '★' : '☆';
      star.setAttribute('aria-pressed', n === value ? 'true' : 'false');
    });
  }
}

function currentMarks(tr) {
  return { tag: tr.dataset.tag || '', rating: tr.dataset.rating || '', kind: tr.dataset.kind || 'book' };
}

document.querySelectorAll('.book-table tbody tr.book').forEach(function (tr) {
  var asinEl = tr.querySelector('.col-asin');
  var asin = asinEl ? asinEl.textContent.trim() : '';
  var saved = getSavedMarks(tr);

  var td = document.createElement('td');
  td.className = 'col-tag';

  var kindBtn = document.createElement('button');
  kindBtn.type = 'button';
  kindBtn.className = 'kind-btn';
  td.appendChild(kindBtn);

  var group = document.createElement('div');
  group.className = 'tag-group';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', 'タグ');
  Object.keys(TAG_LABELS).forEach(function (key) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tag-btn tag-btn-' + key;
    btn.textContent = TAG_LABELS[key];
    btn.dataset.tag = key;
    btn.setAttribute('aria-pressed', 'false');
    group.appendChild(btn);
  });
  td.appendChild(group);

  var ratingGroup = document.createElement('div');
  ratingGroup.className = 'rating-group';
  ratingGroup.setAttribute('role', 'group');
  ratingGroup.setAttribute('aria-label', '★評価（同じ★をもう一度押すと取り消し）');
  for (var n = 1; n <= 5; n++) {
    var star = document.createElement('button');
    star.type = 'button';
    star.className = 'star-btn';
    star.dataset.rating = String(n);
    star.setAttribute('aria-label', '★' + n);
    ratingGroup.appendChild(star);
  }
  td.appendChild(ratingGroup);
  tr.appendChild(td);

  var marks = loadMarks(asin, saved);
  applyMarks(tr, marks);
  if (asin) {
    (rowsByAsin[asin] = rowsByAsin[asin] || []).push(tr);
    savedByAsin[asin] = saved;
    // 書き出し済みの変更が公開ページに追いついたら（取り込み・再公開の後）、ブラウザから消す
    if (hasStoredMarks(asin) && sameMarks(marks, saved) && storedAt(asin) <= lastExportedAt()) {
      clearStoredMarks(asin);
    }
  }

  td.addEventListener('click', function (e) {
    // タグ欄内のタップはボタンを外しても Amazon へ遷移させない（小さいボタンの押し損じ対策）
    e.stopPropagation();
    var btn = e.target.closest('button');
    if (!btn) {
      return;
    }
    var next = currentMarks(tr);
    if (btn.classList.contains('tag-btn')) {
      next.tag = next.tag === btn.dataset.tag ? '' : btn.dataset.tag;
      if (next.tag !== 'seen') {
        next.rating = '';
      }
    } else if (btn.classList.contains('star-btn')) {
      next.rating = next.rating === btn.dataset.rating ? '' : btn.dataset.rating;
    } else if (btn.classList.contains('kind-btn')) {
      next.kind = next.kind === 'manga' ? 'book' : 'manga';
    } else {
      return;
    }
    (asin ? rowsByAsin[asin] : [tr]).forEach(function (row) {
      applyMarks(row, next);
    });
    if (asin) {
      storeMarks(asin, next, btn.classList.contains('kind-btn') ? 'kind' : 'tag');
    }
    // 並べ替えで行を入れ直すとフォーカスが外れるため、押したボタンが見えたままなら戻す（キーボード操作向け）
    applyControls();
    updateMarksSummary();
    if (!btn.closest('.hidden')) {
      btn.focus();
    }
  });
  td.addEventListener('keydown', function (e) {
    if (e.target.closest('button')) {
      e.stopPropagation();
    }
  });
});

var tagFilterSelectEl = document.getElementById('tag-filter-select');
if (tagFilterSelectEl) {
  var storedTagFilter = getStoredTagFilter();
  var isKnownTagFilter = Array.prototype.some.call(tagFilterSelectEl.options, function (option) {
    return option.value === storedTagFilter;
  });
  tagFilterSelectEl.value = isKnownTagFilter ? storedTagFilter : 'all';
  tagFilterSelectEl.addEventListener('change', function () {
    setStoredTagFilter(tagFilterSelectEl.value);
    applyControls();
  });
}
// タグは上で各行に付けたので、保存済みのタグ絞り込みを含めてもう一度適用する
applyControls();

// 「見た・評価を書き出す」: ブラウザに保存した変更を JSON ファイルにする。
// PC で `python run.py import-marks <ファイル>` を実行すると DB に蓄積され、ローカル LLM のおすすめ
// （`python run.py recommend`）に使われる。
var MARKS_FILE_FORMAT = 'kindle-marks';

function collectMarks() {
  var summary = { seen: 0, rated: 0, items: [], unexported: 0 };
  var exportedAt = lastExportedAt();
  Object.keys(rowsByAsin).forEach(function (asin) {
    var tr = rowsByAsin[asin][0];
    var marks = currentMarks(tr);
    var saved = savedByAsin[asin];
    if (marks.tag === 'seen') {
      summary.seen++;
      if (marks.rating) {
        summary.rated++;
      }
    }
    // 書き出すのは押した項目だけ（押していない項目は、取り込み時に DB の値のまま残る）
    var touched = Object.keys(MARK_GROUPS).filter(function (group) {
      return MARK_GROUPS[group].some(function (field) {
        return canStoreMarks ? readStoredMark(asin, field) !== null : marks[field] !== saved[field];
      });
    });
    if (touched.length === 0) {
      return;
    }
    var titleEl = tr.querySelector('.col-title');
    var item = { asin: asin, title: titleEl ? titleEl.textContent : '' };
    if (touched.indexOf('tag') !== -1) {
      item.tag = marks.tag;
      item.rating = marks.rating ? parseInt(marks.rating, 10) : null;
    }
    if (touched.indexOf('kind') !== -1) {
      item.kind = marks.kind;
    }
    summary.items.push(item);
    if (!canStoreMarks || storedAt(asin) > exportedAt) {
      summary.unexported++;
    }
  });
  return summary;
}

function updateMarksSummary() {
  var summaryEl = document.getElementById('marks-summary');
  if (!summaryEl) {
    return;
  }
  var summary = collectMarks();
  var text = '見た ' + summary.seen + '件（★評価 ' + summary.rated + '件）・まだ書き出していない変更 ' + summary.unexported + '件';
  if (!canStoreMarks) {
    text += '（このブラウザには保存できないため、ページを閉じる前に書き出してください）';
  }
  summaryEl.textContent = text;
}

function pad2(n) {
  return (n < 10 ? '0' : '') + n;
}

function exportMarks() {
  var statusEl = document.getElementById('export-marks-status');
  var summary = collectMarks();
  var message;
  if (summary.items.length === 0) {
    message = 'ブラウザに保存した変更はありません（表示中のタグ・★・種別は公開ページと同じです）。';
  } else {
    var now = new Date();
    var fileName =
      'kindle-marks-' + now.getFullYear() + pad2(now.getMonth() + 1) + pad2(now.getDate()) +
      '-' + pad2(now.getHours()) + pad2(now.getMinutes()) + '.json';
    var data = { format: MARKS_FILE_FORMAT, version: 1, exported_at: now.toISOString(), items: summary.items };
    var blob = new Blob([JSON.stringify(data, null, 2) + '\n'], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 10000);
    writeStorage(MARKS_EXPORTED_AT_KEY, String(Date.now()));
    updateMarksSummary();
    message =
      fileName + ' を書き出しました（' + summary.items.length + '件）。PC で「python run.py import-marks ファイルのパス」を実行して取り込むと、' +
      '「python run.py recommend」でローカル LLM のおすすめを出せます。';
  }
  if (statusEl) {
    statusEl.textContent = message;
    statusEl.classList.remove('hidden');
  }
}

var exportMarksButton = document.getElementById('export-marks-button');
if (exportMarksButton) {
  exportMarksButton.addEventListener('click', exportMarks);
}
updateMarksSummary();

document.querySelectorAll('.book-table tbody tr.book').forEach(function (tr) {
  var asinEl = tr.querySelector('.col-asin');
  var asin = asinEl ? asinEl.textContent.trim() : '';
  if (!asin) {
    return;
  }
  var url = 'https://www.amazon.co.jp/dp/' + asin;
  tr.tabIndex = 0;
  tr.setAttribute('role', 'link');
  tr.addEventListener('click', function () {
    window.open(url, '_blank', 'noopener,noreferrer');
  });
  tr.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  });
});

// 表紙は ASIN から Amazon の画像 URL を組み立てる。ASIN 形式に一致しない値は URL に埋め込まない。
var ASIN_PATTERN = /^[A-Z0-9]{10}$/;
var COVER_URL_PREFIX = 'https://images-na.ssl-images-amazon.com/images/P/';
var COVER_URL_SUFFIX = '.09.MZZZZZZZ.jpg';

document.querySelectorAll('.book-table tbody tr.book').forEach(function (tr) {
  var asinEl = tr.querySelector('.col-asin');
  var asin = asinEl ? asinEl.textContent.trim() : '';

  var td = document.createElement('td');
  td.className = 'col-cover';
  td.setAttribute('aria-hidden', 'true');
  if (ASIN_PATTERN.test(asin)) {
    var img = document.createElement('img');
    img.alt = '';
    img.loading = 'lazy';
    img.decoding = 'async';
    img.referrerPolicy = 'no-referrer';
    // 画像が無い ASIN では 1x1 の透明画像が返るため、読み込めても空扱いにしてプレースホルダを見せる
    img.addEventListener('load', function () {
      if (img.naturalWidth <= 1) {
        img.remove();
      }
    });
    img.addEventListener('error', function () {
      img.remove();
    });
    img.src = COVER_URL_PREFIX + asin + COVER_URL_SUFFIX;
    td.appendChild(img);
  }
  tr.insertBefore(td, tr.firstChild);
});

var VIEW_STORAGE_KEY = 'book-view';
var VIEW_LABELS = { grid: 'グリッド表示', list: 'リスト表示' };
var VIEW_ICONS = {
  grid: '<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><rect x="1" y="1" width="6" height="6" rx="1"/><rect x="9" y="1" width="6" height="6" rx="1"/><rect x="1" y="9" width="6" height="6" rx="1"/><rect x="9" y="9" width="6" height="6" rx="1"/></svg>',
  list: '<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><rect x="1" y="2" width="14" height="2" rx="1"/><rect x="1" y="7" width="14" height="2" rx="1"/><rect x="1" y="12" width="14" height="2" rx="1"/></svg>'
};

function getStoredView() {
  try {
    return localStorage.getItem(VIEW_STORAGE_KEY) === 'list' ? 'list' : 'grid';
  } catch (e) {
    return 'grid';
  }
}

function setStoredView(view) {
  try {
    localStorage.setItem(VIEW_STORAGE_KEY, view);
  } catch (e) {}
}

function applyView(view) {
  document.body.classList.toggle('view-grid', view === 'grid');
  document.body.classList.toggle('view-list', view === 'list');
  document.querySelectorAll('.view-toggle-btn').forEach(function (btn) {
    btn.setAttribute('aria-pressed', btn.dataset.view === view ? 'true' : 'false');
  });
}

document.querySelectorAll('.book-section .result-count').forEach(function (countEl) {
  var toolbar = document.createElement('div');
  toolbar.className = 'section-toolbar';
  countEl.parentNode.insertBefore(toolbar, countEl);
  toolbar.appendChild(countEl);

  var group = document.createElement('div');
  group.className = 'view-toggle';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', '表示形式');
  Object.keys(VIEW_LABELS).forEach(function (view) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'view-toggle-btn';
    btn.dataset.view = view;
    btn.setAttribute('aria-label', VIEW_LABELS[view]);
    btn.innerHTML = VIEW_ICONS[view];
    btn.addEventListener('click', function () {
      applyView(view);
      setStoredView(view);
    });
    group.appendChild(btn);
  });
  toolbar.appendChild(group);
});

applyView(getStoredView());

"""


def _build_last_scraped_html(books: list) -> str:
    """
    価格を最後に取得（スクレイピング）した日時をタイトル直下に表示する1行を組み立てる。

    専用の実行記録は持たず、各書籍の最新価格の timestamp（repository.get_books() が返す
    price_history の最新1件。save_price_history() が datetime.now().isoformat() で書く
    ローカル時刻）の最大値を最終取得日時とみなす。1冊も価格を取得していなければ「未取得」。
    ISO 形式として解釈できない値はそのまま（エスケープして）表示する。
    """
    timestamps = [str(book["timestamp"]) for book in books if book.get("timestamp")]
    if not timestamps:
        return '<p class="last-scraped">価格の最終取得: 未取得</p>'
    latest = max(timestamps)
    try:
        label = datetime.fromisoformat(latest).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        label = latest
    return (
        f'<p class="last-scraped">価格の最終取得: '
        f'<time datetime="{html.escape(latest, quote=True)}">{html.escape(label)}</time></p>'
    )


def build_html(books: list) -> str:
    """
    書籍一覧を want済み/購入済み/全部の3カテゴリに分けた単一の静的 HTML として生成する
    （React 不要・軽量 JS のみ。filmarks 方式）。

    books の各要素が is_wanted / is_purchased フラグを持つ場合、そのフラグに応じて
    「読みたい」「購入済み」セクションにも同じ本が重複して表示される（「全部」セクションは
    フラグに関わらず常に全件を表示する）。任意で book["price_history"]
    （repository.get_price_history() と同形式の list）を持たせると価格履歴欄に反映される
    （持たない場合は「データなし」になる）。任意で book["mark"]（repository.get_book_marks()
    の1件）を持たせると、取り込み済みのタグ・★評価・種別がページの初期状態になる。

    タイトル・ASIN・価格表示・価格履歴グラフ座標のエスケープ/無害化は
    _build_book_row() / _build_price_history_svg() が担う（R2 対策）。
    """
    wanted_books = [book for book in books if book.get("is_wanted")]
    purchased_books = [book for book in books if book.get("is_purchased")]

    sections_html = "\n".join(
        [
            _build_category_section("section-all", "全部", books, "本はまだ登録されていません。", visible=True),
            _build_category_section(
                "section-wanted", "読みたい", wanted_books, "読みたい本はまだ登録されていません。", visible=False
            ),
            _build_category_section(
                "section-purchased", "購入済み", purchased_books, "購入済みの本はまだ登録されていません。", visible=False
            ),
        ]
    )

    return (
        "<!DOCTYPE html>\n"
        "<!-- このファイルは kindle_system の report.py が生成する。直接編集すると次の自動公開で消えるため、変更は report.py に入れること -->\n"
        '<html lang="ja">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>蔵書リスト</title>\n"
        '<link rel="icon" type="image/svg+xml" href="favicon.svg">\n'
        f"<style>\n{_PAGE_STYLE}</style>\n"
        "</head>\n"
        '<body class="view-grid">\n'
        "<h1>蔵書リスト</h1>\n"
        f"{_build_last_scraped_html(books)}\n"
        f"{_PAGE_HEADER_HTML}\n"
        f"{sections_html}\n"
        "\n"
        f"<script>\n{_PAGE_SCRIPT}</script>\n"
        "</body>\n"
        "</html>\n"
    )


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

    repository.get_books(filter="all") で全件を1回取得した後、各書籍について
    get_price_history(asin) を呼び出し price_history として付与する（1冊ごとに
    問い合わせる形。個人利用規模の蔵書数を前提としており、KISS/YAGNIにより
    一括取得へのバッチ化は現時点では行わない）。
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
    # run.py import-marks で取り込んだ内容を、ページの初期状態として各行に持たせる。
    # 種別（マンガ/本）の上書きは常に載せるが、「見た」・★評価・読みたくない等のタグは読書記録
    # なので、PUBLISH_MARKS=1 のときだけ公開ページに載せる（既定では DB とローカル LLM だけで使う）。
    marks = get_book_marks()
    publish_marks = os.environ.get("PUBLISH_MARKS", "").strip().lower() in ("1", "true", "yes")
    for book in books:
        book["price_history"] = get_price_history(book["asin"])
        mark = marks.get(book["asin"])
        if mark and not publish_marks:
            mark = {"kind": mark.get("kind")}
        book["mark"] = mark
    output_path = os.path.join(public_site_dir, "index.html")
    _write_replacing(output_path, build_html(books))
    # 日本語をエスケープしないのは、公開リポジトリの差分を人が読めるようにするため
    wishlist_path = os.path.join(public_site_dir, "wishlist.json")
    _write_replacing(wishlist_path, json.dumps(build_wishlist(books), ensure_ascii=False, indent=1) + "\n")

    print(f"生成しました: {output_path} / {wishlist_path}（{len(books)} 冊）")


def _write_replacing(path: str, content: str) -> None:
    # 途中中断で壊れたファイルを公開リポジトリに残さないよう、一時ファイルへ
    # 書き出してから置換する（src/repository.py の DB マイグレーションと同じ方式）。
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, path)


if __name__ == "__main__":
    main()
