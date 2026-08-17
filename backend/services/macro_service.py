"""宏观指标看板

把「先看宏观、再看组合」这一步固化下来：增长、通胀、货币金融、外部、估值五层，
每层几个指标，看最新值 + 相对上期的边际变化 + 一段走势。宏观决策靠的是拐点和
边际变化，不是绝对水平，所以每个指标都必须带上一期的值，不然看板只是个数字墙。

三类数据源，各有各的脾气，分开处理：

1. 月度/季度（东财数据中心 reportName）：PMI、CPI、PPI、M1/M2、新增信贷、社零、
   固投、进出口、GDP。上游一次能给全历史，漏跑几天下次一起补上，不会留缺口。

2. 国债收益率日序列（东财旧版 datacenter）：中美 2/5/10/30 年。同样有全历史
   （九千多个交易日），首次同步一次性回填。

3. 汇率/美元指数/大宗（新浪快照）：**只有当前值，没有历史**。这点必须说在明处——
   这几个指标的历史是从第一次落库那天开始长出来的，早于那天的走势永远是空的。
   看板上不会给它们编一段假曲线。

估值层（全 A 整体法 PE、股债性价比 ERP）不来自外部接口，是拿本地 stock_daily 的
全市场横截面自己算的，口径见 _sync_valuation。

已知缺口（逐个试过东财 datacenter，没有对应 reportName，不硬塞不可靠的源）：
- 社会融资规模存量同比：中国最核心的领先指标，缺它是这个看板最大的短板。
  暂用「新增人民币贷款」当信用扩张的代理指标，但两者不等价——社融含表外和
  政府债，2018 年后两者的背离恰恰是最有信息量的时候。
- LPR、工业增加值、城镇调查失业率、存款准备金率。其中存准率东财有
  （RPT_ECONOMY_DEPOSIT_RESERVE），但它是事件型序列（一年动 0~2 次、按生效日
  而非期间），塞进按期间对齐的月度表里会破坏 period 语义，所以没收。
这些缺口会随接口一起返回给前端（dashboard 的 missing 字段），在页面上写明白，
而不是让人以为看板已经完整。
"""
import asyncio
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy import func as sa_func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from ..config import (
    MACRO_REPORT_BASE,
    REQUEST_TIMEOUT,
    SINA_HQ_BASE,
    TREASURY_YIELD_BASE,
    TREASURY_YIELD_TOKEN,
)
from ..database import async_session
from ..models import MacroDaily, MacroMonthly, StockDaily

