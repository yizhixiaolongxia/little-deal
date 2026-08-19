"""基准指数与超额收益

持仓表此前只有绝对收益率，看不出「这只跌 3% 是它烂还是大盘烂」。这里把基准
点位落库，给单只基金和组合算相对基准的超额收益。

基准统一用沪深300。这是个明确的取舍：标普500 QDII、短债这类标的跟沪深300
本来就不同源，它们的单只超额数基本是汇率和美债行情的噪音，看的时候要知道
这一点。换基准改 BENCHMARKS 就行。

数据源用新浪 K 线（和 market_service 同一个接口）。这里踩过一个坑必须防住：
新浪对部分指数早就停止更新了，但接口仍然返回 200 和完全合法的格式——
中证红利 sh000922 最后一根 K 线是 2019-01-30，中证全指是 2016-06-13。
拿 2019 年的点位跟今天的净值算超额，会得出一个看起来很正常但完全荒谬的数。
所以每次读取都校验 last_date 与期望交易日的距离，超了就标 stale 并说明原因，
绝不静默返回。
"""
from bisect import bisect_right
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import httpx
from sqlalchemy import func as sa_func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from ..config import REQUEST_TIMEOUT
from ..database import async_session
from ..models import IndexQuote

_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}
SINA_KLINE = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/CN_MarketData.getKLineData"
)

# 基准登记表。key 是对外用的短名，symbol 是新浪的标的代码。
# 加新基准前先确认它在新浪还有更新——见模块顶部注释里的停更坑。
#
# 后三个不是给超额收益用的（组合基准只有 hs300），是给市场风险面板算点位
# 历史分位用的：只看沪深300 会漏掉风格分化——2021 年初沪深300 在高位而中证500
# 还在半山腰，两个指数的分位能差 30 个点，只看一个会把「结构性贵」读成「全面贵」。
BENCHMARKS: Dict[str, dict] = {
    "hs300": {"symbol": "sh000300", "name": "沪深300"},
    "sh": {"symbol": "sh000001", "name": "上证指数"},
    "zz500": {"symbol": "sh000905", "name": "中证500"},
    "cyb": {"symbol": "sz399006", "name": "创业板指"},
}
DEFAULT_BENCHMARK = "hs300"

# 收盘 15:00，日线数据要等一会儿才齐，16 点前只期望上一个交易日
_CLOSE_HOUR = 16
# 落后期望交易日超过这么多天就判定上游停更（含长假：春节最长 9 天）
_STALE_TOLERANCE_DAYS = 12
_SAVE_CHUNK = 1000
# datalen 给足就能拿到全历史（沪深300 是 2002 年至今约 6000 条）
_FULL_HISTORY_LEN = 8000


def _symbol(key: str) -> str:
    conf = BENCHMARKS.get(key)
    if not conf:
        raise ValueError(f"未登记的基准：{key}")
    return conf["symbol"]


def name_of(key: str) -> str:
    return (BENCHMARKS.get(key) or {}).get("name", key)


def _expected_trade_date(now: datetime) -> date:
    """此刻上游最多应该能给到哪一天的收盘点位

    和净值同理：法定节假日推不出来，那几天这个值会偏新，
    后果由 _STALE_TOLERANCE_DAYS 的余量兜住。
    """
    d = now.date()
    if now.hour < _CLOSE_HOUR:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def expected_trade_date(now: Optional[datetime] = None,
                        close_hour: int = _CLOSE_HOUR) -> date:
    """对外版的期望交易日。close_hour 可调是给发布更晚的数据源用的——
    两融余额要等交所盘后汇总，十六点拿不到当天的数"""
    now = now or datetime.now()
    d = now.date()
    if now.hour < close_hour:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def staleness(last: Optional[date], label: str,
              now: Optional[datetime] = None,
              close_hour: int = _CLOSE_HOUR) -> Optional[str]:
    """本地最新一天落后期望交易日太多就返回告警文案，否则 None

    抽成公共函数是给分位面板用的：分位算的是「今天在历史上的位置」，
    而「今天」实际上是库里最后一行。要是那一行停在半个月前，面板会拿陈旧
    点位算出一个看起来完全正常的分位，这比缺数更危险。
    """
    expected = expected_trade_date(now, close_hour)
    if last is None:
        return f"{label} 本地无数据"
    lag = (expected - last).days
    if lag > _STALE_TOLERANCE_DAYS:
        return (f"{label} 本地最新只到 {last}，比期望交易日 {expected} "
                f"落后 {lag} 天，分位结果不代表今天")
    return None


# ── 落库 ──────────────────────────────────────────────────────────────

