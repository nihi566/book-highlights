// index.html / 404.html の転送ロジックを取り出して確かめる
const assert = require('assert');
const fs = require('fs');
for (const f of ['index.html', '404.html']) {
  const src = fs.readFileSync(f, 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
  const module = { exports: {} };
  new Function('module', src)(module);
  const { newPath } = module.exports;
  assert.strictEqual(newPath('/book-highlights/'), '/bookshelf/');
  assert.strictEqual(newPath('/book-highlights'), '/bookshelf');
  assert.strictEqual(newPath('/book-highlights/foo/bar'), '/bookshelf/foo/bar');
  assert.strictEqual(newPath('/book-highlights/wishlist.xml'), '/bookshelf/wishlist.xml');
  assert.strictEqual(newPath('/book-highlights-x/'), '/bookshelf/');
  assert.strictEqual(newPath('/'), '/bookshelf/');
}
console.log('ok');
