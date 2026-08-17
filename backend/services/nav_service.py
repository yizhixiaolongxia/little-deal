"""基金净值时间序列服务

对比面板和后续的组合级风险计算都要长历史净值。此前每次都实时穿透 pingzhongdata：
N 只基金就是 N 次串行请求，慢、吃上游限流、接口一抖就整块没数据。

这里加一层落库，读取策略是库优先：数据新鲜直接读库，过期才回源一次并落库。
两个降级方向都留了口子——上游挂了用库里的旧数据（标 stale），
库挂了退化成纯回源（与改造前行为一致），不会因为多了一层反而更脆。
"""
import asyncio
from datetime import date, datetime, time as dtime, timedelta
from typing import List, Optional

from sqlalchemy import func as sa_func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from ..database import async_session
from ..models import FundNav, FundNavSync, SimLot, Watchlist
from . import fund_service

# 净值在收盘后公布，20:00 之前当日净值大多还没出
_PUBLISH_HOUR = 20
# 「问过上游、它还没出」这个结论能信多久。净值是从 20:00 陆续公布到深夜的，
# 隔一会儿再问就可能已经有了。
# 必须短于 scheduler 的 RETRY_GAP（30 分钟）：否则调度器补跑那一轮会被这里判成
# 新鲜直接跳过，重试就成了空转，当晚晚些时候公布的净值依旧补不上。
_TRUST_WINDOW = timedelta(minutes=20)
_SAVE_CHUNK = 1000
# 批量同步时每只之间的间隔：pingzhongdata 是大响应，连续猛拉容易被掐
_SYNC_DELAY = 0.5


def _fnum(v) -> Optional[float]:
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _parse_date(raw) -> Optional[date]:
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


async def _safe(coro):
    """库不可用时不要连带整个接口失败，退化成纯回源"""
    try:
        return await coro
    except Exception:
        return None


# ── 新鲜度判定 ────────────────────────────────────────────────────────

def _expected_nav_date(now: datetime) -> date:
    """此刻「上游最多应该能给到哪一天」的净值

    20:00 之后算当日净值已公布，否则只能期望上一个工作日。
    法定节假日没法从星期几推断，那几天这个值会偏新一天，
    后果只是多回源一次、之后由 synced_at 兜住，不影响正确性。
    """
    d = now.date()
    if now.hour < _PUBLISH_HOUR:
        d -= timedelta(days=1)
    while d.weekday() >= 5:      # 周六周日没有净值
        d -= timedelta(days=1)
    return d


def _is_fresh(sync: Optional[FundNavSync], now: datetime) -> bool:
    """库里是否已经是上游能给的最新，满足其一即可：

    - last_date 追平了期望净值日；
    - 刚刚在期望净值日的公布时点之后问过上游，它就是还没出。
      QDII 净值滞后几个交易日是常态，只看 last_date 会永远判过期。

    第二条必须带时效，否则会把当晚晚些时候才公布的净值锁在门外一整天：
    expected 在次日 20:00 之前一直是前一天，「昨晚 20:30 问过」这个结论也就在整个
    次日白天持续成立，而 20:30 时上游往往只公布了一部分。真实踩过的后果是简报拿
    缺当日净值的数据算回撤，算出来偏小一半，且页面上看不出是错的。
    """
    if sync is None or not sync.last_date:
        return False
    expected = _expected_nav_date(now)
    if sync.last_date >= expected:
        return True
    cutoff = datetime.combine(expected, dtime(hour=_PUBLISH_HOUR))
    if sync.synced_at is None or sync.synced_at < cutoff:
        return False
    return now - sync.synced_at <= _TRUST_WINDOW


# ── 读写 ──────────────────────────────────────────────────────────────

async def _load_sync(code: str) -> Optional[FundNavSync]:
    async with async_session() as session:
        return await session.scalar(
            select(FundNavSync).where(FundNavSync.code == code)
        )


async def _load_series(code: str) -> List[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(FundNav.nav_date, FundNav.nav, FundNav.acc_nav)
            .where(FundNav.code == code)
            .order_by(FundNav.nav_date)
        )
    return [{"date": d, "nav": nav, "acc_nav": acc} for d, nav, acc in result.all()]


