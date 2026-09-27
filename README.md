# 点と線 — book-highlights

Play ブックスと Kindle で線を引いた箇所（ハイライト）を 1 か所に集めて **Obsidian に写し**、PC の **ローカル LLM** がそれらの「点」を **線 → 面 → 立体** に組み立てて、次に読む本を提案するシステムです。

- **Web アプリ（モバイルファースト・PWA）** … GitHub Pages に置ける静的サイト。ハイライトの閲覧・検索・お気に入り・メモ・タグ付け、Obsidian への書き出し、AI 分析の結果表示
- **PC のコマンド `bh`** … 取り込み・Obsidian の Vault への書き込み・AI 分析、そしてスマホと PC をつなぐ **コンパニオンサーバ**
- 依存ライブラリなし（Node.js 20 以上だけ）。ハイライトは自分の端末と PC の中だけに保存されます

## 解決したい課題

| これまで | これから |
| --- | --- |
| アプリを開く → 本を開く → ハイライトを見る | Obsidian（とこの Web アプリ）で、全ての本の線を引いた箇所をすぐ見られる |
| ハイライトは本ごとにバラバラの「点」 | ローカル LLM が点をつないで「線（概念）」にし、線を束ねて「面（テーマ）」にし、面の関係から「立体（知識の全体像）」を作る |
| 次に何を読むかは勘 | 立体と「まだ答えの無い問い」から、AI が次の本を選ぶ（実在を書誌データベースで確認） |

## 全体の構成

```mermaid
flowchart LR
  subgraph Sources[取り込み元]
    K1[Kindle 端末<br>My Clippings.txt]
    K2[Kindle アプリ<br>read.amazon.co.jp/notebook<br>（ブックマークレット）]
    K3[Kindle アプリの<br>ノートブックのエクスポート HTML]
    P1[Play ブックス<br>ドライブ「Play ブックスのメモ」<br>.docx/.html/.md/.zip]
  end
  subgraph Phone[スマホ / どこからでも]
    W[Web アプリ PWA<br>GitHub Pages<br>IndexedDB に保存]
  end
  subgraph PC[PC]
    C[コンパニオンサーバ<br>bh serve :8787]
    L[ローカル LLM<br>Ollama / LM Studio]
    O[(Obsidian Vault)]
  end
  Sources --> W
  Sources --> C
  W <-- 同期・分析の依頼<br>Tailscale の https --> C
  C -- OpenAI 互換 API --> L
  C -- Markdown / Canvas --> O
  O -. Obsidian Sync / iCloud など .-> Phone
```

### 「GitHub の静的サイトで AI 機能を使うには？」への答え

GitHub Pages は静的ファイルしか置けず、スマホではローカル LLM を動かせません。そこで **AI の処理は PC のコンパニオンサーバが引き受け**、Web アプリはそこに頼むだけにしています。

1. **PC**：`bh serve` がローカル LLM（Ollama など）への中継・分析ジョブ・Vault への書き込みを担当します。`http://localhost:8787` を開けば、同じ Web アプリがそのまま使えます（Safari を含むすべてのブラウザで動作）。
2. **スマホ**：`tailscale serve --bg 8787` で PC を自分専用の https URL（`https://<PC名>.<tailnet>.ts.net`）として公開し、スマホの Web アプリ（GitHub Pages 版でもよい）の設定に入れます。分析は PC で走るので、**スマホの画面を閉じても続きます**。
3. **結果は Obsidian にも入る**：分析結果は Vault の Markdown になるので、Obsidian の同期でスマホからも読めます。PC が止まっていても、読み返すのに困りません。

直接つなぐ方式（ブラウザ → `http://localhost:11434`）も選べますが、Safari は https のページから localhost に接続できず、Chrome / Firefox でも「ローカルネットワークへのアクセス」の許可と `OLLAMA_ORIGINS` の設定が要るため、コンパニオンサーバ経由をおすすめしています。詳しくは [docs/architecture.md](docs/architecture.md)。

## はじめかた

### 1. まず試す（インストール不要）

GitHub Pages で公開した Web アプリ（このリポジトリなら `https://nihi566.github.io/book-highlights/`。公開手順は [docs/setup.md](docs/setup.md)）を開き、「サンプルで試す」を押します。架空の 8 冊・48 の点で画面を確認できます。ホーム画面に追加すればアプリのように使えます。

### 2. PC を準備する（Obsidian と AI）

```sh
git clone https://github.com/nihi566/book-highlights.git
cd book-highlights

# ローカル LLM（例: Ollama）
ollama pull qwen3.5:9b     # 日本語が得意なチャットモデル（PC の性能に合わせて 4b〜27b）
ollama pull bge-m3         # 多言語の埋め込みモデル（任意。無ければ文字の特徴で代用）

node cli/bh.js config vault ~/Documents/MyVault   # Obsidian の Vault
node cli/bh.js config model qwen3.5:9b
node cli/bh.js config embed bge-m3
node cli/bh.js serve                               # → http://localhost:8787
```

`npm link` すれば `bh` だけで呼べます。LM Studio や llama.cpp server も `bh config url http://localhost:1234` のように OpenAI 互換 API の URL を指定すれば使えます。

### 3. スマホからつなぐ

```sh
tailscale serve --bg 8787   # https://<PC名>.<tailnet>.ts.net で PC に届くようになる
```

スマホに Tailscale アプリを入れて同じアカウントでログインし、Web アプリの「設定 → AI」にその URL を入れて「接続を確認」。詳しい手順は [docs/setup.md](docs/setup.md)。

