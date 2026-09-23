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

    # 折れ線は視覚情報のみのため、スクリーンリーダー向けに要約テキストを role="img" +
    # aria-label で持たせる（数値のみで構成しユーザー由来の自由文字列は含めない）。
    if min_price == max_price:
        summary = f"価格履歴: 変動なし（¥{prices[-1]:,}、{n}件）"
    else:
        summary = f"価格履歴: 最低¥{min_price:,}〜最高¥{max_price:,}、直近¥{prices[-1]:,}（{n}件）"
    aria_label = html.escape(summary)

    return (
        f'<svg class="price-history-svg" role="img" aria-label="{aria_label}" '
        f'viewBox="0 0 {_PRICE_HISTORY_SVG_WIDTH} {_PRICE_HISTORY_SVG_HEIGHT}" '
        f'width="{_PRICE_HISTORY_SVG_WIDTH}" height="{_PRICE_HISTORY_SVG_HEIGHT}">'
        f'<polyline points="{points_attr}" fill="none" stroke="#0074d9" stroke-width="2"/>'
        f"</svg>"
    )


def _build_book_row(book: dict, index: int) -> str:
    """
    1冊分の表の <tr> を組み立てる（R2対策: title/asin/価格表示は html.escape() を通す）。

    book["price_history"] は repository.get_price_history() と同じ形式の list を
    想定する（main() が呼び出し前に付与する。キーが無い/Noneの場合は履歴なし扱い）。

    data-index は「登録順」への並べ替え復元専用（JS側でsortMode==='default'のとき
    この値で再ソートする。並べ替え後にDOM順が入れ替わっても元の順序へ戻せるようにする）。
    """
    title = html.escape(book.get("title") or "(タイトル不明)")
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

    # Amazon商品ページへのリンク（P1: 本を見つけた後、手動でASIN検索しなくて済むように）。
    # asin は上で既にエスケープ済みの値をそのまま使う（URLパス・可視テキストの両方で
    # 同一の値なので二重生成しない）。読書メーター経由でASIN未確定の本は asin が空文字
    # になるため、その場合は壊れたリンク（.../dp/ 単体）を出さずリンク自体を省略する。
    if asin:
        link_html = (
            f'<a href="https://www.amazon.co.jp/dp/{asin}" target="_blank" rel="noopener noreferrer">'
            f'Amazonで見る<span class="visually-hidden">（{title}）</span></a>'
        )
    else:
        link_html = ""

    return (
        f'<tr class="book" data-price="{data_price}" data-ku="{data_ku}" data-index="{index}">'
        f'<td class="col-title">{title}</td>'
        f'<td class="col-asin">{asin}</td>'
        f'<td class="col-price">{price_text}</td>'
        f'<td class="col-history">{history_html}</td>'
        f'<td class="col-link">{link_html}</td>'
        f"</tr>"
    )


def _build_category_section(section_id: str, heading: str, books: list, empty_message: str, visible: bool) -> str:
    """
    section_id / heading は呼び出し元(build_html)が固定リテラルのみを渡す前提
    （ここではエスケープしない。呼び出し元でユーザー由来の値を渡さないこと）。
    heading と empty_message は html.escape() を通す。行の内容は _build_book_row 側でエスケープ済み。
    """
    if books:
        rows_html = "".join(_build_book_row(book, i) for i, book in enumerate(books))
        list_html = (
            f'<p class="result-count" id="count-{section_id}" aria-live="polite"></p>'
            f'<table class="book-table">'
            f'<caption class="visually-hidden">{html.escape(heading)}の蔵書一覧</caption>'
            f"<thead><tr>"
            f'<th scope="col">タイトル</th>'
            f'<th scope="col">ASIN</th>'
            f'<th scope="col">価格</th>'
            f'<th scope="col">価格履歴</th>'
            f'<th scope="col"><span class="visually-hidden">リンク</span></th>'
            f"</tr></thead>"
            f"<tbody>{rows_html}</tbody>"
            f"</table>"
            f'<p class="no-results hidden" role="status">'
            f"条件に一致する本がありません。検索語や価格条件を見直してください。</p>"
        )
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
    '<div class="filter-bar" role="group" aria-label="表示する分類">'
    '<button type="button" class="filter-btn" data-target="section-all" aria-pressed="true">全部</button>'
    '<button type="button" class="filter-btn" data-target="section-wanted" aria-pressed="false">読みたい</button>'
    '<button type="button" class="filter-btn" data-target="section-purchased" aria-pressed="false">購入済み</button>'
    "</div>"
)