async def _save_series(code: str, points: List[dict]) -> int:
    """整批 upsert 并刷新同步水位，返回该基金落库后的总条数"""
    if not points:
        return 0

    async with async_session() as session:
        for i in range(0, len(points), _SAVE_CHUNK):
            values = [
                {"code": code, "nav_date": p["date"], "nav": p["nav"],
                 "acc_nav": p.get("acc_nav")}
                for p in points[i:i + _SAVE_CHUNK]
            ]
            stmt = mysql_insert(FundNav).values(values)
            # acc_nav 只有部分数据源提供，已有值别被后来的 NULL 冲掉
            stmt = stmt.on_duplicate_key_update(
                nav=stmt.inserted.nav,
                acc_nav=sa_func.coalesce(stmt.inserted.acc_nav, FundNav.acc_nav),
            )
            await session.execute(stmt)

        total = await session.scalar(
            select(sa_func.count()).select_from(FundNav).where(FundNav.code == code)
        )
        first = await session.scalar(
            select(sa_func.min(FundNav.nav_date)).where(FundNav.code == code)
        )
        last = await session.scalar(
            select(sa_func.max(FundNav.nav_date)).where(FundNav.code == code)
        )

        wm = mysql_insert(FundNavSync).values(
            code=code, first_date=first, last_date=last,
            rows_count=total or 0, synced_at=datetime.now(),
        )
        wm = wm.on_duplicate_key_update(
            first_date=wm.inserted.first_date,
            last_date=wm.inserted.last_date,
            rows_count=wm.inserted.rows_count,
            synced_at=wm.inserted.synced_at,
        )
        await session.execute(wm)
        await session.commit()

    return total or 0


async def _fetch_upstream(code: str) -> List[dict]:
    """回源 pingzhongdata 取全历史（单位净值 + 累计净值）"""
    raw = await fund_service.fetch_history_full(code)
    points = []
    for item in raw.get("Data", {}).get("LSJZList", []):
        d = _parse_date(item.get("FSRQ"))
        nav = _fnum(item.get("DWJZ"))
        if d and nav is not None:
            points.append({"date": d, "nav": nav, "acc_nav": _fnum(item.get("LJJZ"))})
    return points


def _to_payload(code: str, series: List[dict], from_cache: bool, stale: bool) -> dict:
    """组装成与 lsjz 一致的结构：前端只认 Data.LSJZList，多出来的字段是给调用方看的"""
    items = [
        {
            "FSRQ": str(p["date"]),
            "DWJZ": f"{p['nav']}",
            "LJJZ": "" if p.get("acc_nav") is None else f"{p['acc_nav']}",
        }
        for p in series
    ]
    return {
        "Data": {"LSJZList": items},
        "code": code,
        "rows": len(items),
        "from_cache": from_cache,
        "stale": stale,
    }


# ── 对外接口 ──────────────────────────────────────────────────────────

async def get_history_full(code: str, force: bool = False) -> dict:
    """完整历史净值，格式兼容原 lsjz 响应

    库优先。stale=True 表示上游这次没拿到、返回的是库里的旧数据，
    调用方要据此决定能不能拿来做决策。
    """
    now = datetime.now()
    sync = await _safe(_load_sync(code))

    if not force and _is_fresh(sync, now):
        series = await _safe(_load_series(code))
        if series:
            return _to_payload(code, series, from_cache=True, stale=False)

    try:
        points = await _fetch_upstream(code)
    except Exception:
        points = []

    if points:
        # 落库失败不影响本次返回，pingzhongdata 给的就是完整序列
        try:
            await _save_series(code, points)
        except Exception:
            pass
        return _to_payload(code, points, from_cache=False, stale=False)

    series = await _safe(_load_series(code))
    if series:
        return _to_payload(code, series, from_cache=True, stale=True)

    raise ValueError(f"基金 {code} 净值获取失败：上游不可用且本地无历史")


