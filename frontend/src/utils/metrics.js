/**
 * 基于完整净值序列本地计算各项指标
 * 移植自原始 fund-scope.html calcCompareMetrics
 */
export function calcCompareMetrics(list) {
    const rows = (list || []).filter(r => r && isFinite(Number(r.DWJZ)));
    const n = rows.length;
    if (n < 2) return null;
    const values = rows.map(r => Number(r.DWJZ));
    const dates = rows.map(r => r.FSRQ);
    const lastV = values[n - 1];
    const firstV = values[0];
    const lastTs = new Date(dates[n - 1]).getTime();
    const firstTs = new Date(dates[0]).getTime();

    function periodReturn(days) {
        const targetTs = lastTs - days * 86400000;
        if (firstTs > targetTs) return null;
        let idx = -1;
        for (let i = n - 1; i >= 0; i--) {
            if (new Date(dates[i]).getTime() <= targetTs) { idx = i; break; }
        }
        if (idx < 0 || idx === n - 1) return null;
        const startV = values[idx];
        if (!(startV > 0)) return null;
        return (lastV - startV) / startV;
    }

    const r1m = periodReturn(30);
    const r3m = periodReturn(90);
    const r6m = periodReturn(180);
    const r1y = periodReturn(365);

    const yearsSpan = (lastTs - firstTs) / (365.25 * 86400000);
    const annReturn = yearsSpan > 0 && firstV > 0
        ? Math.pow(lastV / firstV, 1 / yearsSpan) - 1
        : null;

    let peak = values[0];
    let maxDD = 0;
    for (const v of values) {
        if (v > peak) peak = v;
        if (peak > 0) {
            const dd = (peak - v) / peak;
            if (dd > maxDD) maxDD = dd;
        }
    }

    // 夏普对齐天天基金口径
    const oneYearAgo = lastTs - 365 * 86400000;
    let retsSource = values;
    let sharpeAnnReturn = annReturn;
    if (firstTs <= oneYearAgo) {
        let startIdx = 0;
        for (let i = 0; i < n; i++) {
            if (new Date(dates[i]).getTime() >= oneYearAgo) { startIdx = Math.max(0, i - 1); break; }
        }
        retsSource = values.slice(startIdx);
        sharpeAnnReturn = r1y;
    }
    const rets = [];
    for (let i = 1; i < retsSource.length; i++) {
        const prev = retsSource[i - 1];
        if (prev > 0) {
            const r = (retsSource[i] - prev) / prev;
            if (isFinite(r)) rets.push(r);
        }
    }
    let annVol = null, sharpe = null;
    if (rets.length > 1) {
        const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
        const variance = rets.reduce((s, r) => s + (r - mean) ** 2, 0) / (rets.length - 1);
        const dailyStd = Math.sqrt(variance);
        annVol = dailyStd * Math.sqrt(252);
        const rf = 0.021;
        if (sharpeAnnReturn != null && annVol > 0) sharpe = (sharpeAnnReturn - rf) / annVol;
    }

    const calmar = (annReturn != null && maxDD > 0) ? (annReturn / maxDD) : null;

    function calcSharpeWin(years) {
        const winStart = lastTs - years * 365 * 86400000;
        if (firstTs > winStart) return null;
        let sIdx = 0;
        for (let i = 0; i < n; i++) {
            if (new Date(dates[i]).getTime() >= winStart) { sIdx = Math.max(0, i - 1); break; }
        }
        const slice = values.slice(sIdx);
        if (slice.length < 2) return null;
        const sv = slice[0], ev = slice[slice.length - 1];
        if (!(sv > 0)) return null;
        const ar = Math.pow(ev / sv, 1 / years) - 1;
        const rs = [];
        for (let i = 1; i < slice.length; i++) {
            const p = slice[i - 1];
            if (p > 0) {
                const r = (slice[i] - p) / p;
                if (isFinite(r)) rs.push(r);
            }
        }
        if (rs.length < 2) return null;
        const m = rs.reduce((a, b) => a + b, 0) / rs.length;
        const va = rs.reduce((s, r) => s + (r - m) ** 2, 0) / (rs.length - 1);
        const av = Math.sqrt(va) * Math.sqrt(252);
        if (!(av > 0)) return null;
        return (ar - 0.021) / av;
    }
    const sharpe2y = calcSharpeWin(2);
    const sharpe3y = calcSharpeWin(3);

    return {
        lastV, yearsSpan, r1m, r3m, r6m, r1y,
        annReturn, maxDD, annVol, sharpe, sharpe1y: sharpe, sharpe2y, sharpe3y, calmar,
        dateRange: `${dates[0]} ~ ${dates[n - 1]}`,
    };
}
