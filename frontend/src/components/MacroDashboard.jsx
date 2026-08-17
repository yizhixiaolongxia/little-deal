import React, { useState, useEffect, useCallback } from 'react';
import { fetchMacroDashboard, fetchMacroHistory } from '../hooks/useFundApi';

/**
 * 宏观指标看板
 *
 * 排版顺序就是看的顺序：核心八项在最上面（一眼扫完做判断），下面按增长/通胀/
 * 货币金融/外部/估值分组铺开细项，最后诚实列出还缺哪些指标。
 *
 * 全篇只做一件核心的事：把「最新值」和「相对上期的变化」放在一起。宏观决策靠
 * 拐点和边际变化，光有最新值的看板只是个数字墙。
 */

// 好/坏用独立语义色，不复用 --gain/--loss：那两个变量在本项目里表达的是 A 股
// 涨跌（红涨绿跌）。宏观指标里「PMI 上升」是好事，跟涨跌无关，混用会让人在
// 红色的 CPI 上涨和红色的股票上涨之间来回误读
const GOOD = 'var(--macro-good)';
const BAD = 'var(--macro-bad)';
const FLAT = 'var(--text-muted)';

/**
 * 边际变化的方向与好坏
 *
 * better 为 null 的指标（CPI、各种利率）只判方向不判好坏 —— CPI 涨到 5% 和
 * 跌到 -1% 都是坏事，标成任一方向都是误导。
 */
function verdict(item) {
    const { change, better } = item;
    if (change === null || change === undefined) return { arrow: '', color: FLAT };
    if (change === 0) return { arrow: '→', color: FLAT };
    const arrow = change > 0 ? '↑' : '↓';
    if (!better) return { arrow, color: 'var(--text-secondary)' };
    const good = better === 'up' ? change > 0 : change < 0;
    return { arrow, color: good ? GOOD : BAD };
}

// 值的小数位跟着量级走：国债 1.6964 砍成 1.7 会把 bp 级的变化抹平，
// 而新增贷款 38000 亿保留两位小数只是噪音
function fmtVal(v) {
    if (v === null || v === undefined) return '--';
    const a = Math.abs(v);
    if (a >= 1000) return v.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
    return v.toFixed(a >= 10 ? 2 : a >= 0.01 ? 2 : 3);
}

function fmtChange(v) {
    if (v === null || v === undefined) return '';
    const a = Math.abs(v);
    const digits = a >= 100 ? 0 : a >= 0.01 ? 2 : 3;
    return `${v > 0 ? '+' : ''}${v.toFixed(digits)}`;
}

/**
 * 走势线：带参考线的内联 SVG
 *
 * ref 只在它落进数据区间内时才画。PMI 的荣枯线 50 就在 49~51 里，画出来一眼
 * 看得出扩张还是收缩；而 M1-M2 剪刀差常年为负，硬把 0 轴纳进 y 轴范围会把
 * 整条曲线压成一条直线 —— 那时候参考线帮不上忙，只会毁掉走势本身。
 */
function Spark({ data, refLine, color, width = 96, height = 32 }) {
    if (!data || data.length < 2) return <span className="spark-empty">--</span>;

    const pad = 3;
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const innerW = width - pad * 2;
    const innerH = height - pad * 2;
    const y = (v) => pad + innerH - ((v - min) / range) * innerH;

    const points = data
        .map((v, i) => `${(pad + (i / (data.length - 1)) * innerW).toFixed(1)},${y(v).toFixed(1)}`)
        .join(' ');
    const showRef = refLine !== null && refLine !== undefined && refLine > min && refLine < max;

    return (
        <svg className="sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
            {showRef && (
                <line
                    x1={pad} x2={width - pad} y1={y(refLine)} y2={y(refLine)}
                    stroke="var(--text-muted)" strokeWidth="1" strokeDasharray="3 3" opacity="0.55"
                />
            )}
            <polyline
                fill="none" stroke={color} strokeWidth="1.6"
                strokeLinecap="round" strokeLinejoin="round" points={points}
            />
        </svg>
    );
}

