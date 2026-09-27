// 取り込み・書き出し・設定の画面
import { html } from '../html.js';
import { libraryStats } from '../../core/model.js';
import { ACCEPT } from '../../core/parsers/index.js';
import { isoDate } from '../../core/text.js';
import { fsSupported } from '../services.js';

export const importView = {
  render({ state }) {
    return html`<a class="back" href="#/settings">‹ 設定</a>
      <div class="page-head"><div><h1>取り込み</h1><div class="sub">ファイルは端末の中だけで読み取ります</div></div></div>
      <label class="drop" id="drop">
        <input type="file" id="file-input" multiple accept="${ACCEPT}">
        <b>ファイルを選ぶ</b><br><span class="help">またはここにドロップ（.txt .html .docx .md .json .zip）</span>
      </label>
      <div id="import-result"></div>

      <div class="section"><h2>Kindle</h2></div>
      <div class="card">
        <details>
          <summary>Kindle アプリで読んでいる（おすすめ: ブックマークレット）</summary>
          <p class="help">アプリで引いた線は Amazon のノートブック（read.amazon.co.jp/notebook）に集まります。PC のブラウザで次の手順を 1 度設定すれば、全ての本のハイライトをまとめて取り込めます。</p>
          <ol class="help">
            <li>下のボタンをブックマークバーにドラッグして登録します（または「コピー」して、新しいブックマークの URL に貼り付けます）。</li>
            <li><a href="https://read.amazon.co.jp/notebook" target="_blank" rel="noopener">read.amazon.co.jp/notebook</a> を開いてログインします。</li>
            <li>登録したブックマークをクリック → 集め終わったら「アプリに送る」か「ファイルに保存」。保存した JSON はこの画面で取り込めます。</li>
          </ol>
          <div class="row"><a class="btn primary" id="bookmarklet" href="#" title="ブックマークバーへドラッグ">📥 Kindle ハイライトを集める</a><button class="btn small" data-action="copy-bookmarklet">コピー</button></div>
        </details>
        <details>
          <summary>Kindle 端末（Paperwhite など）で読んでいる</summary>
          <ol class="help">
            <li>Kindle を USB で PC につなぎます。</li>
            <li><span class="code">documents/My Clippings.txt</span> を選んで取り込みます。</li>
          </ol>
          <p class="help">何度取り込んでも重複しません。伸ばしたハイライトは新しい方に置き換わります。</p>
        </details>
        <details>
          <summary>アプリの「ノートブックをエクスポート」を使う（1 冊ずつ）</summary>
          <p class="help">Kindle アプリで本を開く → ノートブック → 共有（エクスポート）→「引用なし」でメール送信。届いた HTML ファイルを取り込みます。</p>
        </details>
      </div>

      <div class="section"><h2>Play ブックス</h2></div>
      <div class="card">
        <details>
          <summary>Google ドライブの「Play ブックスのメモ」から</summary>
          <ol class="help">
            <li>Play ブックスの設定で「メモ、ハイライト、しおりを Google ドライブに保存」をオンにします（本ごとのドキュメントが自動で作られます）。</li>
            <li>PC: Google ドライブで <b>「Play ブックスのメモ」フォルダを右クリック → ダウンロード</b>。できた zip をそのまま取り込めます。</li>
            <li>スマホ: ドキュメントを開き「共有とエクスポート → 形式を指定して保存 → Word（.docx）」で保存し、ここで選びます。</li>
          </ol>
          <p class="help">.docx / .html / .md のどれでも読めます。ドキュメントが自動で更新されるので、時々ダウンロードし直すと差分だけ増えます。</p>
        </details>
      </div>

      <div class="section"><h2>ほかに</h2></div>
      <div class="card row spread"><span class="help grow">架空の 8 冊・48 の点で動きを試せます。</span><button class="btn" data-action="load-sample">サンプルを入れる</button></div>
      <p class="small muted" style="margin-top:12px">現在: 本 ${libraryStats(state.library).books} 冊 / 点 ${libraryStats(state.library).highlights} 件</p>`;
  },
};

