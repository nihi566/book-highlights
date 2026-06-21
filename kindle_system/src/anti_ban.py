"""
anti_ban.py
-----------
Amazon のBOT検知対策ユーティリティ。

主な機能:
  - BROWSER_PROFILES: ブラウザフィンガープリントのバリエーションプール
  - check_ban_signals(): CAPTCHA/ロボット確認ページの検出
  - BanCoordinator: 全ワーカー共有のBAN状態管理（グローバル停止制御）
"""

import asyncio
import random
import time
from typing import Literal, Optional

# ─── フィンガープリントプール ───────────────────────────────────────────────────
# ワーカーごとに異なるプロファイルを割り当てることで、
# 複数アクセスが同一クライアントに見えにくくする。

BROWSER_PROFILES = [
    {
        "id": "profile_a",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1280, "height": 800},
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
        "extra_headers": {
            "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        },
    },
    {
        "id": "profile_b",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
        ),
        "viewport": {"width": 1920, "height": 1080},
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
        "extra_headers": {
            "Accept-Language": "ja,ja-JP;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        },
    },
    {
        "id": "profile_c",
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1440, "height": 900},
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
        "extra_headers": {
            "Accept-Language": "ja-JP,ja;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        },
    },
    {
        "id": "profile_d",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/129.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1366, "height": 768},
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
        "extra_headers": {
            "Accept-Language": "ja-JP,ja;q=0.8,en-US;q=0.6,en;q=0.4",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        },
    },
]


def get_profile(worker_id: int) -> dict:
    """
    ワーカーIDに基づいてブラウザプロファイルを返す。
    ワーカー数がプロファイル数を超えた場合はローテーションする。
    """
    return BROWSER_PROFILES[worker_id % len(BROWSER_PROFILES)]


def get_random_profile() -> dict:
    """ランダムにプロファイルを1つ返す。"""
    return random.choice(BROWSER_PROFILES)


# ─── BAN信号の種別 ─────────────────────────────────────────────────────────────

BanSignal = Literal["ok", "captcha", "robot_check", "access_denied", "suspicious"]

# BAN検知ページに含まれるキーワード
_BAN_TITLE_KEYWORDS = [
    "robot check",
    "ロボット",
    "不審なアクセス",
    "sorry",
    "captcha",
    "verify",
    "エラーが発生しました",
]

_BAN_URL_PATTERNS = [
    "/errors/validateCaptcha",
    "/ap/cvf/",
    "/robot",
    "ref=cs_503_link",
]


async def check_ban_signals(page, worker_id: int = 0, debug: bool = False) -> BanSignal:
    """
    現在のページがBAN/CAPTCHA/ロボット確認ページかどうかを検出する。

    Returns:
        "ok"           : 正常ページ
        "captcha"      : CAPTCHA 認証ページ
        "robot_check"  : ロボット確認ページ
        "access_denied": アクセス拒否ページ（503等）
        "suspicious"   : 不審な状態（タイトルなし等）
    """
    try:
        current_url = page.url
        title = (await page.title()).lower()

        # URL パターンチェック
        for pattern in _BAN_URL_PATTERNS:
            if pattern.lower() in current_url.lower():
                if debug:
                    print(f"  [Worker-{worker_id}][BAN] URLパターン検出: {pattern} in {current_url}")
                if "captcha" in pattern.lower():
                    return "captcha"
                return "robot_check"

        # タイトルキーワードチェック
        for keyword in _BAN_TITLE_KEYWORDS:
            if keyword.lower() in title:
                if debug:
                    print(f"  [Worker-{worker_id}][BAN] タイトルキーワード検出: {keyword!r} in {title!r}")
                if "captcha" in keyword or "captcha" in title:
                    return "captcha"
                if "robot" in keyword or "ロボット" in keyword:
                    return "robot_check"
                return "access_denied"

        # ページ本文が極端に短い場合を不審として扱う
        body_text = await page.evaluate("() => document.body?.innerText?.length ?? 0")
        if body_text < 200:
            if debug:
                print(f"  [Worker-{worker_id}][BAN] 本文が短すぎます ({body_text}文字)")
            return "suspicious"

    except Exception as e:
        if debug:
            print(f"  [Worker-{worker_id}][BAN] 検知エラー: {e}")

    return "ok"


# ─── グローバルBAN調整クラス ──────────────────────────────────────────────────

class BanCoordinator:
    """
    複数ワーカー間でBAN状態を共有し、一時停止・再開を制御する。

    BAN検知時:
      1. 全ワーカーは次のアクセス前に `wait_if_banned()` で停止する
      2. バックオフ時間経過後、自動的に再開する
    """

    def __init__(self) -> None:
        self._ban_until: float = 0.0          # エポック秒。この時刻まで全ワーカー待機
        self._ban_signal: BanSignal = "ok"
        self._lock = asyncio.Lock()

    async def report_ban(self, signal: BanSignal, worker_id: int = 0) -> None:
        """
        BAN/CAPTCHAを検知したワーカーが報告する。
        既にバックオフ中の場合はより長い方を採用。
        """
        backoff_seconds = self._get_backoff(signal)
        new_ban_until = time.monotonic() + backoff_seconds

        async with self._lock:
            if new_ban_until > self._ban_until:
                self._ban_until = new_ban_until
                self._ban_signal = signal
                print(
                    f"\n  [Worker-{worker_id}][BAN-COORDINATOR] "
                    f"BAN検知 ({signal}): {backoff_seconds}秒間 全ワーカーを一時停止します..."
                )

    async def wait_if_banned(self, worker_id: int = 0) -> None:
        """
        BAN中であれば解除されるまで待機する。
        定期的に残り時間をログ出力する。
        """
        while True:
            remaining = self._ban_until - time.monotonic()
            if remaining <= 0:
                break
            print(f"  [Worker-{worker_id}][BAN-WAIT] BAN中のため待機中... 残り {remaining:.0f}秒")
            await asyncio.sleep(min(30.0, remaining))

    @property
    def is_banned(self) -> bool:
        return time.monotonic() < self._ban_until

    @staticmethod
    def _get_backoff(signal: BanSignal) -> float:
        """シグナルの種別に応じたバックオフ秒数を返す。"""
        return {
            "captcha":      120.0,  # 2分 (CAPTCHA は重い対応)
            "robot_check":   90.0,  # 1.5分
            "access_denied": 60.0,  # 1分
            "suspicious":    30.0,  # 30秒
            "ok":             0.0,
        }.get(signal, 60.0)


# シングルトン（main.py から import して使う）
ban_coordinator = BanCoordinator()
