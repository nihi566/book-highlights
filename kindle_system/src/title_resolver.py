"""
title_resolver.py
------------------
タイトル・著者からAmazon Kindleストア検索を経由してKindle版ASINを自動確定する。

読書メーター経由で取得した本にはASIN情報が無く、サンプルASIN起点の既存パイプライン
（src/resolver.py の resolve_sample_to_paid）には載せられない。本モジュールはそれとは
独立した別経路として、タイトル・著者からKindleストア検索で直接Kindle版ASINを解決する。

該当するKindle版が見つからない場合（紙のみ・検索結果0件・BAN検知・例外発生等）は
必ず None を返す（紙の本や無関係な本のASINを誤って返さない）。

使い方:
    python -c "import asyncio; from src.title_resolver import resolve_title_to_paid_asin; \
        print(asyncio.run(resolve_title_to_paid_asin('<タイトル>', '<著者>')))"
"""

import urllib.parse
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

try:
    from src.resolver import apply_stealth, is_kindle_asin
except ImportError:
    from resolver import apply_stealth, is_kindle_asin

# Amazon.co.jp Kindleストア限定検索（i=digital-text で紙の本を事前除外する）
KINDLE_SEARCH_BASE = "https://www.amazon.co.jp/s?k={query}&i=digital-text"

# 検索結果本体を示すclass名。この要素のタグが閉じるまで（子孫のみ）を走査対象とする。
# 文字列一部一致ではなくタグのclass属性で判定するため、<style>/<script>内の
# 同名文字列やhead側の記述に誤反応しない。
_RESULT_CONTAINER_MARKER = "s-main-slot"

# スポンサー枠であることを示すカードの属性（Amazonの検索結果DOMで実際に使われる印）
_SPONSORED_ATTR_MARKERS = ("sp-sponsored-result", "AdHolder")

# スポンサー枠であることを示すカード内の表示テキスト（属性判定の補助）
_SPONSORED_TEXT_MARKERS = ("スポンサー", "Sponsored")

# HTML5の空要素（終了タグを持たないため、深さ追跡のスタックへ積まない）
_VOID_ELEMENTS = {
    "area", "base", "basefont", "bgsound", "br", "col", "embed", "frame",
    "hr", "img", "input", "keygen", "link", "meta", "param", "source",
    "track", "wbr",
}

# 中身がブラウザによって生テキストとしてそのまま再出力される要素。
# HTMLParser が CDATA 扱いするのは script/style だけなので、これらの中に
# 未閉じタグがあるとタグスタックがずれる（コンテナ終端の誤検知につながる）。
_RAW_TEXT_ELEMENTS = {"noscript", "iframe", "noembed", "noframes"}


