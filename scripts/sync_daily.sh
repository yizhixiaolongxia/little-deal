#!/bin/bash
# 每日数据同步：基金净值 + 基准指数 + 宏观指标
#
# 为什么合成一个脚本而不是挂两个 launchd 任务：净值和基准是同一次计算的两半，
# 组合超额收益要拿两边同一天的数对齐。各自独立调度的话，迟早出现「净值更新到
# 今天、基准还停在昨天」——那个超额收益是错的，但页面上看不出来是错的。
#
# 两个都跑完才退出，第一个失败不跳过第二个：基准同步不该被净值上游的抖动带下水。
# 宏观同步同理，它打的是完全不同的上游（东财数据中心/新浪），和净值互不相干。
# 退出码：任一失败为 1，方便在 launchd 日志里一眼看出问题。
#
# 手动跑：bash scripts/sync_daily.sh

set -u

# 不依赖 launchd 的 WorkingDirectory：config.py 的 load_dotenv 要读 backend/.env，
# 手动执行时也得落在同一个目录，否则连不上库（报 root 密码为空）
cd "$(dirname "$0")/../backend" || exit 1

PY=./venv/bin/python
status=0

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 每日同步开始 ====="

$PY ../scripts/sync_nav.py || status=1
$PY ../scripts/sync_benchmark.py || status=1
# 宏观指标：月度报表 + 中美国债 + 汇率大宗快照 + 全A估值。汇率和大宗上游只给
# 当前值，漏跑一天那天的点就永久缺了，所以必须跟着每日任务跑而不是按需拉
$PY ../scripts/sync_macro.py || status=1

# ${status} 必须带花括号：后面紧跟的全角括号会被 bash 当成变量名的一部分，
# 写成 $status） 会报 unbound variable，把真正的退出码吃掉
echo "===== $(date '+%H:%M:%S') 每日同步结束（退出码 ${status}）====="
exit $status
