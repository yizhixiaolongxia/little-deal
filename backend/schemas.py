"""Pydantic schemas"""
import re

from pydantic import BaseModel, Field, model_validator
from datetime import datetime
from typing import List, Literal, Optional


class FundCodeIn(BaseModel):
    fund_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class WatchlistItem(BaseModel):
    id: int
    fund_code: str
    created_at: datetime

    class Config:
        from_attributes = True


class WatchlistOut(BaseModel):
    items: List[WatchlistItem]
    codes: List[str]


class StockQuoteRequest(BaseModel):
    secids: List[str]


class StockCodeIn(BaseModel):
    stock_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class StockWatchlistOut(BaseModel):
    codes: List[str]


class NavSyncIn(BaseModel):
    """净值同步：codes 为空时同步自选 + 模拟盘持仓；force 跳过新鲜度判定强制回源"""
    codes: List[str] = Field(default_factory=list, max_length=200)
    force: bool = False

    @model_validator(mode="after")
    def check_codes(self):
        for c in self.codes:
            if not re.fullmatch(r"\d{6}", c):
                raise ValueError(f"非法基金代码: {c}")
        return self


class SimTradeIn(BaseModel):
    """模拟盘下单：shares（股数/份额）与 amount（金额）二选一"""
    asset_type: Literal["stock", "fund"]
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    side: Literal["buy", "sell"]
    shares: Optional[float] = Field(None, gt=0)
    amount: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def check_one_of(self):
        if (self.shares is None) == (self.amount is None):
            raise ValueError("shares 与 amount 必须且只能提供一个")
        return self
