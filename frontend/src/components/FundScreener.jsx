import React, { useState, useEffect, useMemo } from 'react';
import { fetchFundList } from '../hooks/useFundApi';

const PAGE_SIZE = 50;

const TYPES = ['全部', '股票型', '混合型', '债券型', '指数型', 'QDII', 'FOF'];

// 表头列配置：key 对应后端字段，type 决定排序方式，color 表示涨红跌绿着色
const COLUMNS = [
    { key: 'code', label: '代码', type: 'str' },
    { key: 'name', label: '名称', type: 'str' },
    { key: 'fund_type', label: '类型', type: 'str' },
    { key: 'date', label: '净值日期', type: 'str' },
    { key: 'nav', label: '单位净值', type: 'num', fmt: v => Number(v).toFixed(4) },
    { key: 'daily_pct', label: '日涨幅%', type: 'num', color: true },
    { key: 'week1', label: '近1周%', type: 'num', color: true },
    { key: 'month1', label: '近1月%', type: 'num', color: true },
    { key: 'month3', label: '近3月%', type: 'num', color: true },
    { key: 'month6', label: '近6月%', type: 'num', color: true },
    { key: 'year1', label: '近1年%', type: 'num', color: true },
    { key: 'year2', label: '近2年%', type: 'num', color: true },
    { key: 'year3', label: '近3年%', type: 'num', color: true },
    { key: 'ytd', label: '今年来%', type: 'num', color: true },
    { key: 'since', label: '成立来%', type: 'num', color: true },
];

function fmtCell(col, v) {
    if (v == null || v === '') return '--';
    if (col.fmt) return col.fmt(v);
    if (col.type === 'num') return Number(v).toFixed(2);
    return v;
}

export default function FundScreener({ watchlist = [], onAdd, onRemove, onSelect }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [keyword, setKeyword] = useState('');
    const [fundType, setFundType] = useState('全部');
    const [sortKey, setSortKey] = useState('ytd');
    const [sortDir, setSortDir] = useState('desc');
    const [page, setPage] = useState(1);
    const [onlyWatchlist, setOnlyWatchlist] = useState(false);

    const watchSet = useMemo(() => new Set(watchlist), [watchlist]);

    useEffect(() => {
        load();
    }, []); // eslint-disable-line

    const load = async (force = false) => {
        setLoading(true);
        setError('');
        try {
            const res = await fetchFundList(force);
            setData(res);
        } catch (e) {
            setError(e.message || '加载失败');
        } finally {
            setLoading(false);
        }
    };

    const toggleStar = (code) => {
        if (watchSet.has(code)) {
            onRemove && onRemove(code);
        } else {
            onAdd && onAdd(code);
        }
    };

    // 只看自选 + 类型 + 搜索过滤（代码 / 名称）
    const filtered = useMemo(() => {
        let items = data?.items || [];
        if (onlyWatchlist) {
            items = items.filter(r => watchSet.has(r.code));
        }
        if (fundType !== '全部') {
            items = items.filter(r => r.fund_type === fundType);
        }
        const kw = keyword.trim().toLowerCase();
        if (!kw) return items;
        return items.filter(r =>
            r.code.includes(kw) ||
            (r.name && r.name.toLowerCase().includes(kw))
        );
    }, [data, keyword, fundType, onlyWatchlist, watchSet]);

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

    const handleType = (t) => {
        setFundType(t);
        setPage(1);
    };

    // 排行接口不可用时后端降级返回落库快照，标出快照日期避免误读成实时数据
    const updatedStr = data?.from_cache
        ? `${data.trade_date} 净值（缓存）`
        : data?.updated_at
            ? new Date(data.updated_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
            : '';

    return (
        <section className="stock-screener">
            <div className="stock-toolbar">
                <div className="stock-toolbar-left">
                    <h3>全市场基金筛选</h3>
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
                        placeholder="搜索代码 / 名称"
                        value={keyword}
                        onChange={handleSearch}
                    />
                    <button
                        className={`stock-watch-toggle${onlyWatchlist ? ' active' : ''}`}
                        onClick={() => { setOnlyWatchlist(v => !v); setPage(1); }}
                        title="只看自选"
                    >
                        ★ 自选 ({watchSet.size})
                    </button>
                    <button className="stock-refresh" onClick={() => load(true)} disabled={loading} title="强制刷新数据">
                        刷新
                    </button>
                </div>
            </div>

            <div className="stock-board-tabs">
                {TYPES.map(t => (
                    <button
                        key={t}
                        className={`stock-board-tab${fundType === t ? ' active' : ''}`}
                        onClick={() => handleType(t)}
                    >
                        {t}
                    </button>
                ))}
            </div>

            {loading && (
                <div className="stock-status">首次加载全市场基金数据约需几秒，请稍候…</div>
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
                                                className={`stock-star${watchSet.has(row.code) ? ' active' : ''}`}
                                                onClick={() => toggleStar(row.code)}
                                                title={watchSet.has(row.code) ? '取消自选' : '加入自选'}
                                            >
                                                {watchSet.has(row.code) ? '★' : '☆'}
                                            </button>
                                        </td>
                                        {COLUMNS.map(col => {
                                            const v = row[col.key];
                                            let cls = '';
                                            if (col.color && v != null) {
                                                cls = v > 0 ? 'val-gain' : v < 0 ? 'val-loss' : '';
                                            }
                                            if (col.key === 'name') {
                                                return (
                                                    <td key={col.key}>
                                                        <span
                                                            className="fund-list-name"
                                                            onClick={() => onSelect && onSelect(row.code)}
                                                            title="查看详情"
                                                        >
                                                            {row.name}
                                                        </span>
                                                    </td>
                                                );
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
                                        <td colSpan={COLUMNS.length + 1} className="stock-empty">没有匹配的基金</td>
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
