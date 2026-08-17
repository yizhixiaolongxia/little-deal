"""进程内定时任务：净值/基准/宏观同步 + 决策简报

为什么不用 launchd：项目在 ~/Documents 下，macOS 的 TCC 隐私保护会拒绝 launchd
起的后台进程访问这个目录（报 Operation not permitted，退出码 126）。后端是从
终端启动的，终端已有 TCC 授权，它 spawn 出来的子进程继承这份授权，读得到项目文件。

为什么 spawn 子进程而不是直接 import 那些脚本里的函数：
- sync_daily.sh / daily_brief.py 已经是能独立手动执行的入口。手动和自动走同一份
  代码路径，才不会出现「手动跑是对的、定时跑结果不一样」这种最难查的问题
- daily_brief.py 用同步 urllib 打后端自己的 HTTP 接口，在 event loop 里直接调
  会把整个后端卡死

代价说在明处：后端不跑的时候没有任何东西在同步。这不是「定时任务」，是「后端开着
的时候顺手保证数据新鲜」。所以启动时会检查上一轮该跑的同步有没有跑过，漏了就补。
"""
import asyncio
import json
import sys
from datetime import date, datetime, time as clock, timedelta
from pathlib import Path
from typing import Callable, Awaitable, Dict, List, Optional

from .services import nav_service

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / "backend" / "venv" / "bin" / "python")

# 上次成功跑完的时刻落盘，而不是只放内存：开发时后端是 uvicorn --reload 起的，
# 存一次文件就重启一次进程。只放内存的话每次保存都要重新拉 17 只基金的上游数据，
# 慢，而且容易被上游限流
STAMP = ROOT / ".sync_stamp.json"

# 同步必须早于简报：简报的回撤、风险指标、降仓档位全部源于 fund_nav 表，
# 顺序倒过来的话简报读到的是前一天的净值，回撤滞后一天——而这种错恰好
# 可能把已经该降仓的那天盖过去
# 净值 20:00 之后才陆续公布，而且是持续到深夜的：20:30 跑只能拿到一小部分，
# 剩下的要等一两个小时。踩过一次——那天 17 只里只有 5 只拿到当日净值，
# 简报照跑，回撤算出来偏小一半
SYNC_AT = (22, 0)
BRIEF_AT = (22, 30)

# 净值没到位时简报最多等到这个点。QDII 滞后几个交易日是常态，等它等不到，
# 一直等下去等于今晚没有简报——所以到点就认了，照常出
GIVE_UP_AT = (23, 30)

# 失败后隔多久重试。上游抖动值得再试一次，但别每分钟去捶它
RETRY_GAP = timedelta(minutes=30)

_stamp: Dict[str, datetime] = {}        # 任务名 -> 上次成功完成时刻
_last_try: Dict[str, datetime] = {}     # 任务名 -> 上次尝试时刻（含失败），只用于重试节流
_wait_noted: Optional[date] = None      # 「简报在等净值」的提示当天打过没
_task: Optional[asyncio.Task] = None


def _load_stamp() -> None:
    global _stamp
    try:
        raw = json.loads(STAMP.read_text("utf-8"))
        _stamp = {k: datetime.fromisoformat(v) for k, v in raw.items()}
    except FileNotFoundError:
        _stamp = {}
    except Exception as exc:                                  # noqa: BLE001
        # 文件被改坏就当从没跑过：两个脚本都幂等，多跑一次比漏一次好
        print(f"[scheduler] 时间戳读不出来（{exc!r}），当作从没跑过", flush=True)
        _stamp = {}


def _save_stamp(name: str, when: datetime) -> None:
    _stamp[name] = when
    try:
        STAMP.write_text(
            json.dumps({k: v.isoformat() for k, v in _stamp.items()},
                       ensure_ascii=False, indent=2),
            "utf-8",
        )
    except OSError as exc:
        # 写不进去只是「重启后会多跑一次」，不值得让任务本身算失败
        print(f"[scheduler] 时间戳写不进去：{exc!r}", file=sys.stderr, flush=True)


async def _run(name: str, *argv: str) -> bool:
    """跑一个子进程，把它的输出原样转到后端日志里，返回是否成功

    不抛异常：上游抖动、网络超时都是常态，一次失败不该把调度循环带崩，
    否则后面几天全都不跑了，而日志里只有孤零零一条栈。
    """
    print(f"[scheduler {datetime.now():%H:%M:%S}] {name} 开始", flush=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        for line in (out or b"").decode("utf-8", "replace").splitlines():
            print(f"[{name}] {line}", flush=True)
        ok = proc.returncode == 0
        tag = "完成" if ok else (f"失败（退出码 {proc.returncode}），"
                                 f"{int(RETRY_GAP.total_seconds() // 60)} 分钟后重试")
        print(f"[scheduler {datetime.now():%H:%M:%S}] {name} {tag}", flush=True)
        return ok
    except Exception as exc:                                  # noqa: BLE001
        print(f"[scheduler] {name} 异常：{exc!r}", file=sys.stderr, flush=True)
        return False


async def sync_now() -> bool:
    """同步净值 + 基准 + 宏观。走 sync_daily.sh，保证这几份数据在同一次任务里更新完"""
    return await _run("sync", "/bin/bash", str(ROOT / "scripts" / "sync_daily.sh"))


async def brief_now() -> bool:
    return await _run("brief", PY, str(ROOT / "scripts" / "daily_brief.py"))


async def _fire(name: str, run: Callable[[], Awaitable[bool]]) -> None:
    """跑一次并记账。只有成功才写时间戳——失败的话留给 RETRY_GAP 后重试"""
    _last_try[name] = datetime.now()
    if await run():
        _save_stamp(name, datetime.now())


def _done_today(name: str, now: datetime) -> bool:
    ok = _stamp.get(name)
    return bool(ok and ok.date() >= now.date())


def _past(now: datetime, at: tuple) -> bool:
    """工作日、且已过 at 这个时点。周末不开市，跑了也是拉一遍旧数据"""
    return now.weekday() < 5 and (now.hour, now.minute) >= at


async def _lagging(now: datetime) -> List[str]:
    """还没拿到期望净值日净值的基金；判定本身出错时返回空表

    空表 = 不拦着。这只是个辅助判定，它自己挂了不该把简报永久卡死。
    """
    try:
        return await nav_service.lagging_codes(now)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[scheduler] 净值到位判定失败（{exc!r}），当作已到位",
              file=sys.stderr, flush=True)
        return []


