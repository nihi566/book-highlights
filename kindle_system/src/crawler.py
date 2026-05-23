"""
crawler.py
----------
Kindle 本編 ASIN（EBOK）の Amazon.co.jp 商品ページから
非ログイン状態で「価格・ポイント・キャンペーン情報」を抽出する。

使い方:
    python crawler.py B0GGY819NL
    python crawler.py B0GGY819NL --visible   (ブラウザを表示)
    python crawler.py B0GGY819NL --debug     (デバッグ詳細表示)
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

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# 価格文字列から数値を抽出するパターン（￥1,234 -> 1234）
PRICE_NUM_RE = re.compile(r"[\d,]+")


# ─── ユーティリティ ────────────────────────────────────────────────────────────

def clean_price(text: str) -> Optional[int]:
    """
    価格文字列をクレンジングして整数に変換する。
    例: "￥1,234" -> 1234 / "￥ 2,574" -> 2574 / None -> None
    """
    if not text:
        return None
    m = PRICE_NUM_RE.search(text.replace(",", ""))
    return int(m.group()) if m else None


def clean_points(text: str) -> int:
    """
    ポイント文字列をクレンジングして整数に変換する。
    例: "25pt (1%)" -> 25 / "1,000ポイント" -> 1000 / "" -> 0
    """
    if not text:
        return 0
    # 数字部分だけを抽出（最初の連続数字群）
    nums = re.findall(r"[\d,]+", text)
    if not nums:
        return 0
    return int(nums[0].replace(",", ""))


def clean_campaign(text: str) -> str:
    """キャンペーンテキストの前後空白・改行を除去して返す。"""
    return " ".join(text.split()) if text else ""


async def random_delay(min_sec: float = 1.5, max_sec: float = 3.0) -> None:
    """Bot 検知回避用のランダムウェイト。"""
    await asyncio.sleep(random.uniform(min_sec, max_sec))


# ─── ステルス設定（resolver.py と同一） ────────────────────────────────────────

async def apply_stealth(page) -> None:
    """playwright-stealth + 追加 JS パッチで WebDriver フラグを隠蔽。"""
    try:
        from playwright_stealth import stealth_async
        await stealth_async(page)
    except ImportError:
        pass

    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['ja-JP', 'ja', 'en-US', 'en'],
        });
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
        window.chrome = { runtime: {} };
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (p) =>
            p.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : origQuery(p);
    """)


# ─── 価格抽出ロジック ──────────────────────────────────────────────────────────

async def _try_get_text(page, selector: str) -> str:
    """セレクタにマッチする最初の要素のテキストを返す。失敗時は空文字。"""
    try:
        el = await page.query_selector(selector)
        if el:
            return (await el.inner_text()).strip()
    except Exception:
        pass
    return ""


async def extract_sell_price(page, debug: bool = False) -> Optional[int]:
    """
    Kindle 本の販売価格を取得する。複数のセレクタパターンを優先順で試行。
    """
    # Kindle 価格セレクタの優先順リスト
    price_selectors = [
        # Kindle 専用価格ブロック（最優先）
        "#kindle-price",
        "#kindle_price",
        ".kindle-price",
        # Kindle ストアの通常価格表示
        "#tp_price_block_total_price_ww .a-price .a-offscreen",
        "#tp_price_block_total_price_ww .a-price-whole",
        # 汎用価格ブロック
        "#price_inside_buybox",
        "#buyNewSection .a-price .a-offscreen",
        "#buyNewSection .a-price-whole",
        ".priceToPay .a-offscreen",
        ".priceToPay .a-price-whole",
        # 旧 UI フォールバック
        "#actualPriceValue",
        ".kindle-price span",
        "span.a-color-price",
    ]

    for sel in price_selectors:
        text = await _try_get_text(page, sel)
        if text:
            price = clean_price(text)
            if price and price > 0:
                if debug:
                    print(f"  [価格] sel={sel!r} -> {text!r} -> {price}")
                return price

    # JS フォールバック: ページ内の Kindle 価格を直接 evaluate
    try:
        raw = await page.evaluate("""
            () => {
                // Kindle 価格を持つ要素を広く探す
                const candidates = [
                    document.querySelector('#kindle-price'),
                    document.querySelector('#kindle_price'),
                    document.querySelector('.kindle-price'),
                    document.querySelector('#tp_price_block_total_price_ww .a-offscreen'),
                    document.querySelector('.priceToPay .a-offscreen'),
                    document.querySelector('#price_inside_buybox'),
                ];
                for (const el of candidates) {
                    if (el && el.textContent.trim()) {
                        return el.textContent.trim();
                    }
                }
                return null;
            }
        """)
        if raw:
            price = clean_price(raw)
            if price and price > 0:
                if debug:
                    print(f"  [価格] JS evaluate -> {raw!r} -> {price}")
                return price
    except Exception as e:
        if debug:
            print(f"  [価格] JS evaluate エラー: {e}")

    return None


