"""
bookmeter.py
------------
読書メーター「読みたい本」一覧（公開本棚のみ対応）をページングしながら
全件取得し、[{title, author}, ...] 形式で返す。

対象: https://bookmeter.com/users/1770332/books/wish
非公開本棚・ログイン必須ページへの対応、Amazonリンクの解決、既存の取り込み
フロー（main.py / DB）への統合は本モジュールのスコープ外。

使い方:
    python -m src.bookmeter          (実際に読書メーターへアクセスして取得)
    python -m src.bookmeter --test   (内蔵モックHTMLを使った動作確認)
"""

import argparse
import io
import logging
import sys
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Windows CP932 環境での文字化け防止
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logger = logging.getLogger(__name__)

WISH_LIST_URL = "https://bookmeter.com/users/1770332/books/wish"

# Chrome 安定版相当の User-Agent（resolver.py と同一の値に揃える）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT_SEC = 10
REQUEST_INTERVAL_SEC = 1.5
# ページング終了条件の誤判定（無限ループ化）を防ぐ安全上限
MAX_PAGES = 100


def parse_books(html: str) -> List[Dict[str, str]]:
    """
    1ページ分のHTMLから書籍タイトル・著者を抽出する。

    個別書籍のパース失敗（HTML構造の変更等）は当該書籍のみスキップし、
    ページ全体の処理は継続する。
    """
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("li.group__book")

    if not items:
        # 書籍要素が1件も見つからない場合、そのページに本当に本が無いのか、
        # HTML構造の変更・非公開本棚化等で解析できていないのかをログから
        # 判別できるようにする（無音のまま空リストにしない）
        logger.warning(
            "書籍要素（li.group__book）が見つかりませんでした。"
            "対象ページが空か、HTML構造の変更・非公開化の可能性があります"
        )
        return []

    books: List[Dict[str, str]] = []
    for item in items:
        try:
            title_el = item.select_one("div.detail__title a")
            if title_el is None:
                logger.warning("タイトル要素が見つからないため1件スキップします")
                continue
            title = _full_title(item, title_el.get_text(strip=True))
            authors = [
                a.get_text(strip=True)
                for a in item.select("ul.detail__authors li a")
            ]
            if not authors:
                logger.warning("著者要素が見つかりませんでした（title=%s）", title)
            books.append({"title": title, "author": "、".join(authors)})
        except Exception:
            logger.warning("書籍1件の解析に失敗したためスキップします", exc_info=True)
            continue

    return books


TRUNCATION_MARK = "…"


def _full_title(item, link_text: str) -> str:
    """
    一覧の書名リンクは長い書名を末尾「…」で切って表示するので、表紙画像の alt
    （切れていない書名）があればそちらを使う。alt が書名の続きでない（別の文言・空）
    ときはリンクの文字列のまま返す。
    """
    if not link_text.endswith(TRUNCATION_MARK):
        return link_text
    img = item.select_one("div.thumbnail__cover img")
    alt = (img.get("alt") or "").strip() if img is not None else ""
    prefix = link_text[: -len(TRUNCATION_MARK)].rstrip()
    if alt and alt.startswith(prefix) and len(alt) > len(prefix):
        return alt
    return link_text


def get_next_page_url(html: str, current_url: str) -> Optional[str]:
    """
    次ページへのURLを返す。次ページが存在しない（最終ページ）場合は None を返す。

    読書メーターの一覧ページは最終ページで「次」リンクが無効化され、
    rel="next" を持つ <a> ではなく <div> になる。
    """
    soup = BeautifulSoup(html, "html.parser")
    # rel は複数値属性になりうる（例: rel="next nofollow"）ため完全一致(=)ではなく
    # トークン一致(~=)を使う。完全一致だと多値のrelを持つ次ページが検出できず、
    # 1ページ目で正常終了したように見えてしまう
    next_link = soup.select_one('a[rel~="next"]')
    if next_link is None:
        return None
    href = next_link.get("href")
    if not href:
        return None
    return urljoin(current_url, href)


