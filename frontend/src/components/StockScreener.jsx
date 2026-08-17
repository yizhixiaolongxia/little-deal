import React, { useState, useEffect, useMemo } from 'react';
import { fetchStockList, getStockWatchlist, addStockToWatchlist, removeStockFromWatchlist } from '../hooks/useFundApi';

const PAGE_SIZE = 50;

const BOARDS = ['全部', '沪市主板', '深市主板', '创业板', '科创板', '北交所'];

// 表头列配置：key 对应后端字段，type 决定排序方式，color 表示涨红跌绿着色
const COLUMNS = [
    { key: 'code', label: '代码', type: 'str' },
    { key: 'name', label: '名称', type: 'str' },
    { key: 'board', label: '板块', type: 'str' },
    { key: 'industry', label: '行业', type: 'str' },
    { key: 'price', label: '最新价', type: 'num' },
    { key: 'pct', label: '涨跌幅%', type: 'num', color: true },
    { key: 'total_mv', label: '总市值(亿)', type: 'num', fmt: v => (v / 1e8).toFixed(1) },
    { key: 'pe', label: 'PE(动)', type: 'num' },
    { key: 'pb', label: 'PB', type: 'num' },
    { key: 'roe', label: 'ROE%', type: 'num' },
    { key: 'gross_margin', label: '毛利率%', type: 'num' },
    { key: 'net_margin', label: '净利率%', type: 'num' },
    { key: 'revenue_yoy', label: '营收同比%', type: 'num', color: true },
    { key: 'profit_yoy', label: '净利同比%', type: 'num', color: true },
    { key: 'debt_ratio', label: '资产负债率%', type: 'num' },
    { key: 'ocf_ps', label: '每股现金流', type: 'num' },
];

function fmtCell(col, v) {
    if (v == null) return '--';
    if (col.fmt) return col.fmt(v);
    if (col.type === 'num') return Number(v).toFixed(2);
    return v;
}

