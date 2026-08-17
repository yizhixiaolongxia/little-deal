import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
    fetchSimAccount, fetchSimQuote, submitSimTrade, fetchSimTrades, fetchSimFees,
    settleSimOrders, resetSimAccount, fetchSimCurve, fetchFundExcess,
} from '../hooks/useFundApi';
import { fmtMoney, fmtPctVal } from '../utils/format';
import PortfolioRisk from './PortfolioRisk';
import { showToast } from './Toast';

const ASSET_LABEL = { stock: '股票', fund: '基金' };
// 与后端 sim_fees 保持一致，仅用于下单前的预估展示，实际费用以清算结果为准
const FUND_BUY_RATE = 0.0015;

// 涨红跌绿（A 股习惯，与全局 --gain/--loss 一致）
function pnlClass(v) {
    if (v == null || v === 0) return '';
    return v > 0 ? 'val-gain' : 'val-loss';
}

// 一个标的可能有多个批次，持有天数取区间，赎回费率取最高的那档——
// 提醒作用比精确重要，真正的费用由后端逐批次算
function lotSummary(p) {
    const lots = p.lots || [];
    if (!lots.length) return { days: '--', rate: null };
    const days = lots.map(l => l.hold_days);
    const min = Math.min(...days);
    const max = Math.max(...days);
    const rates = lots.map(l => l.redeem_rate).filter(r => r != null);
    return {
        days: min === max ? String(min) : `${min}~${max}`,
        rate: rates.length ? Math.max(...rates) : null,
    };
}

// 超额单位是百分点（pp），不是百分比——写成 % 会跟收益率搞混
function fmtPP(v) {
    if (v == null || !isFinite(v)) return '--';
    return (v > 0 ? '+' : '') + Number(v).toFixed(2) + 'pp';
}

/**
 * 总览指标卡
 */
function StatCard({ label, value, sub, cls = '', title = '' }) {
    return (
        <div className="sim-stat" title={title}>
            <div className="sim-stat-label">{label}</div>
            <div className={`sim-stat-value ${cls}`}>{value}</div>
            {sub && <div className={`sim-stat-sub ${cls}`}>{sub}</div>}
        </div>
    );
}