export const exportView = {
  render({ state }) {
    const pcAvailable = state.settings.ai.mode === 'companion';
    return html`<a class="back" href="#/settings">‹ 設定</a>
      <div class="page-head"><div><h1>Obsidian に写す</h1><div class="sub">本ごとのノート・線・面・立体（Canvas）・おすすめ</div></div></div>
      <form class="card" data-form="export-settings">
        <label class="field"><span>Vault 内のフォルダ名</span><input type="text" name="root" value="${state.settings.root}" required></label>
        <label class="field"><span>Vault の名前（「Obsidian で開く」リンク用・任意）</span><input type="text" name="vaultName" value="${state.settings.vaultName}" placeholder="例: MyVault"></label>
        <button class="btn small" type="submit">保存</button>
      </form>

      <div class="section"><h2>書き出し方</h2></div>
      <div class="card stack">
        ${pcAvailable ? html`<div><h3>PC の Vault に書き出す</h3><p class="help">PC のコンパニオンサーバが、設定済みの Vault に直接書き込みます（スマホからでも可）。先に PC と同期します。</p><button class="btn primary" data-action="export-pc">PC に書き出す</button></div>` : ''}
        ${fsSupported ? html`<div><h3>この PC のフォルダに直接書き出す</h3><p class="help">Vault のフォルダを選ぶと、以後はワンタップで更新できます（Chrome / Edge）。</p><div class="row"><button class="btn ${pcAvailable ? '' : 'primary'}" data-action="export-fs">Vault に書き出す</button><button class="btn small" data-action="pick-vault">フォルダを選び直す</button></div></div>` : ''}
        <div><h3>zip でダウンロード</h3><p class="help">展開して Vault のフォルダに入れます（iPhone は「ファイル」アプリで展開して Obsidian のフォルダへ）。</p><button class="btn" data-action="export-zip">zip をダウンロード</button></div>
      </div>
      <div id="export-result"></div>
      <div class="section"><h2>書き出されるもの</h2></div>
      <ul class="card plain help">
        <li><span class="code">${state.settings.root}/Books/書名.md</span> — 点。ハイライトごとにブロック ID（^h…）付き</li>
        <li><span class="code">${state.settings.root}/Lines/</span> — 線。つながる点をブロック埋め込みで引用</li>
        <li><span class="code">${state.settings.root}/Planes/</span> — 面。線を束ねたテーマ</li>
        <li><span class="code">${state.settings.root}/Knowledge Map.md / .canvas</span> — 立体</li>
        <li><span class="code">${state.settings.root}/Recommendations.md</span> — おすすめの本</li>
      </ul>
      <p class="help" style="margin-top:8px">各ノートの <span class="code">bh:end</span> より下に書いた自分のメモは、書き出し直しても消えません。グラフビューで点と線のつながりが見えます。</p>`;
  },
};

