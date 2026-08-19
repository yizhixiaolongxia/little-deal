"""市场风险监测路由"""
from fastapi import APIRouter, HTTPException, Query
from ..services import market_service, position_service

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/risk")
async def get_market_risk():
    """获取市场风险综合数据（大盘指数 + 恐贪情绪）"""
    try:
        data = await market_service.fetch_market_risk()
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/risk/history")
async def get_market_risk_history(days: int = Query(default=30, ge=1, le=365)):
    """获取最近 N 个交易日的风险评分历史"""
    try:
        items = await market_service.fetch_risk_history(days)
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/position")
async def get_market_position():
    """市场位置：四个指数的点位历史分位 + 两融杠杆分位 + 交叉判定

    某一路数据缺失时不报错，原因会在 notes 里带回来 —— 位置面板静默少半边
    比直接失败更糟，界面看着正常，人却在拿半个结论做决定"""
    try:
        return await position_service.market_position()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/position/sync")
async def post_position_sync(force: bool = Query(default=False)):
    """手动触发位置数据同步（指数点位 + 两融余额）。
    部分失败仍返回 200，失败的那几路在 failed 字段里"""
    try:
        return await position_service.sync(force=force)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
