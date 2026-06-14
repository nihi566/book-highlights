import React from 'react';

function StatCard({ title, value, valueClass = '' }) {
  return (
    <div className="card stat-card">
      <div className="card-title">{title}</div>
      <div className={`card-value ${valueClass}`}>{value}</div>
    </div>
  );
}

export default function SummaryCards({ books }) {
  const total       = books.length;
  const campaignCnt = books.filter((b) => b.is_unlimited === 0 && b.campaign_text).length;
  const unlimitedCnt = books.filter((b) => b.is_unlimited === 1).length;
  const purchasedCnt = books.filter((b) => b.is_purchased === 1).length;

  return (
    <div className="summary-grid">
      <StatCard title="総監視数"        value={total}        />
      <StatCard title="キャンペーン対象" value={campaignCnt}  valueClass="highlight-red"   />
      <StatCard title="Unlimited 対象"  value={unlimitedCnt} valueClass="highlight-green" />
      <StatCard title="購入済み"         value={purchasedCnt} valueClass="highlight-muted" />
    </div>
  );
}
