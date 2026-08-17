"""宏观看板迁移：新增 macro_monthly / macro_daily 两表（可重复执行）

背景：此前只有市场层（指数点位、情绪）和个股层，没有宏观层。「先看宏观再看
组合」这一步一直靠人翻新闻，本脚本给它建库。

两张长表，一行一个「指标×期间」，加指标不用改 DDL，只改 macro_service 的登记表。
拆成月度/日度两张而不是一张加 freq 列的理由见 models.py 的 MacroDaily。

只建表，不动任何已有表和数据。建完跑一次 scripts/sync_macro.py 回填历史
（月度回 20 年、国债回全历史；汇率和大宗只有当前快照，历史从那天开始长）。

用法（必须在 backend 目录下执行，config.py 的 load_dotenv 读的是当前目录的 .env）：
    cd backend && ./venv/bin/python ../sql/migrate_macro.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from backend.database import engine  # noqa: E402

CREATE_MACRO_MONTHLY = """
CREATE TABLE IF NOT EXISTS macro_monthly (
    id INT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(24) NOT NULL,
    period DATE NOT NULL,
    value DOUBLE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_macro_monthly (code, period),
    KEY idx_macro_monthly_code (code, period)
)
"""

CREATE_MACRO_DAILY = """
CREATE TABLE IF NOT EXISTS macro_daily (
    id INT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(24) NOT NULL,
    trade_date DATE NOT NULL,
    value DOUBLE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_macro_daily (code, trade_date),
    KEY idx_macro_daily_code (code, trade_date)
)
"""


async def main() -> None:
    try:
        async with engine.begin() as conn:
            await conn.execute(text(CREATE_MACRO_MONTHLY))
            print("ready macro_monthly")
            await conn.execute(text(CREATE_MACRO_DAILY))
            print("ready macro_daily")

            empty = True
            for table, key in (("macro_monthly", "period"), ("macro_daily", "trade_date")):
                rows = (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar()
                codes = (await conn.execute(
                    text(f"SELECT COUNT(DISTINCT code) FROM {table}")
                )).scalar()
                last = (await conn.execute(text(f"SELECT MAX({key}) FROM {table}"))).scalar()
                print(f"  {table}: {rows} 条 / {codes} 个指标 / 最新 {last or '-'}")
                empty = empty and not rows

            if empty:
                print("下一步：cd backend && ./venv/bin/python ../scripts/sync_macro.py")
    finally:
        # 不显式关连接池，退出时 aiomysql 析构会在已关闭的事件循环上报一串栈，看着像出错
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