class _KindleSearchCardParser(HTMLParser):
    """
    検索結果HTMLをタグのネストを追跡しながら走査し、data-asin属性を持つ
    要素（カード）ごとに「カード内の全テキスト」と「スポンサー枠かどうか」を
    集計する。

    文字列の出現位置（次のdata-asinまで／ページ末尾まで）で範囲を近似する方式は、
    広告カード内の入れ子data-asinやフッター以降の関連商品カルーセルを取りこぼす
    ため、タグの開閉で正しく境界を取る。

    スポンサー印（class="AdHolder" 等）はdata-asinを持つタグ自身ではなく、
    それを包む外側のラッパー要素に付くケースがある。そのため「このタグ自身が
    スポンサー印を持つか」だけでなく「祖先要素がスポンサー印を持つか」を
    スタック越しに継承して判定する（一度スポンサー印の内側に入ったら、
    その配下の要素は data-asin を独自に持っていてもスポンサー扱いとする）。
    class判定はいずれも `class_attr.split()` によるトークン一致とし、
    別クラス名への部分文字列一致（例: "promo-s-main-slotx"）を避ける。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: List[Dict[str, Any]] = []
        self._stack: List[Dict[str, Any]] = []
        self._in_container = False

    def _start(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        is_void = tag in _VOID_ELEMENTS
        class_tokens = (attrs_dict.get("class", "") or "").split()

        is_container_root = False
        if not self._in_container and _RESULT_CONTAINER_MARKER in class_tokens:
            self._in_container = True
            is_container_root = True

        parent_frame = self._stack[-1] if self._stack else None
        parent_sponsored = parent_frame["sponsored"] if parent_frame else False
        own_sponsored = any(marker in class_tokens for marker in _SPONSORED_ATTR_MARKERS)
        is_sponsored = own_sponsored or parent_sponsored

        asin = (attrs_dict.get("data-asin") or "").strip().upper()
        current_card = parent_frame["card"] if parent_frame else None

        new_card = None
        if asin and current_card is None and self._in_container:
            new_card = {"asin": asin, "sponsored": is_sponsored, "text": ""}
            self.cards.append(new_card)
            current_card = new_card
        elif current_card is not None and is_sponsored:
            # カードの子孫要素側にスポンサー印がある場合も、カード自体をスポンサー扱いにする
            current_card["sponsored"] = True

        if not is_void:
            self._stack.append({
                "card": current_card,
                "is_container_root": is_container_root,
                "sponsored": is_sponsored,
            })

    def handle_starttag(self, tag, attrs) -> None:
        self._start(tag, attrs)
        if tag in _RAW_TEXT_ELEMENTS:
            # noscript 等はブラウザが中身を生テキストとして再出力するため、
            # 未閉じタグを含んでいても構造として解釈しない
            self.set_cdata_mode(tag)

    def handle_startendtag(self, tag, attrs) -> None:
        # 自己終端タグ（<div .../> 等）はスタックへの push/pop を発生させない。
        # _start() は非void要素なら push するため、積んだ分をここで戻す。
        depth = len(self._stack)
        self._start(tag, attrs)
        del self._stack[depth:]

    def handle_endtag(self, tag) -> None:
        if not self._stack:
            return
        popped = self._stack.pop()
        if popped.get("is_container_root"):
            self._in_container = False

    def handle_data(self, data: str) -> None:
        if self.lasttag in ("script", "style"):
            # 表示されないテキスト（インラインJSON等）はスポンサー判定に使わない
            return
        if self._stack and self._stack[-1]["card"] is not None:
            self._stack[-1]["card"]["text"] += data


def _is_sponsored_card(card: Dict[str, Any]) -> bool:
    if card["sponsored"]:
        return True
    if any(marker in card["text"] for marker in _SPONSORED_TEXT_MARKERS):
        return True
    return False


def build_kindle_search_url(title: str, author: str = "") -> str:
    """
    タイトル・著者からKindleストア限定検索URLを組み立てる。
    URLインジェクション防止のため urllib.parse.quote で必ずエンコードする。
    """
    query_text = f"{title} {author}".strip() if author else title
    encoded = urllib.parse.quote(query_text)
    return KINDLE_SEARCH_BASE.format(query=encoded)


def parse_kindle_search_results(html: str) -> List[str]:
    """
    Kindleストア検索結果ページのHTMLから、Kindle版ASINの候補を出現順に抽出する。

    タグのネストを追跡し、検索結果本体（class に s-main-slot を含む要素）の
    子孫にあるカードのみを対象にする。本体が特定できない場合は、無関係な
    要素を誤って候補にしないよう候補を1件も返さない（fail-closed）。

    紙の本が検索結果に混在していても is_kindle_asin（B0始まり10桁）による
    検証で除外する。Kindleストア限定検索（i=digital-text）との二重チェックになる。

    さらに、スポンサー枠（属性 sp-sponsored-result / AdHolder、または
    「スポンサー」表示を伴うカード）は除外する。スポンサー枠もKindle形式ASINを
    持つため、is_kindle_asinの検証だけでは検索語と無関係な本を候補にしてしまう。
    """
    parser = _KindleSearchCardParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return []

    if not parser.cards:
        return []

    seen = set()
    candidates: List[str] = []
    for card in parser.cards:
        asin = card["asin"]
        if not is_kindle_asin(asin):
            continue
        if asin in seen:
            continue
        if _is_sponsored_card(card):
            continue
        seen.add(asin)
        candidates.append(asin)
    return candidates


def _pick_asin_from_search(html: str, ban_signal: str) -> Optional[str]:
    """
    検索結果HTMLとBAN検知シグナルから、採用するASINを決定する（副作用なし）。

    - BAN/CAPTCHA等を検知した場合は None
    - 候補が無い場合（検索結果0件・紙の本のみ）も None
    - 候補がある場合は先頭候補を採用する（複雑な類似度スコアリングは行わない）
    """
    if ban_signal != "ok":
        return None
    candidates = parse_kindle_search_results(html)
    return candidates[0] if candidates else None


async def resolve_title_to_paid_asin(
    title: str,
    author: str = "",
    headless: bool = True,
    debug: bool = False,
    browser_profile: Optional[Dict[str, Any]] = None,
    worker_id: int = 0,
    ban_coordinator=None,
    request_pacer=None,
) -> Optional[str]:
    """
    タイトル・著者からKindle版ASINを解決して返す。

    resolve_sample_to_paid（src/resolver.py）と同じ呼び出し形を持つ。
    Kindle版が見つからない場合（紙のみ・検索結果0件・BAN検知・例外発生）は
    必ず None を返す。

    Args:
        title:            書籍タイトル
        author:           著者名（省略可）
        headless:         True でヘッドレス実行（デフォルト）
        debug:            True で詳細ログを出力
        browser_profile:  フィンガープリント設定 dict（None の場合は anti_ban のプロファイルを使用）
        worker_id:        ログ表示用のワーカーID
        ban_coordinator:  BanCoordinator インスタンス（None の場合はBAN検知なし）
        request_pacer:    RequestPacer インスタンス（None の場合はアクセス間隔調整なし）

    Returns:
        Kindle版ASIN文字列、解決できなかった場合は None
    """
    prefix = f"[Worker-{worker_id}]" if worker_id else ""

    if not title or not title.strip():
        if debug:
            print(f"{prefix}タイトルが空のため解決をスキップします")
        return None

    from playwright.async_api import async_playwright

    try:
        from src.anti_ban import check_ban_signals, get_profile
    except ImportError:
        from anti_ban import check_ban_signals, get_profile

    profile = browser_profile or get_profile(worker_id)
    url = build_kindle_search_url(title, author)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=headless,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                context = await browser.new_context(
                    user_agent=profile["user_agent"],
                    viewport=profile["viewport"],
                    locale=profile.get("locale", "ja-JP"),
                    timezone_id=profile.get("timezone_id", "Asia/Tokyo"),
                )
                await context.set_extra_http_headers(profile.get("extra_headers", {}))

                page = await context.new_page()
                await apply_stealth(page)

                if request_pacer is not None:
                    await request_pacer.wait_for_turn(worker_id=worker_id)

                if debug:
                    print(f"{prefix}検索中: {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                ban_signal = "ok"
                if ban_coordinator is not None:
                    ban_signal = await check_ban_signals(page, worker_id=worker_id, debug=debug)
                    if ban_signal != "ok":
                        await ban_coordinator.report_ban(ban_signal, worker_id=worker_id)

                html = await page.content()
                asin = _pick_asin_from_search(html, ban_signal)
                if debug:
                    print(f"{prefix}解決結果: {asin}")
                return asin
            finally:
                await browser.close()
    except Exception as e:
        # debug の設定に関わらず理由を残す（一過性の通信障害とKindle版なしを区別できるように）
        print(f"{prefix}検索エラー: {type(e).__name__}: {e}")
        return None
