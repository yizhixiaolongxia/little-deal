"""模拟投资服务

单账户模拟盘：初始资金 100 万，可买卖 A 股与场外基金。
账户、持仓批次、委托、流水全部落库（sim_account / sim_lot / sim_order / sim_trade），
行情实时取自东财与天天基金，不落库。

交易规则对齐真实市场。零成本、即时成交的模拟盘会诱导高频调仓，那样跑出来的
收益率换到真钱账户会被手续费和成交价差吃掉，参考价值是负的：
- 场外基金盘后清算：15:00 前下单按当日净值、之后按下一交易日净值。下单时净值
  未公布，所以只落委托，等净值出来才确定成交价与份额
- 股票即时成交，但当日买入的批次当日不可卖（T+1）
- 费用见 sim_fees：基金申购 0.15% 外扣、赎回按持有天数分档；股票佣金 + 印花税 + 过户费
- 卖出按买入批次先进先出，每个批次按自己的持有天数套用赎回费率

有意保留的简化：不模拟份额确认延迟与赎回资金到账延迟（T+2~T+7）。这两条影响的是
资金周转效率，对一年期收益结论的影响远小于费用和成交价，但会让复杂度翻倍。
"""
import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import List, Optional, Tuple

from sqlalchemy import delete, select

from ..database import async_session
from ..models import SimAccount, SimLot, SimOrder, SimTrade
from . import fund_service, sim_fees, stock_service

INITIAL_CASH = Decimal("1000000")   # 初始资金 100 万
LOT_SIZE = Decimal("100")           # A 股一手
FUND_CUTOFF = time(15, 0)           # 场外基金申赎的当日确认截止时点

_CENT = Decimal("0.01")
_SHARE = Decimal("0.0001")


class TradeError(ValueError):
    """业务规则不满足（资金不足 / 持仓不足 / 数量不合法等），路由层转 400"""


def _dec(v) -> Decimal:
    """float/str -> Decimal，先转 str 避免二进制浮点误差"""
    return Decimal(str(v))


def _money(v: Decimal) -> Decimal:
    return v.quantize(_CENT, rounding=ROUND_HALF_UP)


def _shares(v: Decimal) -> Decimal:
    return v.quantize(_SHARE, rounding=ROUND_HALF_UP)


def _parse_date(raw) -> Optional[date]:
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _next_trade_day(d: date) -> date:
    """下一个交易日。只跳周末——没有交易日历，节假日靠清算时比对实际净值日兜底"""
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _target_nav_date(now: Optional[datetime] = None) -> date:
    """场外基金委托对应的净值日：工作日 15:00 前算当日，否则顺延到下一交易日"""
    now = now or datetime.now()
    today = now.date()
    if today.weekday() < 5 and now.time() < FUND_CUTOFF:
        return today
    return _next_trade_day(today)


async def _nav_on(code: str, nav_date: date) -> Optional[Tuple[date, Decimal]]:
    """取 nav_date 的正式净值，返回 (实际净值日, 单位净值)

    nav_date 当天无净值（碰上节假日）时取其后最早的一个交易日净值，这也是真实
    规则；目标日净值还没公布则返回 None，表示这笔委托今天还不能清算。
    """
    try:
        latest = await fund_service.fetch_latest_nav(code)
    except Exception:
        return None
    latest_date = _parse_date(latest.get("jzrq"))
    if latest_date is None or latest_date < nav_date:
        return None
    if latest_date == nav_date:
        try:
            return latest_date, _dec(latest["dwjz"])
        except (KeyError, TypeError, ValueError):
            return None

    # 最新净值比目标日更晚：可能目标日是节假日，也可能隔了几天才清算。
    # 两种情况都得回历史里找目标日（或其后最早）那条，不能拿最新净值当成交价
    try:
        raw = await fund_service.fetch_history(code)
    except Exception:
        return None
    best: Optional[Tuple[date, Decimal]] = None
    for item in raw.get("Data", {}).get("LSJZList", []) or []:
        d = _parse_date(item.get("FSRQ"))
        nav = item.get("DWJZ")
        if d is None or not nav or d < nav_date:
            continue
        if best is None or d < best[0]:
            best = (d, _dec(nav))
    return best


