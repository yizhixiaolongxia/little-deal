"""市场风险监测路由"""
from fastapi import APIRouter, HTTPException, Query
from ..services import market_service

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
