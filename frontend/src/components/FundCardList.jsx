import React from 'react';
import FundCard from './FundCard';

export default function FundCardList({ watchlist, funds, selectedFund, onSelect, onRemove }) {
    if (watchlist.length === 0) {
        return (
            <div className="empty-state">
                <div className="empty-icon">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <line x1="12" y1="1" x2="12" y2="23" /><line x1="17" y1="5" x2="17" y2="23" />
                        <line x1="7" y1="9" x2="7" y2="23" /><line x1="22" y1="7" x2="22" y2="23" />
                        <line x1="2" y1="13" x2="2" y2="23" />
                    </svg>
                </div>
                <h3>开始追踪你的基金</h3>
                <p>在上方输入 6 位基金代码，点击添加</p>
            </div>
        );
    }

    return (
        <div className="fund-grid">
            {watchlist.map(code => (
                <FundCard
                    key={code}
                    code={code}
                    data={funds[code]}
                    selected={selectedFund === code}
                    onSelect={onSelect}
                    onRemove={onRemove}
                />
            ))}
        </div>
    );
}