# ===== 行情 =====
async def _fund_quote(code: str) -> Optional[dict]:
    """基金：优先用盘中估值，盘后/估值缺失时回退到最新净值"""
    try:
        data = await fund_service.fetch_realtime(code)
    except Exception:
        return None
    raw_price = data.get("gsz") or data.get("dwjz")
    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        return None
    try:
        pct = float(data.get("gszzl"))
    except (TypeError, ValueError):
        pct = None
    return {"code": code, "name": data.get("name") or "", "price": price, "pct": pct}


async def _stock_quote(code: str) -> Optional[dict]:
    try:
        quotes = await stock_service.fetch_quotes_by_codes([code])
    except Exception:
        return None
    return quotes.get(code)


async def fetch_quote(asset_type: str, code: str) -> Optional[dict]:
    """单个标的报价，失败返回 None"""
    if asset_type == "stock":
        return await _stock_quote(code)
    return await _fund_quote(code)


async def _fetch_quotes(items: List[Tuple[str, str]]) -> dict:
    """批量报价，key 为 (asset_type, code)；单个标的取价失败置 None"""
    stock_codes = [c for t, c in items if t == "stock"]
    fund_codes = [c for t, c in items if t == "fund"]
    result: dict = {}

    if stock_codes:
        try:
            quotes = await stock_service.fetch_quotes_by_codes(stock_codes)
        except Exception:
            quotes = {}
        for code in stock_codes:
            result[("stock", code)] = quotes.get(code)

    if fund_codes:
        # 基金估值接口只能单只查询，并发拉取
        got = await asyncio.gather(*[_fund_quote(c) for c in fund_codes])
        for code, quote in zip(fund_codes, got):
            result[("fund", code)] = quote

    return result


# ===== 账户 =====
async def _get_or_create_account(session) -> SimAccount:
    """取账户，首次访问时按初始资金开户"""
    result = await session.execute(
        select(SimAccount).order_by(SimAccount.id).limit(1)
    )
    account = result.scalar_one_or_none()
    if account:
        return account
    account = SimAccount(initial_cash=INITIAL_CASH, cash=INITIAL_CASH)
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _load_lots(session) -> List[SimLot]:
    """所有持仓批次，按买入先后排序（先进先出就按这个顺序扣）"""
    result = await session.execute(
        select(SimLot).order_by(
            SimLot.asset_type, SimLot.code, SimLot.acquire_date, SimLot.id
        )
    )
    return list(result.scalars().all())


def _order_dict(o: SimOrder) -> dict:
    return {
        "id": o.id,
        "asset_type": o.asset_type,
        "code": o.code,
        "name": o.name,
        "side": o.side,
        "order_amount": float(o.order_amount) if o.order_amount is not None else None,
        "order_shares": float(o.order_shares) if o.order_shares is not None else None,
        "nav_date": o.nav_date.strftime("%Y-%m-%d") if o.nav_date else "",
        "status": o.status,
        "price": float(o.price) if o.price is not None else None,
        "shares": float(o.shares) if o.shares is not None else None,
        "fee": float(o.fee) if o.fee is not None else None,
        "amount": float(o.amount) if o.amount is not None else None,
        "note": o.note,
        "created_at": o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else "",
    }


