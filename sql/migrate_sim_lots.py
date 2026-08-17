"""模拟盘改造迁移：引入持仓批次与委托单（一次性执行，可重复跑）

背景：模拟盘原来零手续费、按盘中估值即时成交，与真实场外基金规则差太远。
改造后赎回费要按各批次的实际持有天数分档，只存加权平均成本算不出来，
所以持仓从 sim_position（单行汇总）改为 sim_lot（按买入批次）。

本脚本做三件事，每步都先查 information_schema，重复执行不会报错：
1. 给 sim_trade 补 fee / fee_detail / nav_date / order_id 四列
2. 建 sim_lot、sim_order 两张表
3. 把 sim_position 现有持仓按 DATE(created_at) 转成 sim_lot 的初始批次

sim_position 表保留不动，仅业务代码不再读写，便于回滚。

用法（必须在 backend 目录下执行，config.py 的 load_dotenv 读的是当前目录的 .env）：
    cd backend && ./venv/bin/python ../sql/migrate_sim_lots.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from backend.config import DB_NAME  # noqa: E402
from backend.database import engine  # noqa: E402

TRADE_COLUMNS = [
    ("fee", "DECIMAL(18,2) NOT NULL DEFAULT 0"),
    ("fee_detail", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("nav_date", "DATE NULL"),
    ("order_id", "INT NULL"),
]

CREATE_SIM_LOT = """
CREATE TABLE IF NOT EXISTS sim_lot (
    id INT AUTO_INCREMENT PRIMARY KEY,
    asset_type VARCHAR(8) NOT NULL,
    code VARCHAR(6) NOT NULL,
    name VARCHAR(64) NOT NULL DEFAULT '',
    shares DECIMAL(18,4) NOT NULL,
    cost_price DECIMAL(18,4) NOT NULL,
    buy_fee DECIMAL(18,2) NOT NULL DEFAULT 0,
    acquire_date DATE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_sim_lot_pos (asset_type, code, acquire_date)
)
"""

CREATE_SIM_ORDER = """
CREATE TABLE IF NOT EXISTS sim_order (
    id INT AUTO_INCREMENT PRIMARY KEY,
    asset_type VARCHAR(8) NOT NULL,
    code VARCHAR(6) NOT NULL,
    name VARCHAR(64) NOT NULL DEFAULT '',
    side VARCHAR(4) NOT NULL,
    order_amount DECIMAL(18,2) NULL,
    order_shares DECIMAL(18,4) NULL,
    nav_date DATE NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'pending',
    price DECIMAL(18,4) NULL,
    shares DECIMAL(18,4) NULL,
    fee DECIMAL(18,2) NULL,
    amount DECIMAL(18,2) NULL,
    note VARCHAR(255) NOT NULL DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    settled_at DATETIME NULL,
    KEY idx_sim_order_status (status, nav_date)
)
"""


async def _has_column(conn, table: str, column: str) -> bool:
    sql = text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = :db AND table_name = :t AND column_name = :c"
    )
    return bool((await conn.execute(sql, {"db": DB_NAME, "t": table, "c": column})).scalar())


async def main() -> None:
    async with engine.begin() as conn:
        # 1. sim_trade 补列
        for name, ddl in TRADE_COLUMNS:
            if await _has_column(conn, "sim_trade", name):
                print(f"skip  sim_trade.{name} 已存在")
                continue
            await conn.execute(text(f"ALTER TABLE sim_trade ADD COLUMN {name} {ddl}"))
            print(f"added sim_trade.{name}")

        # 2. 建表
        await conn.execute(text(CREATE_SIM_LOT))
        await conn.execute(text(CREATE_SIM_ORDER))
        print("ready sim_lot / sim_order")

        # 3. 迁移持仓。sim_lot 非空说明已迁过，直接跳过避免重复建仓
        existing = (await conn.execute(text("SELECT COUNT(*) FROM sim_lot"))).scalar()
        if existing:
            print(f"skip  sim_lot 已有 {existing} 条批次，不再迁移")
            return

        # 建仓日期只能取 created_at，这些批次的持有天数从建仓日起算
        result = await conn.execute(
            text(
                "INSERT INTO sim_lot (asset_type, code, name, shares, cost_price, "
                "buy_fee, acquire_date, created_at) "
                "SELECT asset_type, code, name, shares, avg_cost, 0, "
                "DATE(created_at), created_at FROM sim_position WHERE shares > 0"
            )
        )
        print(f"moved {result.rowcount} 条持仓 -> sim_lot")


if __name__ == "__main__":
    asyncio.run(main())
