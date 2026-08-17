"""一次性数据修正：把重建仓的 pending 委托目标净值日还原到原始建仓日

背景：原始 7 笔建仓在 07-31(周五)早盘完成，但那批记录没走手续费逻辑（fee=0、
份额未扣申购费）。重置重建时已是 08-02 周日晚，按真实规则目标净值日顺延到
08-03，会导致成本价与此前所有分析对不上，且要等周一晚才有持仓。

这里把目标日改回 07-31（净值已公布），清算后即等价于"当初就正确扣了费"。
只影响这一批历史委托，之后的新单照旧走 _target_nav_date 的真实规则。

用法：./backend/venv/bin/python scripts/fix_order_navdate.py [YYYY-MM-DD]
"""
import asyncio
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 后端是靠 uvicorn --env-file 注入的环境变量，独立脚本得自己把 .env 读进来
env_file = ROOT / 'backend' / '.env'
if env_file.exists():
    for raw in env_file.read_text(encoding='utf-8').splitlines():
        raw = raw.strip()
        if raw and not raw.startswith('#') and '=' in raw:
            key, val = raw.split('=', 1)
            os.environ.setdefault(key.strip(), val.strip())

sys.path.insert(0, str(ROOT))

from sqlalchemy import select                      # noqa: E402
from backend.database import async_session         # noqa: E402
from backend.models import SimOrder                # noqa: E402

DEFAULT_TARGET = date(2026, 7, 31)


async def main(target: date) -> None:
    async with async_session() as session:
        orders = (await session.execute(
            select(SimOrder).where(SimOrder.status == 'pending').order_by(SimOrder.id)
        )).scalars().all()
        if not orders:
            print('没有待清算委托，无需修正')
            return
        for o in orders:
            print(f'  {o.code} {o.side:<4} {o.nav_date} -> {target}')
            o.nav_date = target
        await session.commit()
        print(f'已修正 {len(orders)} 笔委托的目标净值日')


if __name__ == '__main__':
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    tgt = datetime.strptime(arg, '%Y-%m-%d').date() if arg else DEFAULT_TARGET
    asyncio.run(main(tgt))
