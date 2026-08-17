"""模拟投资路由"""
from fastapi import APIRouter, HTTPException, Query
from ..schemas import SimTradeIn
from ..services import portfolio_service, sim_service

router = APIRouter(prefix="/api/sim", tags=["sim"])


@router.get("/account")
async def get_account():
    """账户总览 + 实时估值后的持仓列表"""
    try:
        return await sim_service.get_overview()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/quote")
async def get_quote(
    asset_type: str = Query(..., pattern="^(stock|fund)$"),
    code: str = Query(..., pattern=r"^\d{6}$"),
):
    """下单前预览标的名称与最新价"""
    quote = await sim_service.fetch_quote(asset_type, code)
    if not quote or quote.get("price") is None:
        raise HTTPException(status_code=404, detail=f"未查询到 {code} 的行情数据")
    return quote


@router.post("/trade")
async def trade(body: SimTradeIn):
    """下单。股票当场成交；场外基金只受理，返回 status=pending，等盘后净值公布后清算"""
    try:
        return await sim_service.place_order(
            body.asset_type, body.code, body.side,
            shares=body.shares, amount=body.amount,
        )
    except sim_service.TradeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/settle")
async def settle():
    """手动触发一次清算

    /account 里已经会自动试一次，这个口子主要给定时任务和调试用。
    幂等，重复调不会重复成交。
    """
    try:
        return await sim_service.settle_pending_orders()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/orders")
async def get_orders(limit: int = Query(50, ge=1, le=200)):
    """委托单列表，待清算的排在前面"""
    try:
        items = await sim_service.list_orders(limit)
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/fees")
async def get_fees():
    """累计交易成本"""
    try:
        return await sim_service.total_fees()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/trades")
async def get_trades(limit: int = Query(50, ge=1, le=200)):
    """最近交易流水"""
    try:
        items = await sim_service.list_trades(limit)
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/curve")
async def get_curve(with_points: bool = Query(True)):
    """组合总资产曲线与回撤

    按成交流水 + 逐日收盘净值重算，不依赖任何逐日快照文件。回撤口径是
    「截至最后一个净值日收盘」，不含今日盘中波动——降仓这种动作本来就不该
    被盘中估值触发。

    with_points=false 只要汇总数字，简报和纪律判定用这个就够。
    """
    try:
        data = await portfolio_service.build_curve()
        if not with_points:
            data = {k: v for k, v in data.items() if k != "curve"}
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/reset")
async def reset():
    """重置账户到初始 100 万"""
    try:
        return await sim_service.reset_account()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