def fetch_wish_books(
    base_url: str = WISH_LIST_URL, max_pages: int = MAX_PAGES
) -> List[Dict[str, str]]:
    """
    読書メーター「読みたい本」一覧を全ページ取得する。

    ネットワークエラー（接続エラー・タイムアウト・HTTPエラー）が発生した場合は
    例外を送出せず、ログを出力したうえで安全に終了する。1ページ目で発生した
    場合は空リストを返す。2ページ目以降で発生した場合は、それまでに取得済み
    のページ分は破棄せずに返す（取得できた分まで無かったことにはしない）。
    """
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    books: List[Dict[str, str]] = []
    url: Optional[str] = base_url
    page_count = 0

    while url and page_count < max_pages:
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT_SEC)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error("読書メーターへのアクセスに失敗しました (%s): %s", url, e)
            return books

        # Content-Type に charset が無い応答は requests が ISO-8859-1 と誤判定する
        # ことがあり、その場合 response.text の日本語が無音で文字化けする
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"

        books.extend(parse_books(response.text))
        page_count += 1

        next_url = get_next_page_url(response.text, url)
        if next_url is None or next_url == url:
            return books
        url = next_url

        if page_count < max_pages:
            time.sleep(REQUEST_INTERVAL_SEC)

    if url:
        # 安全上限に達しての打ち切り。まだ次ページが残っているため結果は不完全
        logger.warning(
            "安全上限 %d ページに達したため打ち切りました（未取得のページが残っています）",
            max_pages,
        )

    return books


# ─── 内蔵モックHTMLによる自己検証（--test） ───────────────────────────────────

_MOCK_PAGE_1 = """
<ul class="book-list__group">
  <li class="group__book">
    <div class="book__detail">
      <div class="detail__title"><a href="/books/1">モック書籍A</a></div>
      <ul class="detail__authors"><li><a href="/search?author=x">モック著者A</a></li></ul>
    </div>
  </li>
</ul>
<ul class="bm-pagination">
  <li><a rel="next" class="bm-pagination__link" href="/users/1770332/books/wish?page=2">次</a></li>
</ul>
"""

_MOCK_PAGE_2 = """
<ul class="book-list__group">
  <li class="group__book">
    <div class="book__detail">
      <div class="detail__title"><a href="/books/2">モック書籍B</a></div>
      <ul class="detail__authors"><li><a href="/search?author=y">モック著者B</a></li></ul>
    </div>
  </li>
</ul>
<ul class="bm-pagination">
  <li class="disable"><div class="bm-pagination__link">次</div></li>
</ul>
"""


def _run_builtin_test() -> None:
    """モックHTMLを使い、実ネットワークに接続せず基本動作を確認する。"""
    from unittest.mock import MagicMock, patch

    def fake_get(url, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.text = _MOCK_PAGE_1 if "page=2" not in url else _MOCK_PAGE_2
        return resp

    print("=" * 60)
    print("  bookmeter.py 内蔵テスト（モックHTML使用・実ネットワーク接続なし）")
    print("=" * 60)

    # requests.Session.get / time.sleep をグローバルにパッチする（本モジュールが
    # "src.bookmeter" / "bookmeter" / "__main__" のいずれとして読み込まれても
    # 同じ requests/time モジュールを参照するため、実行方法に依存せず動作する）
    with patch("requests.Session.get", side_effect=fake_get), \
         patch("time.sleep", return_value=None):
        books = fetch_wish_books()

    passed = True
    if books != [
        {"title": "モック書籍A", "author": "モック著者A"},
        {"title": "モック書籍B", "author": "モック著者B"},
    ]:
        print(f"  [NG] 取得結果が期待値と一致しません: {books}")
        passed = False
    else:
        print(f"  [OK] 2ページ分の書籍を取得: {books}")

    print("-" * 60)
    print("  >>> 全テスト PASSED <<<" if passed else "  >>> テスト FAILED <<<")
    if not passed:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="読書メーター「読みたい本」一覧を取得します。"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="内蔵モックHTMLを使った動作確認を行う（実ネットワーク接続なし）",
    )
    args = parser.parse_args()

    if args.test:
        _run_builtin_test()
        return

    books = fetch_wish_books()
    print(f"取得件数: {len(books)}")
    for book in books:
        print(f"  - {book['title']} / {book['author']}")


if __name__ == "__main__":
    main()
