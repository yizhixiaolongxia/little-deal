"""应用配置"""
import os
from dotenv import load_dotenv

load_dotenv()

# MySQL 配置
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "fundscope")

DATABASE_URL = (
    f"mysql+aiomysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    "?charset=utf8mb4"
)

# 外部 API 基地址
FUNDGZ_BASE = "https://fundgz.1234567.com.cn/js"
HISTORY_BASE = "https://api.fund.eastmoney.com/f10/lsjz"
PINGZHONG_BASE = "https://fund.eastmoney.com/pingzhongdata"
HOLDINGS_BASE = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
# A 股快照（沪深，节假日仍返回上一交易日 realtimequote）
STOCK_SNAPSHOT_BASE = "https://hsmarketwg.eastmoney.com/api/SHSZQuoteSnapshot"
# 港股/美股等 K 线（取最近一个交易日涨跌幅）
STOCK_KLINE_BASE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
# A 股全市场列表（含基本面指标，分页拉取）
STOCK_LIST_BASE = "https://push2.eastmoney.com/api/qt/clist/get"
# 主节点被限流时降级到延迟行情节点（基本面筛选场景对实时性不敏感）
STOCK_LIST_FALLBACK_BASE = "https://push2delay.eastmoney.com/api/qt/clist/get"
# 按 secid 批量取最新价/涨跌幅/名称（模拟盘下单与持仓估值用）
STOCK_ULIST_BASE = "https://push2.eastmoney.com/api/qt/ulist.np/get"
STOCK_ULIST_FALLBACK_BASE = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
# 开放式基金排行榜（按各周期收益率排序）
RANK_BASE = "https://fund.eastmoney.com/data/rankhandler.aspx"
# 基金搜索建议（支持中文名/代码/拼音）
SEARCH_BASE = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"

# 宏观月度/季度指标（东财数据中心，reportName 登记在 macro_service.MONTHLY）
MACRO_REPORT_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
# 中美国债收益率日序列（走旧版 datacenter 接口，token 是页面里写死的公开常量，
# 不是凭证；它变了只会整体 401，不会静默返回错数）
TREASURY_YIELD_BASE = "https://datacenter.eastmoney.com/api/data/get"
TREASURY_YIELD_TOKEN = "894050c76af8597a853f5b408b759f5d"
# 汇率/美元指数/大宗实时快照（新浪，GBK 编码且必须带 Referer）
SINA_HQ_BASE = "https://hq.sinajs.cn/list="

# 请求超时（秒）
REQUEST_TIMEOUT = 10.0
