"""市场位置面板迁移：新增 margin_daily 表（可重复执行）

背景：市场风险面板此前只有「今天涨跌多少」和「情绪多少分」，两个都是短期温度，
回答不了「现在这个点位在历史上算贵还是便宜」。补上两融余额（杠杆水平）和指数
点位分位，风险面板才有纵深。

指数点位复用已有的 index_quote，不用建表；本脚本只建两融这一张。

为什么两融不塞进 macro_daily 的长表：见 models.py 的 MarginDaily。

只建表，不动任何已有表和数据。建完跑一次 scripts/sync_margin.py 回填历史
（上游 RPTA_RZRQ_LSHJ 有 2010-03-31 至今约 3980 个交易日）。

用法（必须在 backend 目录下执行，config.py 的 load_dotenv 读的是当前目录的 .env）：
    cd backend && ./venv/bin/python ../sql/migrate_margin.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from backend.database import engine  # noqa: E402

CREATE_MARGIN_DAILY = """
CREATE TABLE IF NOT EXISTS margin_daily (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trade_date DATE NOT NULL,
    rz_ye DOUBLE NOT NULL,
    rz_ye_pct DOUBLE NULL,
    rq_ye DOUBLE NULL,
    rzrq_ye DOUBLE NULL,
    ltsz DOUBLE NULL,
    hs300_close DOUBLE NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_margin_daily (trade_date),
    KEY idx_margin_daily_date (trade_date)
)
"""


async def main() -> None:
    try:
        async with engine.begin() as conn:
            await conn.execute(text(CREATE_MARGIN_DAILY))
            print("ready margin_daily")

            rows = (await conn.execute(text("SELECT COUNT(*) FROM margin_daily"))).scalar()
            first = (await conn.execute(text("SELECT MIN(trade_date) FROM margin_daily"))).scalar()
            last = (await conn.execute(text("SELECT MAX(trade_date) FROM margin_daily"))).scalar()
            print(f"  margin_daily: {rows} 条 / {first or '-'} ~ {last or '-'}")

            # 分位面板还依赖 index_quote 里的四个指数，一起报一下覆盖情况，
            # 免得建完表以为齐了、结果面板上有指数是空的
            idx = (await conn.execute(text(
                "SELECT code, COUNT(*), MIN(trade_date), MAX(trade_date) "
                "FROM index_quote GROUP BY code ORDER BY code"
            ))).all()
            print(f"  index_quote: {len(idx) or '无'} 个指数")
            for code, cnt, lo, hi in idx:
                print(f"    {code}: {cnt} 条 / {lo} ~ {hi}")

            if not rows:
                print("下一步：cd backend && ./venv/bin/python ../scripts/sync_margin.py")
            if len(idx) < 4:
                print("下一步：cd backend && ./venv/bin/python ../scripts/sync_benchmark.py")
    finally:
        # 不显式关连接池，退出时 aiomysql 析构会在已关闭的事件循环上报一串栈，看着像出错
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
