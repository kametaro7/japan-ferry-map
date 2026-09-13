#!/usr/bin/env python3
"""航路台帳（data/regions/*.json）を検証・統合し、地図用の data/routes.js を作る。

線の形は OSM の route=ferry を使う（港と港のあいだごとに）:
  1) 台帳の osm に挙がった way/relation だけで経路探索
  2) だめなら OSM のフェリー航路網全体から経路探索（遠回りしすぎないものだけ採用）
  3) それでもだめなら data/overrides.json の via（経由点）か直線
結果の内訳と、直線になった長い区間は work/build_report.txt に出す。

使い方: python3 tools/build.py [--regions 'data/regions/*.json']
"""
import argparse, collections, glob, heapq, json, math, os, re, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICES = ['longferry', 'ferry', 'passenger', 'sightseeing', 'waterbus']
WATERS = ['sea', 'river', 'lake']
REGION_PREFS = [
    ('北海道', ['北海道']),
    ('東北', ['青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県']),
    ('関東', ['茨城県', '栃木県', '群馬県', '埼玉県', '千葉県', '東京都', '神奈川県']),
    ('中部', ['新潟県', '富山県', '石川県', '福井県', '山梨県', '長野県', '岐阜県', '静岡県', '愛知県']),
    ('近畿', ['三重県', '滋賀県', '京都府', '大阪府', '兵庫県', '奈良県', '和歌山県']),
    ('中国', ['鳥取県', '島根県', '岡山県', '広島県', '山口県']),
    ('四国', ['徳島県', '香川県', '愛媛県', '高知県']),
    ('九州', ['福岡県', '佐賀県', '長崎県', '熊本県', '大分県', '宮崎県', '鹿児島県']),
    ('沖縄', ['沖縄県']),
]
PREF_REGION = {p: r for r, ps in REGION_PREFS for p in ps}
PREF_ORDER = [p for _, ps in REGION_PREFS for p in ps]
PREF_ALIAS = {}
for _p in PREF_ORDER:
    PREF_ALIAS[_p] = _p
    PREF_ALIAS[_p if _p == '北海道' else _p[:-1]] = _p
CONF_RANK = {'high': 3, 'medium': 2, 'low': 1}
SEA = None  # tools/searoute.py の SeaRouter（直線になりそうな区間だけ遅延生成）


# ---------------------------------------------------------------- geometry
def hav_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(min(1.0, math.sqrt(h)))


def plen(pts):
    return sum(hav_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def densify(pts, step):
    out = [pts[0]]
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        n = int(hav_m(a, b) // step)
        for k in range(1, n + 1):
            t = k / (n + 1)
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        out.append(b)
    return out


def simplify(pts, tol):
    if len(pts) < 3:
        return pts
    lat0 = math.radians(sum(p[0] for p in pts) / len(pts))
    xy = [(p[1] * 111320 * math.cos(lat0), p[0] * 110540) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        (x1, y1), (x2, y2) = xy[i], xy[j]
        dx, dy = x2 - x1, y2 - y1
        seg = dx * dx + dy * dy
        dmax, kmax = -1.0, -1
        for k in range(i + 1, j):
            x, y = xy[k]
            if seg == 0:
                d = math.hypot(x - x1, y - y1)
            else:
                t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / seg))
                d = math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))
            if d > dmax:
                dmax, kmax = d, k
        if dmax > tol:
            keep[kmax] = True
            stack += [(i, kmax), (kmax, j)]
    return [p for p, k in zip(pts, keep) if k]


def encode_polyline(pts):
    out, plat, plon = [], 0, 0
    for lat, lon in pts:
        ilat, ilon = int(round(lat * 1e5)), int(round(lon * 1e5))
        if out and ilat == plat and ilon == plon:
            continue
        for v in (ilat - plat, ilon - plon):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return ''.join(out)


