"""组合净值曲线与回撤

回撤纪律（-15% 降半仓 / -20% 降三成仓）此前依赖 briefs/asset_history.jsonl 的
逐日快照，那条路有几个要命的毛病：要攒几个月才有统计意义；快照记的是 14:30 的
盘中估值而不是收盘净值；漏跑一天就断档，而峰值取 max，丢点会低估峰值、进而
低估回撤——错在「该降仓时不提示」这个危险方向上。

净值落库之后不必这么将就：持仓流水和逐日净值都在库里，组合的历史总资产可以
直接重算出来，不用等、不漏点、用的是收盘净值。

模拟盘没有注资/取现接口（只有 reset 清零重来），买卖只是现金与持仓之间的搬运，
所以总资产序列天然干净，直接对它算回撤就是对的，不需要做时间加权（TWR）处理。
同理，日收益率直接拿相邻两天的总资产相除即可，不必剔除外部现金流。
"""
import math
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import select

from ..database import async_session
from ..models import FundNav, SimAccount, SimTrade

# 少于这个交易日数就提醒「回撤还不具参考意义」，别让人拿 3 天的曲线做降仓决策
_MIN_MEANINGFUL_DAYS = 20

# 回撤纪律阈值：组合回撤到这个百分比就降仓。这里是唯一定义处，简报与前端都从
# 接口取，不各自硬编——三处各存一份，改了一处忘了另两处，就会在「该降仓时不提示」
# 这个方向上出错。
DRAWDOWN_HALF = 15.0     # 降至半仓
DRAWDOWN_THIRD = 20.0    # 降至三成仓

# 以下三个常量与前端 utils/metrics.js 保持一致：同一个夏普在两处算出不同的数，
# 比没有这个指标更糟。
_RISK_FREE = 0.021
_TRADING_DAYS_PER_YEAR = 252
_CALENDAR_DAYS_PER_YEAR = 365.25

# 少于这么多交易日不给年化/波动/夏普。把 3 天的 -0.12% 年化会得到「年化 -14%」，
# 那是个看起来像结论的噪音，比留空危险得多。
_MIN_METRIC_DAYS = 20
# 跨度不到这么多交易日（约半年），年化仍会被短窗口放大，出数但要附注说明
_SHORT_SPAN_DAYS = 120
# 年化波动/最大回撤低于这个量级就当作「没有」，不拿它做除数。
# 不能写 > 0：浮点噪音会让一个实际上毫无波动的序列算出 1e-7 的波动，
# 除下去得到夏普 200 万——显示层四舍五入后波动看着是 0.0，夏普却挂着个天文数字，
# 那比不给数危险得多。
_VOL_EPS = 0.005    # 年化波动 0.5%，低于此的组合跟现金无异
_DD_EPS = 0.01      # 最大回撤 0.01 个百分点


def _add_note(out: dict, text: str) -> None:
    """追加一条口径附注（可能同时成立多条，不能互相覆盖）"""
    out["note"] = f"{out['note']}；{text}" if out.get("note") else text


def _dec(v) -> Decimal:
    return Decimal(str(v if v is not None else 0))


def _nav_on(dates: List[date], navs: List[float], day: date) -> Optional[float]:
    """当日净值，没有就用之前最近一个（前向填充）

    QDII 净值滞后几个交易日是常态，不填充的话它在时间轴上会时有时无，
    市值会一天一跳。
    """
    i = bisect_right(dates, day)
    return navs[i - 1] if i else None


def _discipline(drawdown: Optional[float]) -> dict:
    """当前回撤落在哪个纪律档位

    阈值随结果一起下发，前端画纪律线、简报写档位都用这一份。

    不看样本长度：不同于年化类指标（短样本年化出来是噪音），回撤 15%
    是已经发生的亏损，不因为只有三天历史就不算。
    """
    out = {"half": DRAWDOWN_HALF, "third": DRAWDOWN_THIRD,
           "stage": "unknown", "gap_to_half": None}
    if drawdown is None:
        return out
    if drawdown >= DRAWDOWN_THIRD:
        out["stage"] = "third"
    elif drawdown >= DRAWDOWN_HALF:
        out["stage"] = "half"
    else:
        out["stage"] = "normal"
        out["gap_to_half"] = round(DRAWDOWN_HALF - drawdown, 2)
    return out


