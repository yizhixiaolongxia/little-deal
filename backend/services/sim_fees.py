"""模拟盘费用模型

按真实 A 股与场外基金的收费规则算，纯函数、不碰数据库，便于单独核对。

基金（场外，以天天基金等三方渠道的实际费率为准）：
- 申购费 0.15%，外扣法：净申购额 = 委托金额 / (1 + 费率)，份额按净申购额算，
  差额即手续费。注意不是「金额 × 0.15%」，那是内扣法，两者结果差几分钱。
- 赎回费按持有天数分档，< 7 天 1.5% 是监管强制的短炒惩罚，不是渠道定的。
  持有天数以「成交净值日 - 买入净值日」计，与真实的份额确认日口径一致。

股票（A 股）：
- 佣金 万分之三，单笔最低 5 元，买卖双向
- 印花税 千分之一，仅卖出
- 过户费 万分之 0.1，沪深双向
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Tuple

_CENT = Decimal("0.01")

FUND_BUY_RATE = Decimal("0.0015")        # 申购费率（一折后）
FUND_MIN_BUY_FEE = Decimal("0")          # 三方渠道通常无最低申购费

# (持有天数下限, 费率)，取最后一个满足 days >= 下限的档
FUND_REDEEM_TIERS: List[Tuple[int, Decimal]] = [
    (0, Decimal("0.015")),
    (7, Decimal("0.0075")),
    (30, Decimal("0.005")),
    (365, Decimal("0.0025")),
    (730, Decimal("0")),
]

STOCK_COMMISSION_RATE = Decimal("0.0003")   # 佣金万三
STOCK_MIN_COMMISSION = Decimal("5")         # 单笔最低 5 元
STOCK_STAMP_RATE = Decimal("0.001")         # 印花税千一，仅卖出
STOCK_TRANSFER_RATE = Decimal("0.00001")    # 过户费万分之 0.1


def _money(v: Decimal) -> Decimal:
    return v.quantize(_CENT, rounding=ROUND_HALF_UP)


# ===== 基金 =====
def fund_buy_split(order_amount: Decimal) -> Tuple[Decimal, Decimal]:
    """申购外扣：委托金额 -> (进入基金的净申购额, 手续费)"""
    net = _money(order_amount / (Decimal("1") + FUND_BUY_RATE))
    fee = _money(order_amount - net)
    if fee < FUND_MIN_BUY_FEE:
        fee = FUND_MIN_BUY_FEE
        net = _money(order_amount - fee)
    return net, fee


def fund_redeem_rate(hold_days: int) -> Decimal:
    """按持有天数取赎回费率"""
    rate = FUND_REDEEM_TIERS[0][1]
    for min_days, tier_rate in FUND_REDEEM_TIERS:
        if hold_days >= min_days:
            rate = tier_rate
        else:
            break
    return rate


def fund_redeem_fee(gross: Decimal, hold_days: int) -> Tuple[Decimal, Decimal]:
    """赎回一个批次：(手续费, 适用费率)"""
    rate = fund_redeem_rate(hold_days)
    return _money(gross * rate), rate


def describe_redeem(parts: List[Tuple[Decimal, Decimal]]) -> str:
    """把各批次的 (费率, 费用) 汇总成一句人能看懂的说明

    一笔赎回跨多个买入批次时费率不同，只报总额会让人以为算错了。
    """
    if not parts:
        return ""
    merged: dict = {}
    for rate, fee in parts:
        merged[rate] = merged.get(rate, Decimal("0")) + fee
    segs = [
        f"赎回费{(rate * 100).normalize()}%={_money(fee)}"
        for rate, fee in sorted(merged.items(), reverse=True)
        if fee > 0
    ]
    # 持有满两年费率为 0，此时不列明细，直接说明免收
    return " + ".join(segs) if segs else "持有满 2 年免赎回费"


# ===== 股票 =====
def stock_fees(turnover: Decimal, side: str) -> Tuple[Decimal, str]:
    """A 股一笔成交的费用合计与明细说明"""
    commission = _money(turnover * STOCK_COMMISSION_RATE)
    if commission < STOCK_MIN_COMMISSION:
        commission = _money(STOCK_MIN_COMMISSION)
    transfer = _money(turnover * STOCK_TRANSFER_RATE)
    stamp = _money(turnover * STOCK_STAMP_RATE) if side == "sell" else Decimal("0")

    total = _money(commission + transfer + stamp)
    parts = [f"佣金{commission}", f"过户费{transfer}"]
    if stamp:
        parts.append(f"印花税{stamp}")
    return total, " + ".join(parts)
