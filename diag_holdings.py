"""用基金的逻辑分析基金：穿透到底层持仓，看真实暴露与重叠度"""
import json, re
from urllib import request
from collections import defaultdict

BASE = 'http://127.0.0.1:8000'

# 自选里的代表性标的 + 已建仓的进攻标的
TARGETS = ['001753', '001480', '001438', '007490', '006533', '010013', '001513', '018994']

ROW = re.compile(
    r"<td>(\d+)</td>.*?r/[\d.]*(\d{6})'>\2</a></td>"
    r"<td class='tol'><a[^>]*>([^<]+)</a></td>.*?"
    r"<td class='tor'>([\d.]+)%</td>",
    re.S,
)


def holdings(code):
    """返回 [(股票代码, 名称, 占净值%)]，以及报告期。
    页面含多个季度表格，只取第一个 table（最新一期）"""
    d = json.loads(request.urlopen(f'{BASE}/api/fund/holdings/{code}', timeout=40).read())
    html = d.get('content') or ''
    first = html.split('</table>')[0]          # 截断到最新一期
    date = re.search(r"截止至：<font[^>]*>([\d-]+)</font>", html)
    return ROW.findall(first), (date.group(1) if date else '?')


store, allpos = {}, defaultdict(list)
for c in TARGETS:
    try:
        rows, dt = holdings(c)
        picks = [(m[1], m[2], float(m[3])) for m in rows]
        store[c] = (picks, dt)
        for code, name, w in picks:
            allpos[name].append((c, w))
        top = '  '.join(f'{n}({w}%)' for _, n, w in picks[:5])
        print(f"{c} 报告期{dt} 前{len(picks)}大合计占净值 {sum(w for *_, w in picks):.1f}%")
        print(f"      前五：{top}")
    except Exception as e:
        print(f"{c} 解析失败 {e}")

print(f"\n=== 底层股票重叠度（共 {len(store)} 只基金穿透）===")
print(f"{'股票':<10}{'被N只持有':>10}{'平均权重':>10}   明细")
for name, lst in sorted(allpos.items(), key=lambda x: (-len(x[1]), -sum(w for _, w in x[1]))):
    if len(lst) >= 2:
        detail = ' '.join(f'{c}:{w}%' for c, w in lst)
        print(f"{name:<10}{len(lst):>10}{sum(w for _, w in lst)/len(lst):>9.1f}%   {detail}")
