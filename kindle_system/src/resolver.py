"""
resolver.py
-----------
Kindle サンプル本の ASIN（EBSP）を Amazon.co.jp の商品ページから解析し、
有料本編（EBOK）の ASIN に変換する。

使い方:
    python resolver.py B0GGY819NL
    python resolver.py B0GGY819NL --visible     (ブラウザを表示して実行)
    python resolver.py B0GGY819NL --debug       (デバッグ情報を出力)
"""

import asyncio
import re
import random
import sys
import io
import argparse
from typing import Optional, Dict, Any

# Windows CP932 環境での文字化け防止
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── 定数 ──────────────────────────────────────────────────────────────────────
AMAZON_BASE = "https://www.amazon.co.jp/dp/{asin}"
ASIN_RE     = re.compile(r"(?:dp|gp/product)/([A-Z0-9]{10})")

# Chrome 安定版相当の User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# B0 で始まる Kindle 電子書籍 ASIN のパターン
KINDLE_ASIN_RE = re.compile(r'^B0[A-Z0-9]{8}$')

# ─── ユーティリティ ────────────────────────────────────────────────────────────

def extract_asin_from_url(url: str) -> Optional[str]:
    """URL 文字列から 10 桁の ASIN を抽出する。"""
    m = ASIN_RE.search(url)
    return m.group(1) if m else None


def is_kindle_asin(asin: str) -> bool:
    """
    ASIN が Kindle 電子書籍のものかバリデーションする。
    Kindle EBOK の ASIN は 'B0' で始まる 10 桁英数字。
    例: B0GGY819NL -> True / 4845925230 -> False
    """
    return bool(KINDLE_ASIN_RE.match(asin))


async def random_delay(min_sec: float = 1.0, max_sec: float = 3.0) -> None:
    """Bot 検知回避用のランダムウェイト。"""
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)


# ─── ステルス設定 ──────────────────────────────────────────────────────────────

