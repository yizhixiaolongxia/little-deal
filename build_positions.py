"""模拟盘建仓脚本：先 dry-run 验证行情，确认无误后再下单"""
import json, sys
from urllib import request, parse, error

BASE = 'http://127.0.0.1:8000'

# (代码, 名称, 计划金额, 所属层)
PLAN = [
    ('023073', '国泰海通中证港股通高股息投资指数(QDII)A', 120000, '主仓'),
    ('004814', '中欧红利优享混合A',                        100000, '主仓'),
    ('019259', '国泰富时国企红利ETF联接A',                  80000, '主仓'),
    ('014673', '富国中证港股通互联网ETF发起式联接A',         60000, '进攻(第1笔/共16万)'),
    ('006327', '易方达中证海外互联网50ETF联接(QDII)A',       40000, '进攻(第1笔/共12万)'),
    ('096001', '大成标普500等权重指数(QDII)A人民币',        120000, '分散'),
    ('015489', '申万菱信稳鑫30天滚动持有短债A',             300000, '弹药'),
]


def get(path, params=None):
    url = BASE + path
    if params:
        url += '?' + parse.urlencode(params)
    with request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def post(path, payload):
    req = request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except error.HTTPError as e:
        return {'__error__': e.code, 'detail': e.read().decode()}


def dry_run():
    print(f"{'代码':<8}{'层':<22}{'计划金额':>10}  行情")
    ok, bad = [], []
    for code, name, amount, layer in PLAN:
        try:
            q = get('/api/sim/quote', {'asset_type': 'fund', 'code': code})
            price = q.get('price')
            if price:
                print(f"{code:<8}{layer:<22}{amount:>10}  ✓ {q.get('name','')[:22]} 净值/估值={price} 涨跌={q.get('pct')}")
                ok.append(code)
            else:
                print(f"{code:<8}{layer:<22}{amount:>10}  ✗ 无价格 {q}")
                bad.append(code)
        except Exception as e:
            print(f"{code:<8}{layer:<22}{amount:>10}  ✗ 异常 {e}")
            bad.append(code)
    print(f"\n可成交 {len(ok)} 只，失败 {len(bad)} 只：{bad}")
    print(f"计划投入 {sum(p[2] for p in PLAN):,} 元，保留现金 {1000000 - sum(p[2] for p in PLAN):,} 元")
    return bad


def execute():
    print('\n===== 开始建仓 =====')
    for code, name, amount, layer in PLAN:
        res = post('/api/sim/trade', {
            'asset_type': 'fund', 'code': code, 'side': 'buy', 'amount': amount,
        })
        if res.get('ok'):
            print(f"✓ {code} {layer:<22} 投入{amount:>8} 成交价{res.get('price')} "
                  f"份额{res.get('shares')} 余额{res.get('cash')}")
        else:
            print(f"✗ {code} {layer:<22} 失败：{res}")


if __name__ == '__main__':
    bad = dry_run()
    if len(sys.argv) > 1 and sys.argv[1] == 'go':
        if bad:
            print('\n存在无法定价标的，已中止。请先调整方案。')
        else:
            execute()
