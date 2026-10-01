# 設計メモ

## 方針

- **静的サイト + PC のコンパニオン**：画面は GitHub Pages に置ける静的ファイルだけで作り、AI（ローカル LLM）と Obsidian の Vault への書き込みは PC 側が受け持つ
- **同じコードをブラウザと PC で使う**：`web/core/` はブラウザと Node の両方で動く純粋な ES モジュール（DOM や `fs` に依存しない）。パーサ・モデル・Obsidian 出力・分析パイプラインは 1 か所にしかない
- **依存ライブラリなし**：zip の読み書き、docx の解析、HTML の分解、k-means まで自前。ビルド不要で `web/` をそのまま公開できる
- **データは手元だけ**：ハイライトは端末の IndexedDB と PC の `data/*.json` にだけ置く。外に出るのは、ローカル LLM への依頼と、おすすめの本を探す検索語・書名（Google Books / 国立国会図書館サーチ）のみ。ハイライトの本文は外部に送らない

## ブラウザからローカル LLM に届かせる方法の比較

| 方法 | PC の Chrome/Edge/Firefox | PC の Safari | スマホ | 備考 |
| --- | --- | --- | --- | --- |
| GitHub Pages → `http://localhost:11434` 直接 | ○（ローカルネットワーク許可 + `OLLAMA_ORIGINS`） | ×（mixed content） | × | 設定が多い |
| `http://localhost:8787`（コンパニオンが画面も配信） | ○ | ○ | × | 同一オリジンなので CORS も許可も不要 |
| GitHub Pages / `https://…ts.net` → コンパニオン（Tailscale Serve） | ○ | ○ | ○ | **おすすめ**。分析は PC で続くので画面を閉じてもよい |

コンパニオンサーバ（`cli/server.js`）は次を吸収する：

- **CORS**：許可したオリジン（`bh config origin`）・localhost・「Tailscale Serve で配信した自分自身（Origin と Host が同じ `*.ts.net`）」だけに応答。`*.ts.net` を丸ごと許すと公開されている他人の Funnel サイトから読めてしまうので許さない。Chrome の Private/Local Network Access のプリフライト（`Access-Control-Allow-Private-Network`）にも応える
- **Ollama の Host チェック**：Tailscale Serve は元の `*.ts.net` の Host ヘッダを渡すため、Ollama に直接向けると 403 になる。コンパニオンは `fetch` で中継するので Host が `127.0.0.1:11434` に揃う
- **DNS リバインディング対策**：既定で 127.0.0.1 にだけ待ち受け、Host が localhost / `*.ts.net` / 許可リスト以外なら拒否（トークン設定時はトークンで判定）

## データモデル（`web/core/model.js`）

```
Library = { version, books: { [id]: Book }, highlights: { [id]: Highlight }, updatedAt }
Book      = { id: 'b'+hash(書名の正規化), title, author, sources: ['kindle'|'playbooks'], asin?, updatedAt, deleted? }
Highlight = { id: 'h'+hash(bookId+本文の正規化), bookId, source, kind: 'highlight'|'note',
              text, note, chapter, location, locationEnd, page, color, createdAt,
              favorite, tags, userNote, importedAt, updatedAt, deleted?, supersededBy? }
```

- ID は内容から決まるので、何度取り込んでも・どの端末で取り込んでも同じ点は同じ ID になる
- 取り込み（`mergeParsed`）は空欄を補うだけで、ユーザーの編集（★・メモ・タグ・削除）は変えない
- Kindle で伸ばしたハイライト（本文が包含関係・位置が重なる）は新しい方に置き換え、編集を引き継ぐ
- 削除は墓標（`deleted: true`）で持つので、再取り込みでも同期でも復活しない
- 端末間の同期（`mergeLibraries`）は **欄ごと** に統合し、どちら向きに統合しても同じ結果になる
  - 取り込みで決まる欄（章・色・位置・メモなど）は `updatedAt` が新しい方を採り、空欄はもう一方で埋める
  - 利用者の欄（★・タグ・自分のメモ・削除）は、利用者が編集した時刻 `userUpdatedAt` が新しい方をまとめて採る。取り込みで欄が埋まっても `userUpdatedAt` は変わらないので、未同期のスマホの編集が PC 側に上書きされない（古い版のデータは、編集の跡があれば `updatedAt` で代用）
  - 伸ばしたハイライトに置き換わった点（`supersededBy`）は、どちらの端末から来ても消えたまま
- 包含関係での置き換え（伸ばしたハイライト）は Kindle だけで、位置が重なるか、位置が無ければ同じページのときだけ行う（Play ブックスの別ページの短いハイライトを消さない）
- おすすめへの反応は `library.feedback[書名キー] = { status: read|want|no|'', updatedAt }` に持ち、同期では新しい方を採る
- バックアップは `{ format: 'book-highlights/backup', library, analysis }`。取り込み（Web・`bh import`・サーバ共通の `web/core/importing.js`）では、ライブラリを統合し、分析結果は手元より新しいときだけ採用する

## 分析結果（`web/core/analysis/pipeline.js`）

```
Analysis = { createdAt, model: { chat, embed }, stats,
  lines:  [{ id, name, summary, insight, keywords, highlightIds, bookIds }],   // 線
  planes: [{ id, name, summary, lineIds }],                                     // 面
  solid:  { title, core, relations: [{ from, to, type, description }], principles, questions },  // 立体
  isolated: [highlightId],                                                      // まだつながらない点
  recommendations: [{ title, author, kind, planeId, reason, verified }] }
```

