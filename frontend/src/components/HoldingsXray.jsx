import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { fetchHoldings, fetchSimAccount, fetchRealtime } from '../hooks/useFundApi';
import { latestPeriod, sumRatio } from '../utils/holdings';
import { showToast } from './Toast';

const CONCURRENCY = 1;    // 天天基金持仓页对并发敏感（并发 3 会 502），串行反而更快更稳
const MAX_FUNDS = 12;    // 一次穿透的上限，超过意义不大且很慢

// 集中度分档：前十大合计占净值
function concLevel(sum) {
    if (sum >= 60) return { label: '极高', cls: 'conc-extreme' };
    if (sum >= 45) return { label: '高', cls: 'conc-high' };
    if (sum >= 30) return { label: '中', cls: 'conc-mid' };
    return { label: '分散', cls: 'conc-low' };
}

function fmtAmount(v) {
    if (!v) return '—';
    if (v >= 10000) return `${(v / 10000).toFixed(2)}万`;
    return v.toFixed(0);
}

// 上游偶发 502，失败后隔一会儿重试一次
async function holdingsWithRetry(code) {
    try {
        return await fetchHoldings(code);
    } catch (e) {
        await new Promise(r => setTimeout(r, 800));
        return fetchHoldings(code);
    }
}

export default function HoldingsXray({ watchlist = [], funds = {} }) {
    const [positions, setPositions] = useState([]);     // 模拟盘基金持仓
    const [selected, setSelected] = useState([]);       // 待穿透的基金代码
    const [manual, setManual] = useState('');
    const [running, setRunning] = useState(false);
    const [progress, setProgress] = useState({ done: 0, total: 0 });
    const [result, setResult] = useState(null);         // { rows, failed, asof }
    const [onlyShared, setOnlyShared] = useState(true);
    const [source, setSource] = useState(null);         // 当前这批的来源：'watchlist' | 'positions' | null（手动改过）

    // 模拟持仓作为可选来源，同时提供穿透金额
    useEffect(() => {
        fetchSimAccount()
            .then(acc => setPositions((acc.positions || []).filter(p => p.asset_type === 'fund')))
            .catch(() => { });
    }, []);

    const posMap = useMemo(() => {
        const m = {};
        positions.forEach(p => { m[p.code] = p; });
        return m;
    }, [positions]);

    const nameOf = useCallback((code) => (
        funds[code]?.realtime?.name || posMap[code]?.name || code
    ), [funds, posMap]);

    // 来源按钮整批替换，不追加：自选十几只再加上模拟持仓，两边各点一次必然撞上限，
    // 而叠加出来的那一批既不是"我的自选"也不是"我的持仓"，重叠度对不上任何一个问题
    const pickSource = (kind, codes) => {
        const uniq = [...new Set(codes)];
        if (uniq.length > MAX_FUNDS) {
            showToast(`${uniq.length} 只超了上限，取前 ${MAX_FUNDS} 只`, 'error');
        }
        setSelected(uniq.slice(0, MAX_FUNDS));
        setSource(kind);
        setResult(null);      // 旧结果是上一批基金的，留在下面会让人以为已经换过来了
    };

    const handleManualAdd = () => {
        const code = manual.trim();
        if (!/^\d{6}$/.test(code)) { showToast('请输入 6 位基金代码', 'error'); return; }
        if (selected.includes(code)) { showToast('这只已经在列表里了', 'error'); return; }
        if (selected.length >= MAX_FUNDS) { showToast(`一次最多穿透 ${MAX_FUNDS} 只`, 'error'); return; }
        setSelected(prev => [...prev, code]);
        setSource(null);      // 手动加过就不再是某个来源的原样，取消高亮
        setManual('');
    };

    const run = async () => {
        if (selected.length < 2) { showToast('至少选 2 只基金才能看重叠', 'error'); return; }
        setRunning(true);
        setResult(null);
        setProgress({ done: 0, total: selected.length });

        const rows = [];
        const failed = [];
        const queue = [...selected];

        const worker = async () => {
            while (queue.length > 0) {
                const code = queue.shift();
                try {
                    const data = await holdingsWithRetry(code);
                    const period = latestPeriod(data.content);
                    if (!period) throw new Error('无持仓披露');
                    // 名称兜底：自选和模拟持仓都没有时补拉一次实时
                    let name = nameOf(code);
                    if (name === code) {
                        name = await fetchRealtime(code).then(r => r.name || code).catch(() => code);
                    }
                    rows.push({
                        code,
                        name,
                        period: period.period,
                        asof: period.asof,
                        stocks: period.stocks,
                        sum: sumRatio(period.stocks),
                        amount: posMap[code]?.market_value || 0,
                    });
                } catch (e) {
                    failed.push({ code, name: nameOf(code), msg: e.message || '获取失败' });
                }
                setProgress(p => ({ ...p, done: p.done + 1 }));
            }
        };

        await Promise.all(Array.from({ length: Math.min(CONCURRENCY, selected.length) }, worker));

        rows.sort((a, b) => b.sum - a.sum);
        setResult({ rows, failed });
        setRunning(false);
        if (rows.length < 2) showToast('可用持仓数据不足 2 只，无法比对重叠', 'error');
    };

    // 底层股票聚合：被几只持有、平均权重、穿透金额
    const overlap = useMemo(() => {
        if (!result) return [];
        const map = new Map();
        result.rows.forEach(f => {
            f.stocks.forEach(s => {
                if (!s.code) return;
                const ratio = parseFloat(s.ratio) || 0;
                if (!map.has(s.code)) map.set(s.code, { code: s.code, name: s.name, holders: [] });
                map.get(s.code).holders.push({ fund: f.code, fundName: f.name, ratio, amount: f.amount * ratio / 100 });
            });
        });
        return [...map.values()].map(x => ({
            ...x,
            count: x.holders.length,
            avg: x.holders.reduce((a, h) => a + h.ratio, 0) / x.holders.length,
            maxRatio: Math.max(...x.holders.map(h => h.ratio)),
            amount: x.holders.reduce((a, h) => a + h.amount, 0),
        })).sort((a, b) => (b.count - a.count) || (b.avg - a.avg));
    }, [result]);

    // 两两重合度：共同持股上取权重较小值之和，代表"最少这么多仓位在买同一批股票"
    const pairs = useMemo(() => {
        if (!result || result.rows.length < 2) return [];
        const out = [];
        const rows = result.rows;
        for (let i = 0; i < rows.length; i++) {
            for (let j = i + 1; j < rows.length; j++) {
                const a = new Map(rows[i].stocks.map(s => [s.code, parseFloat(s.ratio) || 0]));
                let share = 0;
                const names = [];
                rows[j].stocks.forEach(s => {
                    if (a.has(s.code)) {
                        share += Math.min(a.get(s.code), parseFloat(s.ratio) || 0);
                        names.push(s.name);
                    }
                });
                if (share > 0) out.push({ a: rows[i], b: rows[j], share, names });
            }
        }
        return out.sort((x, y) => y.share - x.share);
    }, [result]);

    const hasAmount = useMemo(() => (result?.rows || []).some(r => r.amount > 0), [result]);
    const shownOverlap = onlyShared ? overlap.filter(o => o.count >= 2) : overlap;

    return (
        <div className="sim-panel xray-panel">
            <div className="sim-head">
                <h3>
                    持仓穿透
                    <span className="sim-sub">看这几只基金底层是不是同一批股票 · 仅前十大重仓 · 报告期滞后约一个月</span>
                </h3>
                <div className="sim-head-actions">
                    <button className="sim-refresh" onClick={run} disabled={running || selected.length < 2}>
                        {running ? `穿透中 ${progress.done}/${progress.total}` : '开始穿透'}
                    </button>
                    {selected.length > 0 && (
                        <button className="sim-reset" onClick={() => { setSelected([]); setResult(null); setSource(null); }}>清空</button>
                    )}
                </div>
            </div>

            {/* 选基金 */}
            <div className="xray-picker">
                <div className="xray-src">
                    <button
                        className={`xray-src-btn${source === 'watchlist' ? ' active' : ''}`}
                        onClick={() => pickSource('watchlist', watchlist)}
                        disabled={watchlist.length === 0}
                    >
                        全部自选（{watchlist.length}）
                    </button>
                    <button
                        className={`xray-src-btn${source === 'positions' ? ' active' : ''}`}
                        onClick={() => pickSource('positions', positions.map(p => p.code))}
                        disabled={positions.length === 0}
                    >
                        模拟持仓（{positions.length}）
                    </button>
                    <div className="xray-manual">
                        <input
                            value={manual}
                            onChange={e => setManual(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') handleManualAdd(); }}
                            placeholder="基金代码"
                            maxLength={6}
                        />
                        <button className="xray-src-btn" onClick={handleManualAdd}>添加</button>
                    </div>
                </div>

                {selected.length === 0 ? (
                    <div className="xray-hint">先选至少 2 只基金。自选和模拟持仓可以混着选，混选时能看到"我实际有多少钱压在同一只股票上"。</div>
                ) : (
                    <div className="xray-chips">
                        {selected.map(c => (
                            <span key={c} className="xray-chip">
                                <span className="xray-chip-code">{c}</span>
                                <span className="xray-chip-name">{nameOf(c)}</span>
                                {posMap[c] && <span className="xray-chip-tag">持仓</span>}
                                <button onClick={() => { setSelected(prev => prev.filter(x => x !== c)); setSource(null); }}>×</button>
                            </span>
                        ))}
                    </div>
                )}
            </div>

            {result && result.rows.length > 0 && (
                <>
                    {/* 1. 各基金集中度 */}
                    <div className="xray-block">
                        <h4>① 每只基金有多集中</h4>
                        <table className="holdings-table">
                            <thead>
                                <tr>
                                    <th>代码</th>
                                    <th>名称</th>
                                    <th>报告期</th>
                                    <th className="num">披露只数</th>
                                    <th className="num">前十大合计%</th>
                                    <th className="num">最大单只%</th>
                                    <th>集中度</th>
                                    <th>第一大重仓</th>
                                </tr>
                            </thead>
                            <tbody>
                                {result.rows.map(f => {
                                    const lv = concLevel(f.sum);
                                    const top1 = f.stocks[0];
                                    return (
                                        <tr key={f.code}>
                                            <td>{f.code}</td>
                                            <td className="stock-name">{f.name}</td>
                                            <td>{f.period}</td>
                                            <td className="num">{f.stocks.length}</td>
                                            <td className="num" style={{ fontWeight: 600 }}>{f.sum.toFixed(1)}</td>
                                            <td className="num">{f.stocks.length ? Math.max(...f.stocks.map(s => parseFloat(s.ratio) || 0)).toFixed(2) : '—'}</td>
                                            <td><span className={`xray-conc ${lv.cls}`}>{lv.label}</span></td>
                                            <td className="stock-name">{top1 ? `${top1.name} ${top1.ratio}` : '—'}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                        <div className="xray-note">
                            前十大合计越高，基金越接近"几只股票的打包"——波动是个股级的，但操作受基金规则约束（不能盘中止损、股票型基金有最低仓位）。
                        </div>
                    </div>

                    {/* 2. 底层重叠 */}
                    <div className="xray-block">
                        <h4>
                            ② 底层股票重叠
                            <button className="xray-toggle" onClick={() => setOnlyShared(v => !v)}>
                                {onlyShared ? `只看重叠（${overlap.filter(o => o.count >= 2).length}）` : `全部（${overlap.length}）`}
                            </button>
                        </h4>
                        {shownOverlap.length === 0 ? (
                            <div className="xray-hint">这几只基金的前十大没有交集——分散得不错。</div>
                        ) : (
                            <table className="holdings-table">
                                <thead>
                                    <tr>
                                        <th>股票</th>
                                        <th>代码</th>
                                        <th className="num">被几只持有</th>
                                        <th className="num">平均权重%</th>
                                        <th className="num">最高权重%</th>
                                        {hasAmount && <th className="num">穿透金额</th>}
                                        <th>分布</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {shownOverlap.map(o => (
                                        <tr key={o.code}>
                                            <td className="stock-name">{o.name}</td>
                                            <td>{o.code}</td>
                                            <td className="num">
                                                <span className={o.count >= result.rows.length * 0.6 ? 'loss-text' : ''} style={{ fontWeight: 600 }}>
                                                    {o.count}/{result.rows.length}
                                                </span>
                                            </td>
                                            <td className="num">{o.avg.toFixed(2)}</td>
                                            <td className="num">{o.maxRatio.toFixed(2)}</td>
                                            {hasAmount && <td className="num">{fmtAmount(o.amount)}</td>}
                                            <td className="xray-dist">
                                                {o.holders.map(h => (
                                                    <span key={h.fund} title={h.fundName}>{h.fund}:{h.ratio.toFixed(1)}</span>
                                                ))}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                        {hasAmount && (
                            <div className="xray-note">穿透金额 = Σ（该基金市值 × 该股票占净值%），只统计有模拟持仓的基金，且只含前十大。</div>
                        )}
                    </div>

                    {/* 3. 两两重合度 */}
                    {pairs.length > 0 && (
                        <div className="xray-block">
                            <h4>③ 哪两只其实是同一只</h4>
                            <table className="holdings-table">
                                <thead>
                                    <tr>
                                        <th>基金 A</th>
                                        <th>基金 B</th>
                                        <th className="num">重合仓位%</th>
                                        <th className="num">共同持股</th>
                                        <th>共同股票</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {pairs.slice(0, 15).map((p, i) => (
                                        <tr key={i}>
                                            <td className="stock-name">{p.a.name}</td>
                                            <td className="stock-name">{p.b.name}</td>
                                            <td className="num">
                                                <span className={p.share >= 30 ? 'loss-text' : ''} style={{ fontWeight: 600 }}>{p.share.toFixed(1)}</span>
                                            </td>
                                            <td className="num">{p.names.length}</td>
                                            <td className="xray-dist">{p.names.map((n, k) => <span key={k}>{n}</span>)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            <div className="xray-note">
                                重合仓位 = 共同持股上取两者权重的较小值求和。30% 以上基本可以认为是同一只基金的两个马甲，分散买它们不降风险。
                            </div>
                        </div>
                    )}

                    {result.failed.length > 0 && (
                        <div className="xray-note xray-failed">
                            未取到持仓：{result.failed.map(f => `${f.code}（${f.msg}）`).join('、')}
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
