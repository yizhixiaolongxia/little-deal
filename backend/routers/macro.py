"""宏观指标看板路由"""
from fastapi import APIRouter, HTTPException, Query

from ..services import macro_service

router = APIRouter(prefix="/api/macro", tags=["macro"])


@router.get("/dashboard")
async def get_dashboard():
    """看板全量：核心八项 + 五个分组 + 已知缺口"""
    try:
        return await macro_service.dashboard()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_history(code: str, limit: int = Query(default=120, ge=2, le=600)):
    """单指标完整序列。code 不认识时返回 404 而不是 500 ——
    前端传错指标名是调用方的问题，不该看起来像服务挂了"""
    try:
        return await macro_service.history(code, limit)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync")
async def post_sync(force: bool = Query(default=False)):
    """手动触发同步。四组数据源里有失败的会带在 failed 字段里返回，
    但仍然是 200 —— 部分成功就是部分成功，报 502 会让已落库的那几组白跑一趟"""
    try:
        return await macro_service.sync(force=force)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
