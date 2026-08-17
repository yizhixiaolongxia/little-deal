"""基准指数与超额收益路由

绝对收益率回答不了「这只跌 3% 是它烂还是大盘烂」，这组接口给出相对基准的答案。
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from ..services import benchmark_service, nav_service

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])


@router.get("/list")
async def list_benchmarks():
    """已登记的基准。加新基准前先确认它在数据源还有更新"""
    return {
        "default": benchmark_service.DEFAULT_BENCHMARK,
        "items": [
            {"key": k, **v} for k, v in benchmark_service.BENCHMARKS.items()
        ],
    }


@router.get("/series")
async def get_series(
    key: str = Query(default=benchmark_service.DEFAULT_BENCHMARK),
    start: str = Query(default=""),
    end: str = Query(default=""),
):
    """基准点位序列（只读库，不回源）"""
    try:
        items = await benchmark_service.get_series(key, start=start or None,
                                                   end=end or None)
        return {"key": key, "name": benchmark_service.name_of(key),
                "total": len(items), "items": items}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/period")
async def get_period(
    start: str,
    end: str,
    key: str = Query(default=benchmark_service.DEFAULT_BENCHMARK),
):
    """基准在指定区间的收益率。pct 为 null 时看 reason，别当成 0"""
    try:
        s = datetime.strptime(start, "%Y-%m-%d").date()
        e = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
    try:
        return await benchmark_service.period_return(s, e, key=key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/excess")
async def get_excess(
    codes: str = Query(default="", description="逗号分隔，为空时取自选 + 持仓"),
    days: int = Query(default=365, ge=7, le=3650),
    key: str = Query(default=benchmark_service.DEFAULT_BENCHMARK),
):
    """各基金近 N 自然日的收益率与超额

    收益率按累计净值算（含分红），basis 字段标明实际用的是哪个口径。
    """
    try:
        want = [c.strip() for c in codes.split(",") if c.strip()]
        if not want:
            want = await nav_service.tracked_codes()
        if not want:
            return {"days": days, "benchmark": benchmark_service.name_of(key),
                    "items": []}
        items = await benchmark_service.funds_excess(want, days=days, key=key)
        return {"days": days, "benchmark": benchmark_service.name_of(key),
                "total": len(items), "items": items}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync")
async def sync(
    key: str = Query(default=benchmark_service.DEFAULT_BENCHMARK),
    force: bool = Query(default=False),
):
    """回源落库基准点位。定时任务走 scripts/sync_benchmark.py

    返回里带 warning 时要当回事：数据源对部分指数会停更却仍返回合法格式。
    """
    try:
        return await benchmark_service.sync(key, force=force)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