_HEADERS = {
    "Referer": "https://data.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}
_SINA_HEADERS = {**_HEADERS, "Referer": "https://finance.sina.com.cn/"}

# 分组：看板按这个顺序排，key 也是前端的分区锚点
GROUPS = [
    ("growth", "增长"),
    ("inflation", "通胀"),
    ("money", "货币金融"),
    ("external", "外部与汇率"),
    ("valuation", "估值与股债性价比"),
]

# ── 指标登记表 ────────────────────────────────────────────────────────
# 加指标只改这里，不用动数据库：macro_monthly / macro_daily 是长表。
#
# better: up=越高越好 / down=越低越好 / None=中性（只看方向不做好坏判断）。
#   通胀和利率刻意留 None——CPI 涨到 5% 和跌到 -1% 都是坏事，标成任一方向都是误导。
# ref: 参考线（PMI 的荣枯线 50、各种利差的 0 轴）。没有就不画。

MONTHLY: Dict[str, dict] = {
    "pmi_make": {
        "name": "制造业PMI", "unit": "", "group": "growth", "better": "up", "ref": 50,
        "report": "RPT_ECONOMY_PMI", "field": "MAKE_INDEX", "freq": "month",
        "desc": "50 以上扩张、以下收缩；月末发布，是当下经济动能最快的读数",
    },
    "pmi_nmake": {
        "name": "非制造业PMI", "unit": "", "group": "growth", "better": "up", "ref": 50,
        "report": "RPT_ECONOMY_PMI", "field": "NMAKE_INDEX", "freq": "month",
        "desc": "含建筑业与服务业，基建开工强弱看这条",
    },
    "gdp_yoy": {
        "name": "GDP同比", "unit": "%", "group": "growth", "better": "up", "ref": None,
        "report": "RPT_ECONOMY_GDP", "field": "SUM_SAME", "freq": "quarter",
        "desc": "季度累计同比，滞后且平滑，只做校验不做决策",
    },
    "retail_yoy": {
        "name": "社零同比", "unit": "%", "group": "growth", "better": "up", "ref": 0,
        "report": "RPT_ECONOMY_TOTAL_RETAIL", "field": "RETAIL_TOTAL_SAME", "freq": "month",
        "desc": "消费需求；基数效应大的月份要结合两年复合看",
    },
    "invest_yoy": {
        "name": "固投当月同比", "unit": "%", "group": "growth", "better": "up", "ref": 0,
        "report": "RPT_ECONOMY_ASSET_INVEST", "field": "BASE_SAME", "freq": "month",
        "desc": "固定资产投资当月同比（非累计），波动远大于累计口径",
    },
    "cpi_yoy": {
        "name": "CPI同比", "unit": "%", "group": "inflation", "better": None, "ref": 0,
        "report": "RPT_ECONOMY_CPI", "field": "NATIONAL_SAME", "freq": "month",
        "desc": "全国居民消费价格同比。上游不提供核心 CPI，这条含猪价油价",
    },
    "cpi_mom": {
        "name": "CPI环比", "unit": "%", "group": "inflation", "better": None, "ref": 0,
        "report": "RPT_ECONOMY_CPI", "field": "NATIONAL_SEQUENTIAL", "freq": "month",
        "desc": "剔除基数效应后的当月价格动能",
    },
    "ppi_yoy": {
        "name": "PPI同比", "unit": "%", "group": "inflation", "better": None, "ref": 0,
        "report": "RPT_ECONOMY_PPI", "field": "BASE_SAME", "freq": "month",
        "desc": "工业品出厂价同比，转正/转负是周期股与债券的分水岭",
    },
    "m1_yoy": {
        "name": "M1同比", "unit": "%", "group": "money", "better": "up", "ref": 0,
        "report": "RPT_ECONOMY_CURRENCY_SUPPLY", "field": "CURRENCY_SAME", "freq": "month",
        "desc": "企业活钱。M1 回升通常领先 A 股盈利与风格切换",
    },
    "m2_yoy": {
        "name": "M2同比", "unit": "%", "group": "money", "better": None, "ref": 0,
        "report": "RPT_ECONOMY_CURRENCY_SUPPLY", "field": "BASIC_CURRENCY_SAME", "freq": "month",
        "desc": "广义货币同比，代表货币投放总量",
    },
    "loan_new": {
        "name": "新增人民币贷款", "unit": "亿元", "group": "money", "better": "up", "ref": 0,
        "report": "RPT_ECONOMY_RMB_LOAN", "field": "RMB_LOAN", "freq": "month",
        "desc": "社融拿不到时的信用扩张代理指标；单月波动极大，看趋势不看单点",
    },
    "export_yoy": {
        "name": "出口同比", "unit": "%", "group": "external", "better": "up", "ref": 0,
        "report": "RPT_ECONOMY_CUSTOMS", "field": "EXIT_BASE_SAME", "freq": "month",
        "desc": "外需强弱",
    },
    "import_yoy": {
        "name": "进口同比", "unit": "%", "group": "external", "better": "up", "ref": 0,
        "report": "RPT_ECONOMY_CUSTOMS", "field": "IMPORT_BASE_SAME", "freq": "month",
        "desc": "内需的另一面镜子",
    },
}

# 国债收益率：上游用内部指标 ID 做列名，映射在这里钉死。
# 校验过：中国 10Y−2Y 恰好等于上游给的 EMM01276014，美国同理，映射没串。
TREASURY_FIELDS = {
    "cn_2y": "EMM00588704",
    "cn_5y": "EMM00166462",
    "cn_10y": "EMM00166466",
    "cn_30y": "EMM00166469",
    "us_2y": "EMG00001306",
    "us_10y": "EMG00001310",
    "us_30y": "EMG00001312",
}

# 新浪快照：(symbol, 最新价在逗号分隔字段里的下标)。
# 下标各品种不一样，只能一个个钉；日期不钉下标，用正则在字段里找 YYYY-MM-DD，
# 上游哪天多插一列也不会把日期读串。
SINA_FIELDS = {
    "usdcny": ("fx_susdcny", 8),
    "usd_index": ("DINIW", 1),
    "wti": ("hf_CL", 0),
    "gold": ("hf_GC", 0),
}

DAILY: Dict[str, dict] = {
    "cn_2y": {
        "name": "中债2年", "unit": "%", "group": "money", "better": None, "ref": None,
        "source": "treasury", "desc": "短端利率，最贴近央行政策取向",
    },
    "cn_10y": {
        "name": "中债10年", "unit": "%", "group": "money", "better": None, "ref": None,
        "source": "treasury", "desc": "国内资产定价锚，也是股债性价比的无风险利率端",
    },
    "cn_30y": {
        "name": "中债30年", "unit": "%", "group": "money", "better": None, "ref": None,
        "source": "treasury", "desc": "超长端，反映长期增长与通胀预期",
    },
    "us_2y": {
        "name": "美债2年", "unit": "%", "group": "external", "better": None, "ref": None,
        "source": "treasury", "desc": "美联储政策路径的市场定价",
    },
    "us_10y": {
        "name": "美债10年", "unit": "%", "group": "external", "better": None, "ref": None,
        "source": "treasury", "desc": "全球资产定价锚",
    },
    "usdcny": {
        "name": "在岸人民币", "unit": "", "group": "external", "better": "down", "ref": None,
        "source": "sina", "desc": "USD/CNY，数值下降=人民币升值。只有落库之后的历史",
    },
    "usd_index": {
        "name": "美元指数", "unit": "", "group": "external", "better": "down", "ref": None,
        "source": "sina", "desc": "美元走强通常压制新兴市场资产。只有落库之后的历史",
    },
    "wti": {
        "name": "纽约原油", "unit": "美元/桶", "group": "external", "better": None, "ref": None,
        "source": "sina", "desc": "输入型通胀的源头。只有落库之后的历史",
    },
    "gold": {
        "name": "纽约黄金", "unit": "美元/盎司", "group": "external", "better": None, "ref": None,
        "source": "sina", "desc": "避险与实际利率的镜像。只有落库之后的历史",
    },
    "a_pe": {
        "name": "全A整体法PE", "unit": "倍", "group": "valuation", "better": "down", "ref": None,
        "source": "local", "desc": "本地全市场快照自算，含亏损股，口径见 _sync_valuation",
    },
    "erp": {
        "name": "全A股债性价比", "unit": "%", "group": "valuation", "better": "up", "ref": 0,
        "source": "local", "desc": "1/PE − 中债10年。越高说明股票相对债券越便宜",
    },
}

# 派生指标：能由已落库的指标算出来的，一律不落库，读取时现算。
# 落一份派生值等于让同一个事实有两处来源，口径一改就得回刷全部历史，
# 而没刷到的那几年会静静地留着旧口径的数——这种错在图上看不出来。
DERIVED: Dict[str, dict] = {
    "m1_m2_gap": {
        "name": "M1-M2剪刀差", "unit": "pp", "group": "money", "better": "up", "ref": 0,
        "freq": "month", "of": ("m1_yoy", "m2_yoy"),
        "desc": "转正代表资金活化、企业愿意花钱，是股市盈利周期的领先信号",
    },
    "ppi_cpi_gap": {
        "name": "PPI-CPI剪刀差", "unit": "pp", "group": "inflation", "better": None, "ref": 0,
        "freq": "month", "of": ("ppi_yoy", "cpi_yoy"),
        "desc": "为正利上游、为负利中下游，决定利润在产业链哪一端",
    },
    "cn_10y_2y": {
        "name": "中债期限利差", "unit": "pp", "group": "money", "better": "up", "ref": 0,
        "freq": "day", "of": ("cn_10y", "cn_2y"),
        "desc": "10Y−2Y。走平或倒挂意味着市场在给衰退定价",
    },
    "cn_us_10y": {
        "name": "中美10年利差", "unit": "pp", "group": "external", "better": "up", "ref": 0,
        "freq": "day", "of": ("cn_10y", "us_10y"),
        "desc": "倒挂越深，人民币和外资流入压力越大",
    },
}

# 核心看板：只放八个。宏观指标堆到二十个就没人看了，
# 这八个能串成一条逻辑链：信用→动能→政策空间→估值→外部约束
CORE = ["pmi_make", "m1_m2_gap", "cpi_yoy", "cn_10y",
        "cn_us_10y", "erp", "usd_index", "usdcny"]

MISSING = [
    "社会融资规模存量同比：东财数据中心无对应报表，暂以「新增人民币贷款」代理，"
    "两者在表外融资与政府债上口径不同，背离时以社融为准",
    "LPR、工业增加值、城镇调查失业率：暂无稳定免费源",
    "存款准备金率：上游是按生效日的事件型序列，与按期间对齐的月度表不兼容，未收录",
]

# 两个东财接口单页都只给 500 条，传更大不报错、直接静默截回来。
# 踩过一次：国债传 ps=10000 只拉到 471 条（到 2024-09），看日志像“成功回填全历史”
_PAGE_MAX = 500
# 月度常规同步取两年（补漏 + 够画 12 期走势），库里空时一页拉到头：
# 这些报表全量都在 250 条上下（最长的 PPI 247 条），一页就是全历史
_MONTHLY_PAGE = 24
_MONTHLY_BACKFILL = _PAGE_MAX
# 国债日序列：常规补最近三个月（一页），首次回填翻页拉全历史。
# 25 页 = 12500 个交易日，比上游现有的 9321 条留了余量，
# 同时当上游 pages 字段不可信时它也能兜住循环，不会无限翻下去
_DAILY_PAGE = 60
_DAILY_BACKFILL_PAGES = 25
# 看板取多少期/多少个交易日画走势
_SPARK_MONTHS = 12
_SPARK_DAYS = 30
# 日度看板只需要最近一段，全表加载会把国债全历史都拽进内存
_DAILY_WINDOW_DAYS = 400
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None      # NaN 当缺数，别落进库里


def spec_of(code: str) -> Optional[dict]:
    """指标登记信息；派生指标也在内"""
    for table in (MONTHLY, DAILY, DERIVED):
        if code in table:
            return {**table[code], "code": code}
    return None


# ── 落库 ──────────────────────────────────────────────────────────────

async def _save(model, key_col: str, rows: List[dict]) -> int:
    """按 (code, 期间) upsert。rows 为空直接返回，别发一条空 insert"""
    if not rows:
        return 0
    async with async_session() as session:
        for i in range(0, len(rows), 1000):
            stmt = mysql_insert(model).values(rows[i:i + 1000])
            stmt = stmt.on_duplicate_key_update(value=stmt.inserted.value)
            await session.execute(stmt)
        await session.commit()
    return len(rows)


async def _row_count(model, code: str) -> int:
    async with async_session() as session:
        return await session.scalar(
            select(sa_func.count()).select_from(model).where(model.code == code)
        ) or 0


# ── 数据源 1：东财月度/季度报表 ────────────────────────────────────────

def _period_of(raw: str) -> Optional[date]:
    """REPORT_DATE 转期间首日。上游给的已经是月首/季末月首，直接取日期部分"""
    m = _DATE_RE.search(raw or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(), "%Y-%m-%d").date()
    except ValueError:
        return None


async def _fetch_report(client: httpx.AsyncClient, report: str, page_size: int) -> List[dict]:
    params = {
        "reportName": report,
        "columns": "ALL",
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
    }
    resp = await client.get(MACRO_REPORT_BASE, params=params, headers=_HEADERS)
    resp.raise_for_status()
    body = resp.json()
    # 报表名写错时上游返回 200 + success:false，不抛异常。不显式检查的话
    # 会当成「这个指标本期没数据」静默跳过，指标从此永远是空的也没人知道
    if not body.get("success"):
        raise ValueError(f"{report} 上游拒绝：{body.get('message') or '未知原因'}")
    return ((body.get("result") or {}).get("data")) or []


async def _sync_monthly(force: bool) -> dict:
    """月度/季度指标。同一个报表带多个指标，按报表去重后只请求一次"""
    by_report: Dict[str, List[str]] = {}
    for code, spec in MONTHLY.items():
        by_report.setdefault(spec["report"], []).append(code)

    saved, failed = 0, []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT * 2) as client:
        for report, codes in by_report.items():
            # 不能写成 all(await _row_count(...) for c in codes)：带 await 的生成器
            # 表达式是 async generator，all() 不认它，会直接 TypeError
            counts = [await _row_count(MacroMonthly, c) for c in codes]
            size = _MONTHLY_PAGE if (all(counts) and not force) else _MONTHLY_BACKFILL
            try:
                data = await _fetch_report(client, report, size)
            except Exception as e:                                # noqa: BLE001
                failed.append(f"{report}: {e}")
                continue

            rows = []
            for item in data:
                period = _period_of(str(item.get("REPORT_DATE") or ""))
                if not period:
                    continue
                for code in codes:
                    v = _num(item.get(MONTHLY[code]["field"]))
                    if v is not None:
                        rows.append({"code": code, "period": period, "value": v})
            saved += await _save(MacroMonthly, "period", rows)

    return {"saved": saved, "failed": failed}


