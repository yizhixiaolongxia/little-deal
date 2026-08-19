"""ORM 模型"""
from sqlalchemy import (
    Column, Integer, String, Date, DateTime, Text, Numeric, Float,
    Index, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Watchlist(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fund_code = Column(String(6), unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class StockWatchlist(Base):
    __tablename__ = "stock_watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(6), unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class MarketRiskDaily(Base):
    """每日市场风险监测快照（按交易日一条，盘中刷新覆盖当日数据）"""
    __tablename__ = "market_risk_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, unique=True, nullable=False)
    score = Column(Integer, nullable=False)
    level = Column(String(10), nullable=False)
    label = Column(String(20), nullable=False)
    indices_json = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class SimAccount(Base):
    """模拟投资账户（单账户，取 id 最小的一条）"""
    __tablename__ = "sim_account"

    id = Column(Integer, primary_key=True, autoincrement=True)
    initial_cash = Column(Numeric(18, 2), nullable=False)
    cash = Column(Numeric(18, 2), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class SimPosition(Base):
    """模拟持仓（已废弃）

    赎回费按持有天数分档、且真实规则是先进先出，只存加权平均成本算不出费率，
    因此持仓改由 SimLot 按批次记录。此表保留仅为可回滚，业务代码不再读写。
    """
    __tablename__ = "sim_position"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_type = Column(String(8), nullable=False)   # stock | fund
    code = Column(String(6), nullable=False)
    name = Column(String(64), nullable=False, default="")
    shares = Column(Numeric(18, 4), nullable=False)  # 股票为股数，基金为份额
    avg_cost = Column(Numeric(18, 4), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("asset_type", "code", name="uk_sim_position"),
    )


class SimLot(Base):
    """持仓批次

    同一标的可有多条，每条对应一次买入成交。卖出按 acquire_date 先进先出扣减，
    这样才能按各批次的实际持有天数套用赎回费率（真实基金就是这个规则）。
    shares 扣减到 0 时删除该批次。
    """
    __tablename__ = "sim_lot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_type = Column(String(8), nullable=False)    # stock | fund
    code = Column(String(6), nullable=False)
    name = Column(String(64), nullable=False, default="")
    shares = Column(Numeric(18, 4), nullable=False)   # 剩余份额/股数
    cost_price = Column(Numeric(18, 4), nullable=False)  # 成交单价，不含费用
    buy_fee = Column(Numeric(18, 2), nullable=False, default=0)  # 本批次买入手续费
    # 成交对应的净值日/交易日：持有天数与股票 T+1 都以此为起点，而非下单时间
    acquire_date = Column(Date, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_sim_lot_pos", "asset_type", "code", "acquire_date"),
    )


class SimOrder(Base):
    """委托单

    场外基金按盘后公布的当日净值成交，下单时成交价未知，故先落 pending，
    待 nav_date 当日净值公布后再清算回填成交明细。股票即时成交，直接落 settled。
    """
    __tablename__ = "sim_order"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_type = Column(String(8), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(64), nullable=False, default="")
    side = Column(String(4), nullable=False)           # buy | sell
    order_amount = Column(Numeric(18, 2))              # 买入委托金额（含手续费）
    order_shares = Column(Numeric(18, 4))              # 卖出委托份额
    nav_date = Column(Date, nullable=False)            # 目标净值日
    status = Column(String(10), nullable=False, default="pending")  # pending|settled|failed
    price = Column(Numeric(18, 4))                     # 清算回填：成交单价
    shares = Column(Numeric(18, 4))                    # 清算回填：成交份额/股数
    fee = Column(Numeric(18, 2))                       # 清算回填：手续费合计
    amount = Column(Numeric(18, 2))                    # 清算回填：实际扣款/到账
    note = Column(String(255), nullable=False, default="")  # 失败原因
    created_at = Column(DateTime, server_default=func.now())
    settled_at = Column(DateTime)

    __table_args__ = (
        Index("idx_sim_order_status", "status", "nav_date"),
    )


class StockDaily(Base):
    """个股每日快照（按 code + 交易日一条，盘中刷新覆盖当日数据）"""
    __tablename__ = "stock_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(6), nullable=False)
    trade_date = Column(Date, nullable=False)
    name = Column(String(32), nullable=False, default="")
    board = Column(String(12), nullable=False, default="")
    industry = Column(String(32), nullable=False, default="")
    price = Column(Float)
    pct = Column(Float)
    total_mv = Column(Float)
    pe = Column(Float)
    pb = Column(Float)
    roe = Column(Float)
    gross_margin = Column(Float)
    net_margin = Column(Float)
    revenue_yoy = Column(Float)
    profit_yoy = Column(Float)
    debt_ratio = Column(Float)
    ocf_ps = Column(Float)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uk_stock_daily"),
    )


class FundDaily(Base):
    """基金每日快照（按 code + 净值日期一条，同一净值日重复刷新覆盖）"""
    __tablename__ = "fund_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(6), nullable=False)
    trade_date = Column(Date, nullable=False)   # 净值日期
    name = Column(String(64), nullable=False, default="")
    fund_type = Column(String(12), nullable=False, default="")
    nav = Column(Float)
    acc_nav = Column(Float)
    daily_pct = Column(Float)
    week1 = Column(Float)
    month1 = Column(Float)
    month3 = Column(Float)
    month6 = Column(Float)
    year1 = Column(Float)
    year2 = Column(Float)
    year3 = Column(Float)
    ytd = Column(Float)
    since = Column(Float)
    inception = Column(String(12), nullable=False, default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uk_fund_daily"),
    )


class FundNav(Base):
    """基金净值时间序列（全历史，逐净值日一条）

    与 fund_daily 的区别：fund_daily 是全市场横截面快照（今天所有基金各周期收益率），
    这张是单只基金的纵向长历史，用来算年化/回撤/波动/夏普这些需要完整序列的指标。
    """
    __tablename__ = "fund_nav"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(6), nullable=False)
    nav_date = Column(Date, nullable=False)
    nav = Column(Float, nullable=False)
    acc_nav = Column(Float)   # pingzhongdata 只给单位净值，累计净值可能为空
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("code", "nav_date", name="uk_fund_nav"),
        Index("idx_fund_nav_date", "nav_date"),
    )


class FundNavSync(Base):
    """净值同步水位

    只看 fund_nav 里的 max(nav_date) 分不清「上游还没公布」和「我们没同步」——
    QDII 净值滞后几个交易日时这两者表现一样，会导致每次请求都白跑一趟上游。
    所以单独记 synced_at：只要在当日净值公布时点之后同步过，库里就是上游能给的最新。
    """
    __tablename__ = "fund_nav_sync"

    code = Column(String(6), primary_key=True)
    first_date = Column(Date)
    last_date = Column(Date)
    rows_count = Column(Integer, nullable=False, default=0)   # rows 是 MySQL 保留字
    synced_at = Column(DateTime, nullable=False, server_default=func.now())


class IndexQuote(Base):
    """基准指数日线收盘点位

    只存收盘价：超额收益只需要区间首末两个点，开高低量存了也用不上。

    code 存上游 symbol 原样（如 sh000300）而不是自己编的别名：出问题时
    能直接拿这个值去调上游接口复现，不用先反查一层映射。
    """
    __tablename__ = "index_quote"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), nullable=False)
    trade_date = Column(Date, nullable=False)
    close = Column(Float, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uk_index_quote"),
        Index("idx_index_quote_date", "trade_date"),
    )


