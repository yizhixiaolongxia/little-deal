export function fmtPct(v, digits = 2) {
    if (v == null || !isFinite(v)) return '--';
    return (v * 100).toFixed(digits) + '%';
}

export function fmtNum(v, digits = 2) {
    if (v == null || !isFinite(v)) return '--';
    return Number(v).toFixed(digits);
}

export function fmtYears(v) {
    if (v == null || !isFinite(v)) return '--';
    return v.toFixed(1) + ' 年';
}

// 金额千分位，入参已是元（不再乘 100）
export function fmtMoney(v, digits = 2) {
    if (v == null || !isFinite(v)) return '--';
    return Number(v).toLocaleString('zh-CN', {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
    });
}

// 已是百分数值的涨跌幅（如 1.23 表示 1.23%），带正号
export function fmtPctVal(v, digits = 2) {
    if (v == null || !isFinite(v)) return '--';
    return (v > 0 ? '+' : '') + Number(v).toFixed(digits) + '%';
}