# ── 数据源 2：中美国债收益率日序列 ────────────────────────────────────

async def _fetch_treasury(client: httpx.AsyncClient, page: int,
                          size: int) -> Tuple[List[dict], int]:
    """拉一页国债收益率，返回（本页数据, 总页数）"""
    params = {
        "type": "RPTA_WEB_TREASURYYIELD",
        "sty": "ALL",
        "st": "SOLAR_DATE",
        "sr": "-1",
        "token": TREASURY_YIELD_TOKEN,
        "p": str(page),
        "ps": str(size),
    }
    resp = await client.get(TREASURY_YIELD_BASE, params=params, headers=_HEADERS)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise ValueError(body.get("message") or "上游拒绝请求")
    result = body.get("result") or {}
    return (result.get("data") or []), int(result.get("pages") or 0)


async def _sync_treasury(force: bool) -> dict:
    have = await _row_count(MacroDaily, "cn_10y")
    backfill = force or not have
    pages = _DAILY_BACKFILL_PAGES if backfill else 1
    size = _PAGE_MAX if backfill else _DAILY_PAGE

    rows, failed = [], []
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT * 3) as client:
            for page in range(1, pages + 1):
                data, total = await _fetch_treasury(client, page, size)
                if not data:
                    break
                for item in data:
                    day = _period_of(str(item.get("SOLAR_DATE") or ""))
                    if not day:
                        continue
                    for code, field in TREASURY_FIELDS.items():
                        v = _num(item.get(field))
                        if v is not None:
                            rows.append({"code": code, "trade_date": day, "value": v})
                if total and page >= total:
                    break
    except Exception as e:                                        # noqa: BLE001
        # 已经拉到的照样落库：翻到第 15 页才断，前 14 页的数没理由丢掉，
        # 下次 --force 会把剩下的补齐
        failed.append(f"国债收益率（已存 {len(rows)} 条）: {e}")
    return {"saved": await _save(MacroDaily, "trade_date", rows), "failed": failed}