class SimTrade(Base):
    """模拟交易流水（只追加，不修改）

    amount 为实际现金变动：买入是含费扣款，卖出是扣费后到账。
    fee 单列便于统计一年下来被手续费磨掉多少。
    """
    __tablename__ = "sim_trade"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_type = Column(String(8), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(64), nullable=False, default="")
    side = Column(String(4), nullable=False)          # buy | sell
    shares = Column(Numeric(18, 4), nullable=False)
    price = Column(Numeric(18, 4), nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    fee = Column(Numeric(18, 2), nullable=False, default=0)
    fee_detail = Column(String(128), nullable=False, default="")  # 如 申购费0.15%
    nav_date = Column(Date)                            # 成交对应的净值日/交易日
    order_id = Column(Integer)                         # 关联 sim_order
    created_at = Column(DateTime, server_default=func.now())


class MacroMonthly(Base):
    """宏观月度/季度指标（长表：一行一个「指标×期间」）

    为什么不用宽表（一列一个指标）：宏观指标是会持续增删的——今天加 PMI，
    明天加社融、失业率。宽表每加一个指标就要改 DDL + 写一次迁移，长表只需要在
    macro_service 的登记表里加一行。代价是查出来要自己 pivot，但指标就几十个、
    历史几百期，全表拉回内存也不到一万行，这个代价可以忽略。

    period 存期间首日而不是发布日：7 月的 CPI 在8 月中旬才公布，按发布日存会把
    「7 月的数」标成 8 月，跟同期其它指标对不上。季度指标（GDP）存季末月首日。

    单位不入表：单位是指标的固有属性，属于登记表而不是数据行。存到行里只会多
    出一种「同一指标两行单位不一致」的脏数据，而它无法自动修复。
    """
    __tablename__ = "macro_monthly"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(24), nullable=False)   # 指标代码，见 macro_service.MONTHLY
    period = Column(Date, nullable=False)       # 期间首日（2026-07-01 = 2026 年 7 月）
    value = Column(Float, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("code", "period", name="uk_macro_monthly"),
        Index("idx_macro_monthly_code", "code", "period"),
    )


