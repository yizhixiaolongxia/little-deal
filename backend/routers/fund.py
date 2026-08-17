"""基金数据代理路由"""
from fastapi import APIRouter, HTTPException, Query
from ..services import fund_service, nav_service

router = APIRouter(prefix="/api/fund", tags=["fund"])


@router.get("/realtime/{code}")
async def get_realtime(code: str):
    """获取实时估值"""
    try:
        data = await fund_service.fetch_realtime(code)
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/nav/{code}")
async def get_latest_nav(code: str):
    """获取最新净值"""
    try:
        data = await fund_service.fetch_latest_nav(code)
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/history/{code}")
async def get_history(code: str, pageSize: int = Query(default=90, ge=1, le=10000)):
    """获取历史净值"""
    try:
        data = await fund_service.fetch_history(code, page_size=pageSize)
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/history-full/{code}")
async def get_history_full(code: str, force: bool = False):
    """完整历史净值（用于对比计算）

    走 nav_service 读库优先，数据过期才回源上游。响应结构与原来一致，
    额外带 from_cache / stale 供调用方判断数据新鲜度。
    """
    try:
        data = await nav_service.get_history_full(code, force=force)
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/holdings/{code}")
async def get_holdings(code: str, year: str = Query(default="")):
    """获取基金持仓"""
    try:
        data = await fund_service.fetch_holdings(code, year=year)
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/ranking")
async def get_ranking(top: int = Query(default=5, ge=1, le=50), sort: str = Query(default="1yzf")):
    """获取基金收益排行（sort: 1yzf=近1月, rzdf=当日, zzf=近1周）"""
    try:
        data = await fund_service.fetch_ranking(top_n=top, sort_by=sort)
        return {"list": data}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/list")
async def get_fund_list(force: bool = False):
    """全市场开放式基金列表（含各周期收益率，后端缓存 10 分钟）"""
    try:
        data = await fund_service.fetch_fund_list(force=force)
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/search")
async def search_fund(key: str = Query(default=""), limit: int = Query(default=10, ge=1, le=30)):
    """基金搜索建议（支持中文名/代码/拼音）"""
    try:
        data = await fund_service.fetch_search(key, limit=limit)
        return {"list": data}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/manager/{code}")
async def get_manager(code: str):
    """获取基金当前基金经理"""
    try:
        data = await fund_service.fetch_manager(code)
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