async def get_overview() -> dict:
    """账户总览 + 持仓列表（实时估值）

    进来先试一次清算，这样不依赖定时任务：只要有人看一眼账户，前一天挂的
    委托就落地了。清算本身幂等，重复调不会重复成交。
    """
    await settle_pending_orders()

    today = date.today()
    async with async_session() as session:
        account = await _get_or_create_account(session)
        initial_cash = _dec(account.initial_cash)
        cash = _dec(account.cash)
        lots = await _load_lots(session)
        pending_rows = list((await session.execute(
            select(SimOrder).where(SimOrder.status == "pending").order_by(SimOrder.id)
        )).scalars().all())

        grouped: dict = {}
        for lot in lots:
            key = (lot.asset_type, lot.code)
            g = grouped.setdefault(key, {
                "name": lot.name, "shares": Decimal("0"),
                "cost": Decimal("0"), "lots": [],
            })
            shares = _dec(lot.shares)
            g["shares"] += shares
            # 成本含买入手续费，所以刚买入会显示小幅浮亏——真实账户就是这样
            g["cost"] += _money(shares * _dec(lot.cost_price) + _dec(lot.buy_fee))
            g["lots"].append((shares, _dec(lot.cost_price), lot.acquire_date))
            if lot.name:
                g["name"] = lot.name

        frozen: dict = {}
        pending_cash = Decimal("0")
        pending_orders = [_order_dict(o) for o in pending_rows]
        for o in pending_rows:
            if o.side == "buy":
                # 买入下单时钱已扣、份额未到，得当成在途资金计入总资产，否则总资产会凭空少一笔
                pending_cash += _dec(o.order_amount or 0)
            else:
                key = (o.asset_type, o.code)
                frozen[key] = frozen.get(key, Decimal("0")) + _dec(o.order_shares or 0)

    quotes = await _fetch_quotes(list(grouped.keys()))

    positions = []
    market_value = Decimal("0")
    day_profit = Decimal("0")
    for (asset_type, code), g in grouped.items():
        shares = _shares(g["shares"])
        cost = g["cost"]
        avg_cost = _shares(cost / shares) if shares else Decimal("0")
        quote = quotes.get((asset_type, code)) or {}
        raw_price = quote.get("price")
        price = _dec(raw_price) if raw_price is not None else None
        pct = quote.get("pct")
        # 取价失败时按成本估值，避免总资产跳变
        eval_price = price if price is not None else avg_cost
        value = _money(shares * eval_price)
        market_value += value

        if price is not None and pct is not None and _dec(pct) != Decimal("-100"):
            prev_close = price / (Decimal("1") + _dec(pct) / 100)
            day_profit += _money(shares * (price - prev_close))

        # 按批次展开持有天数与适用赎回费率，并算出此刻全部卖出要付多少费
        lot_rows = []
        sell_fee_now = Decimal("0")
        for lot_shares, cost_price, acquire_date in g["lots"]:
            hold_days = (today - acquire_date).days
            row = {
                "acquire_date": acquire_date.strftime("%Y-%m-%d"),
                "shares": float(lot_shares),
                "cost_price": float(cost_price),
                "hold_days": hold_days,
            }
            if asset_type == "fund":
                rate = sim_fees.fund_redeem_rate(hold_days)
                row["redeem_rate"] = float(rate * 100)
                sell_fee_now += _money(lot_shares * eval_price * rate)
            lot_rows.append(row)
        if asset_type == "stock":
            sell_fee_now, _detail = sim_fees.stock_fees(value, "sell")

        positions.append({
            "asset_type": asset_type,
            "code": code,
            "name": g["name"] or quote.get("name") or "",
            "shares": float(shares),
            "avg_cost": float(avg_cost),
            "price": float(price) if price is not None else None,
            "pct": pct,
            "cost": float(cost),
            "market_value": float(value),
            "profit": float(value - cost),
            "profit_pct": float((value - cost) / cost * 100) if cost else 0.0,
            "stale": price is None,
            "frozen_shares": float(frozen.get((asset_type, code), Decimal("0"))),
            "sell_fee_now": float(sell_fee_now),
            "lots": lot_rows,
        })

    total_asset = cash + market_value + pending_cash
    total_profit = total_asset - initial_cash
    for p in positions:
        p["weight"] = round(p["market_value"] / float(total_asset) * 100, 2) if total_asset else 0.0

    return {
        "initial_cash": float(initial_cash),
        "cash": float(cash),
        "pending_cash": float(pending_cash),
        "market_value": float(market_value),
        "total_asset": float(total_asset),
        "total_profit": float(total_profit),
        "total_profit_pct": float(total_profit / initial_cash * 100) if initial_cash else 0.0,
        "day_profit": float(day_profit),
        "position_count": len(positions),
        "positions": positions,
        "pending_orders": pending_orders,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def reset_account() -> dict:
    """一键重置：清空持仓批次、委托与流水，资金回到初始 100 万

    已废弃的 sim_position 不动，它留着是批次改造前的快照，方便回滚。
    """
    async with async_session() as session:
        await session.execute(delete(SimLot))
        await session.execute(delete(SimOrder))
        await session.execute(delete(SimTrade))
        account = await _get_or_create_account(session)
        account.initial_cash = INITIAL_CASH
        account.cash = INITIAL_CASH
        await session.commit()
    return {"ok": True, "cash": float(INITIAL_CASH)}


# ===== 交易 =====
def _check_stock_qty(side: str, qty: Decimal) -> None:
    if qty != qty.to_integral_value():
        raise TradeError("股票只能按整数股交易")
    if side == "buy" and qty % LOT_SIZE != 0:
        raise TradeError("股票买入数量必须是 100 股的整数倍")


def _fifo_take(lots: List[SimLot], qty: Decimal) -> List[Tuple[SimLot, Decimal]]:
    """按先进先出从批次里取 qty，返回 [(批次, 本次取用数量)]"""
    taken: List[Tuple[SimLot, Decimal]] = []
    remain = qty
    for lot in lots:
        if remain <= 0:
            break
        avail = _dec(lot.shares)
        take = avail if avail <= remain else remain
        taken.append((lot, _shares(take)))
        remain = _shares(remain - take)
    if remain > 0:
        raise TradeError(f"持仓不足，还差 {remain}")
    return taken


async def _lots_of(session, asset_type: str, code: str) -> List[SimLot]:
    result = await session.execute(
        select(SimLot)
        .where(SimLot.asset_type == asset_type, SimLot.code == code)
        .order_by(SimLot.acquire_date, SimLot.id)
    )
    return list(result.scalars().all())


async def _frozen_shares(session, asset_type: str, code: str) -> Decimal:
    """已挂但未清算的卖单占用的份额，不扣开的话同一笔份额能被卖两次"""
    result = await session.execute(
        select(SimOrder).where(
            SimOrder.status == "pending",
            SimOrder.side == "sell",
            SimOrder.asset_type == asset_type,
            SimOrder.code == code,
        )
    )
    total = Decimal("0")
    for o in result.scalars().all():
        total += _dec(o.order_shares or 0)
    return total


async def place_order(asset_type: str, code: str, side: str,
                      shares: Optional[float] = None,
                      amount: Optional[float] = None) -> dict:
    """下单。股票当场成交，场外基金只落委托、等目标净值日公布后清算"""
    if side not in ("buy", "sell"):
        raise TradeError("交易方向只能是 buy 或 sell")
    if shares is None and amount is None:
        raise TradeError("请填写交易数量或金额")

    quote = await fetch_quote(asset_type, code)
    if asset_type == "stock":
        return await _trade_stock(code, side, shares, amount, quote)
    return await _place_fund_order(code, side, shares, amount, quote)


async def _trade_stock(code: str, side: str, shares, amount, quote) -> dict:
    """A 股即时成交，当日买入的批次当日不可卖"""
    if not quote or quote.get("price") is None:
        raise TradeError(f"无法获取 {code} 的最新价格，请确认代码或稍后重试")
    price = _dec(quote["price"])
    if price <= 0:
        raise TradeError(f"{code} 最新价格异常（{price}），暂不能交易")

    today = date.today()
    async with async_session() as session:
        account = await _get_or_create_account(session)
        cash = _dec(account.cash)
        lots = await _lots_of(session, "stock", code)
        name = quote.get("name") or (lots[0].name if lots else "") or ""

        if side == "buy":
            if shares is not None:
                qty = _shares(_dec(shares))
            else:
                # 按金额买入要先预留费用，否则会算出买不起的数量
                rate = sim_fees.STOCK_COMMISSION_RATE + sim_fees.STOCK_TRANSFER_RATE
                budget = _dec(amount) / (Decimal("1") + rate)
                qty = (budget / price / LOT_SIZE).to_integral_value(ROUND_DOWN) * LOT_SIZE
            _check_stock_qty(side, qty)
            if qty <= 0:
                raise TradeError("按当前价格，该金额不足买 100 股")
            turnover = _money(qty * price)
            fee, detail = sim_fees.stock_fees(turnover, "buy")
            total = _money(turnover + fee)
            if total > cash:
                raise TradeError(
                    f"可用资金不足，本次含费需要 {total} 元，当前可用 {_money(cash)} 元"
                )
            account.cash = _money(cash - total)
            session.add(SimLot(
                asset_type="stock", code=code, name=name, shares=qty,
                cost_price=price, buy_fee=fee, acquire_date=today,
            ))
            cash_flow = total
        else:
            if shares is not None:
                qty = _shares(_dec(shares))
            else:
                # 按金额卖出只能卖整数股，多出的零头卖不掉
                qty = (_dec(amount) / price).to_integral_value(ROUND_DOWN)
            _check_stock_qty(side, qty)
            if qty <= 0:
                raise TradeError("按当前价格，该金额不足卖 1 股")
            # T+1：只能卖今天之前买的批次
            sellable_lots = [lot for lot in lots if lot.acquire_date < today]
            sellable = sum((_dec(lot.shares) for lot in sellable_lots), Decimal("0"))
            locked = sum(
                (_dec(lot.shares) for lot in lots if lot.acquire_date >= today),
                Decimal("0"),
            )
            if qty > sellable:
                msg = f"可卖不足，当前可卖 {_shares(sellable)} 股"
                if locked > 0:
                    msg += f"（另有 {_shares(locked)} 股是当日买入，T+1 才能卖）"
                raise TradeError(msg)
            turnover = _money(qty * price)
            fee, detail = sim_fees.stock_fees(turnover, "sell")
            for lot, take in _fifo_take(sellable_lots, qty):
                remain = _shares(_dec(lot.shares) - take)
                if remain <= 0:
                    await session.delete(lot)
                else:
                    lot.shares = remain
            cash_flow = _money(turnover - fee)
            account.cash = _money(cash + cash_flow)

        session.add(SimTrade(
            asset_type="stock", code=code, name=name, side=side, shares=qty,
            price=price, amount=cash_flow, fee=fee, fee_detail=detail, nav_date=today,
        ))
        await session.commit()
        cash_after = _dec(account.cash)

    return {
        "ok": True,
        "status": "settled",
        "asset_type": "stock",
        "code": code,
        "name": name,
        "side": side,
        "shares": float(qty),
        "price": float(price),
        "fee": float(fee),
        "fee_detail": detail,
        "amount": float(cash_flow),
        "cash": float(cash_after),
        "nav_date": today.strftime("%Y-%m-%d"),
        "message": "已成交",
    }


async def _place_fund_order(code: str, side: str, shares, amount, quote) -> dict:
    """场外基金下单：只确定目标净值日，成交价等盘后清算"""
    nav_date = _target_nav_date()
    ref_price = None
    if quote and quote.get("price") is not None:
        ref_price = _dec(quote["price"])

    async with async_session() as session:
        account = await _get_or_create_account(session)
        cash = _dec(account.cash)
        lots = await _lots_of(session, "fund", code)
        name = (quote.get("name") if quote else "") or (lots[0].name if lots else "") or ""

        if side == "buy":
            if amount is not None:
                order_amount = _money(_dec(amount))
            else:
                # 真实场外申购只能按金额，传份额就用参考净值折算
                if ref_price is None or ref_price <= 0:
                    raise TradeError(f"无法获取 {code} 的净值参考价，请改用金额下单")
                order_amount = _money(_dec(shares) * ref_price)
            if order_amount <= 0:
                raise TradeError("申购金额必须大于 0")
            if order_amount > cash:
                raise TradeError(
                    f"可用资金不足，本次需要 {order_amount} 元，当前可用 {_money(cash)} 元"
                )
            # 真实申购是下单即划款，所以这里就扣现金，清算只决定能买到多少份
            account.cash = _money(cash - order_amount)
            order = SimOrder(
                asset_type="fund", code=code, name=name, side="buy",
                order_amount=order_amount, nav_date=nav_date,
            )
        else:
            if not lots:
                raise TradeError(f"当前没有 {code} 的持仓")
            if shares is not None:
                qty = _shares(_dec(shares))
            else:
                if ref_price is None or ref_price <= 0:
                    raise TradeError(f"无法获取 {code} 的净值参考价，请改用份额下单")
                qty = _shares(_dec(amount) / ref_price)
            if qty <= 0:
                raise TradeError("赎回份额必须大于 0")
            # 同一净值日申购的份额当日不能赎，真实的份额确认是次日
            sellable = sum(
                (_dec(lot.shares) for lot in lots if lot.acquire_date < nav_date),
                Decimal("0"),
            )
            sellable -= await _frozen_shares(session, "fund", code)
            if qty > sellable:
                raise TradeError(
                    f"可赎回份额不足，当前可赎 {_shares(max(sellable, Decimal('0')))} 份"
                    "（当日申购的份额与已挂赎回单不计入）"
                )
            order = SimOrder(
                asset_type="fund", code=code, name=name, side="sell",
                order_shares=qty, nav_date=nav_date,
            )

        session.add(order)
        await session.commit()
        await session.refresh(order)
        result = _order_dict(order)
        cash_after = _dec(account.cash)

    result.update({
        "ok": True,
        "cash": float(cash_after),
        "message": f"已受理，按 {nav_date} 的净值成交，净值公布后自动清算",
    })
    return result


async def settle_pending_orders() -> dict:
    """清算目标净值已公布的委托。幂等：只动 pending，成交后置 settled"""
    async with async_session() as session:
        rows = list((await session.execute(
            select(SimOrder).where(SimOrder.status == "pending").order_by(SimOrder.id)
        )).scalars().all())
        todo = [(o.id, o.code, o.nav_date) for o in rows if o.asset_type == "fund"]
    if not todo:
        return {"settled": 0, "pending": 0}

    # 先把净值拉回来再开写事务，避免等网络的时候一直占着数据库连接
    navs = await asyncio.gather(*[_nav_on(code, d) for _oid, code, d in todo])
    ready = {oid: nav for (oid, _c, _d), nav in zip(todo, navs) if nav is not None}
    if not ready:
        return {"settled": 0, "pending": len(todo)}

    settled = 0
    async with async_session() as session:
        account = await _get_or_create_account(session)
        for oid, (actual_date, nav) in ready.items():
            order = await session.get(SimOrder, oid)
            if order is None or order.status != "pending":
                continue    # 已被另一次调用清算掉
            try:
                await _settle_fund_order(session, account, order, actual_date, nav)
            except TradeError as exc:
                order.status = "failed"
                order.note = str(exc)
                order.settled_at = datetime.now()
                if order.side == "buy" and order.order_amount:
                    # 买入下单时已划款，清算不成就得退回来
                    account.cash = _money(_dec(account.cash) + _dec(order.order_amount))
            settled += 1
        await session.commit()
    return {"settled": settled, "pending": len(todo) - settled}


async def _settle_fund_order(session, account, order: SimOrder,
                             nav_date: date, nav: Decimal) -> None:
    """按 nav_date 的净值把一笔基金委托清算成交"""
    if nav <= 0:
        raise TradeError(f"净值异常（{nav}）")

    if order.side == "buy":
        order_amount = _dec(order.order_amount)
        net, fee = sim_fees.fund_buy_split(order_amount)
        qty = _shares(net / nav)
        if qty <= 0:
            raise TradeError("按该日净值折算份额为 0")
        session.add(SimLot(
            asset_type="fund", code=order.code, name=order.name, shares=qty,
            cost_price=nav, buy_fee=fee, acquire_date=nav_date,
        ))
        detail = f"申购费{(sim_fees.FUND_BUY_RATE * 100).normalize()}%={fee}"
        cash_flow = order_amount        # 现金在下单时已扣，这里不再动账户
    else:
        qty = _shares(_dec(order.order_shares))
        lots = [
            lot for lot in await _lots_of(session, "fund", order.code)
            if lot.acquire_date < nav_date
        ]
        taken = _fifo_take(lots, qty)
        gross_total = Decimal("0")
        fee_total = Decimal("0")
        parts: List[Tuple[Decimal, Decimal]] = []
        for lot, take in taken:
            gross = _money(take * nav)
            hold_days = (nav_date - lot.acquire_date).days
            lot_fee, rate = sim_fees.fund_redeem_fee(gross, hold_days)
            gross_total += gross
            fee_total += lot_fee
            parts.append((rate, lot_fee))
            remain = _shares(_dec(lot.shares) - take)
            if remain <= 0:
                await session.delete(lot)
            else:
                lot.shares = remain
        fee = _money(fee_total)
        detail = sim_fees.describe_redeem(parts)
        cash_flow = _money(gross_total - fee)
        account.cash = _money(_dec(account.cash) + cash_flow)

    session.add(SimTrade(
        asset_type="fund", code=order.code, name=order.name, side=order.side,
        shares=qty, price=nav, amount=cash_flow, fee=fee, fee_detail=detail,
        nav_date=nav_date, order_id=order.id,
    ))
    order.status = "settled"
    order.price = nav
    order.shares = qty
    order.fee = fee
    order.amount = cash_flow
    order.settled_at = datetime.now()


async def list_trades(limit: int = 50) -> List[dict]:
    """最近的交易流水（按时间倒序）"""
    async with async_session() as session:
        result = await session.execute(
            select(SimTrade).order_by(SimTrade.id.desc()).limit(limit)
        )
        rows = list(result.scalars().all())
    return [
        {
            "id": r.id,
            "asset_type": r.asset_type,
            "code": r.code,
            "name": r.name,
            "side": r.side,
            "shares": float(r.shares),
            "price": float(r.price),
            "amount": float(r.amount),
            "fee": float(r.fee or 0),
            "fee_detail": r.fee_detail or "",
            "nav_date": r.nav_date.strftime("%Y-%m-%d") if r.nav_date else "",
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        }
        for r in rows
    ]


async def list_orders(limit: int = 50) -> List[dict]:
    """最近的委托单，pending 的排在最前面便于盯清算进度"""
    async with async_session() as session:
        result = await session.execute(
            select(SimOrder).order_by(SimOrder.id.desc()).limit(limit)
        )
        rows = list(result.scalars().all())
    items = [_order_dict(o) for o in rows]
    items.sort(key=lambda x: (x["status"] != "pending", -x["id"]))
    return items


async def total_fees() -> dict:
    """累计手续费。做这次改造的意义就在这个数字上，单独给个口子看"""
    async with async_session() as session:
        rows = list((await session.execute(select(SimTrade))).scalars().all())
    buy_fee = sum((_dec(r.fee or 0) for r in rows if r.side == "buy"), Decimal("0"))
    sell_fee = sum((_dec(r.fee or 0) for r in rows if r.side == "sell"), Decimal("0"))
    return {
        "buy_fee": float(buy_fee),
        "sell_fee": float(sell_fee),
        "total_fee": float(buy_fee + sell_fee),
        "trade_count": len(rows),
    }
