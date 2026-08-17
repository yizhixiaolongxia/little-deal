import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ThemeProvider } from './context/ThemeContext';
import Header from './components/Header';
import FundInput from './components/FundInput';
import FundCardList from './components/FundCardList';
import FundRecommend from './components/FundRecommend';
import MarketRisk from './components/MarketRisk';
import MacroDashboard from './components/MacroDashboard';
import StockScreener from './components/StockScreener';
import SimTrade from './components/SimTrade';
import FundScreener from './components/FundScreener';
import HoldingsXray from './components/HoldingsXray';
import FundDetail from './components/FundDetail';
import ComparePanel from './components/ComparePanel';
import Toast, { showToast } from './components/Toast';
import { getWatchlist, addToWatchlist, removeFromWatchlist, clearWatchlist, fetchRealtime, fetchLatestNav, fetchManager } from './hooks/useFundApi';
import './styles/global.css';

const REFRESH_MS = 30000;

function App() {
    const [watchlist, setWatchlist] = useState([]);
    const [funds, setFunds] = useState({});
    const [selectedFund, setSelectedFund] = useState(null);
    const [autoRefresh, setAutoRefresh] = useState(true);
    const [showCompare, setShowCompare] = useState(false);
    const [activePage, setActivePage] = useState('funds'); // 'funds' | 'macro' | 'stocks' | 'sim' | 'fundmarket' | 'xray'
    const refreshTimerRef = useRef(null);

    // 初始化：从后端加载 watchlist
    useEffect(() => {
        loadInitialData();
    }, []);

    // 自动刷新
    useEffect(() => {
        if (autoRefresh && watchlist.length > 0) {
            refreshTimerRef.current = setInterval(refreshAll, REFRESH_MS);
        }
        return () => {
            if (refreshTimerRef.current) clearInterval(refreshTimerRef.current);
        };
    }, [autoRefresh, watchlist]); // eslint-disable-line

    const loadInitialData = async () => {
        try {
            const data = await getWatchlist();
            const codes = data.codes || [];
            setWatchlist(codes);
            if (codes.length > 0) {
                const fundsMap = {};
                codes.forEach(c => { fundsMap[c] = { loading: true }; });
                setFunds(fundsMap);
                // 逐个加载实时数据
                for (const code of codes) {
                    try {
                        const rt = await fetchRealtime(code);
                        setFunds(prev => ({ ...prev, [code]: { realtime: rt } }));
                        loadManager(code);
                    } catch (e) {
                        setFunds(prev => ({ ...prev, [code]: { error: e.message } }));
                    }
                }
            }
        } catch (e) {
            console.error('加载 watchlist 失败', e);
        }
    };

    // 拉取基金经理（经理不常变，仅在添加/初次加载时拉一次）
    const loadManager = async (code) => {
        try {
            const m = await fetchManager(code);
            setFunds(prev => (prev[code] ? { ...prev, [code]: { ...prev[code], manager: m.manager } } : prev));
        } catch (e) { /* ignore */ }
    };

    const refreshAll = useCallback(async () => {
        for (const code of watchlist) {
            try {
                const rt = await fetchRealtime(code);
                setFunds(prev => ({ ...prev, [code]: { realtime: rt } }));
            } catch (e) {
                // 保持已有数据不变
            }
        }
    }, [watchlist]);

    const handleAdd = async (code) => {
        if (watchlist.includes(code)) {
            showToast('该基金已在列表中', 'error');
            return;
        }
        // 先乐观更新 UI
        setWatchlist(prev => [...prev, code]);
        setFunds(prev => ({ ...prev, [code]: { loading: true } }));

        try {
            await addToWatchlist(code);
            const rt = await fetchRealtime(code);
            setFunds(prev => ({ ...prev, [code]: { realtime: rt } }));
            loadManager(code);
            showToast(`已添加 ${rt.name || code}`, 'success');
        } catch (e) {
            setFunds(prev => ({ ...prev, [code]: { error: e.message } }));
            showToast('获取数据失败，请检查基金代码或网络', 'error');
        }
    };

    const handleRemove = async (code) => {
        setWatchlist(prev => prev.filter(c => c !== code));
        setFunds(prev => {
            const next = { ...prev };
            delete next[code];
            return next;
        });
        if (selectedFund === code) setSelectedFund(null);
        try {
            await removeFromWatchlist(code);
        } catch (e) { /* ignore */ }
    };

    const handleClearAll = async () => {
        if (watchlist.length === 0) { showToast('自选列表为空', 'error'); return; }
        if (!window.confirm(`确定要清空全部 ${watchlist.length} 只自选基金吗？此操作不可恢复。`)) return;
        setWatchlist([]);
        setFunds({});
        setSelectedFund(null);
        try {
            await clearWatchlist();
            showToast('已清空全部自选', 'success');
        } catch (e) { /* ignore */ }
    };

    const handleCompare = () => {
        if (watchlist.length === 0) { showToast('自选列表为空', 'error'); return; }
        if (watchlist.length === 1) { showToast('至少需要 2 只基金才能对比', 'error'); return; }
        setShowCompare(true);
    };

    const toggleAutoRefresh = () => {
        setAutoRefresh(prev => !prev);
    };

    // ESC 关闭
    useEffect(() => {
        const handler = (e) => {
            if (e.key === 'Escape') {
                if (showCompare) setShowCompare(false);
                else if (selectedFund) setSelectedFund(null);
            }
        };
        document.addEventListener('keydown', handler);
        return () => document.removeEventListener('keydown', handler);
    }, [showCompare, selectedFund]);

    const timeStr = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    return (
        <ThemeProvider>
            <div className="app">
                <Header
                    watchlistCount={watchlist.length}
                    autoRefresh={autoRefresh}
                    onRefresh={refreshAll}
                    onToggleAutoRefresh={toggleAutoRefresh}
                    onCompare={handleCompare}
                    onClearAll={handleClearAll}
                />

                <nav className="page-tabs">
                    <button
                        className={`page-tab ${activePage === 'funds' ? 'active' : ''}`}
                        onClick={() => setActivePage('funds')}
                    >
                        基金观察
                    </button>
                    {/* 宏观排在第二个：自上而下看，先定宏观环境再挑基金和个股。
                        没放第一个是因为默认页是持仓，天天要看的东西不该被挤到后面 */}
                    <button
                        className={`page-tab ${activePage === 'macro' ? 'active' : ''}`}
                        onClick={() => setActivePage('macro')}
                    >
                        宏观看板
                    </button>
                    <button
                        className={`page-tab ${activePage === 'stocks' ? 'active' : ''}`}
                        onClick={() => setActivePage('stocks')}
                    >
                        A股基本面
                    </button>
                    <button
                        className={`page-tab ${activePage === 'fundmarket' ? 'active' : ''}`}
                        onClick={() => setActivePage('fundmarket')}
                    >
                        全市场基金
                    </button>
                    <button
                        className={`page-tab ${activePage === 'xray' ? 'active' : ''}`}
                        onClick={() => setActivePage('xray')}
                    >
                        持仓穿透
                    </button>
                    <button
                        className={`page-tab ${activePage === 'sim' ? 'active' : ''}`}
                        onClick={() => setActivePage('sim')}
                    >
                        模拟投资
                    </button>
                </nav>

                {activePage === 'funds' && (
                    <>
                        <div className="controls" style={{ marginBottom: 24 }}>
                            <FundInput onAdd={handleAdd} />
                        </div>

                        <FundCardList
                            watchlist={watchlist}
                            funds={funds}
                            selectedFund={selectedFund}
                            onSelect={setSelectedFund}
                            onRemove={handleRemove}
                        />

                        <FundRecommend watchlist={watchlist} onAdd={handleAdd} onSelect={setSelectedFund} />

                        <MarketRisk />

                        <div className="status-bar">
                            <div className={`live-dot ${autoRefresh ? '' : 'off'}`}></div>
                            <span>
                                {watchlist.length === 0
                                    ? '等待添加基金'
                                    : `${autoRefresh ? '自动刷新开启' : '自动刷新关闭'} · 追踪 ${watchlist.length} 只基金 · ${timeStr}`
                                }
                            </span>
                        </div>
                    </>
                )}

                {activePage === 'macro' && <MacroDashboard />}

                {activePage === 'stocks' && <StockScreener />}

                {activePage === 'sim' && <SimTrade />}

                {activePage === 'xray' && <HoldingsXray watchlist={watchlist} funds={funds} />}

                {activePage === 'fundmarket' && (
                    <FundScreener
                        watchlist={watchlist}
                        onAdd={handleAdd}
                        onRemove={handleRemove}
                        onSelect={setSelectedFund}
                    />
                )}
            </div>

            {selectedFund && (
                <FundDetail
                    code={selectedFund}
                    fundData={funds[selectedFund]}
                    onClose={() => setSelectedFund(null)}
                />
            )}

            {showCompare && (
                <ComparePanel
                    watchlist={watchlist}
                    funds={funds}
                    onClose={() => setShowCompare(false)}
                />
            )}

            <Toast />
        </ThemeProvider>
    );
}

export default App;