async def extract_points(page, debug: bool = False) -> int:
    """
    Kindle ポイント還元数を取得する（無い場合は 0）。
    """
    point_selectors = [
        # ポイント専用要素
        "#loyalty-points .a-size-base",
        "#loyalty-points",
        "#pointsInsideBuyBox",
        ".loyalty-priceblock-widget-value",
        # ポイント含む汎用テキスト
        "#tp_price_block_total_price_ww .loyalty-points",
        ".loyalty-points",
        "[data-feature-name='loyalty'] .a-size-base",
    ]

    for sel in point_selectors:
        text = await _try_get_text(page, sel)
        if text and re.search(r"\d", text):
            pts = clean_points(text)
            if pts > 0:
                if debug:
                    print(f"  [ポイント] sel={sel!r} -> {text!r} -> {pts}")
                return pts

    # JS フォールバック: "pt" や "ポイント" を含むテキストを広く探す
    try:
        raw = await page.evaluate("""
            () => {
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null, false
                );
                let node;
                while ((node = walker.nextNode())) {
                    const t = node.textContent.trim();
                    // "25pt" や "25ポイント" のパターン
                    if (/\\d+\\s*(pt|ポイント)/.test(t) && t.length < 50) {
                        return t;
                    }
                }
                return null;
            }
        """)
        if raw:
            pts = clean_points(raw)
            if pts > 0:
                if debug:
                    print(f"  [ポイント] JS walker -> {raw!r} -> {pts}")
                return pts
    except Exception as e:
        if debug:
            print(f"  [ポイント] JS walker エラー: {e}")

    return 0


async def extract_campaign(page, debug: bool = False) -> str:
    """
    セールバナー・キャンペーンラベルのテキストを取得する（無い場合は空文字）。
    buybox / 価格ブロック周辺のみを対象とし、ページ全体スキャンは行わない。
    """
    texts: list = []
    seen: set = set()

    def _add(text: str, src: str = "") -> None:
        """重複排除・長さ・内容チェックしてリストに追加。"""
        t = clean_campaign(text)
        # 不要な定型文を除外
        if "ご購入時にプロモーションが適用されます" in t:
            return
        # 80文字超・数字記号のみ・既出 はスキップ（商品リストの混入を防ぐ）
        if not t or t in seen or len(t) > 80:
            return
        if re.fullmatch(r"[\d,\s¥￥%\-\.]+", t):
            return
        seen.add(t)
        texts.append(t)
        if debug:
            print(f"  [キャンペーン] {src} -> {t!r}")

    # ── 戦略1: JS で buybox / 価格ブロック内に限定してバッジ類を取得 ──────────
    try:
        raw_list = await page.evaluate("""
            () => {
                const results = [];
                const roots = [
                    document.getElementById('buybox'),
                    document.getElementById('price'),
                    document.getElementById('tp_price_block_total_price_ww'),
                    document.getElementById('KindleEBookPriceWidget'),
                ];
                const selectors = [
                    '[id*="dealBadge"]', '[class*="dealBadge"]',
                    '[id*="promo"]',     '[class*="promo"]',
                    '.savingMessage',    '.a-color-success',
                    '[class*="badge"]',  '.a-badge-label',
                ];
                const seen = new Set();
                for (const root of roots) {
                    if (!root) continue;
                    for (const sel of selectors) {
                        root.querySelectorAll(sel).forEach(el => {
                            const t = el.innerText.trim();
                            if (t && !seen.has(t) && t.length < 80) {
                                seen.add(t);
                                results.push(t);
                            }
                        });
                    }
                }
                return results;
            }
        """)
        for item in (raw_list or []):
            _add(item, "JS/buybox")
    except Exception as e:
        if debug:
            print(f"  [キャンペーン] JS エラー: {e}")

    # ── 戦略2: ID が確定している安全なセレクタのみ直接取得 ──────────────────────
    for sel in [
        "#dealBadge", ".dealBadge", "#mbb-promo-badge",
        ".promo-badge-wrapper .a-badge-label",
        "#promotionText", "#buybox .a-color-success",
        "#buybox .savingMessage",
    ]:
        try:
            for el in await page.query_selector_all(sel):
                _add(await el.inner_text(), f"sel:{sel}")
        except Exception:
            pass

    return " | ".join(texts) if texts else ""