async def _fetch_upstream(symbol: str, datalen: int = _FULL_HISTORY_LEN) -> List[dict]:
    params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(datalen)}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT * 3) as client:
        resp = await client.get(SINA_KLINE, params=params, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()

    if not isinstance(data, list):
        raise ValueError(f"{symbol} 返回格式异常")

    points = []
    for item in data:
        try:
            d = datetime.strptime(str(item["day"])[:10], "%Y-%m-%d").date()
            points.append({"date": d, "close": float(item["close"])})
        except (KeyError, ValueError, TypeError):
            continue
    if not points:
        raise ValueError(f"{symbol} 未返回有效点位")
    return points


async def _save(symbol: str, points: List[dict]) -> int:
    async with async_session() as session:
        for i in range(0, len(points), _SAVE_CHUNK):
            values = [
                {"code": symbol, "trade_date": p["date"], "close": p["close"]}
                for p in points[i:i + _SAVE_CHUNK]
            ]
            stmt = mysql_insert(IndexQuote).values(values)
            stmt = stmt.on_duplicate_key_update(close=stmt.inserted.close)
            await session.execute(stmt)
        total = await session.scalar(
            select(sa_func.count()).select_from(IndexQuote).where(IndexQuote.code == symbol)
        )
        await session.commit()
    return total or 0


async def _last_date(symbol: str) -> Optional[date]:
    async with async_session() as session:
        return await session.scalar(
            select(sa_func.max(IndexQuote.trade_date)).where(IndexQuote.code == symbol)
        )


async def sync(key: str = DEFAULT_BENCHMARK, force: bool = False) -> dict:
    """把基准全历史点位落库

    上游每次都返回全历史，所以漏跑几天不会留缺口，下次同步一起补上。
    """
    symbol = _symbol(key)
    now = datetime.now()
    expected = _expected_trade_date(now)

    last = await _last_date(symbol)
    if not force and last and last >= expected:
        return {"key": key, "symbol": symbol, "status": "fresh", "last_date": str(last)}

    try:
        points = await _fetch_upstream(symbol)
    except Exception as e:
        return {"key": key, "symbol": symbol, "status": "failed", "message": str(e)}

    total = await _save(symbol, points)
    upstream_last = points[-1]["date"]
    result = {
        "key": key, "symbol": symbol, "status": "synced",
        "rows": total, "last_date": str(upstream_last),
    }
    # 上游给的最后一根就已经很旧 —— 这个 symbol 大概率被停更了，
    # 同步「成功」反而更危险，必须说出来
    lag = (expected - upstream_last).days
    if lag > _STALE_TOLERANCE_DAYS:
        result["warning"] = (
            f"上游最新点位是 {upstream_last}，比期望交易日 {expected} 落后 {lag} 天，"
            f"{symbol} 可能已被数据源停止更新，不要拿它算超额收益"
        )
    return result


# ── 读取与计算 ────────────────────────────────────────────────────────

async def get_series(key: str = DEFAULT_BENCHMARK, start=None, end=None) -> List[dict]:
    """基准点位序列（只读库，不回源）"""
    symbol = _symbol(key)
    async with async_session() as session:
        q = select(IndexQuote.trade_date, IndexQuote.close).where(IndexQuote.code == symbol)
        if start:
            q = q.where(IndexQuote.trade_date >= start)
        if end:
            q = q.where(IndexQuote.trade_date <= end)
        rows = (await session.execute(q.order_by(IndexQuote.trade_date))).all()
    return [{"date": str(d), "close": c} for d, c in rows]


async def _load_points(symbol: str) -> tuple:
    async with async_session() as session:
        rows = (await session.execute(
            select(IndexQuote.trade_date, IndexQuote.close)
            .where(IndexQuote.code == symbol)
            .order_by(IndexQuote.trade_date)
        )).all()
    return [r[0] for r in rows], [r[1] for r in rows]


def _close_on(dates: List[date], closes: List[float], day: date) -> Optional[float]:
    """当日收盘，没有就用之前最近一个交易日（前向填充）

    基金净值日和指数交易日不总是对齐（QDII 滞后好几天），不填充的话
    区间两端会取不到点、直接算不出超额。
    """
    i = bisect_right(dates, day)
    return closes[i - 1] if i else None


def _period_return_from(dates: List[date], closes: List[float],
                        start: date, end: date, key: str) -> dict:
    """用已加载的点位算区间收益率

    单独拆出来是为了批量算多只基金时基准点位只加载一次——沪深300 有六千多条，
    每只都查一遍纯属浪费。
    """
    symbol = _symbol(key)
    base = {"key": key, "name": name_of(key), "symbol": symbol,
            "start": str(start), "end": str(end)}

    if not dates:
        return {**base, "pct": None,
                "reason": f"{symbol} 还没有点位落库，先跑 scripts/sync_benchmark.py"}

    # 上游停更检测：库里最新点位离期望交易日太远
    expected = _expected_trade_date(datetime.now())
    lag = (expected - dates[-1]).days
    if lag > _STALE_TOLERANCE_DAYS:
        return {**base, "pct": None,
                "reason": (f"{symbol} 最新点位 {dates[-1]} 比期望交易日 {expected} "
                           f"落后 {lag} 天，数据源可能已停更，算出来的超额不可信")}

    if start < dates[0]:
        return {**base, "pct": None,
                "reason": f"区间起点 {start} 早于基准最早点位 {dates[0]}"}

    c0 = _close_on(dates, closes, start)
    c1 = _close_on(dates, closes, end)
    if c0 is None or c1 is None or c0 <= 0:
        return {**base, "pct": None, "reason": "区间两端取不到有效点位"}

    return {**base, "pct": round((c1 / c0 - 1) * 100, 2),
            "start_close": round(c0, 2), "end_close": round(c1, 2)}


async def period_return(start: date, end: date,
                        key: str = DEFAULT_BENCHMARK) -> dict:
    """基准在 [start, end] 区间的收益率

    两端都用「不晚于该日的最近交易日」收盘，保证和基金/组合比的是同一段时间。
    取不到点或数据过旧时返回 pct=None 并给出原因，绝不返回一个看着正常的错数。
    """
    dates, closes = await _load_points(_symbol(key))
    return _period_return_from(dates, closes, start, end, key)


def excess(portfolio_pct: Optional[float], bench: dict) -> Optional[float]:
    """超额收益 = 自己的收益率 − 基准同期收益率（算术差，单位 pp）

    基准算不出来时返回 None 而不是拿 0 当基准：0 会被当成「基准没涨」，
    把一个缺数问题伪装成一个真实结论。
    """
    if portfolio_pct is None or bench.get("pct") is None:
        return None
    return round(portfolio_pct - bench["pct"], 2)


async def fund_excess(code: str, days: int = 365,
                      key: str = DEFAULT_BENCHMARK,
                      points: Optional[tuple] = None) -> dict:
    """单只基金近 N 自然日的收益率与超额

    收益率用累计净值（含分红）。红利类基金一年派四次，改用单位净值
    算会把已经到手的分红当成亏损，实测最多低估 3.8 个百分点。
    累计净值缺失时降级用单位净值，但会在 basis 里标出来。

    区间两端都用基金自己的净值日，再拿这两个日期去取基准——QDII 净值滞后
    好几天，直接用自然日对齐会把滞后当成跑输。

    points 是已加载的基准点位，批量调用时传进来避开重复查库。
    """
    from . import nav_service

    end_day = date.today()
    series = await nav_service.get_nav_series(
        code, start=end_day - timedelta(days=days)
    )
    out = {"code": code, "days": days, "pct": None, "excess": None,
           "benchmark": name_of(key)}
    if len(series) < 2:
        out["reason"] = f"{code} 在这个区间内净值不足 2 条，算不了收益率"
        return out

    first, last = series[0], series[-1]
    use_acc = first.get("acc_nav") is not None and last.get("acc_nav") is not None
    v0 = first["acc_nav"] if use_acc else first["nav"]
    v1 = last["acc_nav"] if use_acc else last["nav"]
    out["basis"] = "acc_nav" if use_acc else "nav"
    out["start"] = first["date"]
    out["end"] = last["date"]
    if not use_acc:
        out["warning"] = "累计净值缺失，收益率按单位净值算，有分红的话会被低估"

    if not v0 or v0 <= 0:
        out["reason"] = "起点净值无效"
        return out

    out["pct"] = round((v1 / v0 - 1) * 100, 2)
    dates, closes = points if points is not None else await _load_points(_symbol(key))
    bench = _period_return_from(
        dates, closes,
        datetime.strptime(first["date"], "%Y-%m-%d").date(),
        datetime.strptime(last["date"], "%Y-%m-%d").date(),
        key,
    )
    out["benchmark_pct"] = bench["pct"]
    out["excess"] = excess(out["pct"], bench)
    if bench.get("reason"):
        out["reason"] = bench["reason"]
    return out


async def funds_excess(codes: List[str], days: int = 365,
                       key: str = DEFAULT_BENCHMARK) -> List[dict]:
    """批量算多只基金的超额（基准点位只加载一次）"""
    points = await _load_points(_symbol(key))
    return [await fund_excess(c, days=days, key=key, points=points) for c in codes]


async def portfolio_excess(start: date, end: date, initial_cash: float,
                           latest_total: float,
                           key: str = DEFAULT_BENCHMARK) -> dict:
    """组合相对基准的超额

    起点用 initial_cash 而不是曲线第一个点的总资产：建仓当天的手续费已经从
    第一个点里扣掉了，拿它当基数等于把买入成本排除在超额之外——那是自我美化。

    还有一个结构性偏差必须说清楚：模拟盘没有分红派现逻辑，市值按单位净值算，
    而分红当天单位净值会除权下跌。长期持有红利类基金时，账面总资产会系统性
    低于真实水平，超额也跟着被低估。这是模拟盘本身的缺口，不在超额计算里修。
    """
    out = {"pct": None, "excess": None, "benchmark": name_of(key),
           "start": str(start), "end": str(end)}
    if not initial_cash or initial_cash <= 0:
        out["reason"] = "初始资金为空，算不了组合收益率"
        return out

    out["pct"] = round((latest_total / initial_cash - 1) * 100, 2)
    bench = await period_return(start, end, key=key)
    out["benchmark_pct"] = bench["pct"]
    out["excess"] = excess(out["pct"], bench)
    if bench.get("reason"):
        out["reason"] = bench["reason"]
    out["warning"] = (
        "模拟盘不做分红派现、市值按单位净值估，持有分红型基金时账面会低于真实，"
        "这个超额是偏保守的下限"
    )
    return out
