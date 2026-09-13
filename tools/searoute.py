#!/usr/bin/env python3
"""港と港のあいだを、陸地を避けて水の上だけを通る経路で結ぶ（OSM の海岸線・水域ポリゴンを格子にして探索）。

build.py から: SeaRouter(BASE).route((lat, lon), (lat, lon), water) -> [(lat, lon), ...] または None
単体テスト:    python3 tools/searoute.py <lat1> <lon1> <lat2> <lon2> [sea|lake|river]

- 海: natural=coastline（陸が左、水が右）で格子を区切り、区画ごとに「右側の点」の票が多い方を海とする
- 湖・川: 上に加えて natural=water / waterway=riverbank / landuse=reservoir の面を水とする
- 海岸線は 0.5 度タイル単位で Overpass から取得してキャッシュ（work/searoute/coast_*.json）。経路も routes.json にキャッシュ
"""
import hashlib, heapq, json, math, os, sys, time, urllib.error, urllib.parse, urllib.request

import numpy as np

# 公開 Overpass インスタンス（応答しないものは自動で次へ。2026-09-13 は overpass-api.de が接続拒否だった）
ENDPOINTS = ['https://overpass.private.coffee/api/interpreter', 'https://overpass.kumi.systems/api/interpreter',
             'https://maps.mail.ru/osm/tools/overpass/api/interpreter', 'https://overpass-api.de/api/interpreter']
UA = 'japan-ferry-map/0.1 (personal non-commercial map)'
MAX_CELLS = 1400
TILE = 0.5
PAD = 0.03


def hav_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(min(1.0, math.sqrt(h)))


def _polylines(doc):
    """way の geometry（bbox 外で null になる点で分割）を [(lat, lon), ...] のリストで返す。"""
    out = []
    for e in (doc or {}).get('elements', []):
        cur = []
        for p in e.get('geometry') or []:
            if p is None:
                if len(cur) >= 2:
                    out.append(cur)
                cur = []
            else:
                cur.append((p['lat'], p['lon']))
        if len(cur) >= 2:
            out.append(cur)
    return out


def _stitch(parts):
    """端点が一致する線をつないで輪にする（relation の member way 用）。"""
    parts = [list(p) for p in parts if len(p) >= 2]
    rings = []
    while parts:
        cur = parts.pop()
        changed = True
        while changed and cur[0] != cur[-1]:
            changed = False
            for i, p in enumerate(parts):
                if p[0] == cur[-1]:
                    cur += p[1:]
                elif p[-1] == cur[-1]:
                    cur += p[::-1][1:]
                elif p[-1] == cur[0]:
                    cur = p[:-1] + cur
                elif p[0] == cur[0]:
                    cur = p[::-1][:-1] + cur
                else:
                    continue
                parts.pop(i)
                changed = True
                break
        if len(cur) >= 4:
            rings.append(cur)
    return rings


def _water_features(doc):
    """水域の面を [[ring, ring, ...], ...]（面ごと）で返す。"""
    feats = []
    for e in (doc or {}).get('elements', []):
        if e['type'] == 'way' and e.get('geometry'):
            pts = [(p['lat'], p['lon']) for p in e['geometry'] if p]
            if len(pts) >= 4 and pts[0] == pts[-1]:
                feats.append([pts])
        elif e['type'] == 'relation':
            parts = [[(p['lat'], p['lon']) for p in m['geometry'] if p]
                     for m in e.get('members', []) if m.get('type') == 'way' and m.get('geometry')]
            rings = _stitch(parts)
            if rings:
                feats.append(rings)
    return feats


def label_components(mask):
    """True のセルの 4 近傍連結成分に番号を付ける（行ごとのランを union-find でまとめる）。"""
    ny, nx = mask.shape
    parent = []

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    runs = []
    prev = []
    for y in range(ny):
        d = np.diff(np.concatenate(([0], mask[y].astype(np.int8), [0])))
        starts = np.flatnonzero(d == 1).tolist()
        ends = np.flatnonzero(d == -1).tolist()
        cur = []
        pi = 0
        for s, e in zip(starts, ends):
            rid = len(parent)
            parent.append(rid)
            while pi < len(prev) and prev[pi][1] <= s:
                pi += 1
            k = pi
            while k < len(prev) and prev[k][0] < e:
                ra, rb = find(rid), find(prev[k][2])
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
                k += 1
            cur.append((s, e, rid))
            runs.append((y, s, e, rid))
        prev = cur
    label = np.full((ny, nx), -1, dtype=np.int32)
    comp = {}
    for y, s, e, rid in runs:
        label[y, s:e] = comp.setdefault(find(rid), len(comp))
    return label, len(comp)