# ── 数据源 3：新浪快照（汇率/美元指数/大宗）────────────────────────────

def _parse_sina(line: str, price_idx: int) -> Optional[Tuple[date, float]]:
    """从 var hq_str_xxx="a,b,c"; 里取 (日期, 最新价)

    日期用正则在所有字段里找，不钉下标：上游给各品种的字段布局本来就不一致，
    再按下标取日期，哪天多插一列就会把成交量当成日期解析失败——而失败是静默的。
    """
    body = line.split('"')
    if len(body) < 2:
        return None
    fields = body[1].split(",")
    if len(fields) <= price_idx:
        return None
    price = _num(fields[price_idx])
    if price is None or price <= 0:
        return None
    for f in fields:
        m = _DATE_RE.fullmatch(f.strip())
        if m:
            try:
                return datetime.strptime(m.group(), "%Y-%m-%d").date(), price
            except ValueError:
                continue
    return None


async def _sync_sina() -> dict:
    """一次请求拿全部快照。这些指标上游只给当前值，历史从落库那天开始长"""
    symbols = ",".join(s for s, _ in SINA_FIELDS.values())
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(SINA_HQ_BASE + symbols, headers=_SINA_HEADERS)
            resp.raise_for_status()
        text = resp.content.decode("gbk", "replace")
    except Exception as e:                                        # noqa: BLE001
        return {"saved": 0, "failed": [f"新浪快照: {e}"]}

    lines = {}
    for line in text.splitlines():
        m = re.search(r"hq_str_([A-Za-z_$]+)=", line)
        if m:
            lines[m.group(1)] = line

    rows, failed = [], []
    for code, (symbol, idx) in SINA_FIELDS.items():
        line = lines.get(symbol)
        parsed = _parse_sina(line, idx) if line else None
        if not parsed:
            # 新浪对个别品种会返回空串（hf_DINIW 就是），静默跳过等于指标永远空着
            failed.append(f"{DAILY[code]['name']}({symbol}) 未返回有效报价")
            continue
        day, price = parsed
        rows.append({"code": code, "trade_date": day, "value": price})
    return {"saved": await _save(MacroDaily, "trade_date", rows), "failed": failed}