class Net:
    """フェリー航路線の頂点グラフ（近接した別の線どうしは乗り継ぎ辺でつなぐ）。"""
    CELL = 0.01

    def __init__(self, polylines, step=300, junction=150):
        self.P, self.adj, self.owner = [], [], []
        self.grid = collections.defaultdict(list)
        keymap = {}
        for wi, pts in enumerate(polylines):
            if len(pts) < 2:
                continue
            prev = None
            for p in densify(pts, step):
                k = (round(p[0], 6), round(p[1], 6))
                n = keymap.get(k)
                if n is None:
                    n = len(self.P)
                    keymap[k] = n
                    self.P.append(p)
                    self.adj.append([])
                    self.owner.append(wi)
                    self.grid[self._cell(p)].append(n)
                if prev is not None and prev != n:
                    d = hav_m(self.P[prev], p)
                    self.adj[prev].append((n, d))
                    self.adj[n].append((prev, d))
                prev = n
        if junction:
            for n, p in enumerate(self.P):
                for m in self._near_ids(p, junction):
                    if m > n and self.owner[m] != self.owner[n]:
                        d = hav_m(p, self.P[m])
                        self.adj[n].append((m, d))
                        self.adj[m].append((n, d))

    def _cell(self, p):
        return (int(math.floor(p[0] / self.CELL)), int(math.floor(p[1] / self.CELL)))

    def _near_ids(self, p, r):
        dlat = r / 111000.0
        dlon = r / (111000.0 * max(0.2, math.cos(math.radians(p[0]))))
        c0, c1 = self._cell((p[0] - dlat, p[1] - dlon)), self._cell((p[0] + dlat, p[1] + dlon))
        for i in range(c0[0], c1[0] + 1):
            for j in range(c0[1], c1[1] + 1):
                for m in self.grid.get((i, j), ()):
                    if hav_m(p, self.P[m]) <= r:
                        yield m

    def near(self, p, r, k=40):
        return sorted((hav_m(p, self.P[m]), m) for m in self._near_ids(p, r))[:k]

    def path(self, a, b, r, max_cost, conn_w=1.3):
        starts = self.near(a, r)
        goals = {m: d for d, m in self.near(b, r)}
        if not starts or not goals:
            return None
        g, prev, pq, closed = {}, {}, [], set()
        for d, m in starts:
            c = d * conn_w
            if c < g.get(m, float('inf')):
                g[m], prev[m] = c, None
                heapq.heappush(pq, (c + hav_m(self.P[m], b), c, m))
        best, best_cost = None, float('inf')
        while pq:
            f, c, n = heapq.heappop(pq)
            if f >= best_cost or f > max_cost:
                break
            if n in closed:
                continue
            closed.add(n)
            if n in goals and c + goals[n] * conn_w < best_cost:
                best, best_cost = n, c + goals[n] * conn_w
            for m, d in self.adj[n]:
                nc = c + d
                if nc < g.get(m, float('inf')):
                    g[m], prev[m] = nc, n
                    heapq.heappush(pq, (nc + hav_m(self.P[m], b), nc, m))
        if best is None:
            return None
        seq, n = [], best
        while n is not None:
            seq.append(n)
            n = prev[n]
        seq.reverse()
        coords = [a] + [self.P[n] for n in seq] + [b]
        return coords, hav_m(a, self.P[seq[0]]), hav_m(b, self.P[seq[-1]])


# ---------------------------------------------------------------- OSM
def load_osm():
    els = json.load(open(os.path.join(BASE, 'work/osm/routes.json')))['elements']
    ways, rels = {}, {}
    for e in els:
        if e['type'] == 'way' and e.get('geometry'):
            ways[e['id']] = [(p['lat'], p['lon']) for p in e['geometry']]
    for e in els:
        if e['type'] == 'relation':
            mem = []
            for m in e.get('members', []):
                if m['type'] == 'way' and m.get('geometry'):
                    ways.setdefault(m['ref'], [(p['lat'], p['lon']) for p in m['geometry']])
                    mem.append(m['ref'])
            rels[e['id']] = mem
    return ways, rels


def osm_ways_for(refs, ways, rels, errors, rid):
    out = []
    for ref in refs or []:
        m = re.match(r'^\s*(way|relation|w|r)\s*/?\s*(\d+)\s*$', str(ref))
        if not m:
            errors.append(f'{rid}: osm の形式が不正 {ref!r}')
            continue
        kind, num = m.group(1)[0], int(m.group(2))
        if kind == 'w':
            if num in ways:
                out.append(num)
            else:
                errors.append(f'{rid}: OSM way/{num} が取得データに無い')
        else:
            if num in rels:
                out.extend(rels[num])
            else:
                errors.append(f'{rid}: OSM relation/{num} が取得データに無い')
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- ledger
def norm_operator(s):
    s = re.sub(r'株式会社|（株）|\(株\)|㈱|有限会社|（有）|\(有\)|合同会社|一般社団法人|公益財団法人|一般財団法人', '', s or '')
    return re.sub(r'[\s・･　]', '', s)


