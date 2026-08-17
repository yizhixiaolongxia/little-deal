"""诊断自选基金：赛道归属、动量结构、当前风险位置"""
import json
from urllib import request, parse

BASE = 'http://127.0.0.1:8000'

wl = json.loads(request.urlopen(BASE + '/api/watchlist', timeout=30).read())
codes = wl['codes']

allf = {f['code']: f for f in json.load(open('/tmp/fundlist.json'))['items']}


def rt(code):
    """今日实时估值"""
    try:
        url = BASE + '/api/sim/quote?' + parse.urlencode({'asset_type': 'fund', 'code': code})
        q = json.loads(request.urlopen(url, timeout=30).read())
        return q.get('pct'), q.get('price')
    except Exception:
        return None, None


print(f"{'代码':<8}{'名称':<28}{'今日':>7}{'近1周':>8}{'近1月':>8}{'近3月':>8}{'近6月':>8}{'近1年':>8}{'近2年':>9}{'近3年':>9}")
rows = []
for c in codes:
    f = allf.get(c)
    if not f:
        print(f"{c:<8}{'（全市场列表未收录）':<28}")
        continue
    pct, price = rt(c)
    rows.append((f, pct))
    print(f"{c:<8}{f['name'][:26]:<28}{str(pct):>7}{str(f['week1']):>8}{str(f['month1']):>8}"
          f"{str(f['month3']):>8}{str(f['month6']):>8}{str(f['year1']):>8}"
          f"{str(f['year2']):>9}{str(f['year3']):>9}")

# 汇总：从近1月高点回撤幅度 & 前期涨幅，量化"泡沫程度"
print("\n=== 风险画像（近2年涨幅 vs 近1月回撤，回撤/涨幅比越低说明泡沫吐得越少）===")
print(f"{'代码':<8}{'名称':<28}{'近2年':>9}{'近1月':>9}{'已吐回比例':>12}")
for f, pct in sorted(rows, key=lambda x: (x[0]['month1'] if x[0]['month1'] is not None else 0)):
    y2, m1 = f.get('year2'), f.get('month1')
    ratio = f"{abs(m1) / y2 * 100:.0f}%" if (y2 and m1 and y2 > 0) else '—'
    print(f"{f['code']:<8}{f['name'][:26]:<28}{str(y2):>9}{str(m1):>9}{ratio:>12}")

# 与已建仓方案的重叠检查
held = {'023073', '004814', '019259', '014673', '006327', '096001', '015489'}
print("\n自选与已建仓重叠：", sorted(held & set(codes)) or '无')