# ── 估值层：全 A 整体法 PE 与股债性价比 ────────────────────────────────

async def _sync_valuation() -> dict:
    """拿本地全市场快照算全 A PE 与 ERP

    PE 用整体法（总市值合计 ÷ 净利润合计），净利润由 total_mv / pe 反推。
    **含亏损股**：亏损公司的负盈利照样进分母。剔掉它们会让分母虚高、PE 虚低——
    2026-07-31 这天两个口径差 2.6 倍（19.2 vs 16.5），拿虚低的那个跟历史比会
    系统性地得出「现在很便宜」的结论。宁可数字难看，也不要一个偏向乐观的数。

    ERP = 1/PE − 中债10年，落库而不是读取时派生。ERP 要的是「同一天的 PE 与
    10Y」配对，而两者的可得日期天然不齐（stock_daily 只在拉全市场时才有一天数据，
    国债有全历史），配对得做前向填充。写入时对齐一次并落库，历史 ERP 就固定住了；
    放在读取时算的话，哪天补了一段国债历史，几个月前的 ERP 会悄悄变一个值。

    另外：stock_daily 原本只在用户打开 A 股页面时才更新。ERP 是核心指标，指望
    用户每天去点那个页面等于指望运气，所以同步时主动拉一次全市场快照。
    """
    failed = []
    try:
        from . import stock_service
        await stock_service.fetch_stock_list()
    except Exception as e:                                        # noqa: BLE001
        # 拉不到就用库里已有的快照算，只是当日 ERP 会缺——比整段跳过好
        failed.append(f"全市场快照刷新失败（用已有快照计算）：{e}")

    async with async_session() as session:
        rows = (await session.execute(
            select(
                StockDaily.trade_date,
                sa_func.sum(StockDaily.total_mv),
                sa_func.sum(StockDaily.total_mv / StockDaily.pe),
            )
            .where(
                StockDaily.pe.isnot(None), StockDaily.pe != 0,
                StockDaily.total_mv.isnot(None), StockDaily.total_mv > 0,
            )
            .group_by(StockDaily.trade_date)
        )).all()

    pe_rows = []
    for trade_date, mv_sum, earnings_sum in rows:
        if not mv_sum or not earnings_sum or earnings_sum <= 0:
            # 全市场整体亏损时 PE 是负数，那个数没有任何估值含义，不落
            continue
        pe_rows.append({
            "code": "a_pe", "trade_date": trade_date,
            "value": round(float(mv_sum) / float(earnings_sum), 2),
        })
    saved = await _save(MacroDaily, "trade_date", pe_rows)

    # ERP：每个有 PE 的交易日，配当日或之前最近一个交易日的 10Y
    bond = await _series(MacroDaily, "cn_10y")
    erp_rows = []
    if bond:
        bond_days = [d for d, _ in bond]
        for row in pe_rows:
            y = _asof(bond_days, [v for _, v in bond], row["trade_date"])
            if y is None or row["value"] <= 0:
                continue
            erp_rows.append({
                "code": "erp", "trade_date": row["trade_date"],
                "value": round(100 / row["value"] - y, 2),
            })
    else:
        failed.append("中债10年还没落库，算不了股债性价比")
    saved += await _save(MacroDaily, "trade_date", erp_rows)
    return {"saved": saved, "failed": failed}