# ─── メイン抽出関数 ────────────────────────────────────────────────────────────

async def crawl_price_info(
    asin: str,
    headless: bool = True,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    指定 ASIN の Amazon.co.jp 商品ページから価格情報を取得して返す。

    Returns:
        {
            "asin":          str,
            "sell_price":    int | None,   # 販売価格（円）
            "point_value":   int,          # 還元ポイント（0 = 無し）
            "campaign_text": str,          # キャンペーン文（"" = 無し）
            "url":           str,
        }
    """
    from playwright.async_api import async_playwright

    url = AMAZON_BASE.format(asin=asin)
    result: Dict[str, Any] = {
        "asin":          asin,
        "sell_price":    None,
        "point_value":   0,
        "campaign_text": "",
        "url":           url,
        "is_unlimited":  0,
    }

    print(f"\nアクセス中: {url}")

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
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        await context.set_extra_http_headers({
            "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        })

        page = await context.new_page()
        await apply_stealth(page)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await random_delay(1.5, 3.0)

            if debug:
                title = await page.title()
                print(f"  ページタイトル: {title}")

            # ── 価格取得 ──────────────────────────────────────────────
            print("  [1/3] 販売価格を取得中...")
            sell_price = await extract_sell_price(page, debug)
            result["sell_price"] = sell_price
            print(f"  -> 販売価格: {f'¥{sell_price:,}' if sell_price else '取得不可'}")

            # ── ポイント取得 ──────────────────────────────────────────
            print("  [2/3] ポイント還元を取得中...")
            point_value = await extract_points(page, debug)
            result["point_value"] = point_value
            print(f"  -> ポイント: {point_value} pt")

            # ── キャンペーン情報取得 ──────────────────────────────────
            print("  [3/4] キャンペーン情報を取得中...")
            campaign_text = await extract_campaign(page, debug)
            result["campaign_text"] = campaign_text
            print(f"  -> キャンペーン: {campaign_text if campaign_text else '（なし）'}")

            # ── Kindle Unlimited判定 ──────────────────────────────────
            print("  [4/4] Kindle Unlimited判定中...")
            try:
                text_content = ""
                for sel in ["#buybox", "#combinedBuyBox", "#rightCol"]:
                    if await page.locator(sel).count() > 0:
                        text_content += await page.locator(sel).first.inner_text() + "\n"
                
                text_lower = text_content.lower()
                if "kindle unlimited" in text_lower or "読み放題" in text_lower:
                    result["is_unlimited"] = 1
                    print("  -> Unlimited: 対象 (✅)")
                else:
                    print("  -> Unlimited: 対象外")
            except Exception as e:
                print(f"  -> Unlimited判定エラー: {e}")

        except Exception as e:
            print(f"  ページアクセスエラー: {e}")
        finally:
            await browser.close()

    return result


# ─── 結果表示 ─────────────────────────────────────────────────────────────────

def print_result(data: Dict[str, Any]) -> None:
    """抽出結果をコンソールに整形して出力する。"""
    print()
    print("=" * 60)
    print("  Kindle 価格情報 抽出結果")
    print("=" * 60)
    print(f"  ASIN             : {data['asin']}")
    print(f"  URL              : {data['url']}")
    print("-" * 60)

    sell_price = data["sell_price"]
    if sell_price is not None:
        print(f"  【販売価格】      : ¥{sell_price:,}")
    else:
        print("  【販売価格】      : 取得できませんでした")

    print(f"  【還元ポイント】  : {data['point_value']} pt")

    campaign = data["campaign_text"]
    if campaign:
        print(f"  【キャンペーン】  : {campaign}")
    else:
        print("  【キャンペーン】  : （なし）")

    print("=" * 60)


# ─── エントリポイント ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kindle 本編 ASIN から価格・ポイント・キャンペーン情報を抽出します。"
    )
    parser.add_argument("asin", help="本編 ASIN（例: B0GGY819NL）")
    parser.add_argument(
        "--visible", action="store_true",
        help="ブラウザを表示して実行する",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="デバッグ情報を詳細出力する",
    )
    args = parser.parse_args()

    asin = args.asin.strip().upper()
    print("=" * 60)
    print("  Kindle 価格クローラー")
    print("=" * 60)
    print(f"  対象 ASIN: {asin}")

    result = asyncio.run(
        crawl_price_info(
            asin,
            headless=not args.visible,
            debug=args.debug,
        )
    )

    print_result(result)


if __name__ == "__main__":
    main()
