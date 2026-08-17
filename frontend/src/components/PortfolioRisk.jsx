import React, { useEffect, useMemo, useRef } from 'react';
import { Chart, registerables } from 'chart.js';
import { fmtNum, fmtPctVal } from '../utils/format';

Chart.register(...registerables);

// 回撤后端给的是正数幅度（3.4 表示回撤 3.4%），画图和展示都要带回负号
function fmtDd(v) {
    if (v == null || !isFinite(v)) return '--';
    return v <= 0 ? '0.00%' : `-${Number(v).toFixed(2)}%`;
}

/**
 * 指标卡。复用 .sim-stat 那套样式，不另起一套视觉
 */
function Metric({ label, value, sub, cls = '', title = '' }) {
    return (
        <div className="sim-stat" title={title}>
            <div className="sim-stat-label">{label}</div>
            <div className={`sim-stat-value ${cls}`}>{value}</div>
            {sub && <div className="sim-stat-sub">{sub}</div>}
        </div>
    );
}

/**
 * 组合风险：指标卡 + 回撤曲线 + 降仓纪律线
 *
 * 所有数字都来自 /api/sim/curve，一个都不在前端重算：阈值、档位判定、年化口径
 * 全在后端（portfolio_service）。这里只负责画，不负责判断——两处各算一遍，
 * 迟早会出现「页面说没触发、简报说触发了」。
 */
