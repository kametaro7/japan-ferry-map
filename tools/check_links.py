#!/usr/bin/env python3
"""台帳の operator_url / route_url が開けるか確認する（GET、リダイレクト追従、同時 6 件）。

結果: work/link_report.tsv（status, final_url, url, 航路id）と、失敗の一覧を標準出力へ。
"""
import concurrent.futures as cf, glob, json, os, ssl, urllib.request, urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'


def fetch(url):
    ctxs = [ssl.create_default_context(), ssl._create_unverified_context()]
    last = None
    for i, ctx in enumerate(ctxs):
        try:
            # Accept が無いと接続を切るサーバーがある（例: kukedo.com）ので、ブラウザ相当のヘッダーを付ける
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'ja,en;q=0.8',
                                                       'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'})
            with urllib.request.urlopen(req, timeout=25, context=ctx) as res:
                res.read(2048)
                return res.status, res.geturl(), '' if i == 0 else 'ssl証明書エラー（検証なしなら開ける）'
        except urllib.error.HTTPError as e:
            return e.code, url, ''
        except Exception as e:
            last = e
            if 'CERTIFICATE' not in str(e).upper():
                break
    return 0, url, type(last).__name__ + ': ' + str(last)[:120]


def main():
    urls = {}
    for p in sorted(glob.glob(os.path.join(BASE, 'data/regions/*.json'))):
        for r in json.load(open(p, encoding='utf-8')).get('routes', []):
            for k in ('operator_url', 'route_url'):
                u = (r.get(k) or '').strip()
                if u.startswith('http'):
                    urls.setdefault(u, []).append(f"{r.get('id')}:{k}")
    print(f'URL {len(urls)} 件を確認中…')
    results = {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch, u): u for u in urls}
        for f in cf.as_completed(futs):
            results[futs[f]] = f.result()
    bad = 0
    with open(os.path.join(BASE, 'work/link_report.tsv'), 'w', encoding='utf-8') as out:
        out.write('status\tfinal_url\turl\troutes\tnote\n')
        for u in sorted(urls):
            st, final, note = results[u]
            out.write(f"{st}\t{final}\t{u}\t{' '.join(urls[u])}\t{note}\n")
            if not (200 <= st < 400) or note:
                bad += 1
                print(f"{st}\t{u}\t{' '.join(urls[u])[:80]}\t{note}")
    print(f'問題あり {bad} / {len(urls)} 件 → work/link_report.tsv')


if __name__ == '__main__':
    main()
