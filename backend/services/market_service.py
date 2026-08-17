"""市场风险监测服务

获取大盘指数（上证/深证/创业板）近5日K线，计算市场情绪评分。
使用新浪财经 K 线接口（稳定可靠）。
每次拉取成功后按交易日落库（market_risk_daily），支持历史回溯与接口挂掉时的降级展示。
"""
import asyncio
import json
from datetime import date, datetime
from typing import List

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from ..config import REQUEST_TIMEOUT
from ..database import async_session
from ..models import MarketRiskDaily

_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}

# 新浪K线接口
SINA_KLINE_BASE = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"

# 三大指数配置：(sina_symbol, 名称)
INDICES = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
]


async def _fetch_index_kline(client: httpx.AsyncClient, symbol: str, days: int = 5) -> dict | None:
    """获取单只指数近 N 个交易日 K 线数据（新浪财经接口）"""
    params = {
        "symbol": symbol,
        "scale": "240",    # 日K（240分钟）
        "ma": "no",
        "datalen": str(days),
    }
    try:
        resp = await client.get(SINA_KLINE_BASE, params=params, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    if not data or not isinstance(data, list):
        return None

    closes = []
    for item in data:
        try:
            closes.append(float(item["close"]))
        except (ValueError, TypeError, KeyError):
            continue

    if len(closes) < 2:
        return None

    # 计算每日涨跌幅
    pcts = []
    for i in range(1, len(closes)):
        pct = (closes[i] - closes[i - 1]) / closes[i - 1] * 100
        pcts.append(round(pct, 2))

    return {
        "closes": closes,
        "pcts": pcts,
        "latest_price": closes[-1],
        "latest_pct": pcts[-1] if pcts else 0,
        "latest_day": str(data[-1].get("day", "")),
    }


def _calc_sentiment(index_results: List[dict]) -> dict:
    """根据三大指数数据计算市场情绪评分（0-100）

    计算逻辑：
    - 当日涨跌幅均值 (权重40%)：-3%~+3% 映射到 0~100
    - 5日累计涨跌幅均值 (权重35%)：-8%~+8% 映射到 0~100
    - 5日波动率均值取反 (权重25%)：波动越大越恐惧，0%~3% 映射到 100~0
    """
    if not index_results:
        return {"score": 50, "level": "中性", "label": "neutral"}

    # 当日涨跌幅
    today_pcts = [r["latest_pct"] for r in index_results if r]
    avg_today = sum(today_pcts) / len(today_pcts) if today_pcts else 0

    # 5日累计涨跌幅
    cumulative_pcts = []
    for r in index_results:
        if r and r["pcts"]:
            cumulative_pcts.append(sum(r["pcts"]))
    avg_cumulative = sum(cumulative_pcts) / len(cumulative_pcts) if cumulative_pcts else 0

    # 5日波动率（日涨跌幅标准差）
    volatilities = []
    for r in index_results:
        if r and len(r["pcts"]) >= 2:
            mean = sum(r["pcts"]) / len(r["pcts"])
            var = sum((x - mean) ** 2 for x in r["pcts"]) / len(r["pcts"])
            volatilities.append(var ** 0.5)
    avg_vol = sum(volatilities) / len(volatilities) if volatilities else 0

    # 各维度归一化到 0-100
    # 当日涨跌 -3% ~ +3% -> 0 ~ 100
    score_today = max(0, min(100, (avg_today + 3) / 6 * 100))
    # 5日累计 -8% ~ +8% -> 0 ~ 100
    score_cumul = max(0, min(100, (avg_cumulative + 8) / 16 * 100))
    # 波动率 0% ~ 3% -> 100 ~ 0（波动越大越恐惧）
    score_vol = max(0, min(100, (1 - avg_vol / 3) * 100))

    # 加权
    final_score = round(score_today * 0.40 + score_cumul * 0.35 + score_vol * 0.25)
    final_score = max(0, min(100, final_score))

    # 等级划分
    if final_score <= 25:
        level, label = "极度恐惧", "extreme_fear"
    elif final_score <= 45:
        level, label = "恐惧", "fear"
    elif final_score <= 55:
        level, label = "中性", "neutral"
    elif final_score <= 75:
        level, label = "贪婪", "greed"
    else:
        level, label = "极度贪婪", "extreme_greed"

    return {"score": final_score, "level": level, "label": label}


def _latest_trade_date(index_results: List[dict]) -> date:
    """取 K 线最后一根的日期作为交易日（非交易日刷新不会新增记录）"""
    days = []
    for r in index_results:
        d = (r or {}).get("latest_day") or ""
        try:
            days.append(datetime.strptime(d[:10], "%Y-%m-%d").date())
        except ValueError:
            continue
    return max(days) if days else date.today()


async def _save_daily(trade_date: date, sentiment: dict, indices: list) -> None:
    """按交易日 upsert 当日风险快照"""
    stmt = mysql_insert(MarketRiskDaily).values(
        trade_date=trade_date,
        score=sentiment["score"],
        level=sentiment["level"],
        label=sentiment["label"],
        indices_json=json.dumps(indices, ensure_ascii=False),
    )
    stmt = stmt.on_duplicate_key_update(
        score=stmt.inserted.score,
        level=stmt.inserted.level,
        label=stmt.inserted.label,
        indices_json=stmt.inserted.indices_json,
    )
    async with async_session() as session:
        await session.execute(stmt)
        await session.commit()


async def _load_latest_snapshot() -> dict | None:
    """行情接口全部失败时，回退到最近一次落库数据"""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(MarketRiskDaily).order_by(MarketRiskDaily.trade_date.desc()).limit(1)
            )
            row = result.scalar_one_or_none()
    except Exception:
        return None
    if not row:
        return None
    return {
        "indices": json.loads(row.indices_json) if row.indices_json else [],
        "sentiment": {"score": row.score, "level": row.level, "label": row.label},
        "updated_at": f"{row.trade_date.strftime('%Y-%m-%d')}（缓存）",
        "from_cache": True,
    }