def _risk_metrics(curve: List[dict], initial: Decimal, max_dd: float,
                  peak: Optional[Decimal]) -> dict:
    """组合级风险指标

    口径对齐前端 utils/metrics.js（基金对比面板用的那套）：年化波动 = 日波动×√252，
    夏普 = （年化收益 - 2.1%）/ 年化波动，卡玛 = 年化收益 / 最大回撤。同一个指标
    在单基金页和组合页算法不一致的话，两个数没法横向比，那这指标就白做了。

    样本不够时全部返回 None 并给 reason，不拿几天数据去年化。累计收益率和
    「回到峰值还需涨多少」不涉年化，有两个点就能算，所以不设门槛。
    """
    n = len(curve)
    totals = [c["total_asset"] for c in curve]
    latest = totals[-1]
    base = float(initial)

    out: dict = {
        "trading_days": n,
        "rf": round(_RISK_FREE * 100, 2),
        "cum_return": round((latest / base - 1) * 100, 2) if base > 0 else None,
        "max_drawdown": round(max_dd, 2),
        "ann_return": None, "ann_vol": None, "sharpe": None,
        "calmar": None, "win_rate": None,
        # 从当前位置涨回峰值需要的涨幅：回撤 20% 要涨 25% 才回本，这不对称是降仓纪律的理由
        "recover_pct": None,
        "reason": None, "note": None,
    }

    if peak is not None and peak > 0 and latest > 0:
        out["recover_pct"] = round((float(peak) / latest - 1) * 100, 2)

    if n < _MIN_METRIC_DAYS:
        out["reason"] = (
            f"只有 {n} 个交易日，不足 {_MIN_METRIC_DAYS} 个，年化与波动类指标算出来也是噪音"
        )
        return out

    start = datetime.strptime(curve[0]["date"], "%Y-%m-%d").date()
    end = datetime.strptime(curve[-1]["date"], "%Y-%m-%d").date()
    years = (end - start).days / _CALENDAR_DAYS_PER_YEAR

    if years > 0 and base > 0 and latest > 0:
        out["ann_return"] = round(((latest / base) ** (1 / years) - 1) * 100, 2)

    # 日收益率：总资产序列无外部现金流，相邻两天直接相除
    rets = [totals[i] / totals[i - 1] - 1
            for i in range(1, n) if totals[i - 1] > 0]
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        ann_vol = math.sqrt(var) * math.sqrt(_TRADING_DAYS_PER_YEAR)
        out["ann_vol"] = round(ann_vol * 100, 2)
        out["win_rate"] = round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1)
        if out["ann_return"] is not None and ann_vol >= _VOL_EPS:
            out["sharpe"] = round(
                (out["ann_return"] / 100 - _RISK_FREE) / ann_vol, 2)
        elif out["ann_return"] is not None:
            _add_note(out, "组合几乎没有波动（年化不足 0.5%），夏普比不给数："
                           "除以近乎零的波动只会得到天文数字")

    if out["ann_return"] is not None and max_dd >= _DD_EPS:
        out["calmar"] = round(out["ann_return"] / max_dd, 2)
    elif out["ann_return"] is not None:
        # 还没出现过实质回撤，卡玛比无从谈起（除以近乎零），不是「无限好」
        _add_note(out, "区间内未出现实质回撤，卡玛比算不了（不是无限大）")

    if n < _SHORT_SPAN_DAYS:
        _add_note(out, f"样本 {n} 个交易日（不足半年），年化类指标会被短窗口放大，"
                       f"不要拿它与基金的多年年化直接比")

    return out


