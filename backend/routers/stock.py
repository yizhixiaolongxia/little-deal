"""股票行情代理路由"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from ..database import get_db
from ..models import StockWatchlist
from ..schemas import StockQuoteRequest, StockCodeIn, StockWatchlistOut
from ..services import stock_service

router = APIRouter(prefix="/api/stock", tags=["stock"])


@router.post("/quotes")
async def get_stock_quotes(body: StockQuoteRequest):
    """批量获取股票行情"""
    try:
        data = await stock_service.fetch_stock_quotes(body.secids)
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/list")
async def get_stock_list(force: bool = False):
    """全量 A 股列表（含基本面指标，后端缓存 10 分钟）"""
    try:
        data = await stock_service.fetch_stock_list(force=force)
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ===== 自选股票列表（DB 持久化） =====
@router.get("/watchlist", response_model=StockWatchlistOut)
async def get_stock_watchlist(db: AsyncSession = Depends(get_db)):
    """获取全部自选股票代码"""
    result = await db.execute(
        select(StockWatchlist).order_by(StockWatchlist.created_at)
    )
    items = result.scalars().all()
    return StockWatchlistOut(codes=[i.stock_code for i in items])


@router.post("/watchlist", response_model=StockWatchlistOut)
async def add_stock(body: StockCodeIn, db: AsyncSession = Depends(get_db)):
    """添加自选股票（已存在则忽略）"""
    existing = await db.execute(
        select(StockWatchlist).where(StockWatchlist.stock_code == body.stock_code)
    )
    if not existing.scalar_one_or_none():
        db.add(StockWatchlist(stock_code=body.stock_code))
        await db.commit()
    result = await db.execute(
        select(StockWatchlist).order_by(StockWatchlist.created_at)
    )
    items = result.scalars().all()
    return StockWatchlistOut(codes=[i.stock_code for i in items])


@router.delete("/watchlist/{code}", response_model=StockWatchlistOut)
async def remove_stock(code: str, db: AsyncSession = Depends(get_db)):
    """删除单只自选股票"""
    await db.execute(
        delete(StockWatchlist).where(StockWatchlist.stock_code == code)
    )
    await db.commit()
    result = await db.execute(
        select(StockWatchlist).order_by(StockWatchlist.created_at)
    )
    items = result.scalars().all()
    return StockWatchlistOut(codes=[i.stock_code for i in items])