def _asof(days: List[date], values: List[float], day: date) -> Optional[float]:
    """day 当天的值，没有就取之前最近一个有值的交易日（前向填充）"""
    from bisect import bisect_right
    i = bisect_right(days, day)
    return values[i - 1] if i else None


# ── 对外：同步 ────────────────────────────────────────────────────────

async def sync(force: bool = False) -> dict:
    """全量同步。四组各自独立，一组失败不拖累其它组

    不用 gather 并发：四组打的是三个不同的上游，串行跑总共也就几秒，
    并发省下的时间换不来被限流的风险。
    """
    result = {"monthly": await _sync_monthly(force),
              "treasury": await _sync_treasury(force),
              "sina": await _sync_sina()}
    # 估值必须排在国债之后：ERP 的无风险利率端取自刚落库的 cn_10y
    result["valuation"] = await _sync_valuation()
    result["saved"] = sum(g["saved"] for g in result.values() if isinstance(g, dict))
    result["failed"] = [m for g in result.values()
                        if isinstance(g, dict) for m in g.get("failed", [])]
    return result


# ── 对外：读取 ────────────────────────────────────────────────────────

async def _series(model, code: str, since: Optional[date] = None) -> List[Tuple[date, float]]:
    """单指标序列（日期升序）"""
    key = model.period if model is MacroMonthly else model.trade_date
    async with async_session() as session:
        q = select(key, model.value).where(model.code == code)
        if since:
            q = q.where(key >= since)
        return [(d, v) for d, v in (await session.execute(q.order_by(key))).all()]