async def build_curve() -> dict:
    """按交易流水 + 逐日净值重算组合总资产曲线与回撤"""
    async with async_session() as session:
        account = await session.scalar(select(SimAccount).order_by(SimAccount.id))
        trades = (await session.execute(
            select(SimTrade.nav_date, SimTrade.side, SimTrade.asset_type,
                   SimTrade.code, SimTrade.name, SimTrade.shares,
                   SimTrade.price, SimTrade.amount)
            .where(SimTrade.nav_date.is_not(None))
            .order_by(SimTrade.nav_date, SimTrade.id)
        )).all()

    initial = _dec(account.initial_cash) if account else Decimal(0)
    if not trades:
        return {
            "days": 0, "initial_cash": float(initial),
            "drawdown": None, "max_drawdown": None,
            "benchmark": None, "risk": None,
            "discipline": _discipline(None), "curve": [],
            "warnings": ["还没有任何成交流水，无法重建组合曲线"],
        }

    first_day = min(t.nav_date for t in trades)
    fund_codes = sorted({t.code for t in trades if t.asset_type == "fund"})
    other = sorted({(t.code, t.name) for t in trades if t.asset_type != "fund"})

    async with async_session() as session:
        rows = (await session.execute(
            select(FundNav.code, FundNav.nav_date, FundNav.nav)
            .where(FundNav.code.in_(fund_codes), FundNav.nav_date >= first_day)
            .order_by(FundNav.nav_date)
        )).all() if fund_codes else []

    # 每只基金一份升序的 (日期, 净值)，供 bisect 前向填充
    nav_dates: Dict[str, List[date]] = defaultdict(list)
    nav_vals: Dict[str, List[float]] = defaultdict(list)
    for code, d, nav in rows:
        nav_dates[code].append(d)
        nav_vals[code].append(nav)

    # 时间轴要并上成交日：某只基金净值缺失时，光取净值日会把那天的流水漏掉
    timeline = sorted({d for ds in nav_dates.values() for d in ds}
                      | {t.nav_date for t in trades})
    timeline = [d for d in timeline if d >= first_day]

    trades_by_day: Dict[date, list] = defaultdict(list)
    for t in trades:
        trades_by_day[t.nav_date].append(t)

    shares: Dict[str, Decimal] = defaultdict(Decimal)
    # 非基金标的没有历史价格落库，用最近成交价占位
    last_price: Dict[str, Decimal] = {}
    cash = initial
    curve: List[dict] = []
    peak: Optional[Decimal] = None
    peak_date: Optional[date] = None
    max_dd = 0.0
    max_dd_date: Optional[date] = None

    for day in timeline:
        for t in trades_by_day.get(day, []):
            if t.side == "buy":
                shares[t.code] += _dec(t.shares)
                cash -= _dec(t.amount)          # amount 是含费扣款
            else:
                shares[t.code] -= _dec(t.shares)
                cash += _dec(t.amount)          # amount 是扣费后到账
            last_price[t.code] = _dec(t.price)

        market_value = Decimal(0)
        for code, sh in shares.items():
            if sh <= 0:
                continue
            nav = _nav_on(nav_dates[code], nav_vals[code], day) if code in nav_dates else None
            price = _dec(nav) if nav is not None else last_price.get(code, Decimal(0))
            market_value += sh * price

        total = cash + market_value
        if peak is None or total > peak:
            peak, peak_date = total, day
        dd = float((peak - total) / peak * 100) if peak and peak > 0 else 0.0
        if dd > max_dd:
            max_dd, max_dd_date = dd, day

        curve.append({
            "date": str(day),
            "cash": round(float(cash), 2),
            "market_value": round(float(market_value), 2),
            "total_asset": round(float(total), 2),
            "drawdown": round(dd, 2),
        })

    warnings = []
    if other:
        names = ", ".join(f"{c} {n}"[:24] for c, n in other)
        warnings.append(
            f"持仓含非基金标的（{names}），没有历史价格落库，按最近成交价占位估值——"
            f"这部分不随行情波动，回撤与波动率都会被低估，别只看这一个数就降仓"
        )
    missing = [c for c in fund_codes if c not in nav_dates]
    if missing:
        warnings.append(
            f"这些基金没有净值落库，市值按成交价占位：{', '.join(missing)}；"
            f"先跑 scripts/sync_nav.py 补数据"
        )
    if len(curve) < _MIN_MEANINGFUL_DAYS:
        warnings.append(
            f"样本只有 {len(curve)} 个交易日，回撤还不具备参考意义，"
            f"不足以支撑降仓决策"
        )

    latest = curve[-1]
    risk = _risk_metrics(curve, initial, max_dd, peak)
    # 样本不足的 reason、口径附注都挂在 risk 自己身上，不再往 warnings 里抄一份：
    # 同一句话在简报里出现两遍，读的人会开始跳过这类提示
    #
    # 现金不波动，它把整个组合的波动稀释了。不说清的话，一个半仓现金的组合会显得
    # 比它持仓的基金「稳」，而那不是选股能力带来的
    if risk.get("ann_vol") is not None:
        cash_ratio = (latest["cash"] / latest["total_asset"] * 100
                      if latest["total_asset"] else 0)
        if cash_ratio >= 20:
            _add_note(
                risk,
                f"当前现金占 {cash_ratio:.0f}%，现金不波动，所以组合波动率比持仓基金本身低得多，"
                f"这是仓位带来的而不是选股带来的"
            )
    # 超额收益：懒导入避免和 benchmark_service 绕成循环依赖
    from . import benchmark_service
    bench = await benchmark_service.portfolio_excess(
        datetime.strptime(curve[0]["date"], "%Y-%m-%d").date(),
        datetime.strptime(latest["date"], "%Y-%m-%d").date(),
        float(initial), latest["total_asset"],
    )
    if bench.get("reason"):
        warnings.append(f"超额收益算不出来：{bench['reason']}")

    return {
        "start": curve[0]["date"],
        "end": latest["date"],
        "days": len(curve),
        "initial_cash": float(initial),
        "peak": round(float(peak), 2) if peak is not None else None,
        "peak_date": str(peak_date) if peak_date else None,
        "latest_total": latest["total_asset"],
        # 当前回撤：口径是「截至 end 那天的收盘净值」，不含今日盘中波动
        "drawdown": latest["drawdown"],
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_date": str(max_dd_date) if max_dd_date else None,
        "risk": risk,
        "discipline": _discipline(latest["drawdown"]),
        "benchmark": bench,
        "curve": curve,
        "warnings": warnings,
    }