export default function SimTrade() {
    const [account, setAccount] = useState(null);
    const [trades, setTrades] = useState([]);
    const [fees, setFees] = useState(null);
    const [curve, setCurve] = useState(null);
    // 代码 -> 近一年相对基准的表现
    const [excess, setExcess] = useState({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [settling, setSettling] = useState(false);

    const [assetType, setAssetType] = useState('stock');
    const [side, setSide] = useState('buy');
    const [code, setCode] = useState('');
    const [qty, setQty] = useState('');
    const [quote, setQuote] = useState(null);
    const [quoteErr, setQuoteErr] = useState('');
    const [submitting, setSubmitting] = useState(false);

    // 基金买入按金额下单，其余按股数/份额下单
    const byAmount = assetType === 'fund' && side === 'buy';

    const load = useCallback(async (silent = false) => {
        if (!silent) setLoading(true);
        setError('');
        try {
            // 基准类数据算不出来不应该拖垮整个面板，单独吃掉异常、该列留空就行
            // 曲线要带点位（with_points），回撤图靠它画；一年也就 250 个点，不心疼
            const [acc, tr, fe, cv] = await Promise.all([
                fetchSimAccount(), fetchSimTrades(30), fetchSimFees(),
                fetchSimCurve(true).catch(() => null),
            ]);
            setAccount(acc);
            setTrades(tr.items || []);
            setFees(fe);
            setCurve(cv);

            const codes = (acc.positions || [])
                .filter(p => p.asset_type === 'fund').map(p => p.code);
            const ex = codes.length ? await fetchFundExcess(codes).catch(() => null) : null;
            setExcess(Object.fromEntries((ex?.items || []).map(i => [i.code, i])));
        } catch (e) {
            setError(e.message || '加载失败');
        } finally {
            if (!silent) setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    // 代码输满 6 位后查行情，用于展示名称/现价与预估金额
    useEffect(() => {
        if (!/^\d{6}$/.test(code)) {
            setQuote(null);
            setQuoteErr('');
            return;
        }
        let cancelled = false;
        const timer = setTimeout(async () => {
            try {
                const q = await fetchSimQuote(assetType, code);
                if (!cancelled) { setQuote(q); setQuoteErr(''); }
            } catch (e) {
                if (!cancelled) { setQuote(null); setQuoteErr(e.message || '未查询到行情'); }
            }
        }, 300);
        return () => { cancelled = true; clearTimeout(timer); };
    }, [code, assetType]);

    const positions = account?.positions || [];
    const pendingOrders = account?.pending_orders || [];

    const heldShares = useMemo(() => {
        const p = positions.find(x => x.asset_type === assetType && x.code === code);
        if (!p) return 0;
        // 今天买入的股票被冻结，T+1 才能卖
        return Math.max(0, p.shares - (p.frozen_shares || 0));
    }, [positions, assetType, code]);

    // 基金按金额申购是外扣费：先扣掉手续费，剩下的钱才换份额
    const estimate = useMemo(() => {
        const n = Number(qty);
        if (!quote?.price || !isFinite(n) || n <= 0) return null;
        if (byAmount) {
            const net = n / (1 + FUND_BUY_RATE);
            return { shares: net / quote.price, fee: n - net };
        }
        return { amount: n * quote.price };
    }, [qty, quote, byAmount]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!/^\d{6}$/.test(code)) { showToast('请输入 6 位代码', 'error'); return; }
        const n = Number(qty);
        if (!isFinite(n) || n <= 0) { showToast('请输入有效的数量或金额', 'error'); return; }

        setSubmitting(true);
        try {
            const res = await submitSimTrade({
                asset_type: assetType,
                code,
                side,
                ...(byAmount ? { amount: n } : { shares: n }),
            });
            const action = side === 'buy' ? '买入' : '卖出';
            if (res.status === 'pending') {
                // 场外基金下单时净值未公布，成交价与份额都还不知道
                showToast(`${action}委托已受理：${res.name || res.code}，${res.message}`, 'success');
            } else {
                const unit = assetType === 'stock' ? '股' : '份';
                showToast(
                    `${action} ${res.name || res.code} ${fmtMoney(res.shares, assetType === 'stock' ? 0 : 2)}${unit}`
                    + ` @ ${res.price}，手续费 ${fmtMoney(res.fee)} 元`,
                    'success',
                );
            }
            setQty('');
            await load(true);
        } catch (err) {
            showToast(err.message || '下单失败', 'error');
        } finally {
            setSubmitting(false);
        }
    };

    const handleSettle = async () => {
        setSettling(true);
        try {
            const r = await settleSimOrders();
            showToast(
                r.settled > 0
                    ? `已清算 ${r.settled} 笔委托`
                    : `暂无可清算的委托（${r.pending} 笔在等目标日净值公布）`,
                r.settled > 0 ? 'success' : '',
            );
            await load(true);
        } catch (e) {
            showToast(e.message || '清算失败', 'error');
        } finally {
            setSettling(false);
        }
    };

    // 点持仓行的卖出：把该标的与可卖数量填入表单
    const fillSell = (p) => {
        setAssetType(p.asset_type);
        setSide('sell');
        setCode(p.code);
        const avail = p.shares - (p.frozen_shares || 0);
        if (avail <= 0) {
            setQty('');
            showToast('今天买入的股票要 T+1 才能卖出', 'error');
            return;
        }
        setQty(String(p.asset_type === 'stock' ? Math.trunc(avail) : avail));
    };

    const handleReset = async () => {
        if (!window.confirm('确定重置模拟账户吗？持仓与交易记录将全部清空，资金回到 100 万。')) return;
        try {
            await resetSimAccount();
            setQty('');
            showToast('已重置为初始 100 万', 'success');
            await load(true);
        } catch (e) {
            showToast(e.message || '重置失败', 'error');
        }
    };

    const qtyLabel = assetType === 'stock'
        ? '股数（100 股整数倍）'
        : (byAmount ? '买入金额（元）' : '卖出份额');

    // 每次渲染现算，不提到模块外：页面挂着过夜的话，模块级常量会一直是昨天
    const now = new Date();
    const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
        + `-${String(now.getDate()).padStart(2, '0')}`;

    return (
        <section className="sim-panel">
            <div className="sim-head">
                <h3>
                    模拟投资
                    <span className="sim-sub">
                        初始资金 100 万 · 基金按盘后净值清算 · 股票 T+1 · 按真实费率收手续费
                    </span>
                </h3>
                <div className="sim-head-actions">
                    <button className="sim-refresh" onClick={() => load()} disabled={loading}>刷新</button>
                    <button className="sim-refresh" onClick={handleSettle} disabled={settling}>
                        {settling ? '清算中…' : '手动清算'}
                    </button>
                    <button className="sim-reset" onClick={handleReset}>重置账户</button>
                </div>
            </div>

            {loading && <div className="sim-status">加载模拟账户…</div>}
            {!loading && error && <div className="sim-status sim-error">加载失败：{error}</div>}

            {!loading && !error && account && (
                <>
                    {/* 人会盯着「总资产」问「今天涨了没」，但基金是日频数据：当日净值要等到晚上。
                        不把截止日期摆在明面上，看到的就是一个说不清到底是哪天的数字，
                        容易误以为数据卡住了 */}
                    {curve?.end && (
                        <div className="sim-asof">
                            数据截至 <strong>{curve.end}</strong> 收盘
                            {curve.end !== todayStr && (
                                <span> · 当日净值 20:30 后公布，休市日不更新</span>
                            )}
                        </div>
                    )}

                    <div className="sim-stats">
                        <StatCard label="总资产" value={fmtMoney(account.total_asset)} sub="元" />
                        <StatCard
                            label="可用资金"
                            value={fmtMoney(account.cash)}
                            sub={account.pending_cash > 0 ? `在途 ${fmtMoney(account.pending_cash)}` : '元'}
                        />
                        <StatCard label="持仓市值" value={fmtMoney(account.market_value)} sub={`${account.position_count} 个标的`} />
                        <StatCard
                            label="累计收益"
                            value={fmtMoney(account.total_profit)}
                            sub={fmtPctVal(account.total_profit_pct)}
                            cls={pnlClass(account.total_profit)}
                        />
                        <StatCard
                            label="当日盈亏"
                            value={fmtMoney(account.day_profit)}
                            sub="元"
                            cls={pnlClass(account.day_profit)}
                        />
                        {fees && (
                            <StatCard
                                label="累计手续费"
                                value={fmtMoney(fees.total_fee)}
                                sub={`${fees.trade_count} 笔成交`}
                            />
                        )}
                        {/* 绝对收益答不了「这个亏是我的问题还是大盘的问题」，这张卡答 */}
                        {curve?.benchmark?.excess != null && (
                            <StatCard
                                label={`超额 vs ${curve.benchmark.benchmark}`}
                                value={fmtPP(curve.benchmark.excess)}
                                sub={`组合 ${fmtPctVal(curve.benchmark.pct)}`
                                    + ` / 基准 ${fmtPctVal(curve.benchmark.benchmark_pct)}`}
                                cls={pnlClass(curve.benchmark.excess)}
                                title={`自 ${curve.benchmark.start} 建仓起，截至 ${curve.benchmark.end} 收盘。`
                                    + `基数是初始资金，建仓手续费算在里面。${curve.benchmark.warning || ''}`}
                            />
                        )}
                    </div>

                    <PortfolioRisk curve={curve} />

                    <form className="sim-form" onSubmit={handleSubmit}>
                        <div className="sim-field">
                            <label>类型</label>
                            <select value={assetType} onChange={e => { setAssetType(e.target.value); setQty(''); }}>
                                <option value="stock">股票</option>
                                <option value="fund">基金</option>
                            </select>
                        </div>

                        <div className="sim-field sim-field-code">
                            <label>代码</label>
                            <input
                                type="text"
                                inputMode="numeric"
                                maxLength={6}
                                placeholder="6 位代码"
                                value={code}
                                onChange={e => setCode(e.target.value.replace(/\D/g, ''))}
                            />
                            <div className="sim-quote">
                                {quote && (
                                    <>
                                        <span className="sim-quote-name">{quote.name || quote.code}</span>
                                        <span className="sim-quote-price">{quote.price}</span>
                                        <span className={pnlClass(quote.pct)}>{fmtPctVal(quote.pct)}</span>
                                    </>
                                )}
                                {!quote && quoteErr && <span className="sim-quote-err">{quoteErr}</span>}
                            </div>
                        </div>

                        <div className="sim-field">
                            <label>方向</label>
                            <div className="sim-side">
                                <button
                                    type="button"
                                    className={`sim-side-btn buy${side === 'buy' ? ' active' : ''}`}
                                    onClick={() => { setSide('buy'); setQty(''); }}
                                >买入</button>
                                <button
                                    type="button"
                                    className={`sim-side-btn sell${side === 'sell' ? ' active' : ''}`}
                                    onClick={() => { setSide('sell'); setQty(''); }}
                                >卖出</button>
                            </div>
                        </div>

                        <div className="sim-field sim-field-qty">
                            <label>{qtyLabel}</label>
                            <input
                                type="number"
                                min="0"
                                step={assetType === 'stock' ? 100 : 'any'}
                                placeholder={byAmount ? '如 10000' : '如 100'}
                                value={qty}
                                onChange={e => setQty(e.target.value)}
                            />
                            {side === 'sell' && heldShares > 0 && (
                                <button
                                    type="button"
                                    className="sim-all-btn"
                                    onClick={() => setQty(String(assetType === 'stock' ? Math.trunc(heldShares) : heldShares))}
                                >全部 {fmtMoney(heldShares, assetType === 'stock' ? 0 : 2)}</button>
                            )}
                        </div>

                        <div className="sim-field sim-field-submit">
                            <button type="submit" className={`sim-submit ${side}`} disabled={submitting}>
                                {submitting ? '提交中…' : (side === 'buy' ? '确认买入' : '确认卖出')}
                            </button>
                            {estimate != null && (
                                <div className="sim-estimate">
                                    {byAmount
                                        ? `≈ ${fmtMoney(estimate.shares, 2)} 份（申购费约 ${fmtMoney(estimate.fee)} 元）`
                                        : `≈ ${fmtMoney(estimate.amount)} 元`}
                                </div>
                            )}
                        </div>
                    </form>

                    {pendingOrders.length > 0 && (
                        <>
                            <h4 className="sim-block-title">
                                待清算委托（{pendingOrders.length}）
                                <span className="sim-sub">
                                    下单时净值还没公布，成交价与份额要等盘后清算才定
                                </span>
                            </h4>
                            <div className="sim-table-wrap">
                                <table className="sim-table">
                                    <thead>
                                        <tr>
                                            <th>下单时间</th>
                                            <th>方向</th>
                                            <th>名称</th>
                                            <th>代码</th>
                                            <th>委托</th>
                                            <th>成交净值日</th>
                                            <th>状态</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {pendingOrders.map(o => (
                                            <tr key={o.id}>
                                                <td>{o.created_at}</td>
                                                <td className={o.side === 'buy' ? 'val-gain' : 'val-loss'}>
                                                    {o.side === 'buy' ? '买入' : '卖出'}
                                                </td>
                                                <td>{o.name || '--'}</td>
                                                <td>{o.code}</td>
                                                <td>
                                                    {o.side === 'buy'
                                                        ? `${fmtMoney(o.order_amount)} 元`
                                                        : `${fmtMoney(o.order_shares, 2)} 份`}
                                                </td>
                                                <td>{o.nav_date}</td>
                                                <td>等净值公布</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}

                    <h4 className="sim-block-title">持仓列表（{positions.length}）</h4>
                    <div className="sim-table-wrap">
                        <table className="sim-table">
                            <thead>
                                <tr>
                                    <th>类型</th>
                                    <th>名称</th>
                                    <th>代码</th>
                                    <th>持仓</th>
                                    <th>成本价</th>
                                    <th>现价</th>
                                    <th>涨跌幅</th>
                                    <th>市值(元)</th>
                                    <th>盈亏(元)</th>
                                    <th>盈亏率</th>
                                    <th>仓位</th>
                                    <th>持有(天)</th>
                                    <th title="这只基金近 365 天相对沪深300 的表现（按累计净值、含分红），跟你持有多久无关。短债与海外标的跟沪深300 不同源，这一列对它们只是噪音">
                                        近1年超额
                                    </th>
                                    <th>赎回费率</th>
                                    <th>全卖手续费</th>
                                    <th>操作</th>
                                </tr>
                            </thead>
                            <tbody>
                                {positions.map(p => {
                                    const ls = lotSummary(p);
                                    return (
                                    <tr key={`${p.asset_type}-${p.code}`}>
                                        <td>{ASSET_LABEL[p.asset_type]}</td>
                                        <td>{p.name || '--'}</td>
                                        <td>{p.code}</td>
                                        <td>
                                            {fmtMoney(p.shares, p.asset_type === 'stock' ? 0 : 2)}
                                            {p.frozen_shares > 0 && (
                                                <span className="sim-frozen"> 冻结 {fmtMoney(p.frozen_shares, 0)}</span>
                                            )}
                                        </td>
                                        <td>{fmtMoney(p.avg_cost, 4)}</td>
                                        <td>{p.stale ? '--' : fmtMoney(p.price, 4)}</td>
                                        <td className={pnlClass(p.pct)}>{fmtPctVal(p.pct)}</td>
                                        <td>{fmtMoney(p.market_value)}</td>
                                        <td className={pnlClass(p.profit)}>{fmtMoney(p.profit)}</td>
                                        <td className={pnlClass(p.profit)}>{fmtPctVal(p.profit_pct)}</td>
                                        <td>{fmtPctVal(p.weight).replace('+', '')}</td>
                                        <td>{ls.days}</td>
                                        {/* 超额不染色：它不是我的盈亏，把它涂成红绿会让人当成自己赚了多少 */}
                                        <td title={(() => {
                                            const e = excess[p.code];
                                            if (!e) return p.asset_type === 'fund' ? '没拿到超额数据' : '非基金标的不算超额';
                                            if (e.excess == null) return e.reason || '算不出来';
                                            return `${e.start}~${e.end} 基金 ${fmtPctVal(e.pct)}`
                                                + ` / 基准 ${fmtPctVal(e.benchmark_pct)}`
                                                + (e.basis === 'nav' ? '（累计净值缺失，按单位净值算，有分红会被低估）' : '');
                                        })()}>{fmtPP(excess[p.code]?.excess)}</td>
                                        <td>{p.asset_type === 'fund'
                                            ? (ls.rate != null ? `${ls.rate}%` : '--')
                                            : 'T+1'}</td>
                                        <td>{fmtMoney(p.sell_fee_now)}</td>
                                        <td>
                                            <button className="sim-row-sell" onClick={() => fillSell(p)}>卖出</button>
                                        </td>
                                    </tr>
                                    );
                                })}
                                {positions.length === 0 && (
                                    <tr>
                                        <td colSpan={16} className="sim-empty">还没有持仓，在上方下单买入吧</td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>

                    <h4 className="sim-block-title">交易记录（最近 {trades.length} 笔）</h4>
                    <div className="sim-table-wrap">
                        <table className="sim-table">
                            <thead>
                                <tr>
                                    <th>时间</th>
                                    <th>方向</th>
                                    <th>类型</th>
                                    <th>名称</th>
                                    <th>代码</th>
                                    <th>成交价</th>
                                    <th>数量</th>
                                    <th>金额(元)</th>
                                    <th>手续费(元)</th>
                                    <th>净值日</th>
                                </tr>
                            </thead>
                            <tbody>
                                {trades.map(t => (
                                    <tr key={t.id}>
                                        <td>{t.created_at}</td>
                                        <td className={t.side === 'buy' ? 'val-gain' : 'val-loss'}>
                                            {t.side === 'buy' ? '买入' : '卖出'}
                                        </td>
                                        <td>{ASSET_LABEL[t.asset_type]}</td>
                                        <td>{t.name || '--'}</td>
                                        <td>{t.code}</td>
                                        <td>{fmtMoney(t.price, 4)}</td>
                                        <td>{fmtMoney(t.shares, t.asset_type === 'stock' ? 0 : 2)}</td>
                                        <td>{fmtMoney(t.amount)}</td>
                                        {/* 鼠标悬停看费用明细，如「赎回费1.5%=149.90」 */}
                                        <td title={t.fee_detail || ''}>{fmtMoney(t.fee)}</td>
                                        <td>{t.nav_date || '--'}</td>
                                    </tr>
                                ))}
                                {trades.length === 0 && (
                                    <tr>
                                        <td colSpan={10} className="sim-empty">暂无交易记录</td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </section>
    );
}