# 検索・並べ替え・詳細フィルタ（KU対象のみ / 価格帯）用のコントロール群。
# 値はすべて固定リテラルの id/属性のみで、ユーザー由来の値は含まない。
_CONTROLS_BAR_HTML = (
    '<div class="controls-bar">'
    '<label class="visually-hidden" for="search-input">タイトル・ASINで検索</label>'
    '<input type="search" id="search-input" class="search-input" placeholder="タイトル・ASINで検索" aria-label="タイトル・ASINで検索">'
    '<label class="visually-hidden" for="sort-select">並べ替え</label>'
    '<select id="sort-select" class="sort-select" aria-label="並べ替え">'
    '<option value="default">登録順</option>'
    '<option value="price-asc">価格が安い順</option>'
    '<option value="price-desc">価格が高い順</option>'
    '<option value="title-asc">タイトル順</option>'
    "</select>"
    '<label class="ku-filter"><input type="checkbox" id="ku-only-checkbox"> Kindle Unlimited対象のみ</label>'
    '<div class="price-range">'
    '<label class="visually-hidden" for="price-min">価格下限</label>'
    '<input type="number" id="price-min" class="price-input" placeholder="下限" min="0" inputmode="numeric" aria-label="価格下限">'
    '<span aria-hidden="true">〜</span>'
    '<label class="visually-hidden" for="price-max">価格上限</label>'
    '<input type="number" id="price-max" class="price-input" placeholder="上限" min="0" inputmode="numeric" aria-label="価格上限">'
    "</div>"
    "</div>"
)