export default function StockScreener() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [keyword, setKeyword] = useState('');
    const [board, setBoard] = useState('全部');
    const [sortKey, setSortKey] = useState('total_mv');
    const [sortDir, setSortDir] = useState('desc');
    const [page, setPage] = useState(1);
    const [watchlist, setWatchlist] = useState(() => new Set());
    const [onlyWatchlist, setOnlyWatchlist] = useState(false);

    useEffect(() => {
        load();
        loadWatchlist();
    }, []); // eslint-disable-line

    const loadWatchlist = async () => {
        try {
            const res = await getStockWatchlist();
            setWatchlist(new Set(res.codes || []));
        } catch (e) {
            // 自选加载失败不阻断主列表
        }
    };

    const toggleStar = async (code) => {
        const isStarred = watchlist.has(code);
        // 乐观更新
        setWatchlist(prev => {
            const next = new Set(prev);
            if (isStarred) next.delete(code); else next.add(code);
            return next;
        });
        try {
            const res = isStarred
                ? await removeStockFromWatchlist(code)
                : await addStockToWatchlist(code);
            setWatchlist(new Set(res.codes || []));
        } catch (e) {
            // 失败回滚
            setWatchlist(prev => {
                const next = new Set(prev);
                if (isStarred) next.add(code); else next.delete(code);
                return next;
            });
        }
    };

    const load = async (force = false) => {
        setLoading(true);
        setError('');
        try {
            const res = await fetchStockList(force);
            setData(res);
        } catch (e) {
            setError(e.message || '加载失败');
        } finally {
            setLoading(false);
        }
    };

    // 只看自选 + 板块 + 搜索过滤（代码 / 名称 / 行业）
    const filtered = useMemo(() => {
        let items = data?.items || [];
        if (onlyWatchlist) {
            items = items.filter(r => watchlist.has(r.code));
        }
        if (board !== '全部') {
            items = items.filter(r => r.board === board);
        }
        const kw = keyword.trim().toLowerCase();
        if (!kw) return items;
        return items.filter(r =>
            r.code.includes(kw) ||
            (r.name && r.name.toLowerCase().includes(kw)) ||
            (r.industry && r.industry.toLowerCase().includes(kw))
        );
    }, [data, keyword, board, onlyWatchlist, watchlist]);

    // 表头排序（空值恒排最后）
    const sorted = useMemo(() => {
        const col = COLUMNS.find(c => c.key === sortKey);
        if (!col) return filtered;
        const dir = sortDir === 'asc' ? 1 : -1;
        return [...filtered].sort((a, b) => {
            const va = a[sortKey];
            const vb = b[sortKey];
            if (col.type === 'num') {
                if (va == null && vb == null) return 0;
                if (va == null) return 1;
                if (vb == null) return -1;
                return (va - vb) * dir;
            }
            return String(va || '').localeCompare(String(vb || ''), 'zh-CN') * dir;
        });
    }, [filtered, sortKey, sortDir]);

    const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
    const curPage = Math.min(page, totalPages);
    const pageRows = sorted.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE);

    const handleSort = (key) => {
        if (sortKey === key) {
            setSortDir(prev => (prev === 'desc' ? 'asc' : 'desc'));
        } else {
            setSortKey(key);
            setSortDir('desc');
        }
        setPage(1);
    };

    const handleSearch = (e) => {
        setKeyword(e.target.value);
        setPage(1);
    };

    const handleBoard = (b) => {
        setBoard(b);
        setPage(1);
    };

    // 行情接口不可用时后端降级返回落库快照，标出快照日期避免误读成实时数据
    const updatedStr = data?.from_cache
        ? `${data.trade_date} 收盘（缓存）`
        : data?.updated_at
            ? new Date(data.updated_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
            : '';

    return (
        <section className="stock-screener">
            <div className="stock-toolbar">
                <div className="stock-toolbar-left">
                    <h3>A 股基本面筛选</h3>
                    {data && (
                        <span className="stock-meta">
                            共 {data.total} 只 · 筛出 {sorted.length} 只{updatedStr ? ` · 数据 ${updatedStr}` : ''}
                        </span>
                    )}
                </div>
                <div className="stock-toolbar-right">
                    <input
                        className="stock-search"
                        type="text"
                        placeholder="搜索代码 / 名称 / 行业"
                        value={keyword}
                        onChange={handleSearch}
                    />
                    <button
                        className={`stock-watch-toggle${onlyWatchlist ? ' active' : ''}`}
                        onClick={() => { setOnlyWatchlist(v => !v); setPage(1); }}
                        title="只看自选"
                    >
                        ★ 自选 ({watchlist.size})
                    </button>
                    <button className="stock-refresh" onClick={() => load(true)} disabled={loading} title="强制刷新数据">
                        刷新
                    </button>
                </div>
            </div>

            <div className="stock-board-tabs">
                {BOARDS.map(b => (
                    <button
                        key={b}
                        className={`stock-board-tab${board === b ? ' active' : ''}`}
                        onClick={() => handleBoard(b)}
                    >
                        {b}
                    </button>
                ))}
            </div>

            {loading && (
                <div className="stock-status">首次加载全市场数据约需几秒，请稍候…</div>
            )}
            {!loading && error && (
                <div className="stock-status stock-error">加载失败：{error}</div>
            )}

            {!loading && !error && data && (
                <>
                    <div className="stock-table-wrap">
                        <table className="stock-table">
                            <thead>
                                <tr>
                                    <th className="stock-star-col" title="自选">★</th>
                                    {COLUMNS.map(col => (
                                        <th key={col.key} onClick={() => handleSort(col.key)}>
                                            {col.label}
                                            <span className={`sort-ind ${sortKey === col.key ? 'active' : ''}`}>
                                                {sortKey === col.key ? (sortDir === 'desc' ? '▼' : '▲') : '⇅'}
                                            </span>
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {pageRows.map(row => (
                                    <tr key={row.code}>
                                        <td className="stock-star-col">
                                            <button
                                                className={`stock-star${watchlist.has(row.code) ? ' active' : ''}`}
                                                onClick={() => toggleStar(row.code)}
                                                title={watchlist.has(row.code) ? '取消自选' : '加入自选'}
                                            >
                                                {watchlist.has(row.code) ? '★' : '☆'}
                                            </button>
                                        </td>
                                        {COLUMNS.map(col => {
                                            const v = row[col.key];
                                            let cls = '';
                                            if (col.color && v != null) {
                                                cls = v > 0 ? 'val-gain' : v < 0 ? 'val-loss' : '';
                                            }
                                            return (
                                                <td key={col.key} className={cls}>
                                                    {fmtCell(col, v)}
                                                </td>
                                            );
                                        })}
                                    </tr>
                                ))}
                                {pageRows.length === 0 && (
                                    <tr>
                                        <td colSpan={COLUMNS.length + 1} className="stock-empty">没有匹配的股票</td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>

                    <div className="stock-pager">
                        <button disabled={curPage <= 1} onClick={() => setPage(1)}>«</button>
                        <button disabled={curPage <= 1} onClick={() => setPage(curPage - 1)}>上一页</button>
                        <span className="stock-pager-info">{curPage} / {totalPages} 页</span>
                        <button disabled={curPage >= totalPages} onClick={() => setPage(curPage + 1)}>下一页</button>
                        <button disabled={curPage >= totalPages} onClick={() => setPage(totalPages)}>»</button>
                    </div>
                </>
            )}
        </section>
    );
}
