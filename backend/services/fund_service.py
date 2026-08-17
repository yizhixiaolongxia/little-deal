"""天天基金/东财数据获取服务"""
import asyncio
import re
import json
import time
import httpx
from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, func as sa_func
from sqlalchemy.dialects.mysql import insert as mysql_insert

from ..config import (
    FUNDGZ_BASE, HISTORY_BASE, PINGZHONG_BASE,
    HOLDINGS_BASE, RANK_BASE, SEARCH_BASE, REQUEST_TIMEOUT,
)
from ..database import async_session
from ..models import FundDaily


async def fetch_realtime(code: str) -> dict:
    """请求 fundgz 获取实时估值，解析 JSONP 文本。

    盘后正式净值已公布（lsjz 最新净值日期新于估值基准日 jzrq）时，
    用正式净值/正式日增长率覆盖估算值，并标记 settled=True，
    避免卡片展示过时的盘中估算涨跌（与天天基金对齐）。

    当 fundgz 接口不可用（301/404/解析失败）时，回退到历史净值接口
    构造基本数据，确保前端卡片仍能渲染。
    """
    data = None
    url = f"{FUNDGZ_BASE}/{code}.js"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(url)
            # 3xx 重定向说明基金不在估值服务中
            if resp.status_code >= 300:
                raise ValueError(f"fundgz 返回 {resp.status_code}")
            resp.raise_for_status()
            text = resp.text
        # 解析 jsonpgz({...});
        match = re.search(r"jsonpgz\s*\(\s*(\{.*?\})\s*\)", text, re.DOTALL)
        if not match:
            raise ValueError(f"无法解析 fundgz 响应: {code}")
        data = json.loads(match.group(1))
    except Exception:
        pass

    # 补拉正式净值（fundgz 失败时作为主数据源，成功时做盘后覆盖）
    try:
        latest = await fetch_latest_nav(code)
        if data is None:
            # fundgz 不可用，用历史净值构造兼容格式
            data = {
                "fundcode": code,
                "name": "",
                "dwjz": latest.get("dwjz", ""),
                "gsz": latest.get("dwjz", ""),
                "gszzl": latest.get("jzzzl", ""),
                "jzrq": latest.get("jzrq", ""),
                "settled": True,
            }
        else:
            latest_date = latest.get("jzrq", "")
            if latest_date and latest_date > data.get("jzrq", ""):
                data["dwjz"] = latest.get("dwjz") or data.get("dwjz")
                data["jzrq"] = latest_date
                data["gsz"] = latest.get("dwjz") or data.get("gsz")
                data["gszzl"] = latest.get("jzzzl") or data.get("gszzl")
                data["settled"] = True
    except Exception:
        pass

    # 两个接口都失败
    if data is None:
        raise ValueError(f"基金 {code} 数据获取失败，估值和净值接口均不可用")

    # 尝试补充基金名称（从搜索接口获取）
    if not data.get("name"):
        try:
            search_result = await fetch_search(code, limit=1)
            if search_result and len(search_result) > 0:
                data["name"] = search_result[0].get("name", "")
        except Exception:
            pass

    return data