## ハイライトの取り込み

| 読み方 | 方法 |
| --- | --- |
| Kindle アプリ（スマホ・タブレット） | PC のブラウザで **ブックマークレット**（取り込み画面からドラッグして登録）を read.amazon.co.jp/notebook で実行 → 全冊まとめて「アプリに送る」か JSON で保存 |
| Kindle 端末 | USB でつなぎ `documents/My Clippings.txt` を取り込む |
| Kindle アプリ（1 冊ずつ） | ノートブック → エクスポート でメールした HTML を取り込む |
| Play ブックス | 設定で「メモ、ハイライト、しおりを Google ドライブに保存」をオン → ドライブの「Play ブックスのメモ」フォルダをダウンロード（zip）してそのまま取り込む。スマホでは各ドキュメントを .docx で保存 |

何度取り込んでも重複しません。Kindle で伸ばしたハイライトは新しい方に置き換わり、付けたお気に入り・メモ・タグは引き継がれます。削除した点は再取り込みしても戻りません。Play ブックスでは、長いハイライトの一部を別の箇所でもう一度引いた短いハイライトも、それぞれ残ります。

PC で `bh import <ファイル...>` を実行すると、Vault を設定していれば続けて Obsidian にも書き出します（`--no-obsidian` で止められます）。

### スマホと PC の同期

スマホで付けた★・メモ・タグ・削除と、PC で取り込んだ章・色・位置などは、**欄ごとに統合**します。自分の編集はいつ付けたかで比べるので、同期する前に PC で別の形式を取り込んで同じハイライトの欄が埋まっても、スマホの編集は消えません。

コンパニオンサーバは、同期・取り込み・分析のあとに **Vault を自動で書き出します**（`bh config autoexport off` で止められます）。最後に書き出した時刻と出力先は、Web アプリの「Obsidian に写す」画面と `bh config` で確かめられます。PC のブラウザで Vault のフォルダを直接選んでいる場合も、取り込み・同期・編集のあとに自動で書き出します。

## Obsidian に書き出されるもの

```
Highlights/
├── Index.md                 本の一覧（入口）
├── Books/<書名>.md          点：ハイライトを章ごとに。各ハイライトに ^h… のブロック ID
├── Lines/<線>.md            線：抽象化した概念。つながる点を ![[本#^h…]] で埋め込み
├── Planes/<面>.md           面：線を束ねたテーマと、他の面との関係
├── Knowledge Map.md         立体：知識の核・面・原則・問い
├── Knowledge Map.canvas     立体を Canvas で俯瞰（核 → 面 → 線）
└── Recommendations.md       おすすめの本（確認済みかどうか付き）
```

- ノートの `<!-- bh:end -->` より下に書いた **自分のメモは、書き出し直しても消えません**
- Obsidian で足したプロパティ（`date read` のように空白を含む名前や、`tags` に足したタグも）はそのまま残ります。改行コードが Windows 形式（CRLF）のノートもそのまま扱います
- 同名の自分のノートがあれば上書きしません。分析し直して消えた線・面のノートは、自分の書き込み（メモ・プロパティ・タグ）が無いものだけ片付けます
- 書名の先頭が同じ本が後から増えても、前回と同じノートを同じ本に使い続けます（自分のメモが別の本に付け替わりません）
- グラフビューで、本（点）と線・面のつながりが立体的に見えます

## AI 分析のしくみ（点 → 線 → 面 → 立体）

1. **点**：ハイライトを埋め込みベクトルにする（`bge-m3` など。無ければ文字 n-gram の TF-IDF）
2. **線**：意味の近い点を球面 k-means で束ね、LLM が共通する考えを一段抽象化して「概念」にする。本をまたいだつながりが見つかる
3. **面**：線を束ね、LLM がテーマにまとめる
4. **立体**：LLM が面どうしの関係（支える・対立する・具体化する・補完する）、知識の核、行動の原則、まだ答えの無い問いを組み立てる
5. **おすすめ**：LLM が立体と「問い」から本を探す検索語を決め、**Google Books で見つけた実在の本の中から**「深める・広げる・揺さぶる」本を選んで理由を書く（既読の本は除く）。検索できないときは LLM が挙げた書名を Google Books → 国立国会図書館サーチで照合し、見つからない本には印を付ける
6. **おすすめへの反応**：おすすめの本に「読んだ／読みたい／興味なし」を付けると、次からその本は挙がらず、「読みたい」「興味なし」の傾向が次の選定に使われます。反応は PC と同期され、Obsidian のおすすめノートにも「読みたい本」として出ます

小さなローカルモデルでも崩れにくいよう、1 回の依頼を小さくして JSON スキーマで出力を固定しています（ローカル LLM は本の知識があいまいで、書名だけを挙げさせると実在しない本を作りがちなので、おすすめは書誌データベースの候補から選ばせています）。結果はメンバー構成ごとにキャッシュするので、点が増えたときの再分析は変わった部分だけで済みます。

## 開発

```sh
npm test            # node:test（パーサ・モデル・Obsidian 出力・分析・サーバ・CLI）
node cli/bh.js serve  # http://localhost:8787 で Web アプリを開発
```

- `web/` … 静的サイト（GitHub Pages にそのまま公開）。`web/core/` はブラウザと Node で共用する純粋なモジュール
- `cli/` … `bh` コマンドとコンパニオンサーバ
- `.github/workflows/pages.yml` … テストと GitHub Pages への公開（リポジトリの Settings → Pages で Source を「GitHub Actions」に）

ハイライトのデータ（`data/`）はリポジトリに含めません。
