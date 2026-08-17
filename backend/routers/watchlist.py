"""自选基金列表路由"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from ..database import get_db
from ..models import Watchlist
from ..schemas import FundCodeIn, WatchlistItem, WatchlistOut

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("", response_model=WatchlistOut)
async def get_watchlist(db: AsyncSession = Depends(get_db)):
    """获取全部自选基金"""
    result = await db.execute(
        select(Watchlist).order_by(Watchlist.created_at)
    )
    items = result.scalars().all()
    return WatchlistOut(
        items=[WatchlistItem.model_validate(i) for i in items],
        codes=[i.fund_code for i in items],
    )


@router.post("", response_model=WatchlistItem)
async def add_fund(body: FundCodeIn, db: AsyncSession = Depends(get_db)):
    """添加自选基金"""
    existing = await db.execute(
        select(Watchlist).where(Watchlist.fund_code == body.fund_code)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该基金已在自选列表中")

    item = Watchlist(fund_code=body.fund_code)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return WatchlistItem.model_validate(item)


@router.delete("/{code}")
async def remove_fund(code: str, db: AsyncSession = Depends(get_db)):
    """删除单个自选基金"""
    result = await db.execute(
        delete(Watchlist).where(Watchlist.fund_code == code)
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到该基金")
    return {"ok": True}


@router.delete("")
async def clear_all(db: AsyncSession = Depends(get_db)):
    """清空全部自选"""
    await db.execute(delete(Watchlist))
    await db.commit()
    return {"ok": True}
