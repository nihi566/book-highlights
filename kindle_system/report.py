"""
report.py
---------
蔵書一覧（読みたい本 / 購入済み本 / 全部）と価格履歴を GitHub Pages 公開用の静的 HTML として
書き出すバッチスクリプト。filmarks_scraper の reporter.py 相当（単一 HTML・軽量 JS のみ・
React 不要）。

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

# Windows CP932 環境での文字化け防止（main.py と同じ対処）
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from src.repository import get_books, get_price_history


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
    価格履歴（repository.get_price_history() の返り値形式）からインライン SVG の
    折れ線グラフを組み立てる（外部グラフライブラリ不使用。R1/R3/R4 対策）。

    is_unlimited=1 の点は src/crawler.py の KU 安全弁により価格が常に 0 で保存される
    ため、折れ線の座標計算から除外する（R4）。有効な価格点が2点未満の場合は
    プレースホルダー文言を返す（R3）。座標は数値のみで構成し、ユーザー由来の
    自由文字列を一切埋め込まない（R1: エスケープ対象自体が存在しない設計）。
    """
    valid_points = [
        point
        for point in history
        if not point.get("is_unlimited") and point.get("actual_price") is not None
    ]
    if len(valid_points) < 2:
        return '<p class="price-history-empty">価格履歴データなし</p>'

    prices = [point["actual_price"] for point in valid_points]
    min_price = min(prices)
    max_price = max(prices)
    n = len(valid_points)

    coords = []
    for i, price in enumerate(prices):
        x = i * (_PRICE_HISTORY_SVG_WIDTH / (n - 1))
        if max_price == min_price:
            y = _PRICE_HISTORY_SVG_HEIGHT / 2
        else:
            y = _PRICE_HISTORY_SVG_HEIGHT - (
                (price - min_price) / (max_price - min_price) * _PRICE_HISTORY_SVG_HEIGHT
            )
        coords.append(f"{x:.1f},{y:.1f}")
    points_attr = " ".join(coords)

    return (
        f'<svg class="price-history-svg" viewBox="0 0 {_PRICE_HISTORY_SVG_WIDTH} {_PRICE_HISTORY_SVG_HEIGHT}" '
        f'width="{_PRICE_HISTORY_SVG_WIDTH}" height="{_PRICE_HISTORY_SVG_HEIGHT}">'
        f'<polyline points="{points_attr}" fill="none" stroke="#0074d9" stroke-width="2"/>'
        f"</svg>"
    )


def _build_book_row(book: dict) -> str:
    """
    1冊分の <li> を組み立てる（R2対策: title/asin/価格表示は html.escape() を通す）。

    book["price_history"] は repository.get_price_history() と同じ形式の list を
    想定する（main() が呼び出し前に付与する。キーが無い/Noneの場合は履歴なし扱い）。
    """
    title = html.escape(book.get("title") or "(タイトル不明)")
    asin = html.escape(book.get("asin") or "")
    price_text = html.escape(_format_price(book))
    history_svg = _build_price_history_svg(book.get("price_history") or [])
    return (
        f'<li class="book">'
        f'<span class="title">{title}</span>'
        f'<span class="asin">{asin}</span>'
        f'<span class="price">{price_text}</span>'
        f'<div class="price-history">{history_svg}</div>'
        f"</li>"
    )


def _build_category_section(section_id: str, heading: str, books: list, empty_message: str, visible: bool) -> str:
    """
    section_id / heading は呼び出し元(build_html)が固定リテラルのみを渡す前提
    （ここではエスケープしない。呼び出し元でユーザー由来の値を渡さないこと）。
    heading と empty_message は html.escape() を通す。行の内容は _build_book_row 側でエスケープ済み。
    """
    if books:
        list_html = "<ul>" + "".join(_build_book_row(book) for book in books) + "</ul>"
    else:
        list_html = f'<p class="empty">{html.escape(empty_message)}</p>'
    section_class = "book-section" if visible else "book-section hidden"
    return (
        f'<section id="{section_id}" class="{section_class}" data-category="{section_id}">'
        f"<h2>{html.escape(heading)}</h2>"
        f"{list_html}"
        f"</section>"
    )


