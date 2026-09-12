"""
report.py
---------
「読みたい本」（book_mappings.is_wanted = 1）を GitHub Pages 公開用の静的 HTML として
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

from src.repository import get_wanted_books


def _format_price(book: dict) -> str:
    """actual_price があればそれを整形して返し、無ければ「価格情報なし」を返す（R5 対策）。"""
    actual_price = book.get("actual_price")
    if actual_price is None:
        return "価格情報なし"
    return f"¥{actual_price:,}"


def build_html(books: list) -> str:
    """
    読みたい本一覧を単一の静的 HTML として生成する（React 不要・軽量 JS のみ。filmarks 方式）。

    全出力値は html.escape() でエスケープする（R2: タイトル等に <script> が含まれても
    HTML インジェクションにならないようにするため）。
    """
    rows = []
    for book in books:
        title = html.escape(book.get("title") or "(タイトル不明)")
        asin = html.escape(book.get("asin") or "")
        price_text = html.escape(_format_price(book))
        rows.append(
            f'<li class="book">'
            f'<span class="title">{title}</span>'
            f'<span class="asin">{asin}</span>'
            f'<span class="price">{price_text}</span>'
            f"</li>"
        )

    if rows:
        list_html = "<ul>" + "".join(rows) + "</ul>"
    else:
        list_html = '<p class="empty">読みたい本はまだ登録されていません。</p>'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>読みたい本リスト</title>
<style>
  body {{ font-family: sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; }}
  ul {{ list-style: none; padding: 0; margin: 0; }}
  .book {{ display: flex; justify-content: space-between; gap: 1rem; padding: 0.5rem 0; border-bottom: 1px solid #ddd; }}
  .title {{ flex: 1; }}
  .asin {{ color: #888; font-size: 0.8rem; }}
  .price {{ font-weight: bold; white-space: nowrap; }}
  .empty {{ color: #888; }}
</style>
</head>
<body>
<h1>読みたい本リスト</h1>
{list_html}
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

    books = get_wanted_books()
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
