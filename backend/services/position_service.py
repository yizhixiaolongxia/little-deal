"""市场位置：指数点位历史分位 + 两融杠杆历史分位

原来的市场风险面板只有两个数：今天涨跌多少、恐贪情绪多少分。这两个都是当天的
温度，回答不了唯一真正重要的问题——**现在这个位置在历史上算什么水平**。
2015 年 6 月和 2018 年底的单日涨跌幅看起来可以一模一样，但一个是杠杆顶、一个是
大底，只看温度计的人两次会做出同样的动作。

本模块加两条纵深：

1) 指数点位分位。这里必须先讲清楚一个陷阱：**指数点位是带长期漂移的**。中证500
   和创业板指过去十几年整体在往上走，拿全历史算点位分位，结果会长年趴在 90 以上，
   看着像「永远处于历史高位」，那就成了一个永远亮红灯、因此毫无信息量的指标。
   防这个坑靠两件事：一是同时给近3年/近5年/近10年/全历史四个窗口，短窗口基本
   不受漂移污染，四个数放在一起才看得出「是真的高，还是只是指数长大了」；二是
   额外给「距历史最高点」，这个数漂移影响小、而且直接对应回撤空间。
   四个指数一起看还有一层：只看沪深300 会把结构性行情读成全面行情，2021 年初
   沪深300 在历史高位而中证500 还在半山腰，两者分位差 30 个点。

2) 两融余额分位——这个才是「风险」二字的实处。杠杆钱是市场里最不耐跌的钱，
   它的规模决定了下跌能不能自我放大。口径上有一个必须踩对的选择：
   **用融资余额占流通市值比，不用绝对额**。实测（2010-03-31 ~ 今）绝对额把今天
   排到 97.9 分位、占比只有 89.9 分位，差 8.1 个点；原因是流通市值从 2015 年的
   约 45 万亿膨胀到今天的 102 万亿，绝对额里混进了「市场变大」这个跟杠杆无关的
   增量。用占比算，历史前五高全部落在 2015 年 6~7 月（4.53%~4.70%），正好是
   那轮杠杆牛的顶——这说明占比这把尺子是对的。

数据源：东财 datacenter 报表 RPTA_RZRQ_LSHJ（注意前缀是 RPTA_ 不是 RPT_）。
这个接口有两个坑，都已防住：
  - 报表名写错时返回 200 + success:false，不检查会当成「本期没数据」静默跳过；
  - **单页硬上限 800**，pageSize 给 5000 照样只返回 800 条、连 pages 字段都跟着
    改成 5，不报任何错。基于被截断的数据算分位会得出完全错误的结论（实测：只拿
    2010~2013 三年数据算，今天会被排到 98 分位）。所以全量拉取后用上游给的
    count 做完整性断言，对不上就抛，绝不拿残缺数据算。

免费的双源对账：RPTA_RZRQ_LSHJ 的 NEW 字段就是当日沪深300 收盘点位，和
index_quote 里 sh000300 那一行实测吻合（4725.8134 / 4725.813）。落库时一起存下，
两个独立数据源对不上就说明至少有一边错了。
"""
from bisect import bisect_left
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy import func as sa_func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from ..config import MACRO_REPORT_BASE, REQUEST_TIMEOUT
from ..database import async_session
from ..models import IndexQuote, MarginDaily
from . import benchmark_service