export default function PortfolioRisk({ curve }) {
    const canvasRef = useRef(null);
    const chartRef = useRef(null);

    // useMemo 不是为了省 CPU：`|| []` 每次 render 都是新数组，会把下面那个
    // 画图的 effect 变成每次 render 都重建一次 Chart 实例
    const points = useMemo(() => curve?.curve || [], [curve]);
    const risk = curve?.risk || null;
    const disc = curve?.discipline || null;

    // 回撤离纪律线太远时不画那两条线：y 轴被拉到 -20% 会把真实曲线压成一条直线，
    // 什么都看不出来。远的时候用文字说距离就够了
    const showLimits = useMemo(() => {
        if (!disc?.half || !points.length) return false;
        const worst = Math.max(...points.map(p => p.drawdown || 0));
        return worst >= disc.half / 2;
    }, [disc, points]);

    useEffect(() => {
        if (!canvasRef.current || points.length < 2) return;

        const cs = getComputedStyle(document.documentElement);
        const muted = cs.getPropertyValue('--text-muted').trim();
        const grid = cs.getPropertyValue('--chart-grid').trim();
        const lossColor = cs.getPropertyValue('--loss').trim();   // 跌绿
        const limitColor = cs.getPropertyValue('--gain').trim();  // 纪律线用警示红

        if (chartRef.current) chartRef.current.destroy();

        const labels = points.map(p => p.date);
        const dd = points.map(p => -(p.drawdown || 0));
        const flat = (v) => labels.map(() => -v);

        const datasets = [{
            label: '回撤',
            data: dd,
            borderColor: lossColor,
            borderWidth: 1.5,
            fill: true,
            backgroundColor: 'rgba(34, 211, 167, 0.10)',
            pointRadius: 0,
            tension: 0.2,
        }];
        if (showLimits) {
            datasets.push({
                label: `降半仓线 -${disc.half}%`,
                data: flat(disc.half),
                borderColor: limitColor,
                borderWidth: 1,
                borderDash: [5, 4],
                pointRadius: 0,
                fill: false,
            }, {
                label: `降三成仓线 -${disc.third}%`,
                data: flat(disc.third),
                borderColor: limitColor,
                borderWidth: 1.5,
                borderDash: [2, 3],
                pointRadius: 0,
                fill: false,
            });
        }

        chartRef.current = new Chart(canvasRef.current, {
            type: 'line',
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: showLimits,
                        labels: { color: muted, boxWidth: 18, font: { size: 10 } },
                    },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        callbacks: {
                            label: (ctx) => `${ctx.dataset.label}：${ctx.parsed.y.toFixed(2)}%`,
                        },
                    },
                },
                scales: {
                    x: { ticks: { color: muted, maxTicksLimit: 8, font: { size: 10 } }, grid: { color: grid } },
                    y: {
                        max: 0,
                        ticks: {
                            color: muted, font: { size: 10 },
                            callback: (v) => `${v}%`,
                        },
                        grid: { color: grid },
                    },
                },
                interaction: { mode: 'index', intersect: false },
            },
        });

        return () => {
            if (chartRef.current) {
                chartRef.current.destroy();
                chartRef.current = null;
            }
        };
    }, [points, showLimits, disc]);

    if (!curve || !curve.days) return null;

    const stageText = {
        third: `已触发降至三成仓（≥${disc?.third}%）`,
        half: `已触发降半仓（≥${disc?.half}%）`,
        normal: disc?.gap_to_half != null ? `距降半仓线 ${disc.gap_to_half.toFixed(2)}pp` : '未触发',
        unknown: '档位未知',
    }[disc?.stage] || '档位未知';
    const stageCls = (disc?.stage === 'half' || disc?.stage === 'third') ? 'val-gain' : '';

    return (
        <div className="sim-risk">
            <div className="sim-block-title">
                组合风险
                <span className="sim-sub">
                    样本 {curve.days} 个交易日（{curve.start} → {curve.end}）· 收盘净值口径，不含今日盘中波动
                </span>
            </div>

            <div className="sim-stats">
                <Metric
                    label="当前回撤"
                    value={fmtDd(curve.drawdown)}
                    sub={stageText}
                    cls={stageCls}
                    title={`峰值 ${curve.peak} @ ${curve.peak_date} → 最新 ${curve.latest_total} @ ${curve.end}`}
                />
                <Metric
                    label="最大回撤"
                    value={fmtDd(curve.max_drawdown)}
                    sub={curve.max_drawdown_date || ''}
                />
                <Metric label="累计收益" value={fmtPctVal(risk?.cum_return)} sub="基数为初始资金" />
                <Metric
                    label="年化收益"
                    value={fmtPctVal(risk?.ann_return)}
                    sub={risk?.ann_return == null ? '样本不足' : ''}
                />
                <Metric
                    label="年化波动"
                    value={risk?.ann_vol == null ? '--' : `${risk.ann_vol.toFixed(2)}%`}
                    sub={risk?.ann_vol == null ? '样本不足' : '日波动 ×√252'}
                />
                <Metric
                    label="夏普"
                    value={fmtNum(risk?.sharpe)}
                    sub={risk?.rf != null ? `无风险 ${risk.rf}%` : ''}
                    title="（年化收益 − 无风险利率）/ 年化波动，与基金对比面板同口径"
                />
                <Metric
                    label="卡玛"
                    value={fmtNum(risk?.calmar)}
                    sub="年化 / 最大回撤"
                    title="每承受 1% 最大回撤换来多少年化收益"
                />
                <Metric
                    label="日胜率"
                    value={risk?.win_rate == null ? '--' : `${risk.win_rate.toFixed(1)}%`}
                    sub={risk?.win_rate == null ? '样本不足' : '上涨交易日占比'}
                    title="胜率高不代表赚钱：小赚多次、大亏一次也能有高胜率"
                />
                {risk?.recover_pct ? (
                    <Metric
                        label="回本需涨"
                        value={fmtPctVal(risk.recover_pct)}
                        sub="从当前位置回到峰值"
                        title="跌 20% 要涨 25% 才回本，这个不对称就是降仓纪律的理由"
                    />
                ) : null}
            </div>

            {points.length >= 2 ? (
                <div className="sim-risk-chart"><canvas ref={canvasRef} /></div>
            ) : (
                <div className="sim-risk-note">只有 {points.length} 个净值点，画不出曲线。</div>
            )}

            {!showLimits && disc?.half != null && (
                <div className="sim-risk-note">
                    降仓纪律线（-{disc.half}% / -{disc.third}%）离当前回撤还远，画上去会把曲线压平，暂不绘制。
                </div>
            )}
            {risk?.reason && (
                <div className="sim-risk-note sim-risk-warn">年化与波动类指标空着：{risk.reason}</div>
            )}
            {risk?.note && <div className="sim-risk-note">{risk.note}</div>}
            {(curve.warnings || []).map((w, i) => (
                <div key={i} className="sim-risk-note sim-risk-warn">⚠️ {w}</div>
            ))}
        </div>
    );
}
