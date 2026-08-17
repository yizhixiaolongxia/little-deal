"""基准指数点位同步

把基准指数（默认沪深300）的全历史收盘点位落到 index_quote 表，给超额收益用。

和 sync_nav.py 一样直接调 service 而不是打 HTTP：定时任务跑的时候后端不一定起着。

用法（在 backend 目录下执行，config.py 要读同目录的 .env）：
    cd backend && ./venv/bin/python ../scripts/sync_benchmark.py
    ./venv/bin/python ../scripts/sync_benchmark.py --force   # 跳过新鲜度判定强制回源

退出码：有基准同步失败时为 1。注意「同步成功但上游数据陈旧」也会返回 1 ——
新浪对部分指数会停止更新却仍返回合法格式，这种情况比失败更危险，不能静默放过。
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import engine  # noqa: E402
from backend.services import benchmark_service  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description="同步基准指数历史点位")
    p.add_argument("--keys", help="只同步指定基准，逗号分隔，如 hs300")
    p.add_argument("--force", action="store_true", help="跳过新鲜度判定强制回源")
    return p.parse_args()


async def main() -> int:
    try:
        return await run()
    finally:
        await engine.dispose()


async def run() -> int:
    args = parse_args()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if args.keys:
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        bad = [k for k in keys if k not in benchmark_service.BENCHMARKS]
        if bad:
            print(f"[{stamp}] 未登记的基准：{', '.join(bad)}")
            print(f"可用：{', '.join(benchmark_service.BENCHMARKS)}")
            return 1
    else:
        keys = list(benchmark_service.BENCHMARKS)

    print(f"[{stamp}] 开始同步 {len(keys)} 个基准"
          f"{'（强制回源）' if args.force else ''}")

    failed = 0
    warned = 0
    for key in keys:
        r = await benchmark_service.sync(key, force=args.force)
        label = f"{key}({r['symbol']})"
        if r["status"] == "synced":
            print(f"  {label}  {r['rows']} 条  至 {r['last_date']}")
        elif r["status"] == "fresh":
            print(f"  {label}  已最新  至 {r['last_date']}")
        else:
            print(f"  {label}  失败：{r.get('message', '未知原因')}")
            failed += 1
        if r.get("warning"):
            print(f"  ⚠️  {r['warning']}")
            warned += 1

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 完成："
          f"失败 {failed} / 数据陈旧 {warned}")
    return 1 if (failed or warned) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