def _note_waiting(now: datetime, lag: List[str]) -> None:
    """简报正在等净值。每天只提示一次——循环 60 秒转一圈，不节流会把日志刷满"""
    global _wait_noted
    if _wait_noted == now.date():
        return
    _wait_noted = now.date()
    print(f"[scheduler] {len(lag)} 只基金还没拿到当日净值（{','.join(lag)}），简报先等着，"
          f"到 {GIVE_UP_AT[0]}:{GIVE_UP_AT[1]:02d} 仍没到就照常出", flush=True)


def _due(name: str, now: datetime, at: tuple, lag: List[str]) -> bool:
    """到点了、今天还没成功跑过、且不在重试冷却里，就返回 True

    用「轮询 + 当天是否跑过」而不是「sleep 到下一个时刻」：后者遇到电脑休眠会
    整段睡过头，醒来后那一天就静默漏掉了。轮询的话醒来第一轮就把漏的补上。

    lag 是还没拿到当日净值的基金，同时决定两件事：同步要不要再跑一轮、简报能不能出。
    """
    if not _past(now, at):
        return False
    last = _last_try.get(name)
    if last and now - last < RETRY_GAP:
        return False

    gave_up = _past(now, GIVE_UP_AT)

    if name == "sync":
        if not _done_today(name, now):
            return True
        # 今天跑过而且成功了，但净值没全到手：同步时点那会儿上游只公布了一部分，
        # 剩下的晚一两个小时才出。不再跑一轮的话这批净值要等到明天，而今晚的简报
        # 会拿缺当日净值的数据算回撤——「同步成功」和「数据到手」不是一回事
        return bool(lag) and not gave_up

    if _done_today(name, now):
        return False
    # 简报的回撤、风险指标、降仓档位全部源于 fund_nav 表。同步没成功就出简报，
    # 读到的是前一天的净值——回撤滞后一天，而这种错恰好可能把已经该降仓的那天
    # 盖过去。宁可今天没简报（缺了看得见），也别出一份看不出错的简报
    if not _done_today("sync", now):
        return False
    return not lag or gave_up


def _startup_due(now: datetime) -> Optional[datetime]:
    """启动时该补哪一轮同步；已经跑过了返回 None

    典型场景：昨天 20:30 电脑是关着的，今天早上开机起服务——那一轮只能在这里补回来。
    """
    day = now.date()
    while True:
        moment = datetime.combine(day, clock(*SYNC_AT))
        if moment <= now and day.weekday() < 5:
            break
        day -= timedelta(days=1)
    ok = _stamp.get("sync")
    return None if (ok and ok >= moment) else moment


async def _loop() -> None:
    _load_stamp()

    moment = _startup_due(datetime.now())
    if moment:
        print(f"[scheduler] 补跑 {moment:%m-%d %H:%M} 那一轮同步", flush=True)
        await _fire("sync", sync_now)
    else:
        print(f"[scheduler] 上次同步 {_stamp['sync']:%m-%d %H:%M}，已覆盖最近一轮，跳过补跑",
              flush=True)

    while True:
        try:
            now = datetime.now()
            # 只在同步时点之后才查库：这个循环 60 秒转一圈，白天没必要一直问
            lag = await _lagging(now) if _past(now, SYNC_AT) else []
            if _due("sync", now, SYNC_AT, lag):
                await _fire("sync", sync_now)
                # 刚跑完，lag 已经旧了。不重新查的话简报会拿着同步前的快照判断，
                # 白白多等一轮（或者反过来提前放行）
                lag = await _lagging(datetime.now())
            if _due("brief", now, BRIEF_AT, lag):
                await _fire("brief", brief_now)
            elif lag and _past(now, BRIEF_AT) and not _done_today("brief", now):
                _note_waiting(now, lag)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                              # noqa: BLE001
            print(f"[scheduler] 循环异常：{exc!r}", file=sys.stderr, flush=True)
        await asyncio.sleep(60)


def start() -> None:
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())
    print(f"[scheduler] 已启动：同步 {SYNC_AT[0]}:{SYNC_AT[1]:02d} / "
          f"简报 {BRIEF_AT[0]}:{BRIEF_AT[1]:02d}（工作日，净值没到位最多等到 "
          f"{GIVE_UP_AT[0]}:{GIVE_UP_AT[1]:02d}）", flush=True)


async def stop() -> None:
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
