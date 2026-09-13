#!/usr/bin/env python3
"""OSM の route=ferry と ferry_terminal から、担当エリア別の調査用候補リストを作る。

入力: work/osm/routes.json, work/osm/terminals.json（Overpass API の出力）
      GeoNames cities500（都道府県の割り当て用）
出力: work/candidates/<NN>_<key>.md, work/osm/index.json
"""
import json, math, os, re, collections
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# GeoNames の cities500.txt（https://download.geonames.org/export/dump/ 、CC BY 4.0）。別の場所にあるなら環境変数で指定
GEO = os.environ.get('GEONAMES_CITIES500', os.path.join(BASE, 'work/geonames/cities500.txt'))

PREF = {'01': '愛知県', '02': '秋田県', '03': '青森県', '04': '千葉県', '05': '愛媛県', '06': '福井県', '07': '福岡県',
        '08': '福島県', '09': '岐阜県', '10': '群馬県', '11': '広島県', '12': '北海道', '13': '兵庫県', '14': '茨城県',
        '15': '石川県', '16': '岩手県', '17': '香川県', '18': '鹿児島県', '19': '神奈川県', '20': '高知県', '21': '熊本県',
        '22': '京都府', '23': '三重県', '24': '宮城県', '25': '宮崎県', '26': '長野県', '27': '長崎県', '28': '奈良県',
        '29': '新潟県', '30': '大分県', '31': '岡山県', '32': '大阪府', '33': '佐賀県', '34': '埼玉県', '35': '滋賀県',
        '36': '島根県', '37': '静岡県', '38': '栃木県', '39': '徳島県', '40': '東京都', '41': '鳥取県', '42': '富山県',
        '43': '和歌山県', '44': '山形県', '45': '山口県', '46': '山梨県', '47': '沖縄県'}

AGENTS = [
    ('01', 'long_intl', []),
    ('02', 'hokkaido_tohoku', ['北海道', '青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県']),
    ('03', 'kanto', ['東京都', '神奈川県', '千葉県', '埼玉県', '茨城県', '栃木県', '群馬県', '山梨県', '静岡県']),
    ('04', 'chubu', ['新潟県', '富山県', '石川県', '福井県', '長野県', '岐阜県', '愛知県', '三重県']),
    ('05', 'kinki', ['滋賀県', '京都府', '大阪府', '兵庫県', '奈良県', '和歌山県']),
    ('06', 'setouchi_east', ['岡山県', '香川県', '徳島県', '高知県']),
    ('07', 'hiroshima', ['広島県']),
    ('08', 'yamaguchi_sanin', ['山口県', '島根県', '鳥取県']),
    ('09', 'ehime', ['愛媛県']),
    ('10', 'kyushu_north', ['福岡県', '佐賀県', '長崎県']),
    ('11', 'kyushu_south', ['熊本県', '大分県', '宮崎県', '鹿児島県']),
    ('12', 'okinawa', ['沖縄県']),
]
PREF_AGENT = {p: nn for nn, _, prefs in AGENTS for p in prefs}
CJK = re.compile(r'[぀-ヿ一-鿿]')


def load_places():
    rows = []
    with open(GEO, encoding='utf-8') as f:
        for line in f:
            c = line.rstrip('\n').split('\t')
            if c[8] != 'JP' or c[10] not in PREF:
                continue
            ja = next((a for a in c[3].split(',') if CJK.search(a)), c[2])
            rows.append((float(c[4]), float(c[5]), ja, PREF[c[10]]))
    lat = np.radians([r[0] for r in rows])
    lon = np.radians([r[1] for r in rows])
    return rows, lat, lon


PLACES, PLAT, PLON = load_places()


def nearest(la, lo):
    la1, lo1 = math.radians(la), math.radians(lo)
    d = np.sin((PLAT - la1) / 2) ** 2 + math.cos(la1) * np.cos(PLAT) * np.sin((PLON - lo1) / 2) ** 2
    i = int(np.argmin(d))
    km = 2 * 6371 * math.asin(math.sqrt(float(d[i])))
    return PLACES[i], km


def hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def plen(coords):
    return sum(hav(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def foreign(la, lo):
    if la > 34.75 and lo < 129.6 and la < 39.5:   # 朝鮮半島南岸
        return True
    if lo < 122.8 or (la < 26 and lo < 122.95 and la > 24.6):  # 中国・台湾側
        return True
    if la > 45.7:                                  # サハリン側
        return True
    return False


def endpoint_info(pt):
    if foreign(*pt):
        return {'pt': pt, 'place': '（海外）', 'pref': None, 'km': None}
    (pla, plo, name, pref), km = nearest(*pt)
    return {'pt': pt, 'place': name, 'pref': pref, 'km': round(km, 1)}


def tag_summary(tg):
    keys = ['name:ja', 'operator', 'from', 'to', 'motor_vehicle', 'motorcar', 'duration', 'website',
            'operator:website', 'contact:website', 'note', 'fee', 'ref']
    parts = []
    for k in keys:
        if tg.get(k):
            parts.append(f'{k}={tg[k]}')
    return ' | '.join(parts)


def fmt_pt(e):
    s = f"{e['pt'][0]:.5f},{e['pt'][1]:.5f}"
    if e['pref']:
        s += f" ({e['pref']} {e['place']} {e['km']}km)"
    else:
        s += f" ({e['place']})"
    return s


def main():
    routes = json.load(open(os.path.join(BASE, 'work/osm/routes.json')))['elements']
    terms = json.load(open(os.path.join(BASE, 'work/osm/terminals.json')))['elements']
    index = []
    for e in routes:
        tg = e.get('tags', {})
        if e['type'] == 'way':
            coords = [(p['lat'], p['lon']) for p in e.get('geometry', [])]
            if len(coords) < 2:
                continue
            ends = [endpoint_info(coords[0]), endpoint_info(coords[-1])]
            length = plen(coords)
            members = []
        else:
            segs = [[(p['lat'], p['lon']) for p in m.get('geometry', [])]
                    for m in e.get('members', []) if m['type'] == 'way' and m.get('geometry')]
            if not segs:
                continue
            pts = [s[0] for s in segs] + [s[-1] for s in segs]
            # 最も離れた 2 点を代表の端点にする
            best = (0, pts[0], pts[-1])
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    d = hav(pts[i], pts[j])
                    if d > best[0]:
                        best = (d, pts[i], pts[j])
            ends = [endpoint_info(best[1]), endpoint_info(best[2])]
            length = sum(plen(s) for s in segs)
            members = [f"way/{m['ref']}" for m in e.get('members', []) if m['type'] == 'way']
            # 途中の端点の都道府県も拾う
            extra = [endpoint_info(p) for p in pts]
            ends_all = ends + extra
        prefs = sorted({x['pref'] for x in (ends if e['type'] == 'way' else ends_all) if x['pref']})
        intl = any(x['pref'] is None for x in (ends if e['type'] == 'way' else ends_all))
        agents = sorted({PREF_AGENT[p] for p in prefs if p in PREF_AGENT})
        if length > 250 or intl:
            agents = sorted(set(agents) | {'01'})
        index.append({
            'osm': f"{e['type']}/{e['id']}", 'name': tg.get('name', ''), 'tags': tg,
            'ends': ends, 'length_km': round(length, 1), 'prefs': prefs, 'intl': intl,
            'agents': agents, 'members': members,
        })

    tindex = []
    for e in terms:
        if e['type'] == 'node':
            la, lo = e['lat'], e['lon']
        else:
            c = e.get('center')
            if not c:
                continue
            la, lo = c['lat'], c['lon']
        tg = e.get('tags', {})
        info = endpoint_info((la, lo))
        tindex.append({'osm': f"{e['type']}/{e['id']}", 'name': tg.get('name:ja') or tg.get('name', ''),
                       'lat': round(la, 6), 'lon': round(lo, 6), 'pref': info['pref'], 'place': info['place'],
                       'tags': tg})

    json.dump({'routes': index, 'terminals': tindex}, open(os.path.join(BASE, 'work/osm/index.json'), 'w'),
              ensure_ascii=False)

    outdir = os.path.join(BASE, 'work/candidates')
    os.makedirs(outdir, exist_ok=True)
    for nn, key, prefs in AGENTS:
        mine = [r for r in index if nn in r['agents']]
        mine.sort(key=lambda r: (r['prefs'][0] if r['prefs'] else '', r['name']))
        lines = [f'# 担当{nn} {key} OSM候補リスト',
                 '',
                 f'対象都道府県: {"、".join(prefs) if prefs else "（長距離フェリー・国際航路）"}',
                 'OSM（2026-09-13取得）の route=ferry。古い・廃止済み・名称なし・重複・分割されたものを含む。**運航中かは必ず公式情報で確認**。',
                 '端点の括弧内は最寄りの GeoNames 地名と距離（都道府県の目安。境界付近は誤りあり）。',
                 '',
                 f'## 航路候補 {len(mine)} 件', '']
        for r in mine:
            s = f"- {r['osm']} | {r['name'] or '(名称なし)'} | {fmt_pt(r['ends'][0])} → {fmt_pt(r['ends'][1])} | {r['length_km']}km"
            ts = tag_summary(r['tags'])
            if ts:
                s += ' | ' + ts
            if r['members']:
                s += f" | members={len(r['members'])}本: " + ','.join(r['members'][:12]) + (' …' if len(r['members']) > 12 else '')
            lines.append(s)
        if prefs:
            mt = [t for t in tindex if t['pref'] in prefs]
            mt.sort(key=lambda t: (t['pref'], t['name']))
            lines += ['', f'## フェリーターミナル・乗り場（OSM amenity=ferry_terminal 等）{len(mt)} 件', '']
            for t in mt:
                lines.append(f"- {t['osm']} | {t['name'] or '(名称なし)'} | {t['lat']:.5f},{t['lon']:.5f} | {t['pref']} {t['place']}")
        with open(os.path.join(outdir, f'{nn}_{key}.md'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        print(nn, key, 'routes', len(mine))


if __name__ == '__main__':
    main()
