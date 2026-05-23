"""
debug_ku_button.py
------------------
KU本と非KU本で「Kindle版価格ボタン周辺」のHTML構造を比較し、
正確な絞り込みセレクタを特定するための調査スクリプト。

実行:
    python debug_ku_button.py
"""

import asyncio
from playwright.async_api import async_playwright

BOOKS = [
    {"asin": "B07T3LBGKS", "title": "響けユーフォ（KU対象）",    "expect": "KU対象"},
    {"asin": "B00DKWM6YO", "title": "桶川ストーカー殺人事件（非KU）", "expect": "対象外"},
]

# 調査対象セレクタ（候補）
CANDIDATE_SELECTORS = [
    "a[id*='-announce']",
    "a[id*='announce']",
    ".slot-price",
    "#tmmSwatches",
    "#tmmSwatches a",
    ".a-button-selected",
    "#formats .a-tab-heading-selected",
    "#mediaTab_heading_0",
    "#buybox",
    "#kindle-price",
    "#tp_price_block_total_price_ww",
]


async def inspect_book(page, book: dict):
    url = f"https://www.amazon.co.jp/dp/{book['asin']}"
    print(f"\n{'='*70}")
    print(f"  {book['title']} ({book['asin']}) — 期待: {book['expect']}")
    print(f"{'='*70}")

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(3000)

    # ── 1. 各候補セレクタの件数確認 ──────────────────────────────
    print("\n[候補セレクタの件数]")
    for sel in CANDIDATE_SELECTORS:
        try:
            cnt = await page.locator(sel).count()
            if cnt > 0:
                print(f"  ✅ {sel!r:50s} → {cnt}件")
            else:
                print(f"  ❌ {sel!r:50s} → 0件")
        except Exception as e:
            print(f"  ⚠️  {sel!r:50s} → エラー: {e}")

    # ── 2. ページ全体の i.a-icon-kindle-unlimited 件数 ───────────
    ku_icon_count = await page.locator("i.a-icon-kindle-unlimited").count()
    print(f"\n[ページ全体の i.a-icon-kindle-unlimited] → {ku_icon_count}件")

    # ── 3. 各候補の内部に KU アイコンがあるか確認 ─────────────────
    print("\n[候補セレクタ内部の i.a-icon-kindle-unlimited 有無]")
    for sel in CANDIDATE_SELECTORS:
        try:
            parent = page.locator(sel)
            cnt = await parent.count()
            if cnt == 0:
                continue
            ku_inside = await parent.locator("i.a-icon-kindle-unlimited").count()
            mark = "✅ KUアイコンあり" if ku_inside > 0 else "   KUアイコンなし"
            print(f"  {mark} — {sel!r} (要素{cnt}件中)")
        except Exception as e:
            print(f"  ⚠️  {sel!r} → エラー: {e}")

    # ── 4. a[id*='-announce'] の HTML を直接ダンプ ───────────────
    print("\n[a[id*='-announce'] のHTML（最大3件）]")
    announces = page.locator("a[id*='-announce']")
    ann_count = await announces.count()
    print(f"  合計 {ann_count} 件")
    for i in range(min(ann_count, 3)):
        el = announces.nth(i)
        el_id  = await el.get_attribute("id") or "(no id)"
        el_txt = (await el.inner_text()).strip().replace("\n", " ")[:120]
        ku_in  = await el.locator("i.a-icon-kindle-unlimited").count()
        print(f"  [{i+1}] id={el_id!r}")
        print(f"       text={el_txt!r}")
        print(f"       KUアイコン={ku_in}件")

    # ── 5. #tmmSwatches の内部構造ダンプ ────────────────────────
    tmm = page.locator("#tmmSwatches")
    if await tmm.count() > 0:
        print("\n[#tmmSwatches の inner_text]")
        print(await tmm.first.inner_text())

        # #tmmSwatches 内の a タグそれぞれを確認
        tmm_links = tmm.locator("a")
        lcount = await tmm_links.count()
        print(f"\n[#tmmSwatches 内の a タグ: {lcount}件]")
        for i in range(lcount):
            el = tmm_links.nth(i)
            el_id  = await el.get_attribute("id") or "(no id)"
            el_txt = (await el.inner_text()).strip().replace("\n", " ")[:100]
            ku_in  = await el.locator("i.a-icon-kindle-unlimited").count()
            print(f"  [{i+1}] id={el_id!r}  KUアイコン={ku_in}件  text={el_txt!r}")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        for book in BOOKS:
            await inspect_book(page, book)
            await page.wait_for_timeout(2000)

        await browser.close()
        print("\n=== 完了 ===")


if __name__ == "__main__":
    asyncio.run(main())
