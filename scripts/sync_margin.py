"""同步两融余额（融资余额 / 融券余额 / 占流通市值比）

给市场风险面板的「杠杆分位」用。指数点位那一路走 sync_benchmark.py，不在这里。

首次跑会拉全历史（2010-03-31 至今约 3980 个交易日，上游单页上限 800，分 5 页），
之后每次只拉最新一页（覆盖三年多），漏跑几周也补得回来。

上游 RPTA_RZRQ_LSHJ 超过 800 条会静默截断且不报错，service 里用上游自报的 count
做了完整性断言，对不上直接抛——拿被截断的历史算分位会得出完全相反的结论。

和 sync_nav.py 一样直接调 service 而不是打 HTTP：定时任务跑的时候后端不一定起着。

用法（在 backend 目录下执行，config.py 要读同目录的 .env）：
    cd backend && ./venv/bin/python ../scripts/sync_margin.py
    ./venv/bin/python ../scripts/sync_margin.py --force   # 强制重拉全历史

退出码：失败为 1。「同步成功但上游数据陈旧」也返回 1 —— 分位面板拿陈旧数据
会算出一个看起来完全正常的分位，这比缺数更危险，不能静默放过。
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import engine  # noqa: E402
from backend.services import position_service  # noqa: E402


async def main() -> int:
    try:
        return await run()
    finally:
        await engine.dispose()


async def run() -> int:
    p = argparse.ArgumentParser(description="同步两融余额")
    p.add_argument("--force", action="store_true", help="强制重拉全历史")
    args = p.parse_args()

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] 开始同步两融余额{'（强制重拉全历史）' if args.force else ''}")

    try:
        r = await position_service.sync_margin(force=args.force)
    except Exception as e:
        print(f"  失败：{e}")
        return 1

    if r["status"] == "fresh":
        print(f"  已最新  至 {r['last_date']}")
    else:
        scope = "全历史" if r["scope"] == "full" else "最新一页"
        print(f"  {scope}  {r['rows']} 条  至 {r['last_date']}")

    warned = bool(r.get("warning"))
    if warned:
        print(f"  ⚠️  {r['warning']}")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 完成")
    return 1 if warned else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