/**
 * 指标卡：值 + 单位 + 边际变化 + 走势
 *
 * 没数据的指标照样占位并写明原因。给个 0 或者留白让它看起来像 0，
 * 是宏观看板上最恶劣的一种错。
 */
function MacroCard({ item, size = 'sm', active, onClick }) {
    const v = verdict(item);
    const empty = item.value === null || item.value === undefined;

    return (
        <button
            className={`macro-card macro-card-${size} ${active ? 'active' : ''} ${empty ? 'empty' : ''}`}
            onClick={() => onClick(item)}
            title={item.desc}
        >
            <div className="macro-card-name">
                {item.name}
                {item.derived && <span className="macro-tag">派生</span>}
            </div>
            {empty ? (
                <div className="macro-card-reason">{item.reason || '暂无数据'}</div>
            ) : (
                <>
                    <div className="macro-card-value">
                        {fmtVal(item.value)}
                        {item.unit && <span className="macro-unit">{item.unit}</span>}
                    </div>
                    <div className="macro-card-foot">
                        <span className="macro-change" style={{ color: v.color }}>
                            {v.arrow} {fmtChange(item.change)}
                            {item.change === null && <span className="macro-muted">首次记录</span>}
                        </span>
                        <Spark
                            data={item.spark}
                            refLine={item.ref}
                            color={v.color === FLAT ? 'var(--text-secondary)' : v.color}
                            width={size === 'lg' ? 110 : 88}
                            height={size === 'lg' ? 34 : 28}
                        />
                    </div>
                    <div className="macro-card-period">
                        {item.period}
                        {item.prev_period && <span className="macro-muted"> ← {item.prev_period}</span>}
                    </div>
                </>
            )}
        </button>
    );
}

/**
 * 展开的大图：拉完整历史画折线
 *
 * 卡片上的 spark 只有 12 期/30 天，看不出周期位置。这里默认拉 120 期，
 * 并标出区间高低点 —— 「现在这个数在历史上算高还是低」才是宏观判断的关键。
 */
function MacroDetail({ item, onClose }) {
    const [series, setSeries] = useState(null);
    const [error, setError] = useState('');

    useEffect(() => {
        let alive = true;
        setSeries(null);
        setError('');
        fetchMacroHistory(item.code, 120)
            .then((r) => { if (alive) setSeries(r); })
            .catch((e) => { if (alive) setError(e.message || '加载失败'); });
        // 卡片可以被快速连点，切走之后回来的响应必须丢掉，否则图和标题会对不上
        return () => { alive = false; };
    }, [item.code]);

    const items = series?.items || [];
    const values = items.map((p) => p.value);
    const W = 760;
    const H = 200;
    const pad = { l: 44, r: 12, t: 14, b: 24 };

    let body = null;
    if (error) {
        body = <div className="macro-detail-status macro-detail-error">{error}</div>;
    } else if (!series) {
        body = <div className="macro-detail-status">加载中…</div>;
    } else if (values.length < 2) {
        // 新浪快照类指标（汇率/大宗）和依赖本地快照的估值指标就是只有几个点，
        // 不是出错。写清楚原因，别让人去查一个不存在的 bug
        const snapshot = ['usdcny', 'usd_index', 'wti', 'gold', 'a_pe', 'erp'].includes(item.code);
        body = (
            <div className="macro-detail-status">
                只有 {values.length} 个数据点，画不出走势。
                {snapshot && '这类指标的上游只给当前值，历史从第一次落库那天开始长。'}
            </div>
        );
    } else {
        const min = Math.min(...values);
        const max = Math.max(...values);
        const range = max - min || 1;
        const innerW = W - pad.l - pad.r;
        const innerH = H - pad.t - pad.b;
        const x = (i) => pad.l + (i / (values.length - 1)) * innerW;
        const y = (v) => pad.t + innerH - ((v - min) / range) * innerH;
        const line = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
        const iMax = values.indexOf(max);
        const iMin = values.indexOf(min);
        const refIn = series.ref !== null && series.ref !== undefined && series.ref > min && series.ref < max;
        const ticks = [0, Math.floor((values.length - 1) / 2), values.length - 1];

        body = (
            <svg className="macro-detail-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
                {[max, (max + min) / 2, min].map((v, i) => (
                    <g key={i}>
                        <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)}
                              stroke="var(--chart-grid)" strokeWidth="1" />
                        <text x={pad.l - 6} y={y(v) + 3} textAnchor="end" className="macro-axis">
                            {fmtVal(v)}
                        </text>
                    </g>
                ))}
                {refIn && (
                    <line x1={pad.l} x2={W - pad.r} y1={y(series.ref)} y2={y(series.ref)}
                          stroke="var(--text-muted)" strokeWidth="1" strokeDasharray="4 4" />
                )}
                <polyline fill="none" stroke="var(--accent)" strokeWidth="1.8"
                          strokeLinejoin="round" points={line} />
                <circle cx={x(iMax)} cy={y(max)} r="3" fill={BAD} />
                <circle cx={x(iMin)} cy={y(min)} r="3" fill={GOOD} />
                {ticks.map((i) => (
                    <text key={i} x={x(i)} y={H - 6} className="macro-axis"
                          textAnchor={i === 0 ? 'start' : i === values.length - 1 ? 'end' : 'middle'}>
                        {items[i].period}
                    </text>
                ))}
            </svg>
        );
    }

    return (
        <div className="macro-detail">
            <div className="macro-detail-head">
                <h4>
                    {item.name}
                    {item.unit && <span className="macro-unit">{item.unit}</span>}
                    <span className="macro-detail-sub">
                        {items.length > 0
                            ? `${items[0].period} ~ ${items[items.length - 1].period} · ${items.length} 期`
                            : item.desc}
                    </span>
                </h4>
                <button className="btn-icon" onClick={onClose} title="收起">✕</button>
            </div>
            {body}
            <div className="macro-detail-desc">{item.desc}</div>
        </div>
    );
}

