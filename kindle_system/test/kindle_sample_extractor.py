"""
kindle_sample_extractor.py
--------------------------
Kindle for PC のローカルキャッシュXML（KindleSyncMetadataCache.xml）を
パースし、無料サンプル本（EBSP / Sample）の ASIN とタイトルを抽出する。

使い方:
    python kindle_sample_extractor.py
    python kindle_sample_extractor.py --xml path/to/your.xml   (ファイル指定)
    python kindle_sample_extractor.py --test                    (内蔵テスト実行)
"""

import xml.etree.ElementTree as ET
import os
import sys
import argparse
import io
from typing import List, Dict

# Windows CP932 などの環境でも日本語・記号を正しく出力できるよう UTF-8 に再設定
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── 定数 ──────────────────────────────────────────────────────────────────────
DEFAULT_CACHE_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"),
    "Amazon", "Kindle", "Cache", "KindleSyncMetadataCache.xml"
)

# ─── コア処理 ──────────────────────────────────────────────────────────────────

def extract_samples(xml_path: str) -> List[Dict[str, str]]:
    """
    指定した XML ファイルをパースし、無料サンプル本のリストを返す。

    抽出条件:
      - <cde_contenttype> == "EBSP"
      - <origins><origin><type> == "Sample"

    Returns:
        [{"asin": "...", "title": "..."}, ...]
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    samples: List[Dict[str, str]] = []

    # <meta_data> は直下に存在する場合と、ラッパータグ内に存在する場合の両方に対応
    meta_data_list = root.iter("meta_data")

    for meta in meta_data_list:
        # ── 条件1: cde_contenttype == "EBSP" ──
        cde_elem = meta.find("cde_contenttype")
        if cde_elem is None or (cde_elem.text or "").strip() != "EBSP":
            continue

        # ── 条件2: origins/origin/type == "Sample" ──
        is_sample = False
        for origin in meta.iter("origin"):
            type_elem = origin.find("type")
            if type_elem is not None and (type_elem.text or "").strip() == "Sample":
                is_sample = True
                break

        if not is_sample:
            continue

        # ── データ抽出 ──
        asin_elem  = meta.find("ASIN")
        title_elem = meta.find("title")

        asin  = (asin_elem.text  or "").strip() if asin_elem  is not None else "(不明)"
        title = (title_elem.text or "").strip() if title_elem is not None else "(不明)"

        samples.append({"asin": asin, "title": title})

    return samples


def print_results(samples: List[Dict[str, str]], xml_path: str) -> None:
    """抽出結果をコンソールに整形して出力する。"""
    print("=" * 60)
    print("  Kindle 無料サンプル本 抽出結果")
    print("=" * 60)
    print(f"  対象ファイル : {xml_path}")
    print(f"  抽出件数     : {len(samples)} 件")
    print("-" * 60)

    if not samples:
        print("  該当するサンプル本はありませんでした。")
    else:
        for i, book in enumerate(samples, start=1):
            print(f"  [{i:>3}]")
            print(f"        ASIN  : {book['asin']}")
            print(f"        タイトル: {book['title']}")

    print("=" * 60)


# ─── 内蔵テスト ────────────────────────────────────────────────────────────────

MOCK_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<response>
  <add_update_list>

    <!-- ✅ サンプル本 #1: 両条件を満たす -->
    <meta_data>
      <ASIN>B0SAMPLE001</ASIN>
      <title>Pythonで学ぶ機械学習 (サンプル版)</title>
      <cde_contenttype>EBSP</cde_contenttype>
      <origins>
        <origin>
          <type>Sample</type>
        </origin>
      </origins>
    </meta_data>

    <!-- ✅ サンプル本 #2: 両条件を満たす -->
    <meta_data>
      <ASIN>B0SAMPLE002</ASIN>
      <title>Clean Architecture 達人に学ぶソフトウェアの構造と設計 (サンプル)</title>
      <cde_contenttype>EBSP</cde_contenttype>
      <origins>
        <origin>
          <type>Sample</type>
        </origin>
      </origins>
    </meta_data>

    <!-- ❌ 除外: cde_contenttype が EBOK (通常購入書籍) -->
    <meta_data>
      <ASIN>B0REGULAR01</ASIN>
      <title>通常購入した本 (除外されるべき)</title>
      <cde_contenttype>EBOK</cde_contenttype>
      <origins>
        <origin>
          <type>Purchase</type>
        </origin>
      </origins>
    </meta_data>

    <!-- ❌ 除外: cde_contenttype は EBSP だが origin/type が Sample でない -->
    <meta_data>
      <ASIN>B0MIXED001</ASIN>
      <title>混在パターン (除外されるべき)</title>
      <cde_contenttype>EBSP</cde_contenttype>
      <origins>
        <origin>
          <type>Purchase</type>
        </origin>
      </origins>
    </meta_data>

    <!-- ❌ 除外: origin/type は Sample だが cde_contenttype が異なる -->
    <meta_data>
      <ASIN>B0MIXED002</ASIN>
      <title>別混在パターン (除外されるべき)</title>
      <cde_contenttype>EBOK</cde_contenttype>
      <origins>
        <origin>
          <type>Sample</type>
        </origin>
      </origins>
    </meta_data>

    <!-- ✅ サンプル本 #3: origin が複数あり、うち1つが Sample -->
    <meta_data>
      <ASIN>B0SAMPLE003</ASIN>
      <title>Docker/Kubernetes 実践コンテナ開発入門 (サンプル)</title>
      <cde_contenttype>EBSP</cde_contenttype>
      <origins>
        <origin>
          <type>Purchase</type>
        </origin>
        <origin>
          <type>Sample</type>
        </origin>
      </origins>
    </meta_data>

  </add_update_list>
</response>
"""