export const settingsView = {
  render({ state }) {
    const ai = state.settings.ai;
    const s = libraryStats(state.library);
    return html`<div class="page-head"><h1>設定</h1></div>
      <div class="card stack">
        <a class="row spread" href="#/import"><b>取り込み</b><span class="muted">Kindle・Play ブックス ›</span></a>
        <a class="row spread" href="#/export"><b>Obsidian に写す</b><span class="muted">Vault へ書き出し ›</span></a>
      </div>

      <div class="section"><h2>AI（ローカル LLM）</h2></div>
      <form class="card" data-form="ai-settings">
        <fieldset style="border:none;padding:0;margin:0">
          <legend class="small muted">分析を動かす場所</legend>
          <label class="row" style="margin:8px 0"><input type="radio" name="mode" value="companion" ${ai.mode === 'companion' ? 'checked' : ''}> <span><b>PC のコンパニオンサーバ</b>（おすすめ・スマホからも可）</span></label>
          <label class="row" style="margin:8px 0"><input type="radio" name="mode" value="direct" ${ai.mode === 'direct' ? 'checked' : ''}> <span><b>このブラウザから LLM に直接</b>（PC のみ）</span></label>
        </fieldset>
        <div data-show="companion" ${ai.mode === 'companion' ? '' : 'hidden'}>
          <label class="field"><span>コンパニオンサーバの URL</span><input type="url" name="companionUrl" value="${ai.companionUrl}" placeholder="${state.servedByCompanion ? location.origin : 'http://localhost:8787 または https://<PC名>.<tailnet>.ts.net'}"></label>
          <label class="field"><span>トークン（設定した場合のみ）</span><input type="password" name="token" value="${ai.token}" autocomplete="off"></label>
          <p class="help">PC で <span class="code">node cli/bh.js serve</span>（<span class="code">npm link</span> 済みなら <span class="code">bh serve</span>）を起動します。スマホからは <span class="code">tailscale serve --bg 8787</span> で表示される https の URL を入れます。モデルは PC 側で <span class="code">bh config model …</span> で設定します。</p>
        </div>
        <div data-show="direct" ${ai.mode === 'direct' ? '' : 'hidden'}>
          <label class="field"><span>LLM サーバの URL（OpenAI 互換）</span><input type="url" name="baseUrl" value="${ai.baseUrl}" placeholder="http://localhost:11434"></label>
          <label class="field"><span>チャットモデル</span><input type="text" name="chatModel" value="${ai.chatModel}" list="model-list" placeholder="例: qwen3.5:9b"></label>
          <label class="field"><span>埋め込みモデル（任意）</span><input type="text" name="embedModel" value="${ai.embedModel}" list="model-list" placeholder="例: bge-m3（空なら文字の特徴で代用）"></label>
          <datalist id="model-list"></datalist>
          <p class="help">Ollama は環境変数 <span class="code">OLLAMA_ORIGINS=${location.origin}</span> を設定して再起動してください。Safari は https のページから localhost に接続できないため、コンパニオンサーバを使ってください。</p>
        </div>
        <div class="row"><button class="btn primary" type="submit">保存</button><button class="btn" type="submit" value="test">接続を確認</button></div>
        <div id="ai-test"></div>
      </form>

      <div class="section"><h2>PC と同期</h2></div>
      <div class="card stack">
        <p class="help">スマホで取り込んだ点や編集を PC に送り、PC の分析結果を受け取ります（コンパニオンサーバ経由）。</p>
        <label class="row"><input type="checkbox" data-action="toggle-autosync" ${state.settings.autoSync ? 'checked' : ''}> 起動時に自動で同期する</label>
        <div class="row"><button class="btn" data-action="sync">今すぐ同期</button><span class="small muted">${state.lastSync ? `最終: ${isoDate(state.lastSync)} ${new Date(state.lastSync).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' })}` : '未同期'}</span></div>
      </div>

      <div class="section"><h2>データ</h2></div>
      <div class="card stack">
        <p class="help">ハイライトはこの端末（ブラウザ）の中だけに保存されています。本 ${s.books} 冊 / 点 ${s.highlights} 件。</p>
        <div class="row"><button class="btn" data-action="backup">バックアップを保存</button><a class="btn" href="#/import">バックアップから戻す</a></div>
        <button class="btn danger" data-action="clear-all">この端末のデータをすべて消す</button>
      </div>
      <p class="small muted" style="margin:24px 0 8px;text-align:center">点と線 — <a href="https://github.com/nihi566/book-highlights" target="_blank" rel="noopener">GitHub</a></p>`;
  },
  mount(root) {
    const form = root.querySelector('form[data-form="ai-settings"]');
    form.addEventListener('change', (e) => {
      if (e.target.name !== 'mode') return;
      for (const el of form.querySelectorAll('[data-show]')) el.hidden = el.dataset.show !== e.target.value;
    });
  },
};