_FILTER_TOGGLE_SCRIPT = """<script>
function applyControls() {
  var searchInput = document.getElementById('search-input');
  var sortSelect = document.getElementById('sort-select');
  var kuOnlyCheckbox = document.getElementById('ku-only-checkbox');
  var priceMinInput = document.getElementById('price-min');
  var priceMaxInput = document.getElementById('price-max');
  if (!searchInput || !sortSelect || !kuOnlyCheckbox || !priceMinInput || !priceMaxInput) {
    return;
  }

  var searchTerm = searchInput.value.trim().toLowerCase();
  var sortMode = sortSelect.value;
  var kuOnly = kuOnlyCheckbox.checked;
  var priceMin = parseFloat(priceMinInput.value);
  var priceMax = parseFloat(priceMaxInput.value);

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
      if (searchTerm && title.indexOf(searchTerm) === -1 && asin.indexOf(searchTerm) === -1) {
        visible = false;
      }
      if (kuOnly && !isKu) {
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

    // sortMode==='default' のときも常に並べ直すことで、一度他の並べ替えへ変えた後に
    // 「登録順」へ戻した際、DOM順が変わったままにならないようにする（元の順序へ復元する）。
    var sorted = items.slice().sort(function (a, b) {
      if (sortMode === 'default') {
        return parseInt(a.dataset.index, 10) - parseInt(b.dataset.index, 10);
      }
      if (sortMode === 'title-asc') {
        var titleA = a.querySelector('.col-title');
        var titleB = b.querySelector('.col-title');
        return (titleA ? titleA.textContent : '').localeCompare(titleB ? titleB.textContent : '', 'ja');
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

applyControls();
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>蔵書リスト</title>
<style>
  :root {{
    --color-bg: #f7f7f9;
    --color-surface: #fff;
    --color-text: #222;
    --color-muted: #595959;
    --color-accent: #0074d9;
    --color-price: #d1401f;
    --color-border: #ccc;
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
  }}
  body {{ font-family: -apple-system, "Hiragino Sans", "Yu Gothic", sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 var(--space-xl); background: var(--color-bg); color: var(--color-text); }}
  h1 {{ margin-bottom: var(--space-xl); }}
  .visually-hidden {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }}
  .result-count {{ color: var(--color-muted); font-size: var(--font-size-sm); margin: 0 0 var(--space-sm); }}
  .book-table {{ width: 100%; border-collapse: collapse; background: var(--color-surface); border-radius: var(--radius-md); overflow: hidden; box-shadow: var(--shadow-sm); }}
  .book-table caption {{ text-align: left; }}
  .book-table th, .book-table td {{ padding: var(--space-md) var(--space-lg); text-align: left; border-bottom: 1px solid var(--color-border); vertical-align: middle; }}
  .book-table th {{ font-size: var(--font-size-sm); color: var(--color-muted); font-weight: 600; }}
  .book-table tbody tr:last-child td {{ border-bottom: none; }}
  .book-table tbody tr:hover {{ background: #f0f4f9; }}
  .col-title {{ font-weight: 600; }}
  .col-asin {{ color: var(--color-muted); font-size: var(--font-size-sm); white-space: nowrap; }}
  .col-price {{ font-weight: bold; white-space: nowrap; color: var(--color-price); }}
  .col-history {{ color: var(--color-muted); font-size: var(--font-size-sm); white-space: nowrap; }}
  .col-link a {{ white-space: nowrap; }}
  .empty {{ color: var(--color-muted); }}
  .price-history-svg {{ display: block; }}
  .price-history-empty {{ color: var(--color-muted); font-size: var(--font-size-sm); }}
  .no-results {{ color: var(--color-muted); padding: var(--space-lg); text-align: center; }}
  .hidden {{ display: none; }}
  .filter-bar {{ display: flex; gap: var(--space-sm); margin-bottom: var(--space-lg); }}
  .filter-btn {{ padding: var(--space-sm) 0.8rem; cursor: pointer; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-surface); }}
  .filter-btn[aria-pressed="true"] {{ font-weight: bold; color: #fff; background: var(--color-accent); border-color: var(--color-accent); }}
  .controls-bar {{ display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-md); margin-bottom: var(--space-2xl); padding: var(--space-lg); background: var(--color-surface); border-radius: var(--radius-md); box-shadow: var(--shadow-sm); }}
  .search-input {{ flex: 1 1 200px; padding: var(--space-sm) var(--space-md); border: 1px solid var(--color-border); border-radius: var(--radius-sm); }}
  .sort-select {{ padding: var(--space-sm) var(--space-md); border: 1px solid var(--color-border); border-radius: var(--radius-sm); }}
  .ku-filter {{ display: flex; align-items: center; gap: 0.3rem; white-space: nowrap; font-size: 0.9rem; }}
  .price-range {{ display: flex; align-items: center; gap: var(--space-sm); }}
  .price-input {{ width: 5rem; padding: var(--space-sm) var(--space-md); border: 1px solid var(--color-border); border-radius: var(--radius-sm); }}
  @media (max-width: 480px) {{
    .controls-bar {{ flex-direction: column; align-items: stretch; }}
    .price-input {{ width: auto; flex: 1; }}
    .book-table thead {{ display: none; }}
    .book-table, .book-table tbody, .book-table tr, .book-table td {{ display: block; width: 100%; }}
    .book-table tr {{ border-bottom: 1px solid var(--color-border); padding: var(--space-md) 0; }}
    .book-table td {{ border-bottom: none; padding: var(--space-xs) 0; }}
    .col-asin::before {{ content: "ASIN: "; }}
    .col-history::before {{ content: "価格履歴: "; }}
    .book-table .hidden,
    .book-table.hidden {{ display: none; }}
  }}
</style>
</head>
<body>
<h1>蔵書リスト</h1>
{_FILTER_BAR_HTML}
{_CONTROLS_BAR_HTML}
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
