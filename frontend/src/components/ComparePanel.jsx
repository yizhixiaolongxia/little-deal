import React, { useState, useEffect } from 'react';
import { fetchHistoryFull } from '../hooks/useFundApi';
import { calcCompareMetrics } from '../utils/metrics';
import { fmtPct, fmtNum, fmtYears } from '../utils/format';

const COL_DEFS = [
    { key: 'name', label: '基金' },
    { key: 'yearsSpan', label: '历史跨度', fmt: fmtYears },
    { key: 'r1m', label: '近1月', fmt: fmtPct },
    { key: 'r3m', label: '近3月', fmt: fmtPct },
    { key: 'r6m', label: '近6月', fmt: fmtPct },
    { key: 'r1y', label: '近1年', fmt: fmtPct },
    { key: 'annReturn', label: '年化收益', fmt: fmtPct },
    { key: 'maxDD', label: '最大回撤', fmt: v => fmtPct(v ? -v : v) },
    { key: 'annVol', label: '年化波动', fmt: fmtPct },
    { key: 'sharpe', label: '夏普比率', fmt: v => fmtNum(v) },
    { key: 'calmar', label: 'Calmar', fmt: v => fmtNum(v) },
];

export default function ComparePanel({ watchlist, funds, onClose }) {
    const [results, setResults] = useState([]);
    const [loading, setLoading] = useState(true);
    const [sortKey, setSortKey] = useState(null);
    const [sortAsc, setSortAsc] = useState(false);
    const [sharpeWin, setSharpeWin] = useState('1y');
    const [isFullscreen, setIsFullscreen] = useState(false);

    useEffect(() => {
        loadCompareData();
    }, []); // eslint-disable-line

    const loadCompareData = async () => {
        setLoading(true);
        const res = [];
        for (const code of watchlist) {
            try {
                const data = await fetchHistoryFull(code);
                const list = (data?.Data?.LSJZList || []).slice().sort((a, b) =>
                    (a.FSRQ || '').localeCompare(b.FSRQ || ''));
                const metrics = calcCompareMetrics(list);
                const name = funds[code]?.realtime?.name || code;
                res.push({ code, name, metrics });
            } catch (e) {
                const name = funds[code]?.realtime?.name || code;
                res.push({ code, name, metrics: null, error: e.message });
            }
        }
        setResults(res);
        setLoading(false);
    };

    const getMetricVal = (metrics, key) => {
        if (!metrics) return null;
        if (key === 'sharpe') return metrics['sharpe' + sharpeWin];
        return metrics[key];
    };

    const toggleSort = (key) => {
        if (key === 'name') return;
        if (sortKey === key) {
            setSortAsc(!sortAsc);
        } else {
            setSortKey(key);
            setSortAsc(false);
        }
    };

    const getSorted = () => {
        if (!sortKey) return results;
        return [...results].sort((a, b) => {
            const va = getMetricVal(a.metrics, sortKey);
            const vb = getMetricVal(b.metrics, sortKey);
            if (va == null && vb == null) return 0;
            if (va == null) return 1;
            if (vb == null) return -1;
            return sortAsc ? va - vb : vb - va;
        });
    };

    const sorted = getSorted();

    // 计算每列最优值
    const bestByCol = {};
    COL_DEFS.forEach(def => {
        if (def.key === 'name') return;
        const vals = sorted.map(r => getMetricVal(r.metrics, def.key)).filter(v => v != null && isFinite(v));
        if (vals.length === 0) return;
        const isLowerBetter = def.key === 'maxDD' || def.key === 'annVol';
        bestByCol[def.key] = isLowerBetter ? Math.min(...vals) : Math.max(...vals);
    });

    return (
        <div className="modal-overlay active" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
            <div className={`modal ${isFullscreen ? 'fullscreen' : ''}`}>
                <div className="modal-header">
                    <div>
                        <div className="modal-title">自选对比</div>
                        <div className="modal-code">
                            {loading ? `正在加载 ${watchlist.length} 只基金的历史净值...` : `${results.length} 只基金 · 基于完整历史净值计算`}
                        </div>
                    </div>
                    <div className="modal-actions">
                        <button className="modal-close" onClick={() => setIsFullscreen(!isFullscreen)} title="全屏切换">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                {isFullscreen
                                    ? <><polyline points="4 14 10 14 10 20" /><polyline points="20 10 14 10 14 4" /><line x1="14" y1="10" x2="21" y2="3" /><line x1="3" y1="21" x2="10" y2="14" /></>
                                    : <><polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" /><line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" /></>
                                }
                            </svg>
                        </button>
                        <button className="modal-close" onClick={onClose}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
                        </button>
                    </div>
                </div>
                <div className="compare-section">
                    <div className="compare-intro">
                        基于历史净值本地计算。<span style={{ color: 'var(--accent)' }}>高亮</span>表示同行最优。
                        <br />收益/回撤按基金各自完整历史序列统计，波动率/夏普取近1年窗口（不足1年用全历史），无风险利率 2.1%。
                    </div>
                    {loading ? (
                        <div className="compare-loading">加载中...</div>
                    ) : (
                        <div className="compare-scroll">
                            <table className="compare-table">
                                <thead>
                                    <tr>
                                        {COL_DEFS.map(def => (
                                            <th key={def.key} onClick={() => toggleSort(def.key)}>
                                                {def.key === 'sharpe' ? (
                                                    <>
                                                        {def.label}
                                                        <select
                                                            className="sharpe-win-sel"
                                                            value={sharpeWin}
                                                            onClick={e => e.stopPropagation()}
                                                            onChange={e => setSharpeWin(e.target.value)}
                                                        >
                                                            <option value="1y">1年</option>
                                                            <option value="2y">2年</option>
                                                            <option value="3y">3年</option>
                                                        </select>
                                                    </>
                                                ) : def.label}
                                                {def.key !== 'name' && (
                                                    <span className={`sort-ind ${sortKey === def.key ? 'active' : ''}`}>
                                                        {sortKey === def.key ? (sortAsc ? '▲' : '▼') : '⇅'}
                                                    </span>
                                                )}
                                            </th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {sorted.map(r => (
                                        <tr key={r.code}>
                                            {COL_DEFS.map(def => {
                                                if (def.key === 'name') {
                                                    return (
                                                        <td key={def.key}>
                                                            <span className="compare-fund-link">{r.name}</span>
                                                            <span className="compare-subcode">{r.code}</span>
                                                        </td>
                                                    );
                                                }
                                                const v = getMetricVal(r.metrics, def.key);
                                                const formatted = def.fmt ? def.fmt(v) : (v != null ? v : '--');
                                                const isBest = v != null && bestByCol[def.key] != null && v === bestByCol[def.key];
                                                const isNeg = v != null && v < 0;
                                                const isPos = v != null && v > 0;
                                                let cls = '';
                                                if (isBest) cls += ' best';
                                                if (v == null) cls += ' na';
                                                else if (def.key !== 'annVol' && def.key !== 'yearsSpan') {
                                                    if (def.key === 'maxDD') {
                                                        cls += ' loss-text';
                                                    } else if (isPos) cls += ' gain-text';
                                                    else if (isNeg) cls += ' loss-text';
                                                }
                                                return <td key={def.key} className={cls.trim()}>{formatted}</td>;
                                            })}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