def run_tests() -> None:
    """内蔵テストを実行して結果を検証する。"""
    import tempfile

    print("=" * 60)
    print("  【内蔵テスト実行】")
    print("=" * 60)

    # ── ダミーXMLを一時ファイルに書き出す ──
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".xml", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(MOCK_XML)
        tmp_path = tmp.name

    try:
        results = extract_samples(tmp_path)

        # ── 期待値との照合 ──
        expected_asins = {"B0SAMPLE001", "B0SAMPLE002", "B0SAMPLE003"}
        excluded_asins = {"B0REGULAR01", "B0MIXED001", "B0MIXED002"}
        result_asins   = {r["asin"] for r in results}

        passed = True

        # 件数チェック
        if len(results) != 3:
            print(f"  [NG] 件数エラー: 期待=3, 実際={len(results)}")
            passed = False
        else:
            print(f"  [OK] 件数チェック OK: {len(results)} 件")

        # 含まれるべきASINチェック
        for asin in sorted(expected_asins):
            if asin in result_asins:
                print(f"  [OK] 含むべきASIN    : {asin}")
            else:
                print(f"  [NG] 含むべきASIN NG : {asin} が見つかりません")
                passed = False

        # 含まれてはいけないASINチェック
        for asin in sorted(excluded_asins):
            if asin not in result_asins:
                print(f"  [OK] 除外チェック      : {asin} (未含有)")
            else:
                print(f"  [NG] 除外されるべき ASIN が含まれています: {asin}")
                passed = False

        print("-" * 60)
        print_results(results, tmp_path)

        print()
        if passed:
            print("  >>> 全テスト PASSED <<<")
        else:
            print("  >>> テスト FAILED -- 上記エラーを確認してください <<<")
            sys.exit(1)

    finally:
        os.unlink(tmp_path)


# ─── エントリポイント ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kindle for PC のキャッシュXMLからサンプル本を抽出します。"
    )
    parser.add_argument(
        "--xml",
        metavar="PATH",
        default=None,
        help="XMLファイルのパスを指定 (省略時はデフォルトパスを使用)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="内蔵テストを実行する",
    )
    args = parser.parse_args()

    # ── テストモード ──
    if args.test:
        run_tests()
        return

    # ── 通常モード ──
    xml_path = args.xml or DEFAULT_CACHE_PATH

    if not os.path.exists(xml_path):
        print(f"\n⚠️  ファイルが見つかりません: {xml_path}")
        print()
        print("  以下のいずれかで対処してください:")
        print("  1. Kindle for PC を起動してライブラリを同期する")
        print("  2. --xml オプションで別のパスを指定する")
        print("  3. --test オプションで内蔵テストを実行して動作確認する")
        print()
        print("  例: python kindle_sample_extractor.py --test")
        sys.exit(1)

    try:
        samples = extract_samples(xml_path)
    except ET.ParseError as e:
        print(f"\n❌ XMLパースエラー: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 予期しないエラー: {e}")
        sys.exit(1)

    print_results(samples, xml_path)


if __name__ == "__main__":
    main()