class SeaRouter:
    def __init__(self, base, log=print):
        self.dir = os.path.join(base, 'work/searoute')
        os.makedirs(self.dir, exist_ok=True)
        self.cache_path = os.path.join(self.dir, 'routes.json')
        self.cache = json.load(open(self.cache_path)) if os.path.exists(self.cache_path) else {}
        self.last = 0.0
        self.log = log

    # ------------------------------------------------------------ Overpass
    def overpass(self, q, name=None):
        key = name or hashlib.sha1(q.encode()).hexdigest()[:20]
        path = os.path.join(self.dir, key + '.json')
        if os.path.exists(path):
            return json.load(open(path))
        # 使うサーバーは最初に小さな問い合わせで応答を確かめて決め、失敗したときだけ次へ移る（毎回先頭から試さない）
        if getattr(self, 'ep', None) is None:
            self.ep = self._pick_endpoint()
            if self.ep is None:
                return None
        for attempt in range(5):
            wait = 2.0 - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            url = ENDPOINTS[self.ep]
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode({'data': q}).encode(),
                                             headers={'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=200) as res:
                    doc = json.load(res)
                if doc.get('remark') and ('error' in doc['remark'].lower() or 'timed out' in doc['remark'].lower()):
                    raise RuntimeError(doc['remark'][:200])
                json.dump(doc, open(path, 'w'))
                return doc
            except urllib.error.HTTPError as ex:
                if ex.code == 429:
                    self.log(f'  overpass 429（混雑制限）{url}、{30 * (attempt + 1)}秒待つ')
                    time.sleep(30 * (attempt + 1))
                    continue
                self.log(f'  overpass HTTP {ex.code} {url}、次のサーバーへ')
            except Exception as ex:
                self.log(f'  overpass 失敗 {type(ex).__name__}: {str(ex)[:100]} {url}、次のサーバーへ')
            self.ep = (self.ep + 1) % len(ENDPOINTS)
            time.sleep(5)
        return None

    def _pick_endpoint(self):
        """応答する公開 Overpass インスタンスを小さな問い合わせで探し、その番号を返す。"""
        for i, url in enumerate(ENDPOINTS):
            try:
                t0 = time.time()
                req = urllib.request.Request(url, data=urllib.parse.urlencode(
                    {'data': '[out:json][timeout:20];node(1);out;'}).encode(), headers={'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=40) as res:
                    json.load(res)
                self.log(f'  Overpass: {url} を使用（応答 {time.time() - t0:.0f}秒）')
                return i
            except Exception as ex:
                self.log(f'  Overpass: {url} は応答なし（{type(ex).__name__}）')
        return None

    def coastline(self, S, W, N, E):
        if getattr(self, 'broken', False):
            return None
        lines = []
        for i in range(int(math.floor(S / TILE)), int(math.floor(N / TILE)) + 1):
            for j in range(int(math.floor(W / TILE)), int(math.floor(E / TILE)) + 1):
                s, w = i * TILE - PAD, j * TILE - PAD
                n, e = (i + 1) * TILE + PAD, (j + 1) * TILE + PAD
                bb = f'{s:.3f},{w:.3f},{n:.3f},{e:.3f}'
                doc = self.overpass(f'[out:json][timeout:180];way["natural"="coastline"]({bb});out geom({bb});',
                                    name=f'coast_{i}_{j}')
                if doc is None:
                    self.log('  海岸線タイルを取得できないので、この実行では以降の海上経路探索を省略します')
                    self.broken = True  # 取得できない状態で区間ごとに長く待たないように
                    return None
                lines += _polylines(doc)
        return lines

    # ------------------------------------------------------------ public
    def route(self, a, b, water='sea'):
        key = f'{water}:{a[0]:.5f},{a[1]:.5f}>{b[0]:.5f},{b[1]:.5f}'
        if key in self.cache:
            v = self.cache[key]
            return [tuple(p) for p in v] if v else None
        try:
            res = self._route(a, b, water)
        except Exception as ex:
            self.log(f'  searoute 例外 {type(ex).__name__}: {ex}')
            return None
        if res is not False:
            self.cache[key] = [list(p) for p in res] if res else None
            json.dump(self.cache, open(self.cache_path, 'w'))
        return res or None

    # ------------------------------------------------------------ core
    def _route(self, a, b, water):
        d = hav_m(a, b)
        if d > 250000:
            return None
        margin = min(40000.0, max(2500.0, d * 0.4))
        lat0 = (a[0] + b[0]) / 2
        my = 110540.0
        mx = 111320.0 * math.cos(math.radians(lat0))
        S = min(a[0], b[0]) - margin / my
        N = max(a[0], b[0]) + margin / my
        W = min(a[1], b[1]) - margin / mx
        E = max(a[1], b[1]) + margin / mx
        ext = max((E - W) * mx, (N - S) * my)
        cell = max(25.0, min(400.0, (d + 2 * margin) / 700.0), ext / MAX_CELLS)
        nx = int((E - W) * mx / cell) + 1
        ny = int((N - S) * my / cell) + 1

        lines = self.coastline(S, W, N, E)
        if lines is None:
            return False  # 取得失敗はキャッシュしない
        feats = []
        if water in ('lake', 'river'):
            bbox = f'{S:.5f},{W:.5f},{N:.5f},{E:.5f}'
            wdoc = self.overpass(
                f'[out:json][timeout:180];(way["natural"="water"]({bbox});relation["natural"="water"]({bbox});'
                f'way["waterway"="riverbank"]({bbox});relation["waterway"="riverbank"]({bbox});'
                f'way["landuse"="reservoir"]({bbox});relation["landuse"="reservoir"]({bbox}););out geom;')
            if wdoc is None:
                return False
            feats = _water_features(wdoc)

        # 海岸線の線分を格子座標へ（格子の外だけにある線分は捨てる）
        segs = []
        for pl in lines:
            arr = np.asarray(pl, dtype=float)
            xs = (arr[:, 1] - W) * mx / cell
            ys = (arr[:, 0] - S) * my / cell
            segs.append(np.stack([xs[:-1], ys[:-1], xs[1:], ys[1:]], axis=1))
        seg = np.concatenate(segs) if segs else np.zeros((0, 4))
        if len(seg):
            out = (((seg[:, 0] < -2) & (seg[:, 2] < -2)) | ((seg[:, 0] > nx + 2) & (seg[:, 2] > nx + 2)) |
                   ((seg[:, 1] < -2) & (seg[:, 3] < -2)) | ((seg[:, 1] > ny + 2) & (seg[:, 3] > ny + 2)))
            seg = seg[~out]

        barrier = np.zeros((ny, nx), dtype=bool)
        free = ~barrier
        label = None
        if len(seg):
            dx, dy = seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]
            L = np.hypot(dx, dy)
            n = np.minimum(np.ceil(L * 3).astype(np.int64) + 2, 20000)
            idx = np.repeat(np.arange(len(seg)), n)
            offs = np.repeat(np.cumsum(n) - n, n)
            t = (np.arange(int(n.sum())) - offs) / np.repeat(n - 1, n)
            px = seg[idx, 0] + dx[idx] * t
            py = seg[idx, 1] + dy[idx] * t
            ix, iy = np.floor(px).astype(np.int64), np.floor(py).astype(np.int64)
            ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
            barrier[iy[ok], ix[ok]] = True
            free = ~barrier
            label, ncomp = label_components(free)
            votes = np.zeros((ncomp, 2))
            Ls = np.maximum(L, 1e-9)
            rx, ry = dy / Ls, -dx / Ls            # 進行方向の右 = 海側
            w = np.minimum(L, 5.0) * (L >= 0.3)
            for tt in (0.25, 0.5, 0.75):
                cx, cy = seg[:, 0] + dx * tt, seg[:, 1] + dy * tt
                for sgn, k in ((1.2, 0), (-1.2, 1)):
                    qx = np.floor(cx + rx * sgn).astype(np.int64)
                    qy = np.floor(cy + ry * sgn).astype(np.int64)
                    ok = (qx >= 0) & (qx < nx) & (qy >= 0) & (qy < ny)
                    lab = label[qy[ok], qx[ok]]
                    ww = w[ok]
                    good = lab >= 0
                    np.add.at(votes[:, k], lab[good], ww[good])
            sea_comp = votes[:, 0] > votes[:, 1]
            waterm = (label >= 0) & sea_comp[np.maximum(label, 0)]
        else:
            waterm = np.ones((ny, nx), dtype=bool)   # 海岸線が無い＝沖合とみなす

        # 湖・川の面（面ごとに偶奇塗り、面どうしは和）
        for rings in feats:
            fill = np.zeros((ny, nx), dtype=bool)
            edges = []
            for ring in rings:
                pts = [((p[1] - W) * mx / cell, (p[0] - S) * my / cell) for p in ring]
                edges += [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
            if not edges:
                continue
            ys_all = [p[1] for e in edges for p in e]
            r0, r1 = max(0, int(min(ys_all))), min(ny - 1, int(max(ys_all)) + 1)
            for row in range(r0, r1 + 1):
                yc = row + 0.5
                xs_cross = sorted(x0 + (yc - y0) * (x1 - x0) / (y1 - y0)
                                  for (x0, y0), (x1, y1) in edges if (y0 <= yc < y1) or (y1 <= yc < y0))
                for k in range(0, len(xs_cross) - 1, 2):
                    c0 = max(0, int(math.ceil(xs_cross[k] - 0.5)))
                    c1 = min(nx - 1, int(math.floor(xs_cross[k + 1] - 0.5)))
                    if c1 >= c0:
                        fill[row, c0:c1 + 1] ^= True
            waterm |= fill

        if not waterm.any():
            return None
        land = ~waterm
        near = land.copy()
        near[1:, :] |= land[:-1, :]
        near[:-1, :] |= land[1:, :]
        near[:, 1:] |= land[:, :-1]
        near[:, :-1] |= land[:, 1:]
        near &= waterm

        def snap(p):
            cx, cy = (p[1] - W) * mx / cell, (p[0] - S) * my / cell
            r = int(max(4, 2500 / cell))
            x0, x1 = max(0, int(cx) - r), min(nx, int(cx) + r + 1)
            y0, y1 = max(0, int(cy) - r), min(ny, int(cy) + r + 1)
            iy, ix = np.nonzero(waterm[y0:y1, x0:x1])
            if not len(ix):
                return []
            dd = (ix + x0 + 0.5 - cx) ** 2 + (iy + y0 + 0.5 - cy) ** 2
            return [(int(iy[k] + y0), int(ix[k] + x0)) for k in np.argsort(dd)[:400]]

        sa, sb = snap(a), snap(b)
        if not sa or not sb:
            return None
        wl, _ = label_components(waterm)
        start = goal = None
        for (y, x) in sa:
            g = next(((yy, xx) for (yy, xx) in sb if wl[yy, xx] == wl[y, x]), None)
            if g:
                start, goal = (y, x), g
                break
        if start is None:
            return None

        # A*（8近傍、角の斜め抜けは禁止、岸に接するセルは割高）
        ty, tx = goal
        INF = float('inf')
        gcost = {start: 0.0}
        prev = {start: None}
        pq = [(math.hypot(start[1] - tx, start[0] - ty), 0.0, start)]
        closed = set()
        moves = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                 (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)]
        limit = 4.0 * d / cell + 400
        found = False
        while pq:
            f, gc, cur = heapq.heappop(pq)
            if cur in closed:
                continue
            if cur == goal:
                found = True
                break
            if gc > limit:
                break
            closed.add(cur)
            y, x = cur
            for ddx, ddy, wgt in moves:
                xx, yy = x + ddx, y + ddy
                if not (0 <= xx < nx and 0 <= yy < ny) or not waterm[yy, xx]:
                    continue
                if ddx and ddy and not (waterm[y, xx] and waterm[yy, x]):
                    continue
                ng = gc + wgt * (1.8 if near[yy, xx] else 1.0)
                nb = (yy, xx)
                if ng < gcost.get(nb, INF):
                    gcost[nb] = ng
                    prev[nb] = cur
                    heapq.heappush(pq, (ng + math.hypot(xx - tx, yy - ty), ng, nb))
        if not found:
            return None
        cells = []
        c = goal
        while c is not None:
            cells.append(c)
            c = prev[c]
        cells.reverse()

        def los(p, q):
            (y0, x0), (y1, x1) = p, q
            n = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 2
            t = np.linspace(0, 1, n)
            yy = np.rint(y0 + (y1 - y0) * t).astype(int)
            xx = np.rint(x0 + (x1 - x0) * t).astype(int)
            return bool(waterm[yy, xx].all())

        keep = [cells[0]]
        i = 0
        while i < len(cells) - 1:
            step = 1
            while i + step * 2 < len(cells) and los(cells[i], cells[i + step * 2]):
                step *= 2
            lo, hi = min(len(cells) - 1, i + step), min(len(cells) - 1, i + step * 2)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if los(cells[i], cells[mid]):
                    lo = mid
                else:
                    hi = mid - 1
            j = max(lo, i + 1)
            keep.append(cells[j])
            i = j
        coords = [a] + [(S + (y + 0.5) * cell / my, W + (x + 0.5) * cell / mx) for (y, x) in keep] + [b]
        total = sum(hav_m(coords[k], coords[k + 1]) for k in range(len(coords) - 1))
        if total > 4.0 * d + 5000:
            return None
        return coords


if __name__ == '__main__':
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    a = (float(sys.argv[1]), float(sys.argv[2]))
    b = (float(sys.argv[3]), float(sys.argv[4]))
    water = sys.argv[5] if len(sys.argv) > 5 else 'sea'
    t0 = time.time()
    r = SeaRouter(base).route(a, b, water)
    if r:
        L = sum(hav_m(r[k], r[k + 1]) for k in range(len(r) - 1))
        print(f'{len(r)} 点 / {L / 1000:.1f}km（直線 {hav_m(a, b) / 1000:.1f}km）/ {time.time() - t0:.1f}秒')
        print(json.dumps([[round(p[0], 5), round(p[1], 5)] for p in r]))
    else:
        print('経路なし', f'{time.time() - t0:.1f}秒')