async def apply_stealth(page) -> None:
    """
    playwright-stealth を適用し、追加の JS パッチも注入する。
    WebDriver フラグや Chrome automation 属性を隠蔽。
    """
    try:
        from playwright_stealth import stealth_async
        await stealth_async(page)
    except ImportError:
        pass  # ライブラリ未導入時は JS パッチのみで対応

    # 追加パッチ: navigator.webdriver を undefined に
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['ja-JP', 'ja', 'en-US', 'en'],
        });
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32',
        });
        // Chrome runtime を偽装
        window.chrome = { runtime: {} };
        // permissions.query のオーバーライド
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);
    """)


# ─── 本編 ASIN 抽出ロジック ────────────────────────────────────────────────────

async def route_a_canonical(page, sample_asin: str, debug: bool = False) -> Optional[str]:
    """
    ルートA: <link rel="canonical"> の href から ASIN を抽出。

    Amazon では「サンプル本（EBSP）」と「有料本編（EBOK）」が同一の ASIN・同一ページ
    を共有するケースがある。その場合 canonical URL は自分自身を指す。
    -> このとき canonical の ASIN == sample_asin であっても、
       それが有料本編の ASIN として正解なので返す。

    Kindle ASIN（B0 始まり）のみを有効とする。
    """
    try:
        canonical = await page.get_attribute('link[rel="canonical"]', "href")
        if canonical:
            if debug:
                print(f"  [RouteA] canonical URL: {canonical}")
            asin = extract_asin_from_url(canonical)
            if asin and is_kindle_asin(asin):
                if debug and asin == sample_asin:
                    print(f"  [RouteA] canonical ASIN = サンプル自身 ({asin}) -> 同一ページ型: 本編 ASIN として採用")
                return asin
            elif asin and debug:
                print(f"  [RouteA] ASIN={asin} は Kindle 形式でないためスキップ")
    except Exception as e:
        if debug:
            print(f"  [RouteA] エラー: {e}")
    return None


async def route_b_edition_links(page, sample_asin: str, debug: bool = False) -> Optional[str]:
    """
    ルートB: フォーマット切り替え（tmmSwatches）から「Kindle版」のASINのみを抽出。

    戦略1: JS評価で tmm-grid-swatch-{ASIN} 形式のIDリストから直接ASIN取得
    戦略2: 旧UI (#tmmSwatches li) でKindle版テキストを確認してhrefから取得
    ※ページ全体スキャン（[data-asin]）は誤検出を招くため使用しない
    """

    # ── 戦略1: JS で swatch 要素の id 属性から ASIN を一括取得 ────────────────
    # Amazon の新UIでは <li id="tmm-grid-swatch-0-{ASIN}"> 形式になっている
    try:
        swatch_info = await page.evaluate("""
            () => {
                const results = [];
                const items = document.querySelectorAll("li[id*='tmm-grid-swatch']");
                items.forEach(item => {
                    const id   = item.id || "";
                    const text = item.innerText || "";
                    const link = item.querySelector("a[href*='/dp/']");
                    const href = link ? link.href : "";
                    results.push({ id, text, href });
                });
                return results;
            }
        """)

        if debug:
            print(f"  [RouteB] JS swatch 取得: {len(swatch_info)} 件")

        for info in swatch_info:
            elem_id = info.get("id", "")
            text    = info.get("text", "").strip()
            href    = info.get("href", "")

            if debug:
                print(f"  [RouteB]   id={elem_id!r}, text={text[:50]!r}")

            # id から ASIN を取り出す（tmm-grid-swatch-0-B0XXXXXXXX 形式）
            asin_from_id = None
            for part in reversed(elem_id.split("-")):
                if is_kindle_asin(part.upper()):
                    asin_from_id = part.upper()
                    break

            # href から ASIN を取り出す
            asin_from_href = extract_asin_from_url(href) if href else None
            if asin_from_href and not is_kindle_asin(asin_from_href):
                asin_from_href = None  # 紙本など非Kindleは除外

            # テキストに「Kindle」が含まれる要素のみ有効
            has_kindle_text = "Kindle" in text or "kindle" in text.lower()
            if not has_kindle_text:
                if debug and (asin_from_id or asin_from_href):
                    print(f"  [RouteB]   -> 'Kindle'テキストなし、スキップ")
                continue

            asin = asin_from_id or asin_from_href
            if not asin:
                continue

            if asin == sample_asin:
                if debug:
                    print(f"  [RouteB]   -> サンプル自身のためスキップ ({asin})")
                continue

            if debug:
                print(f"  [RouteB]   -> 本編 Kindle ASIN 確定: {asin}")
            return asin

    except Exception as e:
        if debug:
            print(f"  [RouteB] 戦略1エラー: {e}")

    # ── 戦略2: 旧UI (#tmmSwatches li) でKindle版テキストを確認 ────────────────
    try:
        swatch_items = await page.query_selector_all("#tmmSwatches li")
        if debug and swatch_items:
            print(f"  [RouteB] 旧UI swatch: {len(swatch_items)} 件")

        for item in swatch_items:
            text = (await item.inner_text()).strip()

            if debug:
                print(f"  [RouteB]   旧UI テキスト: {text[:60]!r}")

            if "Kindle" not in text and "kindle" not in text.lower():
                continue

            link = await item.query_selector("a[href*='/dp/']")
            if link:
                href = await link.get_attribute("href") or ""
                asin = extract_asin_from_url(href)
                if asin and is_kindle_asin(asin) and asin != sample_asin:
                    if debug:
                        print(f"  [RouteB]   旧UI -> 本編 ASIN: {asin}")
                    return asin

    except Exception as e:
        if debug:
            print(f"  [RouteB] 戦略2エラー: {e}")

    return None


async def route_c_page_source(page, sample_asin: str, debug: bool = False) -> Optional[str]:
    """
    ルートC（最終フォールバック）: ページソース全体から ASIN を正規表現で抽出。
    B0 始まりの Kindle ASIN のみを対象とし、最頻出のものを返す。
    """
    try:
        content = await page.content()

        # ページ内の全 /dp/ASIN パターンを収集（B0 始まりのみ）
        all_asins = re.findall(r'/dp/([A-Z0-9]{10})', content)
        from collections import Counter
        counter = Counter(
            asin for asin in all_asins if is_kindle_asin(asin)
        )

        if debug:
            print(f"  [RouteC] Kindle ASIN 候補一覧: {dict(counter.most_common(5))}")

        # サンプル自身を除外した最頻出 Kindle ASIN を本編候補とする
        for asin, count in counter.most_common():
            if asin != sample_asin:
                if debug:
                    print(f"  [RouteC] -> 本編候補: {asin} (出現回数={count})")
                return asin

    except Exception as e:
        if debug:
            print(f"  [RouteC] エラー: {e}")

    return None


# ─── メイン解決関数 ────────────────────────────────────────────────────────────

async def resolve_sample_to_paid(
    sample_asin: str,
    headless: bool = True,
    debug: bool = False,
    browser_profile: Optional[Dict[str, Any]] = None,
    worker_id: int = 0,
    ban_coordinator=None,
    request_pacer=None,
) -> Optional[str]:
    """
    サンプル ASIN → 有料本編 ASIN を解決して返す。

    Args:
        sample_asin:      Kindle サンプル本の ASIN（例: "B0GGY819NL"）
        headless:         True でヘッドレス実行（デフォルト）
        debug:            True で詳細ログを出力
        browser_profile:  フィンガープリント設定 dict（None の場合はデフォルト値を使用）
        worker_id:        ログ表示用のワーカーID
        ban_coordinator:  BanCoordinator インスタンス（None の場合はBAN検知なし）
        request_pacer:    RequestPacer インスタンス（None の場合はアクセス間隔調整なし）

    Returns:
        有料本編の ASIN 文字列、解決できなかった場合は None
    """
    from playwright.async_api import async_playwright

    # プロファイルが指定されていない場合はデフォルト値を使用
    profile = browser_profile or {
        "user_agent": USER_AGENT,
        "viewport": {"width": 1280, "height": 800},
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
        "extra_headers": {
            "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Upgrade-Insecure-Requests": "1",
        },
    }

    url = AMAZON_BASE.format(asin=sample_asin)
    prefix = f"[Worker-{worker_id}]" if worker_id else ""

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=profile["user_agent"],
            viewport=profile["viewport"],
            locale=profile.get("locale", "ja-JP"),
            timezone_id=profile.get("timezone_id", "Asia/Tokyo"),
        )

        await context.set_extra_http_headers(profile.get("extra_headers", {}))

        page = await context.new_page()
        await apply_stealth(page)

        try:
            if request_pacer is not None:
                await request_pacer.wait_for_turn(worker_id=worker_id)
            print(f"\n{prefix}アクセス中: {url}")
            # ページロード（30秒タイムアウト）
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await random_delay(1.5, 3.0)

            # ── BAN 検知 ──────────────────────────────────────────
            if ban_coordinator is not None:
                try:
                    from src.anti_ban import check_ban_signals
                except ImportError:
                    from anti_ban import check_ban_signals
                signal = await check_ban_signals(page, worker_id=worker_id, debug=debug)
                if signal != "ok":
                    await ban_coordinator.report_ban(signal, worker_id=worker_id)
                    return None

            # タイトル確認（デバッグ用）
            if debug:
                title = await page.title()
                print(f"  {prefix}ページタイトル: {title}")

            # ── ルートA ──────────────────────────────────────────
            print(f"  {prefix}[1/3] ルートA: canonical タグを確認...")
            asin = await route_a_canonical(page, sample_asin, debug)
            if asin:
                if asin == sample_asin:
                    print(f"  {prefix}-> ルートA: サンプルと本編が同一ページ (ASIN={asin}) -> 本編 ASIN として採用")
                else:
                    print(f"  {prefix}-> ルートA で解決: {asin}")
                return asin
            else:
                print(f"  {prefix}-> ルートA: 本編 ASIN を取得できず、ルートB へ")

            # ── ルートB ──────────────────────────────────────────
            print(f"  {prefix}[2/3] ルートB: エディション切り替えリンクを確認...")
            asin = await route_b_edition_links(page, sample_asin, debug)
            if asin:
                print(f"  {prefix}-> ルートB で解決: {asin}")
                return asin
            else:
                print(f"  {prefix}-> ルートB: 本編 ASIN を取得できず、ルートC へ")

            # ── ルートC (フォールバック) ──────────────────────────
            print(f"  {prefix}[3/3] ルートC: ページソース全体から ASIN を推定...")
            asin = await route_c_page_source(page, sample_asin, debug)
            if asin:
                print(f"  {prefix}-> ルートC で解決: {asin}")
                return asin

            print(f"  {prefix}-> 全ルートで解決できませんでした")
            return None

        except Exception as e:
            print(f"  {prefix}ページアクセスエラー: {e}")
            return None
        finally:
            await browser.close()


# ─── エントリポイント ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kindle サンプル ASIN を有料本編 ASIN に変換します。"
    )
    parser.add_argument("asin", help="サンプル本の ASIN（例: B0GGY819NL）")
    parser.add_argument(
        "--visible",
        action="store_true",
        help="ブラウザを表示して実行する（デフォルトはヘッドレス）",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="デバッグ情報を詳細出力する",
    )
    args = parser.parse_args()

    sample_asin = args.asin.strip().upper()

    print("=" * 60)
    print("  Kindle サンプル -> 本編 ASIN 解決ツール")
    print("=" * 60)
    print(f"  サンプル ASIN : {sample_asin}")

    result = asyncio.run(
        resolve_sample_to_paid(
            sample_asin,
            headless=not args.visible,
            debug=args.debug,
        )
    )

    print()
    print("-" * 60)
    if result:
        print(f"  [成功] 本編 ASIN  : {result}")
        print(f"  本編 URL          : https://www.amazon.co.jp/dp/{result}")
    else:
        print("  [失敗] 本編 ASIN を特定できませんでした。")
        print("  --debug オプションで詳細を確認してください。")
    print("=" * 60)


if __name__ == "__main__":
    main()
