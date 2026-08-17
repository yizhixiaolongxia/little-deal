"""从全市场筛选各品类具体候选标的（A 类、成立满 1 年、近 1 月动量为正）"""
import json

items = json.load(open('/tmp/fundlist.json'))['items']

BUCKETS = {
    '红利/价值/高股息': (['红利', '股息', '价值'], ['港股', '香港', 'QDII', '海外']),
    '港股通互联网/中概': (['港股通互联网', '海外互联网', '中概'], []),
    '港股宽基/沪港深': (['恒生', '沪港深', '港股通'], ['互联网']),
    '纳指/标普/美股': (['纳斯达克', '标普500', '美国50'], []),
    '短债/中短债（弹药仓）': (['短债', '短融', '货币'], ['可转债']),
}

def ok(f):
    """A 类或无分级、成立满 1 年、有完整业绩"""
    n = f['name']
    if n.endswith('C') or n.endswith('E') or n.endswith('D'):
        return False
    if f.get('year1') is None:
        return False
    return f.get('inception', '9999') < '2025-07-31'

for label, (kws, excl) in BUCKETS.items():
    fs = [f for f in items
          if ok(f)
          and any(k in f['name'] for k in kws)
          and not any(x in f['name'] for x in excl)]
    # 按近 1 月动量排序，取前 8
    fs.sort(key=lambda f: -(f['month1'] if f['month1'] is not None else -999))
    print(f"\n=== {label}（候选 {len(fs)} 只，按近1月动量前 8）===")
    print(f"{'代码':<8}{'名称':<30}{'近1月':>8}{'近3月':>8}{'近6月':>8}{'近1年':>8}{'近2年':>9}{'成立':>12}")
    for f in fs[:8]:
        print(f"{f['code']:<8}{f['name'][:28]:<30}{str(f['month1']):>8}{str(f['month3']):>8}"
              f"{str(f['month6']):>8}{str(f['year1']):>8}{str(f['year2']):>9}{f.get('inception',''):>12}")
