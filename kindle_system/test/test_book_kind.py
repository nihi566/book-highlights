"""
test_book_kind.py
------------------
src/book_kind.py（タイトルからマンガ/本を推定する）の単体テスト。

実行:
    python -m unittest test.test_book_kind -v
"""

import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src.book_kind import KIND_BOOK, KIND_MANGA, classify_kind


class ClassifyKindTest(unittest.TestCase):
    def test_comics_labels_are_manga(self):
        for title in [
            "マイペースと歩く　1巻 (バンチコミックス)",
            "全部救ってやる（１） (マンガワンコミックス)",
            "本なら売るほど 1 (ハルタコミックス)",
            "ONE PIECE モノクロ版 107 (ジャンプコミックスDIGITAL)",
            "薬屋のひとりごと 1 (ビッグガンガンコミックス)",
            "よつばと！ 1 (電撃コミックス)",
            "ブルーピリオド（１） (アフタヌーンKC)",
            "花とゆめCOMICS",
            "いぬやしき (it COMICS)",
            "（ＫＣデラックス）",
            "（ＭＦＣ）",
        ]:
            with self.subTest(title=title):
                self.assertEqual(classify_kind(title), KIND_MANGA)

    def test_kc_label_right_after_japanese_text_is_manga(self):
        """「モーニングKC」のように日本語の直後に付く KC もレーベルとして扱うこと。"""
        self.assertEqual(classify_kind("紛争でしたら八田まで(1) (モーニングKC)"), KIND_MANGA)

    def test_books_without_comics_label_are_book(self):
        for title in [
            "コンビニ人間",
            "現代思想入門 (講談社現代新書 2653)",
            "ＮＨＫ １００分 ｄｅ 名著 ウィトゲンシュタイン (ＮＨＫテキスト)",
            "GitHub CI/CD実践ガイド",
            "Kubernetesの知識地図",
            "コミュニケーション論",
            "エコノミックアニマル",
        ]:
            with self.subTest(title=title):
                self.assertEqual(classify_kind(title), KIND_BOOK)

    def test_word_manga_alone_does_not_make_it_manga(self):
        """「マンガの原理」のようなマンガ論の本を、語「マンガ」だけでマンガ扱いしないこと。"""
        self.assertEqual(classify_kind("マンガの原理"), KIND_BOOK)
        self.assertEqual(classify_kind("漫画家入門"), KIND_BOOK)

    def test_kc_inside_english_word_is_not_a_label(self):
        self.assertEqual(classify_kind("Backend Stackcraft"), KIND_BOOK)

    def test_empty_or_none_title_is_book(self):
        self.assertEqual(classify_kind(""), KIND_BOOK)
        self.assertEqual(classify_kind(None), KIND_BOOK)


if __name__ == "__main__":
    unittest.main()
