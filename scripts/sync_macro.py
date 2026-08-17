"""同步宏观指标

四组数据源（东财月度报表 / 东财国债日序列 / 新浪快照 / 本地估值自算），
一组失败不拖累其它组，详见 backend/services/macro_service.py。

和 sync_nav.py 一样直接调 service 而不是打 HTTP：定时任务跑的时候后端不一定起着。

用法（在 backend 目录下执行，config.py 要读同目录的 .env）：
    cd backend && ./venv/bin/python ../scripts/sync_macro.py
    ./venv/bin/python ../scripts/sync_macro.py --force   # 强制回填全历史

退出码：任一数据源失败为 1。失败明细逐条打出来——宏观指标少一个不影响页面
打开，但会让看板上那一格永远空着，日志里不写清楚就没人会发现。
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import engine  # noqa: E402
from backend.services import macro_service  # noqa: E402

# 打印顺序固定，和 sync() 里的执行顺序一致，方便对着日志找哪一步慢
GROUPS = [
    ("monthly", "月度/季度指标"),
    ("treasury", "中美国债收益率"),
    ("sina", "汇率/美元指数/大宗"),
    ("valuation", "全A估值与股债性价比"),
]


async def main() -> int:
    try:
        return await run()
    finally:
        await engine.dispose()


async def run() -> int:
    p = argparse.ArgumentParser(description="同步宏观指标")
    p.add_argument("--force", action="store_true", help="强制回填全历史")
    args = p.parse_args()

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] 开始同步宏观指标{'（强制回填全历史）' if args.force else ''}")

    result = await macro_service.sync(force=args.force)

    for key, label in GROUPS:
        g = result.get(key) or {}
        print(f"  {label}  {g.get('saved', 0)} 条")
        for msg in g.get("failed", []):
            print(f"    ⚠️  {msg}")

    failed = result.get("failed", [])
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 完成："
          f"共 {result.get('saved', 0)} 条，失败 {len(failed)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
