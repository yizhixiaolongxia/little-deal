import React from 'react';

export default function FundCard({ code, data, selected, onSelect, onRemove }) {
    if (!data) return null;

    if (data.loading) {
        return (
            <div className="fund-card">
                <div className="card-loading">
                    <span className="dot-pulse"><span></span><span></span><span></span></span>
                </div>
            </div>
        );
    }

    if (data.error) {
        return (
            <div className="fund-card" onClick={() => onSelect(code)}>
                <div className="card-header">
                    <div>
                        <div className="card-name">{code}</div>
                        <div className="card-code">加载失败</div>
                    </div>
                </div>
                <div className="card-loading" style={{ color: 'var(--gain)' }}>{data.error}</div>
            </div>
        );
    }

    const rt = data.realtime;
    if (!rt) return null;

    const gszzl = parseFloat(rt.gszzl) || 0;
    const isGain = gszzl >= 0;
    const changeClass = isGain ? 'gain' : 'loss';

    return (
        <div className={`fund-card ${changeClass} ${selected ? 'selected' : ''}`} onClick={() => onSelect(code)}>
            <div className="card-header">
                <div>
                    <div className="card-name">{rt.name || code}</div>
                    <div className="card-code">{rt.fundcode || code}</div>
                </div>
                <button className="card-remove" onClick={e => { e.stopPropagation(); onRemove(code); }}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
                </button>
            </div>
            <div className="card-nav">{rt.dwjz || '--'}</div>
            {data.manager && <div className="card-manager">👤 {data.manager}</div>}
            <div className={`card-change ${isGain ? 'gain-text gain-bg' : 'loss-text loss-bg'}`}>
                {gszzl > 0 ? '+' : ''}{gszzl.toFixed(2)}%
            </div>
            <div className="card-estimate">
                <span className="estimate-label">{rt.settled ? `净值 ${rt.jzrq || ''}` : `估值 ${rt.gztime || ''}`}</span>
                <div className="estimate-row">
                    <span className="estimate-val">{rt.gsz || '--'}</span>
                    <span className={`estimate-change ${isGain ? 'gain-text gain-bg' : 'loss-text loss-bg'}`}>
                        {gszzl > 0 ? '+' : ''}{gszzl.toFixed(2)}%
                    </span>
                </div>
            </div>
        </div>
    );
}