class MacroDaily(Base):
    """宏观日度指标（国债收益率/汇率/股债性价比，结构同 MacroMonthly）

    和月度拆两张表而不是共用一张加 freq 列：两边量级差两个数量级（月度几百行/
    指标，日度上万行/指标），而且日度查询永远是「最近 N 个交易日」、月度永远是
    「最近 N 期」，没有任何一个查询需要把两边放一起扫。
    """
    __tablename__ = "macro_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(24), nullable=False)   # 指标代码，见 macro_service.DAILY
    trade_date = Column(Date, nullable=False)
    value = Column(Float, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uk_macro_daily"),
        Index("idx_macro_daily_code", "code", "trade_date"),
    )


class MarginDaily(Base):
    """两融余额日度（全市场合计，沪深两市）

    为什么不塞进 MacroDaily 的长表：那张表一行一个标量值，而两融这几个字段
    必须同期取用——只看融资余额涨到 2.67 万亿会得出「杠杆历史最高」，但同期
    流通市值从 2015 年的 45 万亿涨到了 102 万亿，占比其实还没到当年一半。
    拆成四行标量存，每次算占比都要自己 join 回来对齐日期，反而更容易错。

    rz_ye_pct 是上游直接给的占比（RZYEZB），不是我们用 rz_ye/ltsz 算的。
    两者实测吻合到小数点后三位，但上游口径里流通市值的取数时点我们并不掌握，
    自己算等于换了一把尺子。冗余存 ltsz 只为出问题时能对账。
    """
    __tablename__ = "margin_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, unique=True)
    rz_ye = Column(Float, nullable=False)        # 融资余额，元
    rz_ye_pct = Column(Float)                    # 融资余额占流通市值比，%（主指标）
    rq_ye = Column(Float)                        # 融券余额，元
    rzrq_ye = Column(Float)                      # 两融余额合计，元
    ltsz = Column(Float)                         # 两市流通市值，元（对账用）
    hs300_close = Column(Float)                  # 上游同期给的沪深300 收盘，双源对账用
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_margin_daily_date", "trade_date"),
    )

