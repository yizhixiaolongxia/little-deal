"""东财股票行情服务

按 secid 前缀路由（与原始项目一致）：
- 0./1. 开头且为 6 位数字 → 沪深 A 股，走 SHSZQuoteSnapshot 快照接口
- 其他（港股 116./美股 105/106/107. 等）→ 走 push2his K 线接口取最近交易日涨跌幅
"""
import asyncio
import re
import time
import httpx
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select, func as sa_func
from sqlalchemy.dialects.mysql import insert as mysql_insert

from ..config import (
    STOCK_SNAPSHOT_BASE,
    STOCK_KLINE_BASE,
    STOCK_LIST_BASE,
    STOCK_LIST_FALLBACK_BASE,
    STOCK_ULIST_BASE,
    STOCK_ULIST_FALLBACK_BASE,
    REQUEST_TIMEOUT,
)
from ..database import async_session
from ..models import StockDaily

_HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}


def _fmt_date(raw: str) -> str:
    """20260430 -> 2026-04-30；其他原样返回"""
    s = str(raw or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


async def _fetch_a_snapshot(client: httpx.AsyncClient, code: str) -> Optional[dict]:
    """拉取单只 A 股最近交易日快照"""
    params = {"id": code, "_": str(int(time.time() * 1000))}
    try:
        resp = await client.get(STOCK_SNAPSHOT_BASE, params=params, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    rq = data.get("realtimequote") if isinstance(data, dict) else None
    if not rq:
        return None
    pct_raw = str(rq.get("zdf", "")).replace("%", "").strip()
    try:
        pct = float(pct_raw)
    except (ValueError, TypeError):
        pct = None
    return {"pct": pct, "date": _fmt_date(rq.get("date", ""))}


async def _fetch_kline(client: httpx.AsyncClient, secid: str) -> Optional[dict]:
    """拉取单只非 A 股最近一个交易日 K 线，作为 fallback"""
    params = {
        "secid": secid,
        "fields1": "f1",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59",
        "klt": "101",
        "fqt": "1",
        "end": "20500101",
        "lmt": "1",
        "_": str(int(time.time() * 1000)),
    }
    try:
        resp = await client.get(STOCK_KLINE_BASE, params=params, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    klines = (data.get("data") or {}).get("klines") if isinstance(data, dict) else None
    if not klines:
        return None
    last = str(klines[-1]).split(",")
    if len(last) < 9:
        return None
    date = last[0] or ""
    try:
        pct = float(last[8])
    except (ValueError, TypeError):
        pct = None
    return {"pct": pct, "date": date}


async def _fetch_single(client: httpx.AsyncClient, secid: str) -> Optional[dict]:
    """根据 secid 前缀路由"""
    dot = secid.find(".")
    market = secid[:dot] if dot > 0 else ""
    code = secid[dot + 1:] if dot > 0 else secid
    if market in ("0", "1") and re.fullmatch(r"\d{6}", code):
        return await _fetch_a_snapshot(client, code)
    return await _fetch_kline(client, secid)


async def fetch_stock_quotes(secids: List[str]) -> dict:
    """批量获取持仓股票最近交易日涨跌幅，返回 { code: { pct, date } }"""
    if not secids:
        return {}

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        results = await asyncio.gather(
            *[_fetch_single(client, sid) for sid in secids]
        )

    result = {}
    for secid, quote in zip(secids, results):
        if not quote:
            continue
        dot = secid.find(".")
        code = secid[dot + 1:] if dot > 0 else secid
        result[code] = {"code": code, **quote}
    return result


def _secid_from_code(code: str) -> str:
    """6 位 A 股/ETF 代码 -> 东财 secid（1=沪市，0=深市/北交所）"""
    if code.startswith(("5", "6", "9")):
        return f"1.{code}"
    return f"0.{code}"


async def fetch_quotes_by_codes(codes: List[str]) -> dict:
    """批量获取最新价/涨跌幅/名称，返回 { code: {code, name, price, pct} }

    与 fetch_stock_quotes 的区别：这里需要成交价和名称（模拟盘下单/估值），
    所以走 ulist 批量接口一次拉全；主节点失败时降级到延迟行情节点。
    """
    if not codes:
        return {}

    params = {
        "fltt": "2",
        "invt": "2",
        "np": "1",
        "fields": "f2,f3,f12,f14",
        "secids": ",".join(_secid_from_code(c) for c in codes),
        "_": str(int(time.time() * 1000)),
    }

    async def _get(base: str) -> dict:
        resp = await client.get(base, params=params, headers=_HEADERS)
        resp.raise_for_status()
        return resp.json()

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            data = await _get(STOCK_ULIST_BASE)
        except Exception:
            data = await _get(STOCK_ULIST_FALLBACK_BASE)

    diff = (data.get("data") or {}).get("diff") or []
    # 东财部分节点返回 dict（以下标为 key）而非 list
    if isinstance(diff, dict):
        diff = list(diff.values())

    result = {}
    for d in diff:
        code = str(d.get("f12") or "")
        if not code:
            continue
        result[code] = {
            "code": code,
            "name": d.get("f14") or "",
            "price": _num(d.get("f2")),
            "pct": _num(d.get("f3")),
        }
    return result


# ===== A 股全市场列表（基本面指标） =====
# 东财 clist 字段映射：f12 代码 f14 名称 f100 行业 f2 最新价 f3 涨跌幅
# f20 总市值 f9 市盈率(动) f23 市净率 f37 ROE(加权) f41 营收同比
# f46 净利同比 f49 毛利率 f57 资产负债率 f61 每股经营现金流 f129 净利率
# f124 行情时间戳（用于确定落库的交易日）
_LIST_FIELDS = "f12,f14,f100,f2,f3,f20,f9,f23,f37,f41,f46,f49,f57,f61,f129,f124"
# 沪深主板 + 创业板 + 科创板 + 北交所
_LIST_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
_LIST_PAGE_SIZE = 100  # 东财单页上限
_LIST_CACHE_TTL = 600  # 秒

_list_cache = {"ts": 0.0, "data": None}
_list_lock = asyncio.Lock()

_CST = timezone(timedelta(hours=8))
_SAVE_CHUNK = 500  # 单次 upsert 行数，避免 SQL 过长
# 落库时随交易日一起覆盖的指标列（与 _parse_list_row 对齐）
_DAILY_COLS = (
    "name", "board", "industry", "price", "pct", "total_mv", "pe", "pb", "roe",
    "gross_margin", "net_margin", "revenue_yoy", "profit_yoy", "debt_ratio", "ocf_ps",
)


def _num(v):
    """东财缺失值为 '-'，统一转 float 或 None"""
    if isinstance(v, (int, float)):
        return v
    return None


def _board_from_code(code: str) -> str:
    """根据股票代码前缀判断板块"""
    if code.startswith("688") or code.startswith("689"):
        return "科创板"
    if code.startswith("60"):
        return "沪市主板"
    if code.startswith("00"):
        return "深市主板"
    if code.startswith("30"):
        return "创业板"
    if code.startswith(("8", "4", "920")):
        return "北交所"
    return "其他"


def _parse_list_row(d: dict) -> dict:
    code = d.get("f12", "")
    return {
        "code": code,
        "quote_ts": d.get("f124") if isinstance(d.get("f124"), int) else None,
        "name": d.get("f14", ""),
        "board": _board_from_code(code),
        "industry": d.get("f100") if isinstance(d.get("f100"), str) else "",
        "price": _num(d.get("f2")),
        "pct": _num(d.get("f3")),
        "total_mv": _num(d.get("f20")),
        "pe": _num(d.get("f9")),
        "pb": _num(d.get("f23")),
        "roe": _num(d.get("f37")),
        "gross_margin": _num(d.get("f49")),
        "net_margin": _num(d.get("f129")),
        "revenue_yoy": _num(d.get("f41")),
        "profit_yoy": _num(d.get("f46")),
        "debt_ratio": _num(d.get("f57")),
        "ocf_ps": _num(d.get("f61")),
    }


def _is_delisted(row: dict) -> bool:
    """过滤退市股：无价无市值（已退市）或名称含“退”（退市整理期）；停牌股仍有市值，不会被误杀"""
    if row["price"] is None and row["total_mv"] is None:
        return True
    return "退" in (row["name"] or "")


async def _fetch_list_page(client: httpx.AsyncClient, pn: int, base: str = STOCK_LIST_BASE):
    """拉取列表第 pn 页，返回 (total, rows)"""
    params = {
        "pn": str(pn),
        "pz": str(_LIST_PAGE_SIZE),
        "po": "0",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": _LIST_FS,
        "fields": _LIST_FIELDS,
        "_": str(int(time.time() * 1000)),
    }
    resp = await client.get(base, params=params, headers=_HEADERS)
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    return data.get("total", 0), data.get("diff") or []


async def fetch_stock_list(force: bool = False) -> dict:
    """全量 A 股列表（含基本面指标），内存缓存 10 分钟，拉取成功后落库"""
    if not force and _list_cache["data"] and time.time() - _list_cache["ts"] < _LIST_CACHE_TTL:
        return _list_cache["data"]

    async with _list_lock:
        # 双重检查：等锁期间可能已被其他请求刷新
        if not force and _list_cache["data"] and time.time() - _list_cache["ts"] < _LIST_CACHE_TTL:
            return _list_cache["data"]

        try:
            rows = await _fetch_all_rows()
        except Exception:
            rows = []

        # 两个行情节点都拉不到时降级到最近一个交易日的落库快照
        if not rows:
            cached = await _load_latest_snapshot()
            if cached:
                return cached
            raise ValueError("股票列表获取失败，行情接口不可用")

        trade_date = _trade_date_from_rows(rows)
        for r in rows:
            r.pop("quote_ts", None)

        result = {
            "total": len(rows),
            "updated_at": int(time.time()),
            "items": rows,
        }
        if trade_date:
            result["trade_date"] = str(trade_date)
        _list_cache["ts"] = time.time()
        _list_cache["data"] = result

        # 当日尚未开盘时不落库，写库失败也不影响接口返回
        if trade_date:
            try:
                await _save_daily(trade_date, rows)
            except Exception:
                pass
        return result


async def _fetch_all_rows() -> List[dict]:
    """分页拉取全市场个股并过滤退市股"""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        # 主节点失败（限流/断连）时降级到延迟行情节点
        base = STOCK_LIST_BASE
        try:
            total, first = await _fetch_list_page(client, 1, base)
        except Exception:
            base = STOCK_LIST_FALLBACK_BASE
            total, first = await _fetch_list_page(client, 1, base)
        pages = (total + _LIST_PAGE_SIZE - 1) // _LIST_PAGE_SIZE
        sem = asyncio.Semaphore(8)

        async def _worker(pn: int):
            async with sem:
                try:
                    _, diff = await _fetch_list_page(client, pn, base)
                    return diff
                except Exception:
                    return []

        rest = await asyncio.gather(*[_worker(pn) for pn in range(2, pages + 1)])

    rows = [r for r in (_parse_list_row(d) for d in first) if not _is_delisted(r)]
    for diff in rest:
        rows.extend(r for r in (_parse_list_row(d) for d in diff) if not _is_delisted(r))
    return rows


def _trade_date_from_rows(rows: List[dict]) -> Optional[date]:
    """取全市场最新行情时间戳对应的日期（北京时间），未开盘返回 None

    f124 是行情时间戳而非交易日：开盘前它已跳到新的自然日，但全市场都还没有
    价格，此时落库会写出一批空价格的行，并让同一份收盘数据占用两个交易日。
    所以先看有价格的行占比，过低就判定当日尚未开盘，本次不落库。
    周末/节假日刷新时时间戳仍停在最后一个交易日收盘，覆盖当天即可。
    """
    quoted = sum(1 for r in rows if r.get("price") is not None)
    if quoted < len(rows) * 0.5:
        return None
    ts = max((r.get("quote_ts") or 0 for r in rows), default=0)
    if not ts:
        return datetime.now(_CST).date()
    return datetime.fromtimestamp(ts, tz=_CST).date()


async def _save_daily(trade_date: date, rows: List[dict]) -> None:
    """整批 upsert 当日快照（同一交易日重复刷新则覆盖）"""
    async with async_session() as session:
        for i in range(0, len(rows), _SAVE_CHUNK):
            values = [
                {"code": r["code"], "trade_date": trade_date,
                 **{c: r.get(c) for c in _DAILY_COLS}}
                for r in rows[i:i + _SAVE_CHUNK]
            ]
            stmt = mysql_insert(StockDaily).values(values)
            stmt = stmt.on_duplicate_key_update(
                **{c: stmt.inserted[c] for c in _DAILY_COLS}
            )
            await session.execute(stmt)
        await session.commit()


async def _load_latest_snapshot() -> Optional[dict]:
    """读最近一个交易日的落库快照，无数据或库不可用返回 None"""
    try:
        async with async_session() as session:
            latest = await session.scalar(select(sa_func.max(StockDaily.trade_date)))
            if not latest:
                return None
            result = await session.execute(
                select(StockDaily)
                .where(StockDaily.trade_date == latest)
                .order_by(StockDaily.code)
            )
            items = [
                {"code": o.code, **{c: getattr(o, c) for c in _DAILY_COLS}}
                for o in result.scalars()
            ]
    except Exception:
        return None

    # 收盘时间当作快照时间，避免前端把陈旧数据当成刚刷新的
    snap_ts = datetime(latest.year, latest.month, latest.day, 15, 0, tzinfo=_CST)
    return {
        "total": len(items),
        "updated_at": int(snap_ts.timestamp()),
        "trade_date": str(latest),
        "from_cache": True,
        "items": items,
    }
