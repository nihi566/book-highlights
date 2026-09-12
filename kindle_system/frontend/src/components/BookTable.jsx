import React, { useEffect, useRef } from 'react';

function cleanCampaignText(raw = '') {
  return raw
    .replace('ご購入時にプロモーションが適用されます', '')
    .split('|')
    .map((s) => s.trim())
    .filter(Boolean)
    .join(' | ');
}

function PriceCell({ price }) {
  if (price === null || price === undefined) return <span className="price-null">-</span>;
  return <span>¥{price.toLocaleString()}</span>;
}

function DiscountBadge({ rate }) {
  if (rate < 20) return null;
  return <span className="badge discount">{rate}% 還元</span>;
}

function SourceBadge({ source }) {
  if (source !== 'bookmeter') return null;
  return <span className="badge source-bookmeter">読書メーター</span>;
}

function WantToggle({ asin, isWanted, onToggle }) {
  return (
    <button
      className={`btn-want ${isWanted === 1 ? 'active' : ''}`}
      onClick={() => onToggle(asin, isWanted)}
      title={isWanted === 1 ? '欲しい本から外す' : '欲しい本に追加'}
    >
      {isWanted === 1 ? '★ 欲しい' : '☆ 欲しい'}
    </button>
  );
}

function PurchaseToggle({ asin, isPurchased, onToggle }) {
  return (
    <label className="purchase-toggle" htmlFor={`purchase-cb-${asin}`}>
      <input
        id={`purchase-cb-${asin}`}
        type="checkbox"
        className="purchase-cb"
        checked={isPurchased === 1}
        onChange={() => onToggle(asin, isPurchased)}
      />
      <span className="toggle-track">
        <span className="toggle-thumb" />
      </span>
      <span className="toggle-label">
        {isPurchased === 1 ? '購入済み' : '未購入'}
      </span>
    </label>
  );
}

export default function BookTable({
  books,
  loading,
  onTogglePurchase,
  onToggleWant,
  onOpenHistory,
  hasMore,
  onLoadMore,
}) {
  const observerRef = useRef(null);

  useEffect(() => {
    if (!hasMore || loading) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          onLoadMore();
        }
      },
      { rootMargin: '200px' }
    );

    const currentTarget = observerRef.current;
    if (currentTarget) {
      observer.observe(currentTarget);
    }

    return () => {
      if (currentTarget) {
        observer.unobserve(currentTarget);
      }
    };
  }, [hasMore, loading, onLoadMore]);

  if (loading) {
    return (
      <div className="table-container">
        <div className="empty-state">
          <div className="loading-dots">
            <span /><span /><span />
          </div>
          <p>データをロード中...</p>
        </div>
      </div>
    );
  }

  if (books.length === 0) {
    return (
      <div className="table-container">
        <div className="empty-state">
          <p>該当する書籍がありません。</p>
        </div>
      </div>
    );
  }

  return (
    <div className="table-container">
      <table>
        <thead>
          <tr>
            <th className="col-th-title">書籍名 / キャンペーン</th>
            <th className="col-th-price">販売価格</th>
            <th className="col-th-point">還元 PT</th>
            <th className="col-th-actual">実質価格</th>
            <th className="col-th-want">欲しい本</th>
            <th className="col-th-purchase">購入</th>
            <th className="col-th-history">推移</th>
          </tr>
        </thead>
        <tbody>
          {books.map((book) => {
            const discountRate = book.sell_price
              ? Math.round((book.point_value / book.sell_price) * 100)
              : 0;
            const campaign = cleanCampaignText(book.campaign_text);
            const isCampaign = !!campaign && book.is_unlimited !== 1;

            return (
              <tr
                key={book.asin}
                className={[
                  book.is_purchased === 1 ? 'purchased-row' : '',
                  book.is_wanted === 1    ? 'wanted-row'    : '',
                  isCampaign              ? 'campaign-target': '',
                ]
                  .filter(Boolean)
                  .join(' ')}
              >
                {/* 書籍名 */}
                <td className="col-title">
                  <a
                    href={`https://www.amazon.co.jp/dp/${book.asin}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="book-link"
                  >
                    {book.title || 'タイトル不明'}
                  </a>

                  <div className="badge-row">
                    {book.is_unlimited === 1 && <span className="badge unlimited">Unlimited</span>}
                    {book.is_wanted    === 1 && <span className="badge wanted">欲しい</span>}
                    {isCampaign && <span className="badge campaign">キャンペーン</span>}
                    <DiscountBadge rate={discountRate} />
                    {book.is_purchased === 1 && <span className="badge purchased">購入済み</span>}
                    <SourceBadge source={book.source} />
                  </div>

                  {(campaign || book.timestamp) && (
                    <div className="campaign-text">
                      {book.timestamp && (
                        <span className="updated-at">
                          更新: {book.timestamp.replace('T', ' ').substring(5, 16)}
                        </span>
                      )}
                      {campaign}
                    </div>
                  )}
                </td>

                {/* 価格列 */}
                <td className="col-price">
                  <PriceCell price={book.sell_price} />
                </td>
                <td className="col-point">
                  {book.point_value
                    ? `${book.point_value.toLocaleString()} pt`
                    : '0 pt'}
                  {discountRate > 0 && (
                    <span className="discount-rate"> ({discountRate}%)</span>
                  )}
                </td>
                <td className="col-actual">
                  <PriceCell price={book.actual_price} />
                </td>

                {/* 欲しい本トグル */}
                <td className="col-want">
                  <WantToggle
                    asin={book.asin}
                    isWanted={book.is_wanted}
                    onToggle={onToggleWant}
                  />
                </td>

                {/* 購入トグル */}
                <td className="col-purchase">
                  <PurchaseToggle
                    asin={book.asin}
                    isPurchased={book.is_purchased}
                    onToggle={onTogglePurchase}
                  />
                </td>

                {/* 価格推移ボタン */}
                <td className="col-history">
                  <button
                    className="btn-history"
                    onClick={() => onOpenHistory(book.asin, book.title || 'タイトル不明')}
                    title="価格推移を表示"
                  >
                    推移
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {hasMore && (
        <div ref={observerRef} className="loading-more">
          <div className="loading-dots">
            <span /><span /><span />
          </div>
          <p>さらに読み込み中...</p>
        </div>
      )}
    </div>
  );
}