export default function MacroDashboard() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [picked, setPicked] = useState(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setData(await fetchMacroDashboard());
        } catch (e) {
            setError(e.message || '宏观数据获取失败');
        }
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    // 再点一次同一张卡就收起，省一个关闭按钮的来回
    const pick = (item) => setPicked((prev) => (prev && prev.code === item.code ? null : item));

    return (
        <section className="macro-dash">
            <div className="macro-head">
                <h3>
                    <span className="macro-icon">◎</span>
                    宏观指标看板
                    <span className="macro-sub">
                        {data?.generated_at ? `更新于 ${data.generated_at}` : '增长 · 通胀 · 货币 · 外部 · 估值'}
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
                <div className="macro-status">
                    <span className="dot-pulse"><span></span><span></span><span></span></span>
                </div>
            )}

            {!loading && error && (
                <div className="macro-status macro-error">{error} · 点右上角重试</div>
            )}

            {!loading && !error && data && (
                <>
                    <div className="macro-core-label">
                        核心八项 · 信用 → 动能 → 政策空间 → 估值 → 外部约束
                    </div>
                    <div className="macro-core">
                        {data.core.map((it) => (
                            <MacroCard key={it.code} item={it} size="lg"
                                       active={picked?.code === it.code} onClick={pick} />
                        ))}
                    </div>

                    {picked && <MacroDetail item={picked} onClose={() => setPicked(null)} />}

                    {data.groups.map((g) => (
                        <div className="macro-group" key={g.key}>
                            <div className="macro-group-name">{g.name}</div>
                            <div className="macro-grid">
                                {g.items.map((it) => (
                                    <MacroCard key={it.code} item={it}
                                               active={picked?.code === it.code} onClick={pick} />
                                ))}
                            </div>
                        </div>
                    ))}

                    {/* 缺口写在页面上而不是只留在代码注释里：看板缺一个指标不影响它
                        打开，但会让人以为「这就是全部该看的东西」 */}
                    {data.missing?.length > 0 && (
                        <details className="macro-missing">
                            <summary>还缺 {data.missing.length} 类指标（点开看清单与替代口径）</summary>
                            <ul>
                                {data.missing.map((m, i) => <li key={i}>{m}</li>)}
                            </ul>
                        </details>
                    )}
                </>
            )}
        </section>
    );
}
