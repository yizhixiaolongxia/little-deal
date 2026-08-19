const API_BASE = '/api';

async function request(url, options = {}) {
    const resp = await fetch(`${API_BASE}${url}`, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
}

// ===== Watchlist =====
export async function getWatchlist() {
    return request('/watchlist');
}

export async function addToWatchlist(fundCode) {
    return request('/watchlist', {
        method: 'POST',
        body: JSON.stringify({ fund_code: fundCode }),
    });
}

export async function removeFromWatchlist(code) {
    return request(`/watchlist/${code}`, { method: 'DELETE' });
}

export async function clearWatchlist() {
    return request('/watchlist', { method: 'DELETE' });
}

// ===== Fund Data =====
export async function fetchRealtime(code) {
    return request(`/fund/realtime/${code}`);
}

export async function fetchLatestNav(code) {
    return request(`/fund/nav/${code}`);
}

export async function fetchHistory(code, pageSize = 90) {
    return request(`/fund/history/${code}?pageSize=${pageSize}`);
}

export async function fetchHistoryFull(code) {
    return request(`/fund/history-full/${code}`);
}

export async function fetchHoldings(code, year = '') {
    return request(`/fund/holdings/${code}?year=${year}`);
}

export async function fetchFundList(force = false) {
    return request(`/fund/list${force ? '?force=true' : ''}`);
}

// ===== Stock =====
export async function fetchStockQuotes(secids) {
    return request('/stock/quotes', {
        method: 'POST',
        body: JSON.stringify({ secids }),
    });
}

export async function fetchStockList(force = false) {
    return request(`/stock/list${force ? '?force=true' : ''}`);
}

export async function getStockWatchlist() {
    return request('/stock/watchlist');
}

export async function addStockToWatchlist(stockCode) {
    return request('/stock/watchlist', {
        method: 'POST',
        body: JSON.stringify({ stock_code: stockCode }),
    });
}

export async function removeStockFromWatchlist(code) {
    return request(`/stock/watchlist/${code}`, { method: 'DELETE' });
}

// ===== Ranking =====
export async function fetchRanking(top = 5, sort = '1yzf') {
    return request(`/fund/ranking?top=${top}&sort=${sort}`);
}

// ===== Search =====
export async function fetchSearch(keyword, limit = 10) {
    return request(`/fund/search?key=${encodeURIComponent(keyword)}&limit=${limit}`);
}

// ===== Manager =====
export async function fetchManager(code) {
    return request(`/fund/manager/${code}`);
}

// ===== Market Risk =====
export async function fetchMarketRisk() {
    return request('/market/risk');
}

export async function fetchMarketRiskHistory(days = 30) {
    return request(`/market/risk/history?days=${days}`);
}

export async function fetchMarketPosition() {
    return request('/market/position');
}

// ===== 模拟投资 =====
export async function fetchSimAccount() {
    return request('/sim/account');
}

export async function fetchSimQuote(assetType, code) {
    return request(`/sim/quote?asset_type=${assetType}&code=${code}`);
}

export async function submitSimTrade(payload) {
    return request('/sim/trade', {
        method: 'POST',
        body: JSON.stringify(payload),
    });
}

export async function fetchSimTrades(limit = 30) {
    return request(`/sim/trades?limit=${limit}`);
}

export async function fetchSimFees() {
    return request('/sim/fees');
}

// 场外基金委托要等盘后净值才成交，/sim/account 已会自动清算一次，
// 这个口子留给用户手动催一下
export async function settleSimOrders() {
    return request('/sim/settle', { method: 'POST' });
}

export async function resetSimAccount() {
    return request('/sim/reset', { method: 'POST' });
}

// 组合总资产曲线与回撤，按成交流水 + 逐日收盘净值重算，顺带带回组合超额
export async function fetchSimCurve(withPoints = false) {
    return request(`/sim/curve?with_points=${withPoints}`);
}

// ===== 基准与超额 =====
// codes 为空时后端取自选 + 持仓，页面上一般显式传持仓代码，少算几只
export async function fetchFundExcess(codes = [], days = 365) {
    const q = codes.length ? `codes=${codes.join(',')}&` : '';
    return request(`/benchmark/excess?${q}days=${days}`);
}

// ===== 宏观看板 =====
// 一次把核心八项 + 五个分组 + 已知缺口都拉回来，页面不再逐个指标请求
export async function fetchMacroDashboard() {
    return request('/macro/dashboard');
}

// 单指标完整序列，点开卡片看大图时才拉
export async function fetchMacroHistory(code, limit = 120) {
    return request(`/macro/history?code=${code}&limit=${limit}`);
}