_FILTER_BAR_HTML = (
    '<div class="filter-bar">'
    '<button type="button" class="filter-btn active" data-target="section-all">全部</button>'
    '<button type="button" class="filter-btn" data-target="section-wanted">読みたい</button>'
    '<button type="button" class="filter-btn" data-target="section-purchased">購入済み</button>'
    "</div>"
)

_FILTER_TOGGLE_SCRIPT = """<script>
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
      b.classList.remove('active');
    });
    btn.classList.add('active');
  });
});
</script>"""


def build_html(books: list) -> str:
    """
    書籍一覧を want済み/購入済み/全部の3カテゴリに分けた単一の静的 HTML として生成する
    （React 不要・軽量 JS のみ。filmarks 方式）。

    books の各要素が is_wanted / is_purchased フラグを持つ場合、そのフラグに応じて
    「読みたい」「購入済み」セクションにも同じ本が重複して表示される（「全部」セクションは
    フラグに関わらず常に全件を表示する）。任意で book["price_history"]
    （repository.get_price_history() と同形式の list）を持たせると価格履歴グラフが描画される
    （持たない場合はプレースホルダー表示になる）。

    タイトル・ASIN・価格表示・価格履歴グラフ座標のエスケープ/無害化は
    _build_book_row() / _build_price_history_svg() が担う（R2 対策）。
    """
    wanted_books = [book for book in books if book.get("is_wanted")]
    purchased_books = [book for book in books if book.get("is_purchased")]

    sections_html = (
        _build_category_section("section-all", "全部", books, "本はまだ登録されていません。", visible=True)
        + _build_category_section(
            "section-wanted", "読みたい", wanted_books, "読みたい本はまだ登録されていません。", visible=False
        )
        + _build_category_section(
            "section-purchased", "購入済み", purchased_books, "購入済みの本はまだ登録されていません。", visible=False
        )
    )

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>蔵書リスト</title>
<style>
  body {{ font-family: sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; }}
  ul {{ list-style: none; padding: 0; margin: 0; }}
  .book {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 1rem; padding: 0.5rem 0; border-bottom: 1px solid #ddd; }}
  .title {{ flex: 1; }}
  .asin {{ color: #888; font-size: 0.8rem; }}
  .price {{ font-weight: bold; white-space: nowrap; }}
  .empty {{ color: #888; }}
  .price-history {{ width: 100%; }}
  .price-history-svg {{ display: block; margin-top: 0.25rem; }}
  .price-history-empty {{ color: #888; font-size: 0.75rem; }}
  .hidden {{ display: none; }}
  .filter-bar {{ display: flex; gap: 0.5rem; margin-bottom: 1rem; }}
  .filter-btn {{ padding: 0.4rem 0.8rem; cursor: pointer; }}
  .filter-btn.active {{ font-weight: bold; border-bottom: 2px solid #0074d9; }}
</style>
</head>
<body>
<h1>蔵書リスト</h1>
{_FILTER_BAR_HTML}
{sections_html}
{_FILTER_TOGGLE_SCRIPT}
</body>
</html>
"""


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
    for book in books:
        book["price_history"] = get_price_history(book["asin"])
    html_content = build_html(books)

    # 途中中断で壊れた index.html を公開リポジトリに残さないよう、一時ファイルへ
    # 書き出してから置換する（src/repository.py の DB マイグレーションと同じ方式）。
    output_path = os.path.join(public_site_dir, "index.html")
    tmp_path = output_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    os.replace(tmp_path, output_path)

    print(f"生成しました: {output_path}（{len(books)} 冊）")


if __name__ == "__main__":
    main()
