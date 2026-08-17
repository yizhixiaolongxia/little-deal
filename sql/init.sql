CREATE DATABASE IF NOT EXISTS fundscope DEFAULT CHARACTER SET utf8mb4;
USE fundscope;

CREATE TABLE IF NOT EXISTS watchlist (
    id INT AUTO_INCREMENT PRIMARY KEY,
    fund_code VARCHAR(6) NOT NULL UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_watchlist (
    id INT AUTO_INCREMENT PRIMARY KEY,
    stock_code VARCHAR(6) NOT NULL UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_risk_daily (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trade_date DATE NOT NULL UNIQUE,
    score INT NOT NULL,
    level VARCHAR(10) NOT NULL,
    label VARCHAR(20) NOT NULL,
    indices_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 全市场行情每日快照：个股 / 基金
CREATE TABLE IF NOT EXISTS stock_daily (
    id INT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(6) NOT NULL,
    trade_date DATE NOT NULL,
    name VARCHAR(32) NOT NULL DEFAULT '',
    board VARCHAR(12) NOT NULL DEFAULT '',
    industry VARCHAR(32) NOT NULL DEFAULT '',
    price DOUBLE,
    pct DOUBLE,
    total_mv DOUBLE,
    pe DOUBLE,
    pb DOUBLE,
    roe DOUBLE,
    gross_margin DOUBLE,
    net_margin DOUBLE,
    revenue_yoy DOUBLE,
    profit_yoy DOUBLE,
    debt_ratio DOUBLE,
    ocf_ps DOUBLE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_stock_daily (code, trade_date),
    KEY idx_stock_daily_date (trade_date)
);

CREATE TABLE IF NOT EXISTS fund_daily (
    id INT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(6) NOT NULL,
    trade_date DATE NOT NULL,
    name VARCHAR(64) NOT NULL DEFAULT '',
    fund_type VARCHAR(12) NOT NULL DEFAULT '',
    nav DOUBLE,
    acc_nav DOUBLE,
    daily_pct DOUBLE,
    week1 DOUBLE,
    month1 DOUBLE,
    month3 DOUBLE,
    month6 DOUBLE,
    year1 DOUBLE,
    year2 DOUBLE,
    year3 DOUBLE,
    ytd DOUBLE,
    since DOUBLE,
    inception VARCHAR(12) NOT NULL DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_fund_daily (code, trade_date),
    KEY idx_fund_daily_date (trade_date)
);

-- 基金净值长历史：fund_daily 是横截面快照，这张是单只基金的纵向序列，
-- 年化/回撤/波动/夏普这些指标要完整序列才能算
CREATE TABLE IF NOT EXISTS fund_nav (
    id INT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(6) NOT NULL,
    nav_date DATE NOT NULL,
    nav DOUBLE NOT NULL,
    acc_nav DOUBLE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_fund_nav (code, nav_date),
    KEY idx_fund_nav_date (nav_date)
);

-- 同步水位：只看 max(nav_date) 分不清「上游没公布」和「我们没同步」，
-- 靠 synced_at 判定库里是否已是上游能给的最新
CREATE TABLE IF NOT EXISTS fund_nav_sync (
    code VARCHAR(6) PRIMARY KEY,
    first_date DATE,
    last_date DATE,
    rows_count INT NOT NULL DEFAULT 0,
    synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 模拟投资：账户 / 持仓 / 交易流水
CREATE TABLE IF NOT EXISTS sim_account (
    id INT AUTO_INCREMENT PRIMARY KEY,
    initial_cash DECIMAL(18,2) NOT NULL,
    cash DECIMAL(18,2) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sim_position (
    id INT AUTO_INCREMENT PRIMARY KEY,
    asset_type VARCHAR(8) NOT NULL,
    code VARCHAR(6) NOT NULL,
    name VARCHAR(64) NOT NULL DEFAULT '',
    shares DECIMAL(18,4) NOT NULL,
    avg_cost DECIMAL(18,4) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_sim_position (asset_type, code)
);

-- sim_position 已废弃，持仓改由 sim_lot 按批次记录（赎回费需按各批次持有天数分档）
CREATE TABLE IF NOT EXISTS sim_trade (
    id INT AUTO_INCREMENT PRIMARY KEY,
    asset_type VARCHAR(8) NOT NULL,
    code VARCHAR(6) NOT NULL,
    name VARCHAR(64) NOT NULL DEFAULT '',
    side VARCHAR(4) NOT NULL,
    shares DECIMAL(18,4) NOT NULL,
    price DECIMAL(18,4) NOT NULL,
    amount DECIMAL(18,2) NOT NULL,
    fee DECIMAL(18,2) NOT NULL DEFAULT 0,
    fee_detail VARCHAR(128) NOT NULL DEFAULT '',
    nav_date DATE NULL,
    order_id INT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_sim_trade_created (created_at)
);

-- 持仓批次：卖出先进先出，按各批次实际持有天数套用赎回费率
CREATE TABLE IF NOT EXISTS sim_lot (
    id INT AUTO_INCREMENT PRIMARY KEY,
    asset_type VARCHAR(8) NOT NULL,
    code VARCHAR(6) NOT NULL,
    name VARCHAR(64) NOT NULL DEFAULT '',
    shares DECIMAL(18,4) NOT NULL,
    cost_price DECIMAL(18,4) NOT NULL,
    buy_fee DECIMAL(18,2) NOT NULL DEFAULT 0,
    acquire_date DATE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_sim_lot_pos (asset_type, code, acquire_date)
);

-- 委托单：场外基金按盘后公布的当日净值成交，下单时价未知，先 pending 后清算
CREATE TABLE IF NOT EXISTS sim_order (
    id INT AUTO_INCREMENT PRIMARY KEY,
    asset_type VARCHAR(8) NOT NULL,
    code VARCHAR(6) NOT NULL,
    name VARCHAR(64) NOT NULL DEFAULT '',
    side VARCHAR(4) NOT NULL,
    order_amount DECIMAL(18,2) NULL,
    order_shares DECIMAL(18,4) NULL,
    nav_date DATE NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'pending',
    price DECIMAL(18,4) NULL,
    shares DECIMAL(18,4) NULL,
    fee DECIMAL(18,2) NULL,
    amount DECIMAL(18,2) NULL,
    note VARCHAR(255) NOT NULL DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    settled_at DATETIME NULL,
    KEY idx_sim_order_status (status, nav_date)
);
