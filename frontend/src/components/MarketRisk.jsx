import React, { useState, useEffect, useCallback } from 'react';
import { fetchMarketRisk, fetchMarketRiskHistory, fetchMarketPosition } from '../hooks/useFundApi';

// 情绪等级 -> 颜色映射
const SENTIMENT_COLORS = {
    extreme_fear: '#e74c3c',
    fear: '#e67e22',
    neutral: '#f1c40f',
    greed: '#27ae60',
    extreme_greed: '#2ecc71',
};

// 分位越高 = 位置越贵/杠杆越重 = 风险越高。和情绪那套配色方向相反是故意的：
// 情绪绿色表示贪婪（该警惕），分位绿色表示便宜（可以买），两个色轴含义不同，
// 所以分位条一律带数字，不让人只靠颜色猜。
const PCTL_HIGH = 80;
function pctlColor(v) {
    if (v === null || v === undefined) return 'var(--text-muted)';
    if (v >= PCTL_HIGH) return '#e74c3c';
    if (v >= 70) return '#e67e22';
    if (v <= 30) return '#27ae60';
    return '#f1c40f';
}

// 后端判定文案沿用简报的 **强调** 写法，这里就地渲染，不引 markdown 库
function emphasize(text) {
    if (!text) return null;
    return text.split('**').map((seg, i) => (
        i % 2 ? <strong key={i}>{seg}</strong> : <span key={i}>{seg}</span>
    ));
}

// 后端给的是一位小数的浮点，85.0 过 JSON 到 JS 会渲成 「85」，和旁边的 90.6 摆在
// 一起像两个精度。分位一律对齐到一位小数
function fmtPctl(v) {
    return Number(v).toFixed(1);
}

/**
 * 历史分位条。80% 处画一条刻度线，把「高位」这个阈值直接摆在图上，
 * 免得看到 76 和 84 两个数却不知道分界在哪
 */
function PctlBar({ value }) {
    if (value === null || value === undefined) {
        return (
            <div className="pctl-bar-wrap">
                <div className="pctl-bar pctl-bar-empty">样本不足</div>
            </div>
        );
    }
    // 数字必须放在条外面。放里面踩过坑：数字颜色跟填充色是同一个，填充过 90%
    // 就盖到数字下面，变成红字压红条彻底看不见 —— 而高分位正是最该看清的区间
    return (
        <div className="pctl-bar-wrap" title={`历史分位 ${fmtPctl(value)}%`}>
            <div className="pctl-bar">
                <div className="pctl-bar-fill"
                    style={{ width: `${Math.min(100, Math.max(0, value))}%`, background: pctlColor(value) }} />
                <span className="pctl-bar-tick" style={{ left: `${PCTL_HIGH}%` }} />
            </div>
            <span className="pctl-bar-num" style={{ color: pctlColor(value) }}>{fmtPctl(value)}</span>
        </div>
    );
}

/**
 * 四个窗口的分位并排列出。只给一个窗口会误导：指数点位带长期上行漂移，
 * 全历史分位天然偏高，四个数放一起才看得出是真的高还是指数长大了
 */
function PctlWindows({ items }) {
    if (!items || !items.length) return null;
    return (
        <div className="pctl-windows">
            {items.map((w) => (
                <span className="pctl-win" key={w.key} title={w.reason || `${w.rows} 个交易日`}>
                    {w.label}
                    <b style={{ color: w.value === null ? 'var(--text-muted)' : pctlColor(w.value) }}>
                        {w.value === null ? '–' : fmtPctl(w.value)}
                    </b>
                </span>
            ))}
        </div>
    );
}

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
    const [position, setPosition] = useState(null);
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
        // 历史位置同理独立降级。但它失败时不能静默不显示——只剩情绪仪表盘的
        // 面板看起来是完整的，人会以为位置没问题，所以把错误留在 state 里开说
        try {
            setPosition(await fetchMarketPosition());
        } catch (e) {
            setPosition({ failed: e.message || '位置数据获取失败' });
        }
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    const margin = position?.margin;
    const verdict = position?.verdict;

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

            {!loading && position && (
                <div className="position-block">
                    <div className="position-head">
                        <span className="position-title">历史位置</span>
                        <span className="position-sub">
                            {position.updated_at
                                ? `点位分位 · 两融杠杆分位（截至 ${position.updated_at}）`
                                : '点位分位 · 两融杠杆分位'}
                        </span>
                    </div>

                    {position.failed && (
                        <div className="position-verdict pv-unknown">{position.failed}</div>
                    )}

                    {verdict && (
                        <div className={`position-verdict pv-${verdict.level}`}>
                            {emphasize(verdict.text)}
                        </div>
                    )}

                    {position.indices?.map((idx) => (
                        <div className="position-row" key={idx.key}>
                            <div className="pos-name">{idx.name}</div>
                            <div className="pos-value">{idx.close.toFixed(2)}</div>
                            <div className="pos-bar">
                                <PctlBar value={idx.verdict_pctl} />
                                <PctlWindows items={idx.percentiles} />
                            </div>
                            <div className="pos-extra" title={`历史最高 ${idx.high}（${idx.high_date}）`}>
                                <span className="pos-extra-label">距高点</span>
                                {idx.from_high}%
                            </div>
                        </div>
                    ))}

                    {margin && margin.pct !== null && margin.pct !== undefined && (
                        <div className="position-row position-row-margin">
                            <div className="pos-name">融资余额</div>
                            <div className="pos-value">
                                {margin.pct}%
                                <span className="pos-value-sub">{margin.rz_ye_yi} 亿</span>
                            </div>
                            <div className="pos-bar">
                                <PctlBar value={margin.verdict_pctl} />
                                <PctlWindows items={margin.percentiles} />
                            </div>
                            <div className="pos-extra" title={`历史峰值 ${margin.peak_pct}%（${margin.peak_date}）`}>
                                <span className="pos-extra-label">峰值</span>
                                {margin.peak_pct}%
                            </div>
                        </div>
                    )}

                    {/* 漂移的坑必须写在界面上，写在代码注释里只有改代码的人看得到 */}
                    <div className="position-foot">
                        指数点位含长期上行漂移，全历史分位天然偏高，看短窗口更可靠；
                        「距高点」不受漂移影响。融资余额按占流通市值比判定，不看绝对额。
                    </div>

                    {position.notes?.length > 0 && (
                        <ul className="position-notes">
                            {position.notes.map((n, i) => <li key={i}>{n}</li>)}
                        </ul>
                    )}
                </div>
            )}
        </section>
    );
}
