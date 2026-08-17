"""基金净值时间序列路由

单独挂在 /api/nav 而不是塞进 /api/fund，是因为 fund 路由已有 /nav/{code}
（取最新净值），再加 /nav/coverage 会被那条通配路由吃掉。
"""
from fastapi import APIRouter, HTTPException, Query

from ..schemas import NavSyncIn
from ..services import nav_service

router = APIRouter(prefix="/api/nav", tags=["nav"])


@router.get("/coverage")
async def get_coverage():
    """净值落库覆盖情况：各基金条数/区间/是否新鲜，以及还没落库的自选与持仓"""
    try:
        return await nav_service.coverage()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/series/{code}")
async def get_series(
    code: str,
    start: str = Query(default=""),
    end: str = Query(default=""),
):
    """净值序列（只读库，不回源），给组合级指标与回测用"""
    try:
        items = await nav_service.get_nav_series(code, start=start, end=end)
        return {"code": code, "total": len(items), "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync")
async def sync(body: NavSyncIn):
    """同步净值到库。codes 为空时取自选 + 模拟盘持仓

    串行拉取，基金多时会跑一会儿；定时任务走 scripts/sync_nav.py。
    """
    try:
        codes = body.codes or await nav_service.tracked_codes()
        if not codes:
            return {"total": 0, "synced": 0, "fresh": 0, "failed": 0, "items": []}
        return await nav_service.sync_codes(codes, force=body.force)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