def load_ledger(pattern, warns):
    routes = []
    for path in sorted(glob.glob(os.path.join(BASE, pattern))):
        try:
            doc = json.load(open(path, encoding='utf-8'))
        except Exception as ex:
            warns.append(f'{os.path.basename(path)}: JSON を読めない ({ex})')
            continue
        items = doc.get('routes', doc) if isinstance(doc, dict) else doc
        region = os.path.basename(path).split('_')[0]
        for r in items:
            r['_file'] = os.path.basename(path)
            r['_owner'] = region
            routes.append(r)
    return routes


def validate(r, warns):
    rid = r.get('id') or f"{r.get('_file')}:{r.get('name')}"
    ok = True
    for k in ('id', 'name', 'operator', 'operator_url', 'service', 'ports'):
        if not r.get(k):
            warns.append(f'{rid}: 必須項目 {k} が空')
            ok = False
    if r.get('service') not in SERVICES:
        warns.append(f"{rid}: service が不正 {r.get('service')!r}")
        ok = False
    if r.get('water') not in WATERS:
        warns.append(f"{rid}: water が不正 {r.get('water')!r} → sea とみなす")
        r['water'] = 'sea'
    if r.get('operator_url') and not re.match(r'^https?://', r['operator_url']):
        warns.append(f"{rid}: operator_url が URL でない {r['operator_url']!r}")
        ok = False
    ports = []
    for p in r.get('ports') or []:
        try:
            la, lo = float(p['lat']), float(p['lon'])
        except Exception:
            warns.append(f'{rid}: 港の座標が不正 {p!r}')
            ok = False
            continue
        if not (20 <= la <= 47.5 and 118 <= lo <= 155):
            warns.append(f'{rid}: 港の座標が範囲外 {p!r}')
            ok = False
            continue
        ports.append({'name': str(p.get('name', '')).strip(), 'lat': la, 'lon': lo})
    r['ports'] = ports
    if len(ports) < 2 and not r.get('loop'):
        warns.append(f'{rid}: 港が1つで loop でない')
        ok = False
    prefs = []
    for p in r.get('prefectures') or []:
        q = PREF_ALIAS.get(str(p).strip())
        if q:
            prefs.append(q)
        elif str(p).strip():
            warns.append(f'{rid}: 都道府県名を解釈できない {p!r}')
    r['prefectures'] = sorted(set(prefs), key=PREF_ORDER.index)
    if not r['prefectures'] and not r.get('international'):
        warns.append(f'{rid}: prefectures が空')
    for k in ('car', 'overnight', 'loop', 'international', 'seasonal'):
        v = r.get(k)
        if not isinstance(v, bool):
            if v is not None:
                warns.append(f'{rid}: {k} が真偽値でない {v!r} → false')
            r[k] = bool(v) if isinstance(v, bool) else False
    if r.get('status') not in ('active', 'suspended'):
        r['status'] = 'active'
    return ok


