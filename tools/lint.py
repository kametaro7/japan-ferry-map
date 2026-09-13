#!/usr/bin/env python3
"""台帳（data/regions/*.json）の中身を線形抜きで手早く点検する。

- スキーマ検証（build.py と同じ validate）
- id の重複、担当ファイル間の重複（同じ事業者・同じ港の並び）
- 種別と車の矛盾、国際なのに相手国が無い、sources が空、など
- 同じ港の組み合わせを別事業者名で持つ航路（重複か併走かの目視用）
"""
import collections, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build as B  # noqa: E402


def main():
    os.chdir(B.BASE)
    warns = []
    raw = B.load_ledger('data/regions/*.json', warns)
    ok = [r for r in raw if B.validate(r, warns)]

    per_file = collections.Counter(r['_file'] for r in raw)
    print('ファイル別:', ', '.join(f'{k.split(".")[0]}={v}' for k, v in sorted(per_file.items())), f'／計 {len(raw)}（有効 {len(ok)}）')
    print('種別:', dict(collections.Counter(r['service'] for r in ok)))
    print('水域:', dict(collections.Counter(r['water'] for r in ok)))
    print(f"夜行・船中泊 {sum(r['overnight'] for r in ok)} / マイカー {sum(r['car'] for r in ok)} / 国際 {sum(r['international'] for r in ok)} / "
          f"季節 {sum(r['seasonal'] for r in ok)} / 休航 {sum(r['status'] == 'suspended' for r in ok)} / 周遊 {sum(r['loop'] for r in ok)}")
    print('確度:', dict(collections.Counter(r.get('confidence') for r in ok)))

    notes = []
    for k, v in collections.Counter(r['id'] for r in ok).items():
        if v > 1:
            notes.append(f'id 重複: {k} ×{v}')
    for r in ok:
        rid = r['id']
        if r['service'] == 'passenger' and r['car']:
            notes.append(f'{rid}: passenger なのに car=true')
        if r['service'] in ('ferry', 'longferry') and not r['car']:
            notes.append(f"{rid}: {r['service']} なのに car=false（{r.get('car_note', '')}）")
        if r['international'] and set(r.get('countries') or []) <= {'JP'}:
            notes.append(f'{rid}: 国際なのに countries に外国が無い')
        if not r['international'] and set(r.get('countries') or ['JP']) - {'JP'}:
            notes.append(f'{rid}: 国内なのに countries に外国がある')
        if not r.get('sources'):
            notes.append(f'{rid}: sources が空')
        if r['loop'] and len(r['ports']) >= 2 and B.hav_m((r['ports'][0]['lat'], r['ports'][0]['lon']), (r['ports'][-1]['lat'], r['ports'][-1]['lon'])) < 300:
            notes.append(f'{rid}: loop なのに最後の港が出発港と同じ（重ねて書かない約束）')
        if r['service'] == 'longferry' and r['_owner'] != '01':
            notes.append(f'{rid}: longferry は担当01のみ')
        if r['international'] and r['_owner'] != '01':
            notes.append(f'{rid}: 国際航路は担当01のみ')
        for p in r['ports']:
            if not p['name']:
                notes.append(f'{rid}: 名前の無い港')

    ok.sort(key=lambda r: r['_owner'])
    for i, a in enumerate(ok):
        for b in ok[i + 1:]:
            if a['_owner'] != b['_owner'] and B.same_route(a, b):
                notes.append(f"担当間の重複: {a['id']}（{a['_file']}）と {b['id']}（{b['_file']}）")

    # 同じ港の組み合わせ（1.5km 以内・順不同）を持つ別事業者の航路
    def key_ports(r):
        return [(round(p['lat'], 2), round(p['lon'], 2)) for p in r['ports']]
    same = []
    for i, a in enumerate(ok):
        for b in ok[i + 1:]:
            if len(a['ports']) != len(b['ports']) or B.norm_operator(a['operator']) == B.norm_operator(b['operator']):
                continue
            pa = [(p['lat'], p['lon']) for p in a['ports']]
            pb = [(p['lat'], p['lon']) for p in b['ports']]
            if all(any(B.hav_m(x, y) < 1500 for y in pb) for x in pa) and all(any(B.hav_m(x, y) < 1500 for y in pa) for x in pb):
                same.append(f"{a['id']}（{a['operator']}）⇔ {b['id']}（{b['operator']}）: {a['name']} / {b['name']}")

    print(f'\n## 検証の警告 {len(warns)} 件')
    print('\n'.join(warns))
    print(f'\n## 内容の注意 {len(notes)} 件')
    print('\n'.join(notes))
    print(f'\n## 同じ港の組み合わせで事業者が違う航路 {len(same)} 件（併走か重複かを目視）')
    print('\n'.join(same))


if __name__ == '__main__':
    main()
