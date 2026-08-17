import React, { useState, useEffect, useCallback } from 'react';
import { fetchRanking } from '../hooks/useFundApi';

const TABS = [
    { key: 'rzdf', label: '当日收益 TOP 5', pctField: 'daily_pct', pctLabel: '当日' },
    { key: '1yzf', label: '近一月收益 TOP 5', pctField: 'month1', pctLabel: '近1月' },
];

export default function FundRecommend({ watchlist, onAdd, onSelect }) {
    const [activeTab, setActiveTab] = useState('rzdf');
    const [list, setList] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);

    const load = useCallback(async (sort) => {
        setLoading(true);
        setError(false);
        try {
            const data = await fetchRanking(5, sort);
            setList(data.list || []);
        } catch (e) {
            setError(true);
        }
        setLoading(false);
    }, []);

    useEffect(() => { load(activeTab); }, [activeTab, load]);

    const handleTabChange = (key) => {
        if (key !== activeTab) {
            setActiveTab(key);
        }
    };

    const currentTab = TABS.find(t => t.key === activeTab) || TABS[0];

    return (
        <section className="recommend">
            <div className="recommend-head">
                <h3>
                    <span className="recommend-spark">▲</span>
                    收益排行
                    <span className="recommend-sub">开放式基金 · 数据来源东方财富</span>
                </h3>
                <button
                    className={`btn-icon recommend-refresh ${loading ? 'spinning' : ''}`}
                    onClick={() => load(activeTab)}
                    title="刷新数据"
                    disabled={loading}
                >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" />
                        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                    </svg>
                </button>
            </div>

            <div className="recommend-tabs">
                {TABS.map(tab => (
                    <button
                        key={tab.key}
                        className={`recommend-tab ${activeTab === tab.key ? 'active' : ''}`}
                        onClick={() => handleTabChange(tab.key)}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {loading && (
                <div className="recommend-status">
                    <span className="dot-pulse"><span></span><span></span><span></span></span>
                </div>
            )}

            {!loading && error && (
                <div className="recommend-status recommend-error">推荐数据暂时获取失败，点击右上角重试</div>
            )}

            {!loading && !error && list && list.length === 0 && (
                <div className="recommend-status">暂无推荐数据</div>
            )}

            {!loading && !error && list && list.length > 0 && (
                <div className="recommend-list">
                    {list.map((f, i) => {
                        const added = watchlist.includes(f.code);
                        const val = parseFloat(f[currentTab.pctField]);
                        const hasVal = !Number.isNaN(val);
                        const isGain = hasVal && val >= 0;
                        return (
                            <div
                                className="recommend-item"
                                key={f.code}
                                style={{ animationDelay: `${i * 0.05}s`, cursor: 'pointer' }}
                                onClick={() => onSelect && onSelect(f.code)}
                                title="点击查看详情"
                            >
                                <span className={`rank-badge rank-${i + 1}`}>{i + 1}</span>
                                <div className="recommend-info">
                                    <div className="recommend-name" title={f.name}>{f.name}</div>
                                    <div className="recommend-code">{f.code}</div>
                                </div>
                                <div className="recommend-return">
                                    <div className={`recommend-pct ${hasVal ? (isGain ? 'gain-text' : 'loss-text') : ''}`}>
                                        {hasVal ? `${val > 0 ? '+' : ''}${val.toFixed(2)}%` : '—'}
                                    </div>
                                    <div className="recommend-return-label">{currentTab.pctLabel}</div>
                                </div>
                                <button
                                    className={`recommend-add ${added ? 'added' : ''}`}
                                    onClick={(e) => { e.stopPropagation(); if (!added) onAdd(f.code); }}
                                    disabled={added}
                                >
                                    {added ? '已在自选' : '+ 加自选'}
                                </button>
                            </div>
                        );
                    })}
                </div>
            )}
        </section>
    );
}
