"""
src/bookmeter_sync.py
----------------------
読書メーター「読みたい本」の取得→Kindle版ASIN解決→重複排除しての登録→価格クロール
という一連の処理を1冊ずつ逐次実行するオーケストレーションモジュール。

1冊ごとに try/except で独立させて失敗を握りつぶし、次の本の処理へ進む
（main.py の process_book と同様の方針。1冊の失敗で全体を止めると残りの
本が一切処理されなくなるため、失敗は戻り値の failed_titles で呼び出し元へ
返す。ログとしての記録は progress_cb 経由でのみ行われる — 呼び出し元が
渡さない限り記録されないため、server.py 側では必ず job.emit() に接続する）。

並列化はしない（読書メーター同期は手動の一括処理であり、逐次実行で十分。
BanCoordinator / RequestPacer は main.py の process_book と同じ呼び出し位置
（ASIN解決前・クロール前）で wait_if_banned() を挟み、BAN検知後のバックオフが
実際に効くようにする）。

使い方:
    python -c "import asyncio; from src.bookmeter_sync import sync_bookmeter_wishlist; \
        print(asyncio.run(sync_bookmeter_wishlist()))"
"""

import asyncio
from typing import Callable, Dict, List, Optional

from src.anti_ban import BanCoordinator, RequestPacer
from src.bookmeter import fetch_wish_books
from src.crawler import crawl_price_info
from src.database import get_session
from src.repository import attach_bookmeter_ids, fix_truncated_bookmeter_titles, get_or_create_by_paid_asin, save_price_history
from src.title_resolver import resolve_title_to_paid_asin

ProgressCallback = Callable[[str], None]


async def sync_bookmeter_wishlist(progress_cb: Optional[ProgressCallback] = None) -> Dict:
    """
    読書メーター「読みたい本」を取得し、1冊ずつ ASIN解決→dedup登録→価格クロール
    を逐次実行する。

    Args:
        progress_cb: 進捗ログを1行ずつ受け取るコールバック（省略時は出力しない。
                     server.py 側で job.emit() に接続する想定）

    Returns:
        {
            "total":         int,        # 読書メーターから取得した本の総数
            "registered":    int,        # 登録・クロールまで完了した件数
            "skipped":       int,        # スキップした件数（ASIN解決失敗・登録失敗・クロール失敗）
            "failed_titles": list[str],  # スキップした本のタイトル一覧
        }
    """

    def emit(line: str) -> None:
        if progress_cb:
            progress_cb(line)

    emit("=== 読書メーター同期 開始 ===")

    try:
        # fetch_wish_books は requests + time.sleep による同期実装で、最大
        # MAX_PAGES（100）ページを直列に取得するため数十秒〜数分ブロックしうる。
        # await せず直接呼ぶとイベントループ全体が止まり、SSE配信や /api/stop
        # が応答しなくなるため、get_books() 等の既存規約と同様にスレッドへ逃がす。
        books = await asyncio.to_thread(fetch_wish_books)
    except Exception as e:
        emit(f"[エラー] 読書メーター取得に失敗しました: {e}")
        return {"total": 0, "registered": 0, "skipped": 0, "failed_titles": []}

    emit(f"[OK] 読書メーターから「読みたい本」を {len(books)} 件取得しました。")

    # 「…」で切れたまま登録された書名を、ASIN 解決の成否に関係なく一覧の完全な書名で直す
    # （失敗しても同期は続ける。次の同期で再び試みる）
    try:
        fixed = fix_truncated_bookmeter_titles([book.get("title", "") for book in books])
        if fixed:
            emit(f"[OK] 切れていた書名を {fixed} 件直しました。")
    except Exception as e:
        emit(f"  [Error] 切れていた書名の修正に失敗しました（同期は続けます）: {e}")

    # 読書メーターの本 ID を、ASIN 解決の成否に関係なく書名の一致で付ける（失敗しても同期は続ける）
    try:
        attached = attach_bookmeter_ids(books)
        if attached:
            emit(f"[OK] 読書メーターの本 ID を {attached} 件付けました。")
    except Exception as e:
        emit(f"  [Error] 読書メーターの本 ID の保存に失敗しました（同期は続けます）: {e}")

    ban_coordinator = BanCoordinator()
    request_pacer = RequestPacer()

    registered = 0
    skipped = 0
    failed_titles: List[str] = []

    for i, book in enumerate(books, 1):
        title = book.get("title", "")
        author = book.get("author", "")
        emit(f"[{i}/{len(books)}] {title}")

        # BAN 発生中は解除を待つ（main.py の process_book と同じ呼び出し位置）
        await ban_coordinator.wait_if_banned(worker_id=1)

        try:
            paid_asin = await resolve_title_to_paid_asin(
                title,
                author,
                headless=True,
                worker_id=1,
                ban_coordinator=ban_coordinator,
                request_pacer=request_pacer,
            )
        except Exception as e:
            emit(f"  [Error] ASIN解決中にエラーが発生しました: {e}")
            skipped += 1
            failed_titles.append(title)
            continue

        if not paid_asin:
            emit(f"  [Skip] Kindle版ASINを解決できませんでした: {title}")
            skipped += 1
            failed_titles.append(title)
            continue

        emit(f"  [OK] ASIN解決成功: {paid_asin}")

        try:
            with get_session() as session:
                bookmeter_id = book.get("bookmeter_id")
                get_or_create_by_paid_asin(
                    session, paid_asin, title=title, source="bookmeter", is_wanted=1,
                    **({"bookmeter_id": bookmeter_id} if bookmeter_id else {}),
                )
                session.commit()
        except Exception as e:
            emit(f"  [Error] 登録中にエラーが発生しました: {e}")
            skipped += 1
            failed_titles.append(title)
            continue

        # BAN 発生中は解除を待つ（main.py の process_book と同じ呼び出し位置）
        await ban_coordinator.wait_if_banned(worker_id=1)

        try:
            price_data = await crawl_price_info(
                paid_asin,
                headless=True,
                worker_id=1,
                ban_coordinator=ban_coordinator,
                request_pacer=request_pacer,
            )
        except Exception as e:
            # crawl_price_info は通常 BAN 検知・ページ取得失敗を例外にせず
            # sell_price=None の dict で返すため、ここに到達するのは
            # ブラウザ起動失敗等の想定外の例外のみ（下の sell_price チェックが主経路）。
            emit(f"  [Error] クロール中にエラーが発生しました: {e}")
            skipped += 1
            failed_titles.append(title)
            continue

        if price_data.get("sell_price") is None:
            emit(f"  [Skip] 価格情報を取得できませんでした（BAN検知またはページ取得失敗の可能性）: {title}")
            skipped += 1
            failed_titles.append(title)
            continue

        try:
            save_price_history(price_data)
        except Exception as e:
            emit(f"  [Error] 保存中にエラーが発生しました: {e}")
            skipped += 1
            failed_titles.append(title)
            continue

        emit(f"  [OK] クロール成功: 価格=¥{price_data.get('sell_price')}")
        registered += 1

    emit(f"=== 読書メーター同期 完了: 登録 {registered} 件 / スキップ {skipped} 件 ===")

    return {
        "total": len(books),
        "registered": registered,
        "skipped": skipped,
        "failed_titles": failed_titles,
    }