def same_route(a, b):
    """同じ事業者・同じ種別と車の可否・同じ港の並び（航路の長さに応じた距離以内）なら同一航路とみなす。"""
    if a['service'] != b['service'] or bool(a['car']) != bool(b['car']):
        return False
    na, nb = norm_operator(a['operator']), norm_operator(b['operator'])
    if not (na == nb or (na and nb and (na in nb or nb in na))):
        return False
    pa, pb = a['ports'], b['ports']
    if len(pa) != len(pb):
        return False
    pts = [(p['lat'], p['lon']) for p in pa]
    length = sum(hav_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    tol = min(2000.0, max(150.0, length * 0.2))  # 短い渡船どうし（数百m間隔）を誤って統合しない
    close = lambda x, y: hav_m((x['lat'], x['lon']), (y['lat'], y['lon'])) < tol
    return all(close(x, y) for x, y in zip(pa, pb)) or all(close(x, y) for x, y in zip(pa, reversed(pb)))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--regions', default='data/regions/*.json')
    ap.add_argument('--out', default='data/routes.js')
    args = ap.parse_args()

    warns, report = [], []
    overrides = {}
    opath = os.path.join(BASE, 'data/overrides.json')
    if os.path.exists(opath):
        overrides = json.load(open(opath, encoding='utf-8'))

    raw = load_ledger(args.regions, warns)
    routes, seen_ids = [], set()
    for r in raw:
        ov = overrides.get(r.get('id'), {})
        if ov.get('drop'):
            report.append(f"除外(overrides): {r.get('id')} {r.get('name')}")
            continue
        r.update(ov.get('set', {}))
        if 'osm' in ov:
            r['osm'] = ov['osm']
        if not validate(r, warns):
            report.append(f"不正のため除外: {r.get('id')} {r.get('name')}")
            continue
        if r['id'] in seen_ids:
            warns.append(f"{r['id']}: id が重複 → 末尾に _2")
            r['id'] += '_2'
        seen_ids.add(r['id'])
        r['_via'] = ov.get('via', {})
        routes.append(r)

    # 重複（同じ事業者・同じ港の並び）を除く。番号の小さい担当・確度の高い方を残す
    routes.sort(key=lambda r: (r['_owner'], -CONF_RANK.get(r.get('confidence'), 0)))
    # 統合するのは担当ファイルをまたぐ重複だけ（同じファイル内は担当者が意図して分けたものとして残す）
    kept = []
    for r in routes:
        dup = next((k for k in kept if k['_owner'] != r['_owner'] and same_route(k, r)), None)
        if dup:
            dup['osm'] = list(dict.fromkeys((dup.get('osm') or []) + (r.get('osm') or [])))
            report.append(f"重複を統合: {r['id']}（{r['_file']}）→ {dup['id']}")
            continue
        near = next((k for k in kept if k['_owner'] == r['_owner'] and same_route(k, r)), None)
        if near:
            report.append(f"同じファイル内で事業者・港が同じ（別便なら問題なし）: {r['id']} / {near['id']}")
        kept.append(r)
    routes = kept

    ways, rels = load_osm()
    gnet = Net(list(ways.values()))
    stats = collections.Counter()
    straight_long = []
    out = []
    for r in routes:
        rid = r['id']
        refs = osm_ways_for(r.get('osm'), ways, rels, warns, rid)
        pts = [(p['lat'], p['lon']) for p in r['ports']]
        pairs = list(zip(pts, pts[1:]))
        if r['loop'] and len(pts) >= 2:
            pairs.append((pts[-1], pts[0]))
        lnet = Net([ways[w] for w in refs]) if refs else None
        lines, srcs = [], []
        if r['loop'] and len(pts) == 1:
            if refs:
                lines = [ways[w] for w in refs]
                srcs = ['listed']
            else:
                srcs = ['none']
        for i, (a, b) in enumerate(pairs):
            direct = hav_m(a, b)
            via = r['_via'].get(str(i))
            seg, src = None, None
            if via:
                seg, src = [a] + [tuple(v) for v in via] + [b], 'via'
            elif direct < 300:
                seg, src = [a, b], 'short'
            else:
                if lnet:
                    res = lnet.path(a, b, 3000, max_cost=3.5 * direct + 5000)
                    if res and res[1] <= 3000 and res[2] <= 3000 and plen(res[0]) <= 3.5 * direct + 5000:
                        seg, src = res[0], 'listed'
                if seg is None:
                    res = gnet.path(a, b, 1500, max_cost=1.6 * direct + 3000)
                    if res and plen(res[0]) <= 1.6 * direct + 3000:
                        seg, src = res[0], 'network'
                if seg is None and 800 < direct < 250000 and os.environ.get('NO_SEA') != '1':
                    global SEA
                    if SEA is None:
                        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                        from searoute import SeaRouter
                        SEA = SeaRouter(BASE, log=lambda m: print(m, file=sys.stderr))
                        if os.environ.get('SEA_CACHE_ONLY') == '1':
                            SEA.broken = True  # キャッシュ済みの海上経路だけ使い、Overpass には問い合わせない
                    res = SEA.route(a, b, r['water'])
                    if res:
                        seg, src = res, 'sea'
                if seg is None:
                    seg, src = [a, b], 'straight'
                    if direct > 3000:
                        straight_long.append((direct, rid, r['name'], i, r['ports'][i]['name'],
                                              r['ports'][(i + 1) % len(pts)]['name']))
            srcs.append(src)
            if lines and src != 'listed' and lines[-1][-1] == seg[0]:
                lines[-1] = lines[-1] + seg[1:]
            elif lines and lines[-1][-1] == seg[0]:
                lines[-1] = lines[-1] + seg[1:]
            else:
                lines.append(seg)
        for s in srcs:
            stats[s] += 1
        total = sum(plen(l) for l in lines)
        tol = min(60.0, max(6.0, total / 2500.0))
        enc = [encode_polyline(simplify(l, tol)) for l in lines if len(l) >= 2]
        if 'straight' in srcs:
            gsrc = 'straight' if all(s in ('straight', 'short') for s in srcs) else 'partial'
        elif srcs == ['none']:
            gsrc = 'none'
        elif 'sea' in srcs:
            gsrc = 'approx'
        else:
            gsrc = 'osm'
        regions = sorted({PREF_REGION[p] for p in r['prefectures']}, key=[x for x, _ in REGION_PREFS].index)
        if r.get('international'):
            regions.append('国際')
        out.append({
            'id': rid, 'name': r['name'], 'operator': r['operator'], 'operator_url': r['operator_url'],
            'route_url': r.get('route_url') or '', 'service': r['service'], 'water': r['water'],
            'loop': r['loop'], 'car': r['car'], 'car_note': r.get('car_note') or '',
            'bike': r.get('bike'), 'bicycle': r.get('bicycle'),
            'overnight': r['overnight'], 'overnight_note': r.get('overnight_note') or '',
            'international': r['international'], 'countries': r.get('countries') or ['JP'],
            'prefectures': r['prefectures'], 'regions': regions,
            'seasonal': r['seasonal'], 'season_note': r.get('season_note') or '',
            'status': r['status'], 'duration': r.get('duration') or '', 'frequency': r.get('frequency') or '',
            'vessels': r.get('vessels') or '', 'notes': r.get('notes') or '',
            'confidence': r.get('confidence') or '', 'sources': r.get('sources') or [],
            'ports': [[p['name'], round(p['lat'], 5), round(p['lon'], 5)] for p in r['ports']],
            'lines': enc, 'gsrc': gsrc,
        })

    order = {s: i for i, s in enumerate(SERVICES)}
    out.sort(key=lambda x: (PREF_ORDER.index(x['prefectures'][0]) if x['prefectures'] else -1,
                            order[x['service']], x['name']))
    doc = {'updated': max([r.get('updated', '') for r in []] + ['2026-09-13']), 'count': len(out), 'routes': out}
    js = 'window.FERRY_DATA=' + json.dumps(doc, ensure_ascii=False, separators=(',', ':')) + ';\n'
    with open(os.path.join(BASE, args.out), 'w', encoding='utf-8') as f:
        f.write(js)

    c_srv = collections.Counter(x['service'] for x in out)
    lines_rep = [
        f'航路 {len(out)} 件（台帳 {len(raw)} 件）',
        '種別: ' + ', '.join(f'{k}={c_srv[k]}' for k in SERVICES),
        f"夜行・船中泊 {sum(x['overnight'] for x in out)} / マイカー {sum(x['car'] for x in out)} / 国際 {sum(x['international'] for x in out)} / 季節 {sum(x['seasonal'] for x in out)} / 休航 {sum(x['status'] == 'suspended' for x in out)}",
        '区間の線の出どころ: ' + ', '.join(f'{k}={v}' for k, v in stats.most_common()),
        '線の種類（航路単位）: ' + ', '.join(f'{k}={v}' for k, v in collections.Counter(x['gsrc'] for x in out).most_common()),
        f'出力 {args.out} {os.path.getsize(os.path.join(BASE, args.out)) / 1024:.0f}KB',
        '', f'## 警告 {len(warns)} 件', *warns,
        '', f'## 処理メモ {len(report)} 件', *report,
        '', f'## 直線になった3km超の区間 {len(straight_long)} 件',
        *[f'{d / 1000:.1f}km {rid} [{i}] {pa}→{pb} ({name})' for d, rid, name, i, pa, pb in sorted(straight_long, reverse=True)],
    ]
    with open(os.path.join(BASE, 'work/build_report.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines_rep) + '\n')
    print('\n'.join(lines_rep[:6]))
    print(f'警告 {len(warns)} / 処理メモ {len(report)} / 直線3km超 {len(straight_long)} → work/build_report.txt')


if __name__ == '__main__':
    main()
