"""每日净值同步

把自选 + 模拟盘持仓的基金全历史净值落到 fund_nav 表。这样对比面板、回撤曲线
这类要长序列的计算就不用每次都实时穿透上游，上游抖动时也还有数可用。

直接调 nav_service 而不是打后端的 HTTP 接口：定时任务跑的时候后端不一定起着，
少一个依赖少一个失败点。

用法（在 backend 目录下执行，config.py 要读同目录的 .env）：
    cd backend && ./venv/bin/python ../scripts/sync_nav.py
    ./venv/bin/python ../scripts/sync_nav.py --codes 018957,004746
    ./venv/bin/python ../scripts/sync_nav.py --force      # 跳过新鲜度判定强制回源

退出码：有基金同步失败时为 1，方便定时任务日志里一眼看出问题。
"""
import argparse
import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import engine  # noqa: E402
from backend.services import nav_service  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="同步基金历史净值到本地库")
    p.add_argument(
        "--codes", default="",
        help="逗号分隔的基金代码；不传则同步自选 + 模拟盘持仓",
    )
    p.add_argument(
        "--force", action="store_true",
        help="忽略新鲜度判定，强制回源（上游补数据后想立刻拉全用得上）",
    )
    return p.parse_args()


async def main() -> int:
    try:
        return await run()
    finally:
        # 不显式关连接池，退出时 aiomysql 析构会在已关闭的事件循环上报一串栈，
        # 定时任务日志里看着像出错
        await engine.dispose()


async def run() -> int:
    args = parse_args()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        bad = [c for c in codes if not re.fullmatch(r"\d{6}", c)]
        if bad:
            print(f"[{stamp}] 非法基金代码：{', '.join(bad)}")
            return 1
    else:
        codes = await nav_service.tracked_codes()

    if not codes:
        print(f"[{stamp}] 没有需要同步的基金（自选与模拟盘持仓都是空的）")
        return 0

    print(f"[{stamp}] 开始同步 {len(codes)} 只基金"
          f"{'（强制回源）' if args.force else ''}")

    result = await nav_service.sync_codes(codes, force=args.force)

    # 已经是最新的那些不用逐条报，只列真正拉了数据的和失败的
    for item in result["items"]:
        if item["status"] == "synced":
            print(f"  {item['code']}  {item['rows']} 条  至 {item['last_date']}")
        elif item["status"] == "failed":
            print(f"  {item['code']}  失败：{item.get('message', '未知原因')}")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
          f"完成：更新 {result['synced']} / 已最新 {result['fresh']} / 失败 {result['failed']}")

    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
