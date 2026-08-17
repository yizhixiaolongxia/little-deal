"""基准落库迁移：新增 index_quote 表（可重复执行）

背景：持仓表此前只有绝对收益率，看不出「这只跌 3% 是它烂还是大盘烂」。
算超额收益需要基准指数的历史点位，本脚本建表。

只建表，不动任何已有表和数据。建完跑一次 scripts/sync_benchmark.py 回填历史。

用法（必须在 backend 目录下执行，config.py 的 load_dotenv 读的是当前目录的 .env）：
    cd backend && ./venv/bin/python ../sql/migrate_index_quote.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from backend.database import engine  # noqa: E402

CREATE_INDEX_QUOTE = """
CREATE TABLE IF NOT EXISTS index_quote (
    id INT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(16) NOT NULL,
    trade_date DATE NOT NULL,
    close DOUBLE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_index_quote (code, trade_date),
    KEY idx_index_quote_date (trade_date)
)
"""


async def main() -> None:
    try:
        async with engine.begin() as conn:
            await conn.execute(text(CREATE_INDEX_QUOTE))
            print("ready index_quote")

            existing = (await conn.execute(text("SELECT COUNT(*) FROM index_quote"))).scalar()
            codes = (await conn.execute(
                text("SELECT COUNT(DISTINCT code) FROM index_quote")
            )).scalar()
            print(f"当前已有 {existing} 条点位，覆盖 {codes} 个基准")
            if not existing:
                print("下一步：cd backend && ./venv/bin/python ../scripts/sync_benchmark.py")
    finally:
        # 不显式关连接池，退出时 aiomysql 析构会在已关闭的事件循环上报一串栈，看着像出错
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
