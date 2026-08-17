import React, { useState, useRef, useEffect, useCallback } from 'react';
import { fetchSearch } from '../hooks/useFundApi';

const HISTORY_KEY = 'fund_search_history';
const MAX_HISTORY = 10;

function getHistory() {
    try {
        return JSON.parse(localStorage.getItem(HISTORY_KEY)) || [];
    } catch { return []; }
}

function saveHistory(list) {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, MAX_HISTORY)));
}

export default function FundInput({ onAdd }) {
    const [keyword, setKeyword] = useState('');
    const [suggestions, setSuggestions] = useState([]);
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [activeIndex, setActiveIndex] = useState(-1);
    const [history, setHistory] = useState(getHistory);
    const [showHistory, setShowHistory] = useState(false);
    const wrapRef = useRef(null);
    const debounceRef = useRef(null);
    const reqIdRef = useRef(0);

    // 添加搜索记录
    const addToHistory = (code, name) => {
        const newHistory = [{ code, name, time: Date.now() }, ...history.filter(h => h.code !== code)].slice(0, MAX_HISTORY);
        setHistory(newHistory);
        saveHistory(newHistory);
    };

    const clearHistory = (e) => {
        e.preventDefault();
        e.stopPropagation();
        setHistory([]);
        localStorage.removeItem(HISTORY_KEY);
        setShowHistory(false);
    };

    const runSearch = useCallback(async (kw) => {
        const trimmed = kw.trim();
        if (!trimmed) {
            setSuggestions([]);
            setOpen(false);
            return;
        }
        setShowHistory(false);
        const myReq = ++reqIdRef.current;
        setLoading(true);
        try {
            const data = await fetchSearch(trimmed, 10);
            if (myReq !== reqIdRef.current) return;
            setSuggestions(data.list || []);
            setOpen(true);
            setActiveIndex(-1);
        } catch (e) {
            if (myReq !== reqIdRef.current) return;
            setSuggestions([]);
            setOpen(true);
        } finally {
            if (myReq === reqIdRef.current) setLoading(false);
        }
    }, []);

    const handleChange = (e) => {
        const val = e.target.value;
        setKeyword(val);
        if (!val.trim()) {
            setSuggestions([]);
            setOpen(false);
            if (history.length > 0) setShowHistory(true);
        } else {
            setShowHistory(false);
            if (debounceRef.current) clearTimeout(debounceRef.current);
            debounceRef.current = setTimeout(() => runSearch(val), 250);
        }
    };

    const pick = (code, name) => {
        addToHistory(code, name || code);
        onAdd(code);
        setKeyword('');
        setSuggestions([]);
        setOpen(false);
        setShowHistory(false);
        setActiveIndex(-1);
    };

    const handleSubmit = () => {
        const trimmed = keyword.trim();
        if (activeIndex >= 0 && suggestions[activeIndex]) {
            const s = suggestions[activeIndex];
            pick(s.code, s.name);
            return;
        }
        if (/^\d{6}$/.test(trimmed)) {
            pick(trimmed, '');
            return;
        }
        if (suggestions.length > 0) {
            const s = suggestions[0];
            pick(s.code, s.name);
        }
    };

    const handleKeyDown = (e) => {
        if (!open || suggestions.length === 0) {
            if (e.key === 'Enter') handleSubmit();
            return;
        }
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActiveIndex(i => (i + 1) % suggestions.length);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActiveIndex(i => (i <= 0 ? suggestions.length - 1 : i - 1));
        } else if (e.key === 'Enter') {
            e.preventDefault();
            handleSubmit();
        } else if (e.key === 'Escape') {
            setOpen(false);
            setShowHistory(false);
        }
    };

    const handleFocus = () => {
        if (suggestions.length > 0) {
            setOpen(true);
        } else if (!keyword.trim() && history.length > 0) {
            setShowHistory(true);
        }
    };

    // 点击外部关闭下拉
    useEffect(() => {
        const handler = (e) => {
            if (wrapRef.current && !wrapRef.current.contains(e.target)) {
                setOpen(false);
                setShowHistory(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    return (
        <div className="add-form" ref={wrapRef}>
            <div className="search-box">
                <input
                    type="text"
                    placeholder="输入基金代码或名称，如 001060 / 白酒"
                    value={keyword}
                    onChange={handleChange}
                    onFocus={handleFocus}
                    onKeyDown={handleKeyDown}
                    autoComplete="off"
                />
                {/* 搜索建议 */}
                {open && (
                    <div className="search-suggest">
                        {loading && suggestions.length === 0 && (
                            <div className="search-suggest-empty">搜索中…</div>
                        )}
                        {!loading && suggestions.length === 0 && (
                            <div className="search-suggest-empty">未找到匹配的基金</div>
                        )}
                        {suggestions.map((f, i) => (
                            <div
                                key={f.code}
                                className={`search-suggest-item ${i === activeIndex ? 'active' : ''}`}
                                onMouseEnter={() => setActiveIndex(i)}
                                onMouseDown={(e) => { e.preventDefault(); pick(f.code, f.name); }}
                            >
                                <span className="ss-code">{f.code}</span>
                                <span className="ss-name" title={f.name}>{f.name}</span>
                                {f.type && <span className="ss-type">{f.type}</span>}
                            </div>
                        ))}
                    </div>
                )}
                {/* 历史搜索记录 */}
                {showHistory && !open && (
                    <div className="search-suggest search-history">
                        <div className="search-history-header">
                            <span>最近搜索</span>
                            <button className="search-history-clear" onMouseDown={clearHistory}>清除</button>
                        </div>
                        {history.map((h) => (
                            <div
                                key={h.code}
                                className="search-suggest-item"
                                onMouseDown={(e) => { e.preventDefault(); pick(h.code, h.name); }}
                            >
                                <span className="ss-code">{h.code}</span>
                                <span className="ss-name" title={h.name}>{h.name || h.code}</span>
                                <span className="ss-type">历史</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
            <button onClick={handleSubmit}>添加</button>
        </div>
    );
}