_HEADERS = {
    "Referer": "https://data.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}

_MARGIN_REPORT = "RPTA_RZRQ_LSHJ"
# 上游单页硬上限。给更大的值不会报错，只会静默返回 800 条 —— 见模块顶部注释
_PAGE_SIZE = 800
_MAX_PAGES = 20          # 3980 条 ≈ 5 页，20 页的上限只为防上游异常导致死循环
# 两融数据要等交易所盘后汇总，通常晚上才出。用 16 点判新鲜度会导致每次都判「不新鲜」
_MARGIN_PUBLISH_HOUR = 21

# 面板展示的指数，顺序即前端顺序。key 取自 benchmark_service.BENCHMARKS
PANEL_INDICES = ["sh", "hs300", "zz500", "cyb"]

# 分位窗口。min_rows 是这个窗口至少要有多少个交易日才肯给分位：
# 一年约 244 个交易日，这里要求覆盖到 55% 以上。样本不够就返回 null 并说明原因，
# 不编一个「3 个点里排第 2 = 67 分位」这种看着像结论其实什么都没说的数。
_WINDOWS: List[Tuple[str, str, Optional[int], int]] = [
    ("y3", "近3年", 3, 400),
    ("y5", "近5年", 5, 700),
    ("y10", "近10年", 10, 1400),
    ("all", "全历史", None, 250),
]

# 分位档位阈值（%）。高分位 = 位置高/杠杆重 = 风险高，两个指标同向。
_PCTL_HIGH = 80.0
_PCTL_LOW = 30.0
# 判定用哪个窗口。选 5 年而不是全历史：5 年跨得过一轮完整牛熊，又不至于被
# 十几年的指数漂移带偏；全历史那一列留给人自己看。
_VERDICT_WINDOW = "y5"

_SPARK_POINTS = 60       # 前端 sparkline 的目标点数，多了只是浪费带宽


# ── 分位计算 ──────────────────────────────────────────────────────────

def _percentile(vals: List[float], cur: float) -> float:
    """cur 在 vals 里的历史分位（%），vals 含 cur 本身

    用「小于等于 cur 的占比」而不是插值分位数：这个定义能直接读成
    「历史上有 X% 的交易日比今天更低」，跟人脑里的问法一一对应。
    """
    return round(sum(1 for v in vals if v <= cur) / len(vals) * 100, 1)


def _window_slice(dates: List[date], vals: List[float],
                  years: Optional[int]) -> List[float]:
    """按自然年切窗口，不按「最近 N 个交易日」

    按交易日切会有个隐蔽问题：如果序列中间有缺口（上游漏数、标的停牌），
    「最近 750 个交易日」实际可能跨了四五年，窗口标签就撒谎了。按日期切，
    缺口只会让样本变少，而样本不足会被 min_rows 拦下来。
    """
    if years is None:
        return list(vals)
    cutoff = dates[-1] - timedelta(days=365 * years)
    return list(vals[bisect_left(dates, cutoff):])


def _percentile_windows(dates: List[date], vals: List[float]) -> List[dict]:
    cur = vals[-1]
    out = []
    for key, label, years, min_rows in _WINDOWS:
        sub = _window_slice(dates, vals, years)
        if len(sub) < min_rows:
            out.append({
                "key": key, "label": label, "value": None, "rows": len(sub),
                "reason": f"{label}只有 {len(sub)} 个交易日，不足 {min_rows}，分位无意义",
            })
        else:
            out.append({
                "key": key, "label": label, "value": _percentile(sub, cur),
                "rows": len(sub), "reason": None,
            })
    return out


def _pick(windows: List[dict], key: str) -> Optional[float]:
    """取指定窗口的分位；该窗口样本不足时退到下一个更长的窗口

    退化而不是直接返回 None：新标的（创业板指只有 2010 年至今）在长窗口上必然
    不足，但它的近3年分位是真实可用的，因为拿不到 5 年就整个不给结论太浪费。
    """
    order = [w[0] for w in _WINDOWS]
    if key not in order:
        return None
    for k in order[order.index(key):]:
        for w in windows:
            if w["key"] == k and w["value"] is not None:
                return w["value"]
    # 长窗口都不够就往回找短窗口
    for k in reversed(order[:order.index(key)]):
        for w in windows:
            if w["key"] == k and w["value"] is not None:
                return w["value"]
    return None


def _downsample(vals: List[float], target: int = _SPARK_POINTS) -> List[float]:
    if len(vals) <= target:
        return [round(v, 4) for v in vals]
    step = len(vals) / target
    out = [vals[int(i * step)] for i in range(target)]
    out[-1] = vals[-1]          # 末点必须是真的最新值，不能被抽样抹掉
    return [round(v, 4) for v in out]


# ── 两融同步 ──────────────────────────────────────────────────────────

async def _fetch_margin_page(client: httpx.AsyncClient, page: int) -> Tuple[List[dict], int, int]:
    params = {
        "reportName": _MARGIN_REPORT,
        "columns": "ALL",
        "pageNumber": str(page),
        "pageSize": str(_PAGE_SIZE),
        "sortColumns": "DIM_DATE",
        "sortTypes": "-1",
    }
    resp = await client.get(MACRO_REPORT_BASE, params=params, headers=_HEADERS)
    resp.raise_for_status()
    body = resp.json()
    # 报表名写错时上游返回 200 + success:false，不显式检查会静默当成没数据
    if not body.get("success"):
        raise ValueError(f"{_MARGIN_REPORT} 上游拒绝：{body.get('message') or '未知原因'}")
    result = body.get("result") or {}
    return (result.get("data") or [], int(result.get("count") or 0),
            int(result.get("pages") or 1))


def _f(row: dict, field: str) -> Optional[float]:
    v = row.get(field)
    if v in (None, "", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_margin(row: dict) -> Optional[dict]:
    raw = str(row.get("DIM_DATE") or "")[:10]
    rz = _f(row, "RZYE")
    if not raw or rz is None:
        return None
    try:
        d = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None
    return {
        "trade_date": d,
        "rz_ye": rz,
        "rz_ye_pct": _f(row, "RZYEZB"),
        "rq_ye": _f(row, "RQYE"),
        "rzrq_ye": _f(row, "RZRQYE"),
        "ltsz": _f(row, "LTSZ"),
        "hs300_close": _f(row, "NEW"),
    }


async def _save_margin(rows: List[dict]) -> int:
    if not rows:
        return 0
    async with async_session() as session:
        for i in range(0, len(rows), 500):
            stmt = mysql_insert(MarginDaily).values(rows[i:i + 500])
            stmt = stmt.on_duplicate_key_update(
                rz_ye=stmt.inserted.rz_ye,
                rz_ye_pct=stmt.inserted.rz_ye_pct,
                rq_ye=stmt.inserted.rq_ye,
                rzrq_ye=stmt.inserted.rzrq_ye,
                ltsz=stmt.inserted.ltsz,
                hs300_close=stmt.inserted.hs300_close,
            )
            await session.execute(stmt)
        await session.commit()
    return len(rows)


async def _margin_last_date() -> Optional[date]:
    async with async_session() as session:
        return await session.scalar(select(sa_func.max(MarginDaily.trade_date)))


async def sync_margin(force: bool = False) -> dict:
    """两融余额落库

    首次（或 force）拉全历史，之后只拉第一页。上游按日期倒序返回，一页 800 条
    覆盖三年多，漏跑几周也补得回来，不会留缺口。
    """
    expected = benchmark_service.expected_trade_date(close_hour=_MARGIN_PUBLISH_HOUR)
    last = await _margin_last_date()
    if not force and last and last >= expected:
        return {"status": "fresh", "last_date": str(last)}

    full = force or last is None
    rows: List[dict] = []
    raw_count = 0
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT * 3) as client:
        page = 1
        while True:
            data, count, pages = await _fetch_margin_page(client, page)
            raw_count += len(data)
            rows += [r for r in (_parse_margin(x) for x in data) if r]
            if not full or page >= min(pages, _MAX_PAGES):
                break
            page += 1

    # 全量拉取必须对上上游自己报的 count。上游超过 800 会静默截断，
    # 拿被截断的历史算分位会得出完全错误的结论（见模块顶部注释）
    if full and raw_count != count:
        raise ValueError(
            f"{_MARGIN_REPORT} 分页不完整：上游 count={count}，实际取到 {raw_count} 条，"
            f"疑似被静默截断，拒绝用残缺历史算分位"
        )
    if not rows:
        raise ValueError(f"{_MARGIN_REPORT} 未返回有效数据")

    saved = await _save_margin(rows)
    newest = max(r["trade_date"] for r in rows)
    result = {
        "status": "synced", "rows": saved, "scope": "full" if full else "recent",
        "last_date": str(newest),
    }
    warn = benchmark_service.staleness(newest, "两融余额",
                                       close_hour=_MARGIN_PUBLISH_HOUR)
    if warn:
        result["warning"] = warn
    return result


# ── 读取 ──────────────────────────────────────────────────────────────

async def _index_series(symbol: str) -> Tuple[List[date], List[float]]:
    async with async_session() as session:
        rows = (await session.execute(
            select(IndexQuote.trade_date, IndexQuote.close)
            .where(IndexQuote.code == symbol)
            .order_by(IndexQuote.trade_date)
        )).all()
    return [r[0] for r in rows], [float(r[1]) for r in rows]


async def _margin_series() -> List[Tuple[date, float, Optional[float], Optional[float]]]:
    async with async_session() as session:
        rows = (await session.execute(
            select(MarginDaily.trade_date, MarginDaily.rz_ye,
                   MarginDaily.rz_ye_pct, MarginDaily.hs300_close)
            .order_by(MarginDaily.trade_date)
        )).all()
    return [(r[0], float(r[1]), r[2], r[3]) for r in rows]


def _index_block(key: str, dates: List[date], closes: List[float]) -> dict:
    windows = _percentile_windows(dates, closes)
    high = max(closes)
    high_date = dates[closes.index(high)]
    close = closes[-1]
    # 前一交易日涨跌，给面板上「今天」这一列用
    prev = closes[-2] if len(closes) >= 2 else None
    return {
        "key": key,
        "name": benchmark_service.name_of(key),
        "symbol": benchmark_service.BENCHMARKS[key]["symbol"],
        "date": str(dates[-1]),
        "close": round(close, 2),
        "pct": round((close / prev - 1) * 100, 2) if prev else None,
        "percentiles": windows,
        "verdict_pctl": _pick(windows, _VERDICT_WINDOW),
        "high": round(high, 2),
        "high_date": str(high_date),
        "from_high": round((close / high - 1) * 100, 2),
        "spark": _downsample(_window_slice(dates, closes, 3)),
    }


def _margin_block(series) -> dict:
    dates = [r[0] for r in series]
    rz = [r[1] for r in series]
    # 占比缺失的行不能直接跳过——跳过会让日期数组和数值数组错位，
    # 窗口切片按日期二分就会切错。只在算占比分位时单独过滤出对齐的两个数组
    pct_pairs = [(d, p) for d, _, p, _ in series if p is not None]
    pct_dates = [p[0] for p in pct_pairs]
    pct_vals = [p[1] for p in pct_pairs]

    block = {
        "date": str(dates[-1]),
        "rz_ye": rz[-1],
        "rz_ye_yi": round(rz[-1] / 1e8, 1),          # 亿元，界面上好读
        "hs300_close": series[-1][3],
        "rows": len(series),
        "first_date": str(dates[0]),
    }
    if pct_vals:
        windows = _percentile_windows(pct_dates, pct_vals)
        peak = max(pct_vals)
        block.update({
            "pct": round(pct_vals[-1], 3),
            "percentiles": windows,
            "verdict_pctl": _pick(windows, _VERDICT_WINDOW),
            "peak_pct": round(peak, 3),
            "peak_date": str(pct_dates[pct_vals.index(peak)]),
            "spark": _downsample(_window_slice(pct_dates, pct_vals, 3)),
            # 绝对额分位只作对照，不参与判定。摆出来是为了让「为什么用占比」
            # 这个取舍在界面上可验证，而不是只写在代码注释里
            "abs_pctl_all": _percentile(rz, rz[-1]),
        })
    else:
        block.update({
            "pct": None, "percentiles": [], "verdict_pctl": None,
            "reason": "上游未提供融资余额占流通市值比（RZYEZB），无法算杠杆分位",
        })
    return block


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _verdict(indices: List[dict], margin: dict, stale: List[str]) -> dict:
    """点位 × 杠杆 的交叉判定

    四个格子里最反直觉的是「点位不高但杠杆很高」：融资盘在下跌里没撤，浮亏还挂着，
    这种结构下任何一次下跌都容易触发强平连锁，比双高更脆。所以它不能因为
    「点位不高」就被判成低风险。

    stale 非空时直接不给操作结论。这一条是踩过坑掉回来加的：一开始陈旧告警只放在
    notes 里，判定句照样输出「双高，不加仓，把利润挪进短债」——人读的是加粗的
    判定框，旁边的灰色小字拦不住任何人。拿半年前的数算出的分位看起来完全正常，
    这比缺数危险得多。
    """
    idx_vals = [i["verdict_pctl"] for i in indices if i["verdict_pctl"] is not None]
    idx = round(_median(idx_vals), 1) if idx_vals else None
    mgn = margin.get("verdict_pctl")
    label = _WINDOWS[[w[0] for w in _WINDOWS].index(_VERDICT_WINDOW)][1]

    out = {"index_pctl": idx, "margin_pctl": mgn, "window": label}
    if stale:
        out.update({"level": "stale", "text": (
            f"⚠️ {'、'.join(stale)} 的数据已陈旧，算出的分位不代表今天。"
            f"本面板今天不给位置结论，先把同步修好")})
        return out
    if idx is None and mgn is None:
        out.update({"level": "unknown",
                    "text": "点位和杠杆分位都算不出来，本面板今天不提供位置判断"})
        return out
    if idx is None or mgn is None:
        missing = "点位" if idx is None else "杠杆"
        have = mgn if idx is None else idx
        out.update({
            "level": "partial",
            "text": (f"{missing}分位缺失，只有另一条腿（{have} 分位）。"
                     f"位置判断需要两条腿都在，先补数据再看结论"),
        })
        return out

    if idx >= _PCTL_HIGH and mgn >= _PCTL_HIGH:
        out.update({"level": "extreme", "text": (
            f"**点位 {idx} 分位 + 杠杆 {mgn} 分位，双高**。这是 2015 年 6 月那种组合："
            "上涨本身是被融资盘推的，回撤会自我放大。不加仓，考虑把利润挪进短债")})
    elif mgn >= _PCTL_HIGH and idx <= 50:
        out.update({"level": "high", "text": (
            f"⚠️ 点位只有 {idx} 分位、杠杆却在 {mgn} 分位 —— 融资盘在下跌里没撤，"
            "浮亏还挂着。这种结构比双高更脆，抢底前先看杠杆有没有出清")})
    elif idx >= _PCTL_HIGH:
        out.update({"level": "high", "text": (
            f"点位 {idx} 分位偏高（杠杆 {mgn} 分位尚可）。价格已经透支了不少，"
            "新钱按计划金额买，别加码")})
    elif mgn >= _PCTL_HIGH:
        out.update({"level": "high", "text": (
            f"杠杆 {mgn} 分位偏高（点位 {idx} 分位）。融资盘拥挤，波动会被放大")})
    elif idx <= _PCTL_LOW and mgn <= _PCTL_LOW:
        out.update({"level": "low", "text": (
            f"点位 {idx} 分位 + 杠杆 {mgn} 分位，双低。价格在历史下沿且没人用杠杆，"
            "这是分批建仓的位置")})
    else:
        out.update({"level": "mid", "text": (
            f"点位 {idx} 分位、杠杆 {mgn} 分位，都在中段。位置本身不给操作信号，"
            "按既定计划推进")})
    return out


async def market_position() -> dict:
    """面板数据：四个指数的点位分位 + 两融杠杆分位 + 交叉判定

    任何一路取不到都不抛异常，而是把原因塞进 notes 让界面显示出来。
    整个面板的价值就是「告诉你现在在哪」，静默少一半数据比报错更糟——
    界面看起来正常，人却在拿半个结论做决定。
    """
    notes: List[str] = []
    # stale 只存标的名，给判定句用。分位算的是「今天在历史上的位置」，而这个
    # 「今天」是库里最后一行 —— 那一行陈旧时算出的分位看起来完全正常，所以
    # 判定句必须自己知道数据不新鲜，不能靠 notes 里的小字兜底
    stale: List[str] = []
    indices: List[dict] = []
    for key in PANEL_INDICES:
        conf = benchmark_service.BENCHMARKS.get(key)
        if not conf:
            notes.append(f"未登记的指数 {key}，已跳过")
            continue
        name = conf["name"]
        dates, closes = await _index_series(conf["symbol"])
        if not dates:
            notes.append(f"{name} 本地无点位，先跑 scripts/sync_benchmark.py")
            continue
        warn = benchmark_service.staleness(dates[-1], name)
        if warn:
            notes.append(warn)
            stale.append(name)
        indices.append(_index_block(key, dates, closes))

    series = await _margin_series()
    if series:
        margin = _margin_block(series)
        warn = benchmark_service.staleness(series[-1][0], "两融余额",
                                          close_hour=_MARGIN_PUBLISH_HOUR)
        if warn:
            notes.append(warn)
            stale.append("两融余额")
        if margin.get("reason"):
            notes.append(margin["reason"])
        abs_all = margin.get("abs_pctl_all")
        pct_all = next((w["value"] for w in margin.get("percentiles") or []
                        if w["key"] == "all"), None)
        # 两个口径差得多的时候说一声，否则看到别处引用「融资余额创历史新高」
        # 会以为我们这个面板算错了
        if abs_all is not None and pct_all is not None and abs(abs_all - pct_all) >= 5:
            notes.append(
                f"融资余额绝对额处于全历史 {abs_all} 分位，占流通市值比只有 {pct_all} "
                f"分位。差异来自流通市值本身的膨胀，本面板按占比判定")
    else:
        margin = {"date": None, "pct": None, "percentiles": [], "verdict_pctl": None}
        notes.append("本地无两融数据，先跑 scripts/sync_margin.py")

    # 双源对账：上游 NEW 字段应该等于同日 sh000300 收盘
    hs300 = next((i for i in indices if i["key"] == "hs300"), None)
    up = margin.get("hs300_close")
    if hs300 and up and hs300["date"] == margin.get("date"):
        if abs(hs300["close"] - up) > max(1.0, up * 0.001):
            notes.append(
                f"对账不一致：两融接口给的沪深300 收盘 {up}，"
                f"index_quote 里是 {hs300['close']}，至少有一边的数据是错的")

    dates_seen = [i["date"] for i in indices] + ([margin["date"]] if margin.get("date") else [])
    return {
        "updated_at": max(dates_seen) if dates_seen else None,
        "window_labels": [{"key": k, "label": lb} for k, lb, _, _ in _WINDOWS],
        "indices": indices,
        "margin": margin,
        "verdict": _verdict(indices, margin, stale),
        "notes": notes,
    }


async def sync(force: bool = False) -> dict:
    """面板依赖的两路数据一起同步：指数点位 + 两融余额

    指数那路直接复用 benchmark_service.sync，它自带停更告警。
    """
    out: Dict[str, dict] = {}
    failed: List[str] = []
    for key in PANEL_INDICES:
        try:
            out[key] = await benchmark_service.sync(key, force=force)
            if out[key].get("status") == "failed":
                failed.append(key)
        except Exception as e:
            out[key] = {"status": "failed", "message": str(e)}
            failed.append(key)
    try:
        out["margin"] = await sync_margin(force=force)
    except Exception as e:
        out["margin"] = {"status": "failed", "message": str(e)}
        failed.append("margin")
    return {"results": out, "failed": failed}
