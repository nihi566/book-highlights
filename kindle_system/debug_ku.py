"""
debug_ku.py
-----------
非ログイン状態で Amazon の商品ページを開き、
Kindle Unlimited 関連テキストが存在するか緊急検証する使い捨てスクリプト。

実行:
    python debug_ku.py
"""

import asyncio
from playwright.async_api import async_playwright

URL = "https://www.amazon.co.jp/dp/B07T3LBGKS"

KEYWORDS = ["unlimited", "読み放題", "￥0", "¥0"]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
        )

        print(f"=== ページ取得開始: {URL} ===")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)

        # ページが十分に描画されるまで少し待機
        await page.wait_for_timeout(3000)

        # ── #rightCol 全体テキスト ──────────────────────────────────────
        print("\n" + "=" * 60)
        print("【#rightCol の inner_text】")
        print("=" * 60)
        right_col = page.locator("#rightCol")
        if await right_col.count() > 0:
            text_right = await right_col.inner_text()
            print(text_right)
        else:
            text_right = ""
            print("(#rightCol が見つかりませんでした)")

        # ── #tst-content テキスト ────────────────────────────────────────
        print("\n" + "=" * 60)
        print("【#tst-content の inner_text】")
        print("=" * 60)
        tst = page.locator("#tst-content")
        if await tst.count() > 0:
            text_tst = await tst.inner_text()
            print(text_tst)
        else:
            text_tst = ""
            print("(#tst-content が見つかりませんでした)")

        # ── #cooButtonGroup テキスト ─────────────────────────────────────
        print("\n" + "=" * 60)
        print("【#cooButtonGroup の inner_text】")
        print("=" * 60)
        coo = page.locator("#cooButtonGroup")
        if await coo.count() > 0:
            text_coo = await coo.inner_text()
            print(text_coo)
        else:
            text_coo = ""
            print("(#cooButtonGroup が見つかりませんでした)")

        # ── ページ全体 body テキスト（フォールバック） ──────────────────
        print("\n" + "=" * 60)
        print("【body 全体の inner_text（最初の 3000 文字）】")
        print("=" * 60)
        text_body = await page.locator("body").inner_text()
        print(text_body[:3000])

        # ── キーワード検索 ───────────────────────────────────────────────
        all_text = (text_right + text_tst + text_coo + text_body).lower()

        print("\n" + "=" * 60)
        print("【キーワード検索結果】")
        print("=" * 60)
        for kw in KEYWORDS:
            found = kw.lower() in all_text
            mark = "✅ 存在する" if found else "❌ 存在しない"
            print(f"  「{kw}」 → {mark}")

        # ── HTML ソースの一部も出力（セレクタが見つからない場合の調査用） ──
        print("\n" + "=" * 60)
        print("【ページソース内の 'unlimited' 前後 200 文字（最大5箇所）】")
        print("=" * 60)
        html = await page.content()
        html_lower = html.lower()
        idx = 0
        count = 0
        while count < 5:
            pos = html_lower.find("unlimited", idx)
            if pos == -1:
                break
            snippet = html[max(0, pos - 100): pos + 100]
            print(f"--- [{count+1}] pos={pos} ---")
            print(snippet)
            idx = pos + 1
            count += 1
        if count == 0:
            print("(HTML ソース内に 'unlimited' は見つかりませんでした)")

        await browser.close()
        print("\n=== 完了 ===")


if __name__ == "__main__":
    asyncio.run(main())
