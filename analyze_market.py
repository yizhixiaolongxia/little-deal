"""基于全市场基金横截面数据判断当前市场结构（风格、拥挤度、避险迹象）"""
import json, statistics as st
from collections import defaultdict

d = json.load(open('/tmp/fundlist.json'))
items = d['items']
print(f"样本 {len(items)} 只，数据日期 {items[0]['date']}\n")

def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 2) if xs else None

by_type = defaultdict(list)
for f in items:
    by_type[f.get('fund_type') or '未知'].append(f)

print("=== 各类型基金收益中位数（%）===")
print(f"{'类型':<12}{'数量':>6}{'昨日':>8}{'近1周':>8}{'近1月':>8}{'近3月':>9}{'近6月':>9}{'近1年':>9}{'近2年':>9}")
rows = [(len(fs), t, fs) for t, fs in by_type.items() if len(fs) >= 30]
for n, t, fs in sorted(rows, key=lambda x: -x[0]):
    print(f"{t:<12}{n:>6}{str(med([f['daily_pct'] for f in fs])):>8}"
          f"{str(med([f['week1'] for f in fs])):>8}{str(med([f['month1'] for f in fs])):>8}"
          f"{str(med([f['month3'] for f in fs])):>9}{str(med([f['month6'] for f in fs])):>9}"
          f"{str(med([f['year1'] for f in fs])):>9}{str(med([f['year2'] for f in fs])):>9}")

print("\n=== 近1月涨幅前 30（去重同系列）===")
cand = sorted([f for f in items if f.get('month1') is not None], key=lambda f: -f['month1'])
seen = set(); cnt = 0
for f in cand:
    key = f['name'].rstrip('ABCDE').replace('联接', '')
    if key in seen:
        continue
    seen.add(key)
    print(f"{f['code']}  {f['name'][:26]:<28}近1月{f['month1']:>7}  近3月{str(f['month3']):>7}  "
          f"近1年{str(f['year1']):>7}  近2年{str(f['year2']):>7}")
    cnt += 1
    if cnt >= 30:
        break

print("\n=== 近1月跌幅前 15 ===")
cand2 = sorted([f for f in items if f.get('month1') is not None], key=lambda f: f['month1'])
seen = set(); cnt = 0
for f in cand2:
    key = f['name'].rstrip('ABCDE')
    if key in seen:
        continue
    seen.add(key)
    print(f"{f['code']}  {f['name'][:26]:<28}近1月{f['month1']:>7}  近1年{str(f['year1']):>7}  近2年{str(f['year2']):>7}")
    cnt += 1
    if cnt >= 15:
        break

print("\n=== 主题维度：近2年涨幅 vs 近1月回撤 ===")
KW = {
    '半导体/芯片': ['半导体', '芯片', '集成电路'],
    '科技/成长': ['科技', '成长', '创新', '新兴', '数字经济', '专精特新'],
    '人工智能': ['人工智能', '算力', '机器人'],
    '医药': ['医药', '医疗', '生物', '创新药'],
    '消费/白酒': ['消费', '白酒', '食品'],
    '红利/价值': ['红利', '价值', '低波', '股息'],
    '银行/金融': ['银行', '金融', '证券'],
    '黄金/贵金属': ['黄金', '有色', '贵金属'],
    '债券类': ['债'],
    '货币/存单': ['货币', '存单'],
    '宽基指数': ['沪深300', '中证500', '中证A500', '上证50', '中证1000'],
    'QDII/海外': ['纳斯达克', '标普', '美国', '恒生', '港股', '德国', '日本'],
}
res = []
for label, kws in KW.items():
    fs = [f for f in items if any(k in f['name'] for k in kws)]
    if len(fs) < 10:
        continue
    res.append((label, len(fs), med([f['daily_pct'] for f in fs]), med([f['week1'] for f in fs]),
                med([f['month1'] for f in fs]), med([f['month3'] for f in fs]),
                med([f['year1'] for f in fs]), med([f['year2'] for f in fs])))
print(f"{'主题':<14}{'数量':>6}{'昨日':>8}{'近1周':>8}{'近1月':>8}{'近3月':>9}{'近1年':>9}{'近2年':>9}")
for r in sorted(res, key=lambda x: -(x[4] if x[4] is not None else -999)):
    print(f"{r[0]:<14}{r[1]:>6}{str(r[2]):>8}{str(r[3]):>8}{str(r[4]):>8}{str(r[5]):>9}{str(r[6]):>9}{str(r[7]):>9}")