async def _load_all() -> Tuple[Dict[str, list], Dict[str, list]]:
    """一次把看板要用的序列全捞出来，避免每个指标查一次库"""
    since = date.today() - timedelta(days=_DAILY_WINDOW_DAYS)
    async with async_session() as session:
        m_rows = (await session.execute(
            select(MacroMonthly.code, MacroMonthly.period, MacroMonthly.value)
            .order_by(MacroMonthly.period)
        )).all()
        d_rows = (await session.execute(
            select(MacroDaily.code, MacroDaily.trade_date, MacroDaily.value)
            .where(MacroDaily.trade_date >= since)
            .order_by(MacroDaily.trade_date)
        )).all()

    monthly: Dict[str, list] = {}
    for code, period, value in m_rows:
        monthly.setdefault(code, []).append((period, value))
    daily: Dict[str, list] = {}
    for code, day, value in d_rows:
        daily.setdefault(code, []).append((day, value))
    return monthly, daily


def _derive(series: Dict[str, list], left: str, right: str) -> list:
    """两条序列相减，只保留两边都有值的期间

    按期间求交而不是各取最新相减：M1 出了 7 月、M2 还停在 6 月的时候，
    直接拿两个最新值相减得到的是跨期差，那个数没有意义。
    """
    a, b = dict(series.get(left) or []), dict(series.get(right) or [])
    return [(k, round(a[k] - b[k], 4)) for k in sorted(a.keys() & b.keys())]


