# kyokuincho-data

旅行貯金の記録アプリ「局印帳」で使っている、全国の郵便局データです。毎月2日に自動で更新します。

| ファイル | 内容 |
|---|---|
| `data/offices.json` | 郵便局の一覧。列：`id, name, kana, lat, lon, city, kani, closed, addr, gone` |
| `data/areas.json` | 都道府県・市区町村の名前（局がある市区町村のみ） |
| `data/moved.json` | OSMのIDが変わった局（旧ID → 新ID） |
| `data/meta.json` | 基準日・局数 |

- `id`：OSMの種類（n/w/r）＋番号。`u` で始まるものはアプリの「みんなで追加」で確認された局
- `city`：市区町村コード（政令市は区）。国土数値情報の行政区域で判定
- `kani`：簡易郵便局、`closed`：OSMで一時閉鎖中、`gone`：データから消えた局（廃止など）
- `addr`：市区町村より後ろの住所（OSMにあるときだけ）

## ライセンス

データは **ODbL 1.0**（© OpenStreetMap contributors）。市区町村の判定に「国土数値情報（行政区域データ）」（国土交通省、CC BY 4.0）を使っています。詳しくは [LICENSE](LICENSE)。

## 作り方

```
pip install shapely
python -I scripts/monthly.py osm.json N03-20250101.geojson 2026-10-21 changes.json .
```

`osm.json` は `scripts/query.overpassql` を Overpass API で実行した結果、`changes.json` はアプリの「みんなで追加」で承認された局です。
