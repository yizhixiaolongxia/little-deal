import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Chart, registerables } from 'chart.js';
import { fetchHistory, fetchHoldings, fetchManager, fetchRealtime } from '../hooks/useFundApi';
import HoldingsTable from './HoldingsTable';

Chart.register(...registerables);

const PERIODS = [
    { label: '1月', days: 30 },
    { label: '3月', days: 90 },
    { label: '6月', days: 180 },
    { label: '1年', days: 365 },
    { label: '全部', days: 99999 },
];

export default function FundDetail({ code, fundData, onClose }) {
    const [period, setPeriod] = useState(90);
    const [historyData, setHistoryData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [holdings, setHoldings] = useState(null);
    const [manager, setManager] = useState(fundData?.manager || '');
    const [realtime, setRealtime] = useState(fundData?.realtime || null);
    const chartInstanceRef = useRef(null);
    const canvasRef = useRef(null);

    const rt = realtime;

    // 加载历史净值
    const loadHistory = useCallback(async (days) => {
        if (!code) return;
        setLoading(true);
        try {
            const data = await fetchHistory(code, days === 99999 ? 10000 : days);
            setHistoryData(data);
        } catch (e) {
            console.error('加载历史净值失败', e);
        }
        setLoading(false);
    }, [code]);

    useEffect(() => {
        if (code) {
            loadHistory(period);
            loadHoldingsData(code);
            if (!fundData?.manager) {
                fetchManager(code).then(m => setManager(m.manager || '')).catch(() => {});
            } else {
                setManager(fundData.manager);
            }
            if (!fundData?.realtime) {
                fetchRealtime(code).then(r => setRealtime(r)).catch(() => {});
            } else {
                setRealtime(fundData.realtime);
            }
        }
        return () => {
            if (chartInstanceRef.current) {
                chartInstanceRef.current.destroy();
                chartInstanceRef.current = null;
            }
        };
    }, [code]);  // eslint-disable-line

    useEffect(() => {
        if (code) loadHistory(period);
    }, [period]);  // eslint-disable-line

    // 绘制图表
    useEffect(() => {
        if (!historyData || !canvasRef.current) return;
        const list = historyData?.Data?.LSJZList || [];
        if (list.length === 0) return;

        const sorted = [...list].sort((a, b) => (a.FSRQ || '').localeCompare(b.FSRQ || ''));
        const labels = sorted.map(r => r.FSRQ);
        const values = sorted.map(r => parseFloat(r.DWJZ));

        if (chartInstanceRef.current) {
            chartInstanceRef.current.destroy();
        }

        const cs = getComputedStyle(document.documentElement);
        const muted = cs.getPropertyValue('--text-muted').trim();
        const grid = cs.getPropertyValue('--chart-grid').trim();
        const accent = cs.getPropertyValue('--accent').trim();

        chartInstanceRef.current = new Chart(canvasRef.current, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    data: values,
                    borderColor: accent,
                    borderWidth: 1.5,
                    fill: true,
                    backgroundColor: 'rgba(99, 140, 255, 0.08)',
                    pointRadius: 0,
                    tension: 0.3,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false } },
                scales: {
                    x: { ticks: { color: muted, maxTicksLimit: 8, font: { size: 10 } }, grid: { color: grid } },
                    y: { ticks: { color: muted, font: { size: 10 } }, grid: { color: grid } },
                },
                interaction: { mode: 'index', intersect: false },
            },
        });
    }, [historyData]);

    // 加载持仓
    const loadHoldingsData = async (fundCode) => {
        try {
            const data = await fetchHoldings(fundCode);
            setHoldings(data);
        } catch (e) {
            console.warn('持仓加载失败', e);
        }
    };

    if (!code) return null;

    const recentList = (historyData?.Data?.LSJZList || []).slice(0, 10);

    return (
        <div className="modal-overlay active" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
            <div className="modal">
                <div className="modal-header">
                    <div>
                        <div className="modal-title">{rt?.name || code}</div>
                        <div className="modal-code">{code}{manager ? ` · 基金经理 ${manager}` : ''}</div>
                    </div>
                    <button className="modal-close" onClick={onClose}>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
                    </button>
                </div>

                {rt && (
                    <div className="modal-metrics">
                        <div className="metric">
                            <div className="metric-label">单位净值</div>
                            <div className="metric-value">{rt.dwjz || '--'}</div>
                        </div>
                        <div className="metric">
                            <div className="metric-label">{rt.settled ? '最新净值' : '估值'}</div>
                            <div className="metric-value">{rt.gsz || '--'}</div>
                        </div>
                        <div className="metric">
                            <div className="metric-label">{rt.settled ? '日增长率' : '估算涨跌'}</div>
                            <div className={`metric-value ${parseFloat(rt.gszzl) >= 0 ? 'gain-text' : 'loss-text'}`}>
                                {rt.gszzl ? `${parseFloat(rt.gszzl) > 0 ? '+' : ''}${rt.gszzl}%` : '--'}
                            </div>
                        </div>
                        <div className="metric">
                            <div className="metric-label">净值日期</div>
                            <div className="metric-value small">{rt.jzrq || '--'}</div>
                        </div>
                    </div>
                )}

                <div className="chart-section">
                    <div className="period-selector">
                        {PERIODS.map(p => (
                            <button
                                key={p.days}
                                className={`period-btn ${period === p.days ? 'active' : ''}`}
                                onClick={() => setPeriod(p.days)}
                            >
                                {p.label}
                            </button>
                        ))}
                    </div>
                    <div className="chart-container">
                        <canvas ref={canvasRef}></canvas>
                        {loading && <div className="chart-loading">加载中...</div>}
                    </div>
                </div>

                <div className="history-section">
                    <h4>近期净值</h4>
                    <table className="history-table">
                        <thead><tr><th>日期</th><th>单位净值</th><th>累计净值</th><th>日增长率</th></tr></thead>
                        <tbody>
                            {recentList.map(row => (
                                <tr key={row.FSRQ}>
                                    <td>{row.FSRQ}</td>
                                    <td>{row.DWJZ}</td>
                                    <td>{row.LJJZ}</td>
                                    <td className={parseFloat(row.JZZZL) >= 0 ? 'gain-text' : 'loss-text'}>
                                        {row.JZZZL ? `${row.JZZZL}%` : '--'}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>

                <HoldingsTable code={code} holdingsData={holdings} />
            </div>
        </div>
    );
}
