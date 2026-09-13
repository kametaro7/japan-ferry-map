#!/usr/bin/env python3
"""OSM の route=ferry 候補のうち、台帳（data/regions/*.json）のどの航路にも対応していないものを担当別に列挙する。

対応しているとみなす条件:
  - 台帳の osm にその way/relation（または relation のメンバー way）が挙がっている
  - または候補の両端が、同じ航路のいずれかの港から 1.5km 以内
出力: work/coverage/<NN>_<key>.md と標準出力の件数
"""
import glob, json, math, os, re, collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def hav_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(min(1.0, math.sqrt(h)))


def main():
    idx = json.load(open(os.path.join(BASE, 'work/osm/index.json')))
    cands = idx['routes']
    member_of = collections.defaultdict(set)
    for c in cands:
        for m in c['members']:
            member_of[m].add(c['osm'])

    routes = []
    for p in sorted(glob.glob(os.path.join(BASE, 'data/regions/*.json'))):
        try:
            doc = json.load(open(p, encoding='utf-8'))
        except Exception as ex:
            print('読めない:', p, ex)
            continue
        for r in doc.get('routes', []):
            r['_file'] = os.path.basename(p)
            routes.append(r)

    referenced = set()
    for r in routes:
        for ref in r.get('osm') or []:
            m = re.match(r'^\s*(way|relation|w|r)\s*/?\s*(\d+)', str(ref))
            if not m:
                continue
            key = ('way/' if m.group(1)[0] == 'w' else 'relation/') + m.group(2)
            referenced.add(key)
            referenced |= member_of.get(key, set())
    for c in cands:
        if c['osm'].startswith('relation/') and any(m in referenced for m in c['members']):
            referenced.add(c['osm'])

    ports = []
    for r in routes:
        pts = []
        for q in r.get('ports') or []:
            try:
                pts.append((float(q['lat']), float(q['lon'])))
            except Exception:
                pass
        ports.append((r, pts))

    def covered_by_ports(c):
        a, b = c['ends'][0]['pt'], c['ends'][1]['pt']
        for r, pts in ports:
            if any(hav_m(a, p) < 1500 for p in pts) and any(hav_m(b, p) < 1500 for p in pts):
                return r['id']
        return None

    outdir = os.path.join(BASE, 'work/coverage')
    os.makedirs(outdir, exist_ok=True)
    by_agent = collections.defaultdict(list)
    n_ref = n_port = 0
    for c in cands:
        if c['osm'] in referenced:
            n_ref += 1
            continue
        hit = covered_by_ports(c)
        if hit:
            n_port += 1
            continue
        for a in c['agents']:
            by_agent[a].append(c)
    files = {os.path.basename(p).split('_')[0]: os.path.basename(p)[:-3] for p in glob.glob(os.path.join(BASE, 'work/candidates/*.md'))}
    print(f'候補 {len(cands)} / osm参照で対応 {n_ref} / 港の位置で対応 {n_port} / 未対応（担当ごと、重複あり）:')
    for a in sorted(files):
        lst = sorted(by_agent.get(a, []), key=lambda c: (-c['length_km']))
        lines = [f'# {files[a]} 未対応のOSM候補 {len(lst)} 件', '',
                 '台帳のどの航路の osm にも無く、両端がどの航路の港からも1.5km以上離れているもの。廃止・対象外なら無視してよい。', '']
        for c in lst:
            e0, e1 = c['ends']
            tg = c['tags']
            extra = ' | '.join(f'{k}={tg[k]}' for k in ('operator', 'website', 'duration', 'motor_vehicle') if tg.get(k))
            lines.append(f"- {c['osm']} | {c['name'] or '(名称なし)'} | {e0['pt'][0]:.5f},{e0['pt'][1]:.5f} → {e1['pt'][0]:.5f},{e1['pt'][1]:.5f} | {c['length_km']}km" + (' | ' + extra if extra else ''))
        with open(os.path.join(outdir, files[a] + '.md'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        n_routes = sum(1 for r in routes if r['_file'].startswith(a + '_'))
        print(f'  {files[a]}: 台帳 {n_routes} 件 / 未対応候補 {len(lst)} 件（名前あり {sum(1 for c in lst if c["name"])}）')


if __name__ == '__main__':
    main()
