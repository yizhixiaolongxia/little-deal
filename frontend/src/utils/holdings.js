// 天天基金持仓页 HTML 解析（多个报告期共存，需按期拆分）

// 解析持仓 HTML 内容 → [{ period, asof, stocks: [...] }]
export function parseHoldings(html) {
    if (!html) return [];
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    const items = doc.querySelectorAll('.box');
    const result = [];

    items.forEach(item => {
        const titleEl = item.querySelector('h4.t .left');
        const titleText = titleEl ? titleEl.textContent.trim() : '';
        const periodMatch = titleText.match(/(\d{4})年([1-4一-四])季度/);
        const period = periodMatch ? `${periodMatch[1]}年${periodMatch[2]}季` : (titleText || '未知期');
        const asofEl = item.querySelector('h4.t .right .px12, h4.t .right font');
        const asof = asofEl ? asofEl.textContent.trim() : '';
        const rows = item.querySelectorAll('table.tzxq tbody tr');
        const stocks = [];
        rows.forEach(tr => {
            const tds = tr.querySelectorAll('td');
            if (tds.length < 4) return;
            const ratio = tds[tds.length - 3]?.textContent.trim() || '--';
            const shares = tds[tds.length - 2]?.textContent.trim() || '--';
            const value = tds[tds.length - 1]?.textContent.trim() || '--';
            const idx = tds[0]?.textContent.trim() || '';
            const stockCode = tds[1]?.textContent.trim() || '';
            const stockName = tds[2]?.textContent.trim() || '';
            let secid = '';
            const link = tds[1]?.querySelector('a[href*="/unify/r/"]') || tds[2]?.querySelector('a[href*="/unify/r/"]');
            if (link) {
                const m = link.getAttribute('href').match(/\/unify\/r\/([0-9]+\.[0-9A-Za-z]+)/);
                if (m) secid = m[1];
            }
            stocks.push({ idx, code: stockCode, name: stockName, ratio, shares, value, secid });
        });
        if (stocks.length > 0) result.push({ period, asof, stocks });
    });
    return result;
}

// 报告期倒序（最新在前）
export function sortPeriodsDesc(periods) {
    return [...periods].sort((a, b) => {
        const ka = a.period.replace(/[^0-9]/g, '');
        const kb = b.period.replace(/[^0-9]/g, '');
        return kb.localeCompare(ka);
    });
}

// 只取最新一期，无数据返回 null
export function latestPeriod(html) {
    const parsed = sortPeriodsDesc(parseHoldings(html));
    return parsed.length > 0 ? parsed[0] : null;
}

// 已披露持仓占净值合计（前十大之和），返回 number
export function sumRatio(stocks) {
    return stocks.reduce((acc, s) => acc + (parseFloat(s.ratio) || 0), 0);
}
