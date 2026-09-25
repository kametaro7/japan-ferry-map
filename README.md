# 日本の航路マップ

**公開ページ: https://kametaro7.github.io/japan-ferry-map/**

日本全国のフェリー・高速船・観光船・水上バス・渡船・湖や川の定期船など、**旅客が乗れる定期航路**を地図で探せる静的サイトです。日本発着の国際航路も含みます（2026年9月時点で 585 航路）。

- 夜行・船中泊あり／マイカー搭載可で絞り込み
- 種別（長距離フェリー・フェリー・旅客船・観光船・水上バス）、水域（海・川・湖）、地方、通年運航のみ、休航中を含む
- 港・航路・会社名で検索、港をクリックで発着する航路の一覧
- 各航路から運航会社の公式サイト・時刻表ページへリンク
- URL の `#r=<航路id>` で航路を共有、絞り込み条件も URL に残る

## ファイル構成

| パス | 内容 |
|---|---|
| `index.html`, `assets/` | 画面（MapLibre GL 6.11.2〈jsDelivr の ES モジュール版〉+ OpenFreeMap の地図） |
| `data/regions/NN_*.json` | **航路台帳**。担当エリア別 12 ファイル |
| `data/regions/99_manual.json` | 手作業で確認・追加した航路（追加はここへ） |
| `data/overrides.json` | 任意。航路ごとの上書き（`drop` / `set` / `osm` / `via`） |
| `data/routes.js` | 画面が読むデータ（`tools/build.py` が生成） |
| `docs/SPEC.md` | 台帳の収録基準とスキーマ |
| `tools/build.py` | 台帳を検証・重複統合し、OSM の航路線で線の形を作って `data/routes.js` を出力 |
| `tools/searoute.py` | 線の形が無い区間を、OSM の海岸線・水域から陸を避けて推定（build.py から使う） |
| `tools/lint.py` | 台帳の中身の簡易チェック |
| `tools/coverage.py` | 台帳に入っていない OSM 航路を担当別に列挙（取りこぼしの確認） |
| `tools/check_links.py` | 公式サイト URL が開けるか確認 |
| `tools/osm_candidates.py` | OSM の航路・ターミナルから担当別の調査用候補リストを作る |

作業用の `work/`（OSM の取得データ、海岸線キャッシュ、調査時の資料など）はリポジトリに含めていません。

## 更新手順

```bash
python3 tools/lint.py         # 台帳の点検
python3 tools/build.py        # data/regions → data/routes.js（結果は work/build_report.txt）
python3 tools/coverage.py     # 取りこぼし候補 → work/coverage/
python3 tools/check_links.py  # リンク切れ → work/link_report.tsv
```

`tools/build.py` は OSM の航路データ `work/osm/routes.json` を使います。Overpass API で次のクエリの結果を保存してください（`tools/osm_candidates.py` 用のフェリーターミナルは `nwr["amenity"="ferry_terminal"](area.jp); out tags center;` で `work/osm/terminals.json` に保存）。

```
[out:json][timeout:800];
area["ISO3166-1"="JP"]["admin_level"="2"]->.jp;
(way["route"="ferry"](area.jp); relation["route"="ferry"](area.jp););
out tags geom;
```

### 線の形の決め方（build.py）

港と港の区間ごとに、(1) 台帳の `osm` に挙げた OSM 航路線だけで経路探索 → (2) OSM の航路網全体から探索（直線距離の 1.6 倍 + 3km 以内のときだけ採用）→ (3) `overrides.json` の `via`（経由点）→ (4) `tools/searoute.py` で海岸線から推定 → (5) 直線、の順に使います。直線になった 3km 超の区間は `work/build_report.txt` に一覧が出ます。

- `NO_SEA=1 python3 tools/build.py` … (4) を使わない
- `SEA_CACHE_ONLY=1 python3 tools/build.py` … (4) は取得済みのキャッシュだけ使い、Overpass に問い合わせない

`overrides.json` の例:

```json
{
  "07-example": { "via": { "0": [[34.25, 132.80], [34.20, 132.85]] } },
  "05-closed-route": { "drop": true },
  "10-some-route": { "set": { "car": false, "car_note": "軽自動車のみ" } }
}
```

## データの出典とライセンス

- 航路の情報: 各運航会社・自治体・運輸局などの公式情報をもとに 2026年9月時点で作成（各航路の `sources` に確認した URL）
- 航路線の形・港の位置の一部: © OpenStreetMap contributors。OSM から作った線形データ（`data/routes.js` の線）は [ODbL](https://opendatacommons.org/licenses/odbl/) に従います
- 背景地図: OpenFreeMap / OpenMapTiles / OpenStreetMap
- 運航状況・ダイヤ・運賃は変わるため、乗船前に必ず公式サイトで確認してください

## 手元で見る

このフォルダで `python3 -m http.server 8140` を実行し http://localhost:8140 を開く（地図タイルの読み込みにインターネット接続が必要）。
