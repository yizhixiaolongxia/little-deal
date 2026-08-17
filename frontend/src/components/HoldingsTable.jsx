import React, { useState, useEffect } from 'react';
import { fetchStockQuotes } from '../hooks/useFundApi';
import { parseHoldings, sortPeriodsDesc } from '../utils/holdings';

export default function HoldingsTable({ code, holdingsData }) {
    const [periods, setPeriods] = useState([]);
    const [currentIdx, setCurrentIdx] = useState(0);
    const [quotes, setQuotes] = useState({});
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!holdingsData) { setLoading(true); return; }
        const parsed = sortPeriodsDesc(parseHoldings(holdingsData.content));
        setPeriods(parsed.slice(0, 4));
        setCurrentIdx(0);
        setQuotes({});
        setLoading(false);
    }, [holdingsData]);

    // 加载股票行情
    useEffect(() => {
        if (periods.length === 0) return;
        const cur = periods[currentIdx];
        if (!cur || quotes[currentIdx]) return;
        const secids = cur.stocks.filter(s => s.secid).map(s => s.secid);
        if (secids.length === 0) return;

        fetchStockQuotes(secids).then(data => {
            setQuotes(prev => ({ ...prev, [currentIdx]: data }));
        }).catch(() => {});
    }, [periods, currentIdx]); // eslint-disable-line

    if (loading) {
        return (
            <div className="holdings-section">
                <div className="holdings-header"><h4>最近一年持仓</h4></div>
                <div className="holdings-body"><div className="inline-loading">加载中...</div></div>
            </div>
        );
    }

    if (periods.length === 0) {
        return (
            <div className="holdings-section">
                <div className="holdings-header"><h4>最近一年持仓</h4></div>
                <div className="holdings-body"><div className="inline-empty">暂无持仓数据</div></div>
            </div>
        );
    }

    const cur = periods[currentIdx];
    const quoteMap = quotes[currentIdx] || null;

    return (
        <div className="holdings-section">
            <div className="holdings-header">
                <h4>最近一年持仓 <span className="holdings-meta">{cur.asof ? `截至 ${cur.asof}` : ''}</span></h4>
                <div className="period-selector">
                    {periods.map((p, i) => (
                        <button
                            key={i}
                            className={`period-btn ${i === currentIdx ? 'active' : ''}`}
                            onClick={() => setCurrentIdx(i)}
                        >
                            {p.period}
                        </button>
                    ))}
                </div>
            </div>
            <div className="holdings-body">
                <table className="holdings-table">
                    <thead>
                        <tr>
                            <th style={{ width: 32 }}>#</th>
                            <th>代码</th>
                            <th>名称</th>
                            <th className="num">涨跌%</th>
                            <th className="num">占净值%</th>
                            <th className="num">持股数(万)</th>
                            <th className="num">市值(万元)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {cur.stocks.map((s, i) => {
                            const ratioNum = parseFloat(s.ratio) || 0;
                            const barWidth = Math.min(60, Math.max(2, ratioNum * 4));
                            let pctCell = <span style={{ color: 'var(--text-muted)' }}>—</span>;
                            if (quoteMap) {
                                const q = quoteMap[s.code];
                                if (q && q.pct != null && isFinite(q.pct)) {
                                    const sign = q.pct > 0 ? '+' : '';
                                    const cls = q.pct > 0 ? 'gain-text' : (q.pct < 0 ? 'loss-text' : '');
                                    pctCell = <span className={cls} style={{ fontWeight: 600 }}>{sign}{q.pct.toFixed(2)}%</span>;
                                }
                            }
                            return (
                                <tr key={i}>
                                    <td>{s.idx}</td>
                                    <td>{s.code}</td>
                                    <td className="stock-name">{s.name}</td>
                                    <td className="num">{pctCell}</td>
                                    <td className="num">
                                        <span className="ratio-bar" style={{ width: barWidth }}></span>
                                        {s.ratio}
                                    </td>
                                    <td className="num">{s.shares}</td>
                                    <td className="num">{s.value}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