def _fmt(d: date, freq: str) -> str:
    if freq == "month":
        return d.strftime("%Y-%m")
    if freq == "quarter":
        return f"{d.year}Q{(d.month - 1) // 3 + 1}"
    return d.strftime("%Y-%m-%d")


def _item(code: str, spec: dict, series: list, freq: str, spark_n: int) -> dict:
    """一个指标在看板上的完整形态：最新值 + 上期 + 变化 + 走势"""
    out = {
        "code": code, "name": spec["name"], "unit": spec.get("unit", ""),
        "group": spec["group"], "better": spec.get("better"), "ref": spec.get("ref"),
        "desc": spec.get("desc", ""), "freq": freq,
        "derived": code in DERIVED, "points": len(series),
        "value": None, "prev": None, "change": None, "period": None, "spark": [],
    }
    if not series:
        # 没数据就说没数据。给个 0 或者拿上一个指标的值凑，是最恶劣的一种错
        out["reason"] = "还没有数据落库，先跑 scripts/sync_macro.py"
        return out

    tail = series[-spark_n:]
    out["value"] = series[-1][1]
    out["period"] = _fmt(series[-1][0], freq)
    out["spark"] = [v for _, v in tail]
    if len(series) >= 2:
        out["prev"] = series[-2][1]
        out["prev_period"] = _fmt(series[-2][0], freq)
        out["change"] = round(series[-1][1] - series[-2][1], 4)
    return out


async def dashboard() -> dict:
    """看板全量数据：核心八项 + 五个分组 + 已知缺口"""
    monthly, daily = await _load_all()

    items: Dict[str, dict] = {}
    for code, spec in MONTHLY.items():
        items[code] = _item(code, spec, monthly.get(code) or [],
                            spec["freq"], _SPARK_MONTHS)
    for code, spec in DAILY.items():
        items[code] = _item(code, spec, daily.get(code) or [], "day", _SPARK_DAYS)
    for code, spec in DERIVED.items():
        src = monthly if spec["freq"] == "month" else daily
        n = _SPARK_MONTHS if spec["freq"] == "month" else _SPARK_DAYS
        items[code] = _item(code, spec, _derive(src, *spec["of"]), spec["freq"], n)

    groups = []
    for key, name in GROUPS:
        members = [it for it in items.values() if it["group"] == key]
        if members:
            groups.append({"key": key, "name": name, "items": members})

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "core": [items[c] for c in CORE if c in items],
        "groups": groups,
        "missing": MISSING,
    }


async def history(code: str, limit: int = 120) -> dict:
    """单指标完整序列，给前端画大图用"""
    spec = spec_of(code)
    if not spec:
        raise ValueError(f"未登记的指标：{code}")

    if code in DERIVED:
        monthly, daily = await _load_all()
        src = monthly if spec["freq"] == "month" else daily
        series = _derive(src, *spec["of"])
        freq = spec["freq"]
    elif code in MONTHLY:
        series = await _series(MacroMonthly, code)
        freq = spec["freq"]
    else:
        series = await _series(MacroDaily, code)
        freq = "day"

    series = series[-limit:]
    return {
        "code": code, "name": spec["name"], "unit": spec.get("unit", ""),
        "freq": freq, "ref": spec.get("ref"),
        "items": [{"period": _fmt(d, freq), "value": v} for d, v in series],
    }
