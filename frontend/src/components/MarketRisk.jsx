import React, { useState, useEffect, useCallback } from 'react';
import { fetchMarketRisk, fetchMarketRiskHistory } from '../hooks/useFundApi';

// 情绪等级 -> 颜色映射
const SENTIMENT_COLORS = {
    extreme_fear: '#e74c3c',
    fear: '#e67e22',
    neutral: '#f1c40f',
    greed: '#27ae60',
    extreme_greed: '#2ecc71',
};

/**
 * Sparkline 小折线图（内联 SVG）
 */
function Sparkline({ data, color, width = 80, height = 28 }) {
    if (!data || data.length < 2) return <span className="spark-empty">--</span>;

    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const padding = 2;
    const innerW = width - padding * 2;
    const innerH = height - padding * 2;

    const points = data.map((v, i) => {
        const x = padding + (i / (data.length - 1)) * innerW;
        const y = padding + innerH - ((v - min) / range) * innerH;
        return `${x},${y}`;
    }).join(' ');

    return (
        <svg className="sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
            <polyline
                fill="none"
                stroke={color}
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                points={points}
            />
        </svg>
    );
}

/**
 * 情绪仪表盘
 */
function SentimentGauge({ score, level, label }) {
    const gaugeColor = SENTIMENT_COLORS[label] || '#f1c40f';

    // 半圆进度：score 0-100 映射到 0-180 度
    const angle = (score / 100) * 180;
    const rad = (angle * Math.PI) / 180;
    const r = 40;
    const cx = 50;
    const cy = 50;
    const x = cx - r * Math.cos(rad);
    const y = cy - r * Math.sin(rad);
    const largeArc = angle > 90 ? 1 : 0;

    return (
        <div className="sentiment-gauge">
            <svg width="100" height="58" viewBox="0 0 100 58">
                {/* 底部灰色轨道 */}
                <path
                    d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
                    fill="none"
                    stroke="var(--chart-grid, #e0e0e0)"
                    strokeWidth="8"
                    strokeLinecap="round"
                />
                {/* 彩色进度 */}
                {score > 0 && (
                    <path
                        d={`M ${cx - r} ${cy} A ${r} ${r} 0 ${largeArc} 1 ${x.toFixed(2)} ${y.toFixed(2)}`}
                        fill="none"
                        stroke={gaugeColor}
                        strokeWidth="8"
                        strokeLinecap="round"
                    />
                )}
            </svg>
            <div className="sentiment-score" style={{ color: gaugeColor }}>{score}</div>
            <div className="sentiment-level">{level}</div>
        </div>
    );
}

export default function MarketRisk() {
    const [data, setData] = useState(null);
    const [history, setHistory] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError(false);
        try {
            const result = await fetchMarketRisk();
            setData(result);
        } catch (e) {
            setError(true);
        }
        // 历史曲线加载失败不影响主面板展示
        try {
            const his = await fetchMarketRiskHistory(30);
            setHistory(his.items || []);
        } catch (e) {
            setHistory([]);
        }
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    return (
        <section className="market-risk">
            <div className="market-risk-head">
                <h3>
                    <span className="market-risk-icon">⚠</span>
                    市场风险监测
                    <span className="market-risk-sub">
                        {data?.updated_at ? `更新于 ${data.updated_at}` : '大盘指数 · 恐贪情绪'}
                    </span>
                </h3>
                <button
                    className={`btn-icon recommend-refresh ${loading ? 'spinning' : ''}`}
                    onClick={load}
                    title="刷新数据"
                    disabled={loading}
                >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" />
                        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                    </svg>
                </button>
            </div>

            {loading && (
                <div className="market-risk-status">
                    <span className="dot-pulse"><span></span><span></span><span></span></span>
                </div>
            )}

            {!loading && error && (
                <div className="market-risk-status market-risk-error">市场数据获取失败，点击右上角重试</div>
            )}

            {!loading && !error && data && (
                <div className="market-risk-body">
                    <div className="market-risk-indices">
                        {data.indices.map((idx) => {
                            const pct = idx.pct;
                            const hasPct = pct !== null && pct !== undefined;
                            const isGain = hasPct && pct >= 0;
                            const color = hasPct ? (isGain ? 'var(--gain)' : 'var(--loss)') : 'var(--text-muted)';
                            return (
                                <div className="index-card" key={idx.code}>
                                    <div className="index-name">{idx.name}</div>
                                    <div className="index-price">{idx.price ? idx.price.toFixed(2) : '--'}</div>
                                    <div className={`index-pct ${hasPct ? (isGain ? 'gain-text' : 'loss-text') : ''}`}>
                                        {hasPct ? `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%` : '--'}
                                    </div>
                                    <Sparkline data={idx.spark} color={color} />
                                </div>
                            );
                        })}
                    </div>
                    <div className="market-risk-sentiment">
                        <SentimentGauge
                            score={data.sentiment.score}
                            level={data.sentiment.level}
                            label={data.sentiment.label}
                        />
                        <div className="sentiment-desc">恐惧贪婪指数{data.from_cache ? '（缓存）' : ''}</div>
                        {history.length >= 2 && (
                            <div className="sentiment-history">
                                <Sparkline
                                    data={history.map((h) => h.score)}
                                    color={SENTIMENT_COLORS[history[history.length - 1].label] || '#f1c40f'}
                                    width={110}
                                    height={30}
                                />
                                <div className="sentiment-history-label">近{history.length}日走势</div>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </section>
    );
}