async def fetch_risk_history(days: int = 30) -> List[dict]:
    """获取最近 N 个交易日的风险评分历史（按日期升序）"""
    async with async_session() as session:
        result = await session.execute(
            select(MarketRiskDaily).order_by(MarketRiskDaily.trade_date.desc()).limit(days)
        )
        rows = list(result.scalars().all())
    rows.reverse()
    return [
        {
            "date": r.trade_date.strftime("%Y-%m-%d"),
            "score": r.score,
            "level": r.level,
            "label": r.label,
        }
        for r in rows
    ]


async def fetch_market_risk() -> dict:
    """获取市场风险综合数据"""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        tasks = [_fetch_index_kline(client, symbol) for symbol, _ in INDICES]
        results = await asyncio.gather(*tasks)

    indices = []
    valid_results = []
    for (symbol, name), result in zip(INDICES, results):
        if result:
            valid_results.append(result)
            indices.append({
                "name": name,
                "code": symbol,
                "price": result["latest_price"],
                "pct": result["latest_pct"],
                "spark": result["closes"],
            })
        else:
            indices.append({
                "name": name,
                "code": symbol,
                "price": None,
                "pct": None,
                "spark": [],
            })

    # 行情全部拉取失败时降级到最近一次落库数据
    if not valid_results:
        cached = await _load_latest_snapshot()
        if cached:
            return cached

    sentiment = _calc_sentiment(valid_results)
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 按交易日落库，写入失败不影响接口返回
    if valid_results:
        try:
            await _save_daily(_latest_trade_date(valid_results), sentiment, indices)
        except Exception:
            pass

    return {
        "indices": indices,
        "sentiment": sentiment,
        "updated_at": updated_at,
    }