- 点 → 線：球面 k-means（k ≒ 点の数 / 5、上限 40）。中心から外れすぎた点（類似度が平均 − 1.5σ 未満）と 1 点だけの束は「まだつながらない点」にする
- 線 → 面：線の中心ベクトル + 線の説明文の埋め込みで k-means（k ≒ √線の数、2〜8）
- LLM への依頼は線 1 本・面 1 つずつに分け、プロンプトには中心に近い点から最大 12 件だけ入れる
- `response_format` は `json_schema` → `json_object` → なし の順に自動で緩める（LM Studio は `json_object` 非対応、古いサーバは `json_schema` 非対応）。壊れた JSON は 1 回だけ言い直させる
- LLM の結果は「メンバー構成 + モデル + プロンプト版」のハッシュでキャッシュ。埋め込みも点ごとにキャッシュ

## おすすめの本（`web/core/analysis/recommend.js`）

ローカル LLM（特に 7B 以下）は本の知識があいまいで、書名を挙げさせると実在しない本をもっともらしく作る（実機の検証でも 1.5B・7B とも架空の書名が出た）。そこで:

1. **書誌 DB を使う版（通常）**：LLM は「検索語」だけを決める → Google Books（関連度順）で実在する候補を集める（既読の本は除く）→ LLM が番号で選び、理由を書く。選ばれる本は必ず実在する
2. **検索できないとき**：LLM に書名を挙げさせ、Google Books → 国立国会図書館サーチ（タイトル + 著者で照合。CORS 対応でブラウザからも使える）で確認。見つからない本には印を付け、確認できた本を先に並べる
3. おすすめの段階で失敗しても、線・面・立体の結果は捨てない（`recommendationNote` に理由を残し、「おすすめを選び直す」で再実行できる）

## 画面の安全性

- すべての埋め込みはエスケープする（`web/js/html.js` の `html` タグ付きテンプレート）。知識マップの SVG も ID・座標・ラベルをエスケープ／数値化して組み立てる
- 外部由来の URL（書誌 DB のリンク・表紙画像）は `https:` だけ通す
- ブックマークレットからの `postMessage` は Kindle ノートブックのドメイン（`read.amazon.com` / `.co.jp` など）の完全一致だけ受け付ける
- Obsidian に書き出す引用文中のコードフェンス（```` ```dataviewjs ```` など）は無害化する

## Obsidian 出力（`web/core/obsidian.js`）

- 自動生成部分を `<!-- bh:start -->`〜`<!-- bh:end -->` で囲み、外側（自分のメモ）は残す。frontmatter は生成するキーだけ差し替える
- 線のノートは点を `![[Highlights/Books/書名#^hxxxx]]` で埋め込むので、元の本のノートと常に一致する
- `Knowledge Map.canvas` の配置（中心 = 核、内側の輪 = 面、外側の輪 = 線）は Web アプリの知識マップと同じ関数（`layoutKnowledgeMap`）
- `.bh-manifest.json` に前回書き出したファイルと、各ファイルの持ち主（本・線・面の ID）を記録する
  - 次の書き出しでは同じファイルを同じ持ち主に使い続ける（書名の先頭 80 文字が同じ本が増えても付け替わらない）
  - 分析から外れたノートは、自分の書き込み（見出しの下・`bh:end` の下・frontmatter に足したキー・`tags` に足したタグ・コメント）が無ければ削除、あれば残す
- frontmatter は YAML の最上位のキー単位で扱う（空白や引用符を含む名前、複数行の値、コメントも 1 まとまり）。生成するキーだけ差し替え、`tags` は自分で足したタグと合わせる。CRLF・BOM のノートは元の形のまま書き戻す
- コンパニオンサーバは同期・取り込み・分析結果の保存のあと、少し待って Vault を自動で書き出し、結果を `data/state.json` の `lastExport` に残す（`/api/info` で Web アプリに見せる）。Web アプリで「Vault 内のフォルダ名」を自分で決めたときは「PC に書き出す」でそれを送り、PC の設定にも残す（決めていなければ Web 側が PC の設定に合わせる）。分析中に届いた同期の自動書き出しは、分析が Vault まで書き出せなかったときに分析の後で行う
- 線・面の ID は分析し直すと変わるので、前回の持ち主による予約は「本」と「今も存在する線・面」にだけ効かせる（同じ名前の線・面は同じノートを使い続け、自分のメモが残る）
- 同名になるノートの「 (2)」は ID 順で決めるので、並び順が変わってもノートが入れ替わらない

## パーサ（`web/core/parsers/`）

| 形式 | 判定 | 要点 |
| --- | --- | --- |
| My Clippings.txt | `==========` 区切り + メタ行 | 日本語・英語ほか 9 言語のメタ行、旧形式の「ページ222」、`Loc. 1234-56` の省略形、BOM・CRLF・Shift_JIS、メモをハイライトに紐付け |
| Kindle のエクスポート HTML | `noteHeading` / `noteText` | 新旧形式（旧形式は `</h3>` と `</div>` が食い違う）、色・ページ・位置（`1,210`）・章 |
| ブックマークレット JSON | `format: book-highlights/kindle-notebook` | read.amazon.co.jp/notebook の DOM から集めたもの |
| Play ブックスのメモ | 注釈の表（3 列・最後の段落が日付） | docx は文字の網掛け、HTML は CSS クラスの背景色で「ハイライト本文」と「読者のメモ」を区別。新形式で 2 回出る注釈（色別・全体）は 1 回にまとめ、色と章をそれぞれから取る |

文言（「All your annotations」など）に頼らず構造で判定しているので、表示言語が違っても読める。