async def get_nav_series(code: str, start=None, end=None) -> List[dict]:
    """干净格式的净值序列，给组合级指标与回测用（只读库，不回源）

    start/end 接受 date 或 'YYYY-MM-DD' 字符串，解不出来当没传。
    acc_nav 一并返回：算收益率要用它，否则分红会被当成亏损。
    """
    lo = start if isinstance(start, date) else _parse_date(start) if start else None
    hi = end if isinstance(end, date) else _parse_date(end) if end else None

    async with async_session() as session:
        q = select(FundNav.nav_date, FundNav.nav, FundNav.acc_nav).where(FundNav.code == code)
        if lo:
            q = q.where(FundNav.nav_date >= lo)
        if hi:
            q = q.where(FundNav.nav_date <= hi)
        result = await session.execute(q.order_by(FundNav.nav_date))
    return [{"date": str(d), "nav": nav, "acc_nav": acc} for d, nav, acc in result.all()]


async def tracked_codes() -> List[str]:
    """需要长期维护净值历史的基金：自选 + 模拟盘持仓"""
    async with async_session() as session:
        wl = await session.execute(select(Watchlist.fund_code))
        lots = await session.execute(
            select(SimLot.code).where(SimLot.asset_type == "fund").distinct()
        )
    codes = {c for (c,) in wl.all()} | {c for (c,) in lots.all()}
    return sorted(codes)


async def sync_code(code: str, force: bool = False) -> dict:
    """同步单只基金的全历史净值"""
    now = datetime.now()
    sync = await _safe(_load_sync(code))
    if not force and _is_fresh(sync, now):
        return {
            "code": code, "status": "fresh",
            "rows": sync.rows_count, "last_date": str(sync.last_date),
        }

    try:
        points = await _fetch_upstream(code)
    except Exception as e:
        return {"code": code, "status": "failed", "message": str(e)}
    if not points:
        return {"code": code, "status": "failed", "message": "上游未返回净值数据"}

    total = await _save_series(code, points)
    return {
        "code": code, "status": "synced",
        "rows": total, "last_date": str(points[-1]["date"]),
    }


async def sync_codes(codes: List[str], force: bool = False) -> dict:
    """串行同步多只。并发拉 pingzhongdata 会被上游掐，串行加间隔反而更快拿全"""
    results = []
    for i, code in enumerate(codes):
        if i:
            await asyncio.sleep(_SYNC_DELAY)
        results.append(await sync_code(code, force=force))
    counts = {"synced": 0, "fresh": 0, "failed": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"total": len(results), **counts, "items": results}


async def lagging_codes(now: Optional[datetime] = None) -> List[str]:
    """tracked 基金里 last_date 还没追平期望净值日的那些

    给调度器判断「今天的净值到位了没」用，故意不看 synced_at：那个字段的语义是
    「问过上游了」，而这里要问的是「数据真的到手了没」。把这两件事当成一回事，
    就会出现同步任务报成功、简报却拿着缺当日净值的数据算回撤。
    """
    now = now or datetime.now()
    expected = _expected_nav_date(now)
    codes = await tracked_codes()
    if not codes:
        return []
    async with async_session() as session:
        result = await session.execute(
            select(FundNavSync.code, FundNavSync.last_date)
            .where(FundNavSync.code.in_(codes))
        )
        level = dict(result.all())
    return sorted(c for c in codes
                  if level.get(c) is None or level[c] < expected)


async def coverage() -> dict:
    """落库覆盖情况：哪些基金有多少条净值、到哪天、还有哪些完全没数据"""
    async with async_session() as session:
        result = await session.execute(select(FundNavSync).order_by(FundNavSync.code))
        rows = list(result.scalars().all())

    now = datetime.now()
    items = [
        {
            "code": r.code,
            "rows": r.rows_count,
            "first_date": str(r.first_date) if r.first_date else None,
            "last_date": str(r.last_date) if r.last_date else None,
            "synced_at": r.synced_at.strftime("%Y-%m-%d %H:%M") if r.synced_at else None,
            "fresh": _is_fresh(r, now),
        }
        for r in rows
    ]
    synced = {r.code for r in rows}
    tracked = await tracked_codes()
    return {
        "expected_nav_date": str(_expected_nav_date(now)),
        "total": len(items),
        "stale": sum(1 for i in items if not i["fresh"]),
        "missing": [c for c in tracked if c not in synced],
        "items": items,
    }
