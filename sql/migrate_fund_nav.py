"""净值落库迁移：新增 fund_nav / fund_nav_sync 两张表（可重复执行）

背景：对比面板此前每次都实时穿透 pingzhongdata 取全历史净值，N 只基金就是
N 次串行请求，慢、吃上游限流、接口一抖整块没数据；而且组合级的波动率与回撤
需要长净值序列，不落库根本算不出来。

本脚本只建表，不动任何已有表和数据。建完跑一次 scripts/sync_nav.py 回填历史。

用法（必须在 backend 目录下执行，config.py 的 load_dotenv 读的是当前目录的 .env）：
    cd backend && ./venv/bin/python ../sql/migrate_fund_nav.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from backend.database import engine  # noqa: E402

CREATE_FUND_NAV = """
CREATE TABLE IF NOT EXISTS fund_nav (
    id INT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(6) NOT NULL,
    nav_date DATE NOT NULL,
    nav DOUBLE NOT NULL,
    acc_nav DOUBLE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_fund_nav (code, nav_date),
    KEY idx_fund_nav_date (nav_date)
)
"""

# rows 是 MySQL 8.0 保留字，列名用 rows_count
CREATE_FUND_NAV_SYNC = """
CREATE TABLE IF NOT EXISTS fund_nav_sync (
    code VARCHAR(6) PRIMARY KEY,
    first_date DATE,
    last_date DATE,
    rows_count INT NOT NULL DEFAULT 0,
    synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


async def main() -> None:
    try:
        async with engine.begin() as conn:
            await conn.execute(text(CREATE_FUND_NAV))
            await conn.execute(text(CREATE_FUND_NAV_SYNC))
            print("ready fund_nav / fund_nav_sync")

            existing = (await conn.execute(text("SELECT COUNT(*) FROM fund_nav"))).scalar()
            codes = (await conn.execute(text("SELECT COUNT(DISTINCT code) FROM fund_nav"))).scalar()
            print(f"当前已有 {existing} 条净值，覆盖 {codes} 只基金")
            if not existing:
                print("下一步：cd backend && ./venv/bin/python ../scripts/sync_nav.py")
    finally:
        # 不显式关连接池，退出时 aiomysql 析构会在已关闭的事件循环上报一串栈，看着像出错
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