async def fetch_latest_nav(code: str) -> dict:
    """获取最新净值（lsjz 取 pageSize=1）"""
    params = {
        "fundCode": code,
        "pageIndex": 1,
        "pageSize": 1,
    }
    headers = {"Referer": "https://fund.eastmoney.com/"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(HISTORY_BASE, params=params, headers=headers)
        resp.raise_for_status()
        text = resp.text
    # 解析 JSONP: callback({...})
    match = re.search(r"\(\s*(\{.*\})\s*\)", text, re.DOTALL)
    if match:
        data = json.loads(match.group(1))
    else:
        data = json.loads(text)
    items = data.get("Data", {}).get("LSJZList", [])
    if not items:
        return {}
    item = items[0]
    return {
        "jzrq": item.get("FSRQ", ""),
        "dwjz": item.get("DWJZ", ""),
        "ljjz": item.get("LJJZ", ""),
        "jzzzl": item.get("JZZZL", ""),
    }


async def fetch_history(code: str, page_size: int = 90) -> dict:
    """获取历史净值列表"""
    params = {
        "fundCode": code,
        "pageIndex": 1,
        "pageSize": page_size,
    }
    headers = {"Referer": "https://fund.eastmoney.com/"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(HISTORY_BASE, params=params, headers=headers)
        resp.raise_for_status()
        text = resp.text
    match = re.search(r"\(\s*(\{.*\})\s*\)", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    return json.loads(text)


async def fetch_history_full(code: str) -> dict:
    """从 pingzhongdata 获取完整净值趋势（用于对比计算）

    同时取单位净值和累计净值。两者必须都要：单位净值算市值（份额×净值
    就是账面钱），累计净值算收益率。分红当天单位净值会除权下跌，只看它会把
    已经落进口袋的分红当成亏损——红利类基金一年派四次，这么算会系统性低估。
    """
    url = f"{PINGZHONG_BASE}/{code}.js"
    headers = {"Referer": "https://fund.eastmoney.com/"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT * 2) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        text = resp.text

    # 解析 var Data_netWorthTrend = [...];
    match = re.search(r"var\s+Data_netWorthTrend\s*=\s*(\[.*?\])\s*;", text, re.DOTALL)
    if not match:
        raise ValueError(f"无法解析 pingzhongdata: {code}")
    trend = json.loads(match.group(1))

    # 累计净值另一个变量，格式是 [[时间戳, 值], ...]。按时间戳对齐，
    # 不能按下标——两个序列长度通常一致但没有任何保证
    acc_by_ts = {}
    ac_match = re.search(r"var\s+Data_ACWorthTrend\s*=\s*(\[.*?\])\s*;", text, re.DOTALL)
    if ac_match:
        for pair in json.loads(ac_match.group(1)):
            if isinstance(pair, list) and len(pair) >= 2 and pair[1] is not None:
                acc_by_ts[pair[0]] = pair[1]

    # 转换为与 lsjz 兼容的格式
    lsjz_list = []
    for item in trend:
        ts = item.get("x", 0)
        val = item.get("y")
        if val is None:
            continue
        from datetime import datetime
        date_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        acc = acc_by_ts.get(ts)
        lsjz_list.append({
            "FSRQ": date_str,
            "DWJZ": str(val),
            "LJJZ": "" if acc is None else str(acc),
        })

    return {"Data": {"LSJZList": lsjz_list}}


async def fetch_holdings(code: str, year: str = "") -> dict:
    """获取基金持仓数据（东方财富 HTML 接口）"""
    params = {
        "type": "jjcc",
        "code": code,
        "topline": "10",
        "year": year,
        "month": "",
    }
    headers = {
        "Referer": f"https://fundf10.eastmoney.com/ccmx_{code}.html",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(HOLDINGS_BASE, params=params, headers=headers)
        resp.raise_for_status()
        text = resp.text

    # 解析 JS 响应: var apidata={ content:"...", arryear:[...], ... }
    content_match = re.search(r'content:"(.*?)"', text, re.DOTALL)
    arryear_match = re.search(r'arryear:\s*(\[.*?\])', text)

    content = content_match.group(1) if content_match else ""
    arryear = json.loads(arryear_match.group(1)) if arryear_match else []

    return {"content": content, "arryear": arryear}


async def fetch_ranking(top_n: int = 5, sort_by: str = "1yzf", fund_type: str = "all") -> list:
    """获取开放式基金收益排行（东财 rankhandler）

    sort_by: '1yzf'=近1月, 'rzdf'=当日, 'zzf'=近1周, '3yzf'=近3月
    返回 [{code, name, date, nav, daily_pct, week1, month1, month3, month6, year1}, ...]
    """
    params = {
        "op": "ph",
        "dt": "kf",
        "ft": fund_type,
        "rs": "",
        "gs": "0",
        "sc": sort_by,   # 排序字段
        "st": "desc",
        "pi": "1",
        "pn": str(max(top_n, 1)),
        "dx": "1",
    }
    headers = {
        "Referer": "https://fund.eastmoney.com/data/fundranking.html",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(RANK_BASE, params=params, headers=headers)
        resp.raise_for_status()
        text = resp.text

    # 响应形如 var rankData = {datas:["code,name,py,date,nav,...", ...], ...};
    match = re.search(r"datas:\s*(\[.*?\])", text, re.DOTALL)
    if not match:
        raise ValueError("无法解析基金排行响应")
    rows = json.loads(match.group(1))

    result = []
    for row in rows[:top_n]:
        parts = row.split(",")
        if len(parts) < 9:
            continue
        result.append({
            "code": parts[0],
            "name": parts[1],
            "date": parts[3] if len(parts) > 3 else "",
            "nav": parts[4] if len(parts) > 4 else "",
            "daily_pct": parts[6] if len(parts) > 6 else "",
            "week1": parts[7] if len(parts) > 7 else "",
            "month1": parts[8] if len(parts) > 8 else "",
            "month3": parts[9] if len(parts) > 9 else "",
            "month6": parts[10] if len(parts) > 10 else "",
            "year1": parts[11] if len(parts) > 11 else "",
        })
    return result


# ===== 全市场基金列表（按类型并发拉取 rankhandler，内存缓存） =====
# rankhandler datas 行格式（逗号分隔）：
# 0 代码 1 名称 2 拼音 3 净值日期 4 单位净值 5 累计净值 6 日增长率
# 7 近1周 8 近1月 9 近3月 10 近6月 11 近1年 12 近2年 13 近3年
# 14 今年来 15 成立来 16 成立日期 ...
_FUND_TYPES = [
    ("gp", "股票型"),
    ("hh", "混合型"),
    ("zq", "债券型"),
    ("zs", "指数型"),
    ("qdii", "QDII"),
    ("fof", "FOF"),
]
_FUND_LIST_CACHE_TTL = 600  # 秒

_fund_list_cache = {"ts": 0.0, "data": None}
_fund_list_lock = asyncio.Lock()

_SAVE_CHUNK = 500  # 单次 upsert 行数，避免 SQL 过长
_SNAPSHOT_LOOKBACK = 15  # 降级读库时回看天数（QDII 净值普遍滞后几个交易日）
# 落库时随净值日一起覆盖的列（与 _parse_fund_row 对齐）
_DAILY_COLS = (
    "name", "fund_type", "nav", "acc_nav", "daily_pct", "week1", "month1",
    "month3", "month6", "year1", "year2", "year3", "ytd", "since", "inception",
)


def _fnum(v):
    """rankhandler 缺失值为空串，统一转 float 或 None"""
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


async def _fetch_rank_rows(client: httpx.AsyncClient, fund_type: str) -> list:
    """拉取某一类型的全量排行数据行（pn 足够大时单页即可拿全）"""
    params = {
        "op": "ph",
        "dt": "kf",
        "ft": fund_type,
        "rs": "",
        "gs": "0",
        "sc": "jnzf",
        "st": "desc",
        "pi": "1",
        "pn": "20000",
        "dx": "1",
    }
    headers = {
        "Referer": "https://fund.eastmoney.com/data/fundranking.html",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    resp = await client.get(RANK_BASE, params=params, headers=headers)
    resp.raise_for_status()
    match = re.search(r"datas:\s*(\[.*?\])", resp.text, re.DOTALL)
    if not match:
        return []
    return json.loads(match.group(1))


def _parse_fund_row(row: str, type_label: str) -> dict | None:
    parts = row.split(",")
    if len(parts) < 17 or not parts[0]:
        return None
    return {
        "code": parts[0],
        "name": parts[1],
        "fund_type": type_label,
        "date": parts[3],
        "nav": _fnum(parts[4]),
        "acc_nav": _fnum(parts[5]),
        "daily_pct": _fnum(parts[6]),
        "week1": _fnum(parts[7]),
        "month1": _fnum(parts[8]),
        "month3": _fnum(parts[9]),
        "month6": _fnum(parts[10]),
        "year1": _fnum(parts[11]),
        "year2": _fnum(parts[12]),
        "year3": _fnum(parts[13]),
        "ytd": _fnum(parts[14]),
        "since": _fnum(parts[15]),
        "inception": parts[16],
    }


async def fetch_fund_list(force: bool = False) -> dict:
    """全市场开放式基金列表（各类型并集，含各周期收益率）

    内存缓存 10 分钟，拉取成功后按各自的净值日期落库。
    """
    if not force and _fund_list_cache["data"] and time.time() - _fund_list_cache["ts"] < _FUND_LIST_CACHE_TTL:
        return _fund_list_cache["data"]

    async with _fund_list_lock:
        # 双重检查：等锁期间可能已被其他请求刷新
        if not force and _fund_list_cache["data"] and time.time() - _fund_list_cache["ts"] < _FUND_LIST_CACHE_TTL:
            return _fund_list_cache["data"]

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT * 3) as client:
            results = await asyncio.gather(
                *[_fetch_rank_rows(client, ft) for ft, _ in _FUND_TYPES],
                return_exceptions=True,
            )
            # 并发拉取大响应时个别类型可能超时/断连，串行重试一次，避免缓存不完整数据
            results = list(results)
            for i, res in enumerate(results):
                if isinstance(res, BaseException):
                    try:
                        results[i] = await _fetch_rank_rows(client, _FUND_TYPES[i][0])
                    except Exception:
                        pass

        rows = []
        for (_, label), res in zip(_FUND_TYPES, results):
            if isinstance(res, BaseException):
                continue
            for raw in res:
                parsed = _parse_fund_row(raw, label)
                if parsed:
                    rows.append(parsed)

        # 排行接口全部不可用时降级到落库快照
        if not rows:
            cached = await _load_latest_snapshot()
            if cached:
                return cached
            raise ValueError("基金列表获取失败，排行接口不可用")

        result = {
            "total": len(rows),
            "updated_at": int(time.time()),
            "items": rows,
        }
        _fund_list_cache["ts"] = time.time()
        _fund_list_cache["data"] = result

        # 写库失败不影响接口返回
        try:
            await _save_daily(rows)
        except Exception:
            pass
        return result


def _parse_nav_date(raw: str) -> Optional[date]:
    """净值日期字符串 -> date，新基金未公布净值时为空串"""
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


async def _save_daily(rows: List[dict]) -> None:
    """整批 upsert，每只基金落到各自的净值日（同一净值日重复刷新则覆盖）"""
    valid = []
    for r in rows:
        nav_date = _parse_nav_date(r.get("date"))
        if nav_date:
            valid.append((r, nav_date))
    if not valid:
        return

    async with async_session() as session:
        for i in range(0, len(valid), _SAVE_CHUNK):
            values = [
                {"code": r["code"], "trade_date": d,
                 **{c: r.get(c) for c in _DAILY_COLS}}
                for r, d in valid[i:i + _SAVE_CHUNK]
            ]
            stmt = mysql_insert(FundDaily).values(values)
            stmt = stmt.on_duplicate_key_update(
                **{c: stmt.inserted[c] for c in _DAILY_COLS}
            )
            await session.execute(stmt)
        await session.commit()


async def _load_latest_snapshot() -> Optional[dict]:
    """读每只基金最新一条落库快照，无数据或库不可用返回 None

    各类型净值公布进度不一（QDII 普遍滞后），所以不能只取 max(trade_date)
    那一天，而是回看一个窗口后按 code 取最新。
    """
    try:
        async with async_session() as session:
            latest = await session.scalar(select(sa_func.max(FundDaily.trade_date)))
            if not latest:
                return None
            floor = latest - timedelta(days=_SNAPSHOT_LOOKBACK)
            result = await session.execute(
                select(FundDaily)
                .where(FundDaily.trade_date >= floor)
                .order_by(FundDaily.trade_date)
            )
            # 按 trade_date 升序遍历，同一 code 后写的就是最新的
            newest = {}
            for o in result.scalars():
                newest[o.code] = {
                    "code": o.code,
                    "date": str(o.trade_date),
                    **{c: getattr(o, c) for c in _DAILY_COLS},
                }
    except Exception:
        return None

    items = sorted(newest.values(), key=lambda r: r["code"])
    snap_ts = datetime.combine(latest, datetime.min.time())
    return {
        "total": len(items),
        "updated_at": int(snap_ts.timestamp()),
        "trade_date": str(latest),
        "from_cache": True,
        "items": items,
    }


async def fetch_manager(code: str) -> dict:
    """从 pingzhongdata 解析当前基金经理

    返回 {"managers": [name, ...], "manager": "name1 / name2"}
    """
    url = f"{PINGZHONG_BASE}/{code}.js"
    headers = {"Referer": "https://fund.eastmoney.com/"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT * 2) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        text = resp.text

    match = re.search(r"Data_currentFundManager\s*=\s*(\[[\s\S]*?\])\s*;", text)
    managers = []
    if match:
        try:
            arr = json.loads(match.group(1))
            managers = [m.get("name", "") for m in arr if m.get("name")]
        except Exception:
            managers = []
    return {"managers": managers, "manager": " / ".join(managers)}


async def fetch_search(keyword: str, limit: int = 10) -> list:
    """基金搜索建议（东财 FundSearchAPI，支持中文名/代码/拼音）

    返回 [{code, name, type}, ...]，仅保留基金类型结果
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    params = {"m": "1", "key": kw}
    headers = {
        "Referer": "https://fund.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(SEARCH_BASE, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    result = []
    for item in data.get("Datas", []):
        code = item.get("CODE", "")
        # 仅保留基金（CATEGORY==700），排除股票、基金公司、基金经理等
        if item.get("CATEGORY") != 700 or not code:
            continue
        base = item.get("FundBaseInfo") or {}
        result.append({
            "code": code,
            "name": item.get("NAME", ""),
            "type": base.get("FTYPE", ""),
        })
        if len(result) >= limit:
            break
    return result
