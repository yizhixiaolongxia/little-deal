"""每日 14:30 决策简报生成器

设计前提：我（AI）没有常驻进程，无法自己定时醒来。所以由 cron 在每个交易日
14:30 跑这个脚本，把「市场温度 + 持仓体检 + 约束越界 + 费率窗口 + 主题热度」
落成一份 Markdown。之后你叫我时，我读当天的简报再决定要不要动手。

脚本本身**不下单**，只产出事实与触发信号。下单永远是读完简报后的显式动作。

用法：
    python3 scripts/daily_brief.py            # 生成当天简报
    python3 scripts/daily_brief.py --stdout   # 同时打印到终端

输出：
    briefs/YYYY-MM-DD.md        当天简报
    briefs/asset_history.jsonl  每日盘中总资产快照，仅作审计留痕

回撤纪律读的是 /api/sim/curve（按成交流水 + 逐日收盘净值重算），不是上面那份
jsonl。早期版本靠 jsonl 攒逐日快照算峰值，漏跑就断档、且记的是盘中估值，
会低估回撤——那是「该降仓时不提示」的错法，已经改掉。
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path
from urllib import error, parse, request

BASE = 'http://127.0.0.1:8000'
ROOT = Path(__file__).resolve().parent.parent
BRIEF_DIR = ROOT / 'briefs'
HISTORY = BRIEF_DIR / 'asset_history.jsonl'

# ── 风险约束（档位尚未最终确认，当前用「平衡档」建议值）─────────────────
# 改这里就能换档，简报会按新阈值判定越界
#
# 回撤阈值不在这里：它的唯一定义处是 backend/services/portfolio_service.py 的
# DRAWDOWN_HALF / DRAWDOWN_THIRD，随 /api/sim/curve 的 discipline 字段下发，
# 档位判定也由后端做。两处各存一份阈值的话，改了一处忘了另一处，错法就是
# 「该降仓时不提示」——跟当初用 jsonl 快照低估回撤是同一种错。
LIMITS = {
    'single_max': 250000,      # 单一标的市值上限
    'theme_max': 400000,       # 单主题市值上限
    'monthly_trades': 4,       # 每月调仓次数上限
}

# 赎回费档位边界（天），用来算「再等几天降一档」
FEE_STEPS = [7, 30, 365, 730]

# 这两类是现金替代（弹药池），不参与集中度约束——
# 否则 30 万短债会天天触发「单一标的超限」，信号喊多了就没人看了
CASH_LIKE = ('债券类', '货币/存单')

# 主题归类关键词（按赛道分桶，有增删在这里改就行）
THEMES = {
    '半导体/芯片': ['半导体', '芯片', '集成电路'],
    '人工智能': ['人工智能', '算力', '机器人'],
    '科技/成长': ['科技', '成长', '创新', '新兴', '数字经济', '专精特新'],
    '互联网/港股科技': ['互联网', '恒生科技'],
    '医药': ['医药', '医疗', '生物', '创新药'],
    '消费/白酒': ['消费', '白酒', '食品'],
    '红利/价值': ['红利', '价值', '低波', '股息', '高股息', '国企'],
    '银行/金融': ['银行', '金融', '证券'],
    '黄金/贵金属': ['黄金', '有色', '贵金属'],
    '债券类': ['债'],
    '货币/存单': ['货币', '存单'],
    '宽基指数': ['沪深300', '中证500', '中证A500', '上证50', '中证1000'],
    'QDII/海外': ['纳斯达克', '标普', '美国', '德国', '日本', '海外'],
}


def get(path, params=None, timeout=60):
    url = BASE + path
    if params:
        url += '?' + parse.urlencode(params)
    try:
        with request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (error.URLError, error.HTTPError, TimeoutError) as e:
        return {'__error__': str(e)}


def ok(data):
    return isinstance(data, dict) and '__error__' not in data


def money(v, digits=2):
    if v is None:
        return '--'
    return f'{float(v):,.{digits}f}'


def pct(v, digits=2, sign=False):
    """百分比。取不到就是 '--'，不拿 0 冒充——0% 和「算不出来」是两件事"""
    if v is None:
        return '--'
    return f'{float(v):+.{digits}f}%' if sign else f'{float(v):.{digits}f}%'


def ratio(v):
    """夏普、卡玛这类无量纲比值"""
    return '--' if v is None else f'{float(v):.2f}'


def drawdown_pct(v):
    """回撤后端给的是正数幅度，显示时带回负号"""
    if v is None:
        return '--'
    return '0.00%' if float(v) <= 0 else f'-{float(v):.2f}%'


def theme_of(name):
    """按名称关键词给标的归一个主题，命中多个取第一个"""
    for label, kws in THEMES.items():
        if any(k in (name or '') for k in kws):
            return label
    return '其他'


# ── 各区块 ────────────────────────────────────────────────────────────

def section_market(risk, hist):
    lines = ['## 一、市场温度', '']
    if not ok(risk):
        lines += [f'⚠️ 行情接口没拿到数据：{risk.get("__error__")}', '']
        return lines, None

    s = risk.get('sentiment') or {}
    score = s.get('score')
    lines.append(f'**情绪评分 {score} / 100 — {s.get("level")}**（更新于 {risk.get("updated_at")}）')
    lines.append('')
    lines.append('| 指数 | 点位 | 当日 |')
    lines.append('| --- | --- | --- |')
    for idx in risk.get('indices') or []:
        pct = idx.get('pct')
        lines.append(f'| {idx.get("name")} | {money(idx.get("price"))} | '
                     f'{"--" if pct is None else f"{pct:+.2f}%"} |')
    lines.append('')

    # 近 10 个交易日的分数走向，判断是在升温还是降温
    if isinstance(hist, list) and hist:
        tail = hist[-10:]
        track = ' → '.join(str(h['score']) for h in tail)
        lines.append(f'近 {len(tail)} 个交易日评分：{track}')
        if len(tail) >= 2:
            delta = tail[-1]['score'] - tail[0]['score']
            trend = '升温' if delta > 3 else ('降温' if delta < -3 else '横盘')
            lines.append(f'区间变化 {delta:+d} 分，判定：**{trend}**')
        lines.append('')
    return lines, score


def section_position(acc, curve, excess):
    lines = ['## 二、持仓体检', '']
    if not ok(acc):
        lines += [f'⚠️ 账户接口没拿到数据：{acc.get("__error__")}', '']
        return lines, [], None

    positions = acc.get('positions') or []
    total = acc.get('total_asset')
    lines.append(f'总资产 **{money(total)}** ｜ 可用现金 {money(acc.get("cash"))} ｜ '
                 f'在途 {money(acc.get("pending_cash"))} ｜ 持仓市值 {money(acc.get("market_value"))}')
    lines.append('')
    lines += _portfolio_excess_block(curve)
    lines += _portfolio_risk_block(curve)
    lines.append('| 名称 | 代码 | 市值 | 占比 | 浮盈 | 收益率 | 持有天数 | 近1年超额 | 此刻全卖手续费 |')
    lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    stale = []
    for p in sorted(positions, key=lambda x: -(x.get('market_value') or 0)):
        lots = p.get('lots') or []
        days = [l.get('hold_days', 0) for l in lots]
        day_txt = '--' if not days else (str(days[0]) if len(set(days)) == 1
                                         else f'{min(days)}~{max(days)}')
        profit = p.get('profit') or 0
        ex = (excess or {}).get(p.get('code')) or {}
        ex_txt = '--' if ex.get('excess') is None else f'{ex["excess"]:+.2f}pp'
        lines.append(
            f'| {(p.get("name") or "")[:20]} | {p.get("code")} | {money(p.get("market_value"))} | '
            f'{money(p.get("weight"), 1)}% | {profit:+,.2f} | {(p.get("profit_pct") or 0):+.2f}% | '
            f'{day_txt} | {ex_txt} | {money(p.get("sell_fee_now"))} |'
        )
        if p.get('stale'):
            stale.append(p.get('code'))
    lines.append('')
    lines.append(f'合计「此刻全部清仓」需付手续费 **{money(sum(p.get("sell_fee_now") or 0 for p in positions))} 元**')
    if stale:
        lines.append(f'⚠️ 这些标的用的是过期价格，别拿来做决策：{", ".join(stale)}')
    lines.append('')
    lines.append('> 14:30 跑的简报，基金显示的是盘中估值，不是当日最终净值。')
    lines.append('> 「近1年超额」是这只基金近 365 天相对沪深300 的表现（按累计净值、含分红），')
    lines.append('> 跟你持有多久无关——刚买两天的标的也会显示一整年的数，别把它当成自己赚的。')
    lines.append('> 短债与海外标的跟沪深300 不同源，它们那一列基本是汇率和利率行情的噪音，')
    lines.append('> 不能当成「该换掉」的依据——弹药仓存在的意义本来就不是跟大盘赛跑。')
    lines.append('')

    pending = acc.get('pending_orders') or []
    if pending:
        lines.append(f'**还有 {len(pending)} 笔委托在等清算**，别在同一标的上重复下单：')
        for o in pending:
            lines.append(f'- {o.get("code")} {o.get("name") or ""} '
                         f'{"买入" if o.get("side") == "buy" else "卖出"}，'
                         f'按 {o.get("nav_date")} 净值成交')
        lines.append('')
    return lines, positions, total


def _portfolio_excess_block(curve):
    """组合相对基准的表现

    绝对收益率答不了「这两天亏 0.12% 是我的问题还是大盘的问题」，这一段答。
    组合收益率的基数是初始资金，建仓手续费算在里面——不把成本排除出去。
    """
    b = (curve.get('benchmark') or {}) if ok(curve) else {}
    if not b:
        return []
    if b.get('excess') is None:
        why = b.get('reason') or '基准数据缺失'
        return [f'⚠️ 组合超额收益算不出来：{why}', '']
    return [
        f'自 {b["start"]} 建仓以来组合 **{b["pct"]:+.2f}%**，同期{b["benchmark"]} '
        f'{b["benchmark_pct"]:+.2f}%，超额 **{b["excess"]:+.2f}pp**'
        f'（截至 {b["end"]} 收盘）',
        '',
        f'> {b["warning"]}',
        '',
    ]


def _portfolio_risk_block(curve):
    """组合风险指标

    年化/波动/夏普/卡玛都由后端算（口径对齐前端基金对比面板那套）。样本不够
    时后端返回 None 并给 reason，这里就照实空着：把 3 天的 -0.12% 年化会得到
    「年化 -14%」，那是个看着像结论的噪音，比留空危险。
    """
    r = (curve.get('risk') or {}) if ok(curve) else {}
    if not r:
        return []
    lines = [
        f'组合风险指标（样本 {r.get("trading_days")} 个交易日）：',
        '',
        '| 累计收益 | 年化收益 | 年化波动 | 夏普 | 卡玛 | 最大回撤 | 日胜率 |',
        '| --- | --- | --- | --- | --- | --- | --- |',
        f'| {pct(r.get("cum_return"), sign=True)} | {pct(r.get("ann_return"), sign=True)} | '
        f'{pct(r.get("ann_vol"))} | {ratio(r.get("sharpe"))} | {ratio(r.get("calmar"))} | '
        f'{drawdown_pct(r.get("max_drawdown"))} | {pct(r.get("win_rate"), 1)} |',
        '',
    ]
    if r.get('reason'):
        lines += [f'⚠️ 年化与波动类指标空着，是因为：{r["reason"]}', '']
    lines.append(f'> 夏普按无风险利率 {pct(r.get("rf"))} 算，与基金对比面板同口径，'
                 f'两边的数可以横向比。')
    if r.get('note'):
        lines.append(f'> {r["note"]}')
    lines.append('')
    return lines


def section_risk_check(positions, total, curve):
    lines = ['## 三、约束越界检查', '']
    disc = (curve.get('discipline') or {}) if ok(curve) else {}
    half, third = disc.get('half'), disc.get('third')
    dd_txt = (f'回撤 -{half}% 降半仓 / -{third}% 降三成仓'
              if half is not None and third is not None
              else '回撤阈值本次没取到（见下方回撤纪律）')
    lines.append(f'当前档位：单一标的 ≤{LIMITS["single_max"]:,} ｜ '
                 f'单主题 ≤{LIMITS["theme_max"]:,} ｜ {dd_txt}')
    lines.append('（上限只约束风险资产，债券与货币算弹药不计入）')
    lines.append('')
    hits = []

    buckets = {}
    for p in positions:
        buckets.setdefault(theme_of(p.get('name')), []).append(p)
    risk_mv = sum(p.get('market_value') or 0
                  for t, ps in buckets.items() if t not in CASH_LIKE for p in ps)

    for p in positions:
        mv = p.get('market_value') or 0
        if theme_of(p.get('name')) in CASH_LIKE:
            continue
        if mv > LIMITS['single_max']:
            hits.append(f'**单一标的超限**：{p.get("code")} {(p.get("name") or "")[:16]} '
                        f'市值 {money(mv)}，超出 {money(mv - LIMITS["single_max"])}')

    lines.append(f'风险资产 **{money(risk_mv)}**，弹药（债券/货币）{money((total or 0) - risk_mv)}'
                 f'（含现金）')
    lines.append('')
    lines.append('| 主题 | 市值 | 占总资产 | 占风险仓 | 标的 |')
    lines.append('| --- | --- | --- | --- | --- |')
    for t, ps in sorted(buckets.items(), key=lambda kv: -sum(p.get('market_value') or 0 for p in kv[1])):
        mv = sum(p.get('market_value') or 0 for p in ps)
        pct = mv / total * 100 if total else 0
        cash_like = t in CASH_LIKE
        risk_pct = '弹药' if cash_like else (f'{mv / risk_mv * 100:.1f}%' if risk_mv else '--')
        lines.append(f'| {t} | {money(mv)} | {pct:.1f}% | {risk_pct} | '
                     f'{", ".join(p.get("code") for p in ps)} |')
        if not cash_like and mv > LIMITS['theme_max']:
            hits.append(f'**单主题超限**：{t} 市值 {money(mv)}，超出 {money(mv - LIMITS["theme_max"])}')
    lines.append('')

    lines += _drawdown_block(curve, hits)

    if hits:
        lines.append('### 越界项')
        lines += [f'- {h}' for h in hits]
    else:
        lines.append('✅ 没有越界项。')
    lines.append('')
    return lines, hits


def _drawdown_block(curve, hits):
    """回撤纪律判定

    数据源是 /api/sim/curve：按成交流水 + 逐日收盘净值重算。不再读
    asset_history.jsonl 那份逐日快照——它漏跑一天就断档，而峰值取 max，
    丢点会低估峰值、进而低估回撤，错在「该降仓时不提示」这个方向上。

    档位判定也交给后端（discipline.stage），这里只把结论写成人话。后端不因
    样本短就不判：回撤 15% 是真金白银的亏损，不因为历史短就不算。
    """
    if not ok(curve):
        return [f'⚠️ 组合曲线没拿到，回撤纪律本次无法判定：{curve.get("__error__")}', '']
    if not curve.get('days'):
        return ['⚠️ 还没有成交流水，回撤无从计算。', '']

    dd = curve.get('drawdown') or 0.0
    lines = [
        f'组合从峰值回撤 **{dd:.2f}%**（峰值 {money(curve.get("peak"))} '
        f'@ {curve.get("peak_date")} → 最新 {money(curve.get("latest_total"))} '
        f'@ {curve.get("end")}）',
        '',
        f'区间最大回撤 {(curve.get("max_drawdown") or 0.0):.2f}% '
        f'@ {curve.get("max_drawdown_date")}'
        f'，样本 {curve.get("days")} 个交易日（自 {curve.get("start")}）',
        '',
        f'> 回撤按收盘净值算，截至 {curve.get("end")}，不含今日盘中波动——',
        '> 降仓这种动作不应该被盘中估值触发。',
        '',
    ]
    # 回本需要的涨幅比回撤幅度大（跌 20% 要涨 25%），这不对称就是降仓纪律的理由。
    # 但回撤很小时两个数四舍五入后一模一样，那句提醒反而像胡话，拉开了再说
    recover = (curve.get('risk') or {}).get('recover_pct')
    if recover:
        txt = f'从当前位置涨回峰值需 **{recover:+.2f}%**'
        if recover - dd >= 0.5:
            txt += f'（回撤 {dd:.2f}% 不是涨 {dd:.2f}% 就能回本，这不对称就是降仓纪律的理由）'
        lines += [txt, '']

    for w in curve.get('warnings') or []:
        lines.append(f'⚠️ {w}')
    if curve.get('warnings'):
        lines.append('')

    disc = curve.get('discipline') or {}
    stage = disc.get('stage')
    if stage == 'third':
        hits.append(f'**回撤触发降至三成仓**：已回撤 {dd:.2f}%（阈值 {disc.get("third")}%）')
    elif stage == 'half':
        hits.append(f'**回撤触发降半仓**：已回撤 {dd:.2f}%（阈值 {disc.get("half")}%）')
    elif stage == 'normal':
        gap = disc.get('gap_to_half')
        if gap is not None:
            lines += [f'当前未触发降仓，距降半仓线还有 {gap:.2f} 个百分点。', '']
    else:
        # 接口没下发 discipline（旧版后端或字段改名）。默默不判就是「该降仓时不提示」，
        # 所以当成越界项喊出来，而不是当成「没触发」
        hits.append(f'**回撤纪律无法判定**：接口没下发 discipline 档位（当前回撤 '
                    f'{dd:.2f}%），这不等于没触发，得人工看一眼')
    return lines


def section_fee_window(positions):
    """算出每只再等几天能降一档赎回费，避免为了小波动付 1.5% 惩罚费"""
    lines = ['## 四、赎回费窗口', '', '想卖之前先看这张表：差一两天就降档的，等一下更划算。', '']
    lines.append('| 名称 | 代码 | 持有天数 | 当前费率 | 再等 | 降到 | 能省 |')
    lines.append('| --- | --- | --- | --- | --- | --- | --- |')
    tips = []
    for p in positions:
        if p.get('asset_type') != 'fund':
            continue
        for lot in p.get('lots') or []:
            d = lot.get('hold_days', 0)
            rate = lot.get('redeem_rate')
            if rate is None:
                continue
            nxt = next((b for b in FEE_STEPS if b > d), None)
            if nxt is None:
                continue
            wait = nxt - d
            # 下一档费率：用边界天数反查
            nxt_rate = 0.75 if nxt == 7 else (0.5 if nxt == 30 else (0.25 if nxt == 365 else 0.0))
            value = float(lot.get('shares') or 0) * float(p.get('price') or 0)
            save = value * (float(rate) - nxt_rate) / 100
            lines.append(f'| {(p.get("name") or "")[:18]} | {p.get("code")} | {d} | {rate}% | '
                         f'{wait} 天 | {nxt_rate}% | {money(save)} |')
            if wait <= 3 and save > 200:
                tips.append(f'{p.get("code")} 再等 {wait} 天，赎回费从 {rate}% 降到 {nxt_rate}%，省 {money(save)} 元')
    lines.append('')
    if tips:
        lines.append('### 值得等一等')
        lines += [f'- {t}' for t in tips]
        lines.append('')
    return lines, tips


def section_theme(fundlist):
    lines = ['## 五、全市场主题热度', '']
    if not ok(fundlist):
        lines += [f'⚠️ 全市场列表没拿到：{fundlist.get("__error__")}', '']
        return lines
    items = fundlist.get('items') or []
    if not items:
        lines += ['⚠️ 列表为空。', '']
        return lines

    def med(xs):
        xs = sorted(x for x in xs if x is not None)
        if not xs:
            return None
        n = len(xs)
        return round(xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2, 2)

    lines.append(f'样本 {len(items)} 只，数据日期 {items[0].get("date")}。各主题收益中位数（%）：')
    lines.append('')
    lines.append('| 主题 | 数量 | 昨日 | 近1周 | 近1月 | 近3月 | 近1年 |')
    lines.append('| --- | --- | --- | --- | --- | --- | --- |')
    rows = []
    for label, kws in THEMES.items():
        fs = [f for f in items if any(k in (f.get('name') or '') for k in kws)]
        if len(fs) < 10:
            continue
        rows.append((label, len(fs),
                     med([f.get('daily_pct') for f in fs]), med([f.get('week1') for f in fs]),
                     med([f.get('month1') for f in fs]), med([f.get('month3') for f in fs]),
                     med([f.get('year1') for f in fs])))
    for r in sorted(rows, key=lambda x: -(x[4] if x[4] is not None else -999)):
        lines.append(f'| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} |')
    lines.append('')
    return lines


def section_signals(score, hits, tips):
    lines = ['## 六、今天的触发信号', '']
    sigs = []
    if score is not None:
        if score >= 76:
            sigs.append(f'情绪 {score} 分（极度贪婪）→ 考虑减仓、把利润挪进短债')
        elif score <= 25:
            sigs.append(f'情绪 {score} 分（极度恐惧）→ 考虑动用短债弹药加仓')
        elif score >= 56:
            sigs.append(f'情绪 {score} 分（贪婪）→ 不加仓，盯住越界项')
        elif score <= 45:
            sigs.append(f'情绪 {score} 分（恐惧）→ 可小步分批，别一次打完')
        else:
            sigs.append(f'情绪 {score} 分（中性）→ 无操作必要')
    sigs += hits
    if tips:
        sigs.append('有标的临近赎回费降档，卖出动作建议推迟（见第四节）')
    lines += [f'- {s}' for s in sigs] if sigs else ['- 无。']
    lines += ['', '---', '',
              '这份简报只陈述事实和信号，**没有任何自动下单**。',
              '需要调仓时，把这份简报给我，我读完再给出具体买卖清单。', '']
    return lines


# ── 组装 ──────────────────────────────────────────────────────────────

def append_history(total, acc):
    """记一条当日快照；同一天重跑就覆盖，别让文件长出重复行

    注意：这份文件现在只是审计留痕（事后想知道某天盘中看到的是多少），
    回撤纪律已改由 /api/sim/curve 按收盘净值重算，不再依赖这里的逐日点。
    所以漏记几天不再影响任何判定。
    """
    if total is None:
        return
    BRIEF_DIR.mkdir(exist_ok=True)
    today = date.today().strftime('%Y-%m-%d')
    kept = []
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding='utf-8').splitlines():
            try:
                if json.loads(line).get('date') != today:
                    kept.append(line)
            except json.JSONDecodeError:
                continue
    kept.append(json.dumps({'date': today,
                            'time': datetime.now().strftime('%H:%M:%S'),
                            'total_asset': total,
                            'cash': acc.get('cash') if ok(acc) else None,
                            'market_value': acc.get('market_value') if ok(acc) else None,
                            'intraday': True},
                           ensure_ascii=False))
    HISTORY.write_text('\n'.join(kept) + '\n', encoding='utf-8')


def main():
    risk = get('/api/market/risk')
    hist = get('/api/market/risk/history', {'days': 30})
    acc = get('/api/sim/account')
    curve = get('/api/sim/curve', {'with_points': 'false'})
    fundlist = get('/api/fund/list', timeout=120)

    # 持仓里的基金各自相对基准的表现，非基金标的没有净值序列，取不到就留空
    held = [p.get('code') for p in ((acc.get('positions') or []) if ok(acc) else [])
            if p.get('asset_type') == 'fund' and p.get('code')]
    ex = get('/api/benchmark/excess',
             {'codes': ','.join(held), 'days': 365}) if held else {}
    excess = {i['code']: i for i in (ex.get('items') or [])} if ok(ex) else {}

    if isinstance(hist, dict) and '__error__' in hist:
        hist = []
    elif isinstance(hist, dict):
        hist = hist.get('list') or hist.get('items') or []

    head = [f'# 决策简报 {date.today().strftime("%Y-%m-%d")}',
            '', f'生成时间 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', '']

    m_lines, score = section_market(risk, hist)
    p_lines, positions, total = section_position(acc, curve, excess)

    r_lines, hits = section_risk_check(positions, total, curve) if positions else ([], [])
    f_lines, tips = section_fee_window(positions) if positions else ([], [])
    t_lines = section_theme(fundlist)
    s_lines = section_signals(score, hits, tips)

    body = '\n'.join(head + m_lines + p_lines + r_lines + f_lines + t_lines + s_lines)

    BRIEF_DIR.mkdir(exist_ok=True)
    out = BRIEF_DIR / f'{date.today().strftime("%Y-%m-%d")}.md'
    out.write_text(body, encoding='utf-8')
    append_history(total, acc)

    print(f'简报已写入 {out}')
    if '--stdout' in sys.argv:
        print()
        print(body)


if __name__ == '__main__':
    main()
