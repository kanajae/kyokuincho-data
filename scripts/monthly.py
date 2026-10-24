"""局印帳 국 데이터 월간 갱신.

usage: python -I scripts/monthly.py <osm.json> <N03.geojson> <date yyyy-MM-dd> <changes.json> <repo_dir>

1. OSM `amenity=post_office` 중 「〜郵便局」 추출 (접미사 「（一時閉鎖中）」 → closed), 같은 이름 150m 안은 하나로
2. 국토수치정보 N03(정밀)로 시구정촌 코드 판정, OSM 주소 태그로 町名・番地(addr)
3. 지난번 offices.json과 비교
   - 사라진 id와 같은 이름이 200m 안에 새로 있으면 → 같은 국(moved.json에 old → new)
   - 없으면 → 줄을 남기고 gone=1 (앱에서 「廃止」, 방문 기록이 계속 보이게)
4. みんなで追加 승인분(Worker /offices/changes, id 'u…')을 합침. OSM에 같은 국이 생겼으면 OSM 쪽으로 moved
5. offices.json · areas.json · moved.json · meta.json 을 repo_dir/data 에 쓴다

offices.json 열: id, name, kana, lat, lon, city, kani, closed, addr, gone
"""
import collections
import json
import math
import os
import re
import sys

from shapely import STRtree, points
from shapely.geometry import shape

osm_path, n03_path, date, changes_path, repo = sys.argv[1:6]
data_dir = os.path.join(repo, 'data')
COLS = ['id', 'name', 'kana', 'lat', 'lon', 'city', 'kani', 'closed', 'addr', 'gone']
SAME_PLACE_M = 200


def dist_m(a, b):
    dy = (a['lat'] - b['lat']) * 111_000
    dx = (a['lon'] - b['lon']) * 111_000 * math.cos(math.radians(a['lat']))
    return math.hypot(dx, dy)


def addr_detail(t):
    """市区町村より後ろ(町名・番地). 없으면 ''."""
    town = (t.get('addr:quarter', '') + t.get('addr:neighbourhood', '')).strip()
    num = t.get('addr:block_number', '')
    if num and t.get('addr:housenumber'):
        num += '-' + t['addr:housenumber']
    detail = town + num
    if detail:
        return detail
    full = t.get('addr:full', '')
    for k in ('addr:province', 'addr:county', 'addr:city', 'addr:suburb'):
        v = t.get(k)
        if v and full.startswith(v):
            full = full[len(v):]
    return full


# ---------- 1. OSM 추출 ----------
SUFFIX = re.compile(r'[（(]([^）)]*)[）)]\s*$')
offices = []
for e in json.load(open(osm_path))['elements']:
    t = e.get('tags', {})
    raw = t.get('name', '')
    note = ''
    base = raw
    m = SUFFIX.search(raw)
    if m and not raw.endswith('郵便局'):
        note = m.group(1)
        base = raw[:m.start()].strip()
    if not base.endswith('郵便局'):
        continue
    if e['type'] == 'node':
        lat, lon = e['lat'], e['lon']
    else:
        lat, lon = e['center']['lat'], e['center']['lon']
    offices.append({
        'id': e['type'][0] + str(e['id']),
        'name': base,
        'kana': t.get('name:ja-Hira', ''),
        'lat': round(lat, 5), 'lon': round(lon, 5),
        'kani': 1 if base.endswith('簡易郵便局') else 0,
        'closed': 1 if '閉鎖' in note else 0,
        'addr': addr_detail(t),
        'ntags': len(t),
    })

by_name = collections.defaultdict(list)
for o in offices:
    by_name[o['name']].append(o)
drop = set()
for group in by_name.values():
    group.sort(key=lambda o: -o['ntags'])
    for i, a in enumerate(group):
        if a['id'] in drop:
            continue
        for b in group[i + 1:]:
            if b['id'] not in drop and dist_m(a, b) < 150:
                drop.add(b['id'])
offices = [o for o in offices if o['id'] not in drop]
print('osm offices', len(offices), 'duplicates dropped', len(drop))
if len(offices) < 20000:  # Overpass가 일부만 돌려준 경우 등 — 덮어쓰지 않고 멈춤
    sys.exit(f'too few offices ({len(offices)}), aborting')

# ---------- 2. 시구정촌 ----------
geoms, meta, pref_names, city_names = [], [], {}, {}
for f in json.load(open(n03_path))['features']:
    p = f['properties']
    code = p.get('N03_007')
    if not code or not f['geometry']:
        continue
    geoms.append(shape(f['geometry']))
    meta.append(code)
    pref_names[code[:2]] = p['N03_001']
    # 政令市는 「横浜市中区」처럼 시+구, 郡은 붙이지 않음
    city_names[code] = (p.get('N03_004') or '') + (p.get('N03_005') or '')
tree = STRtree(geoms)


def assign(rows):
    pts = points([[o['lon'], o['lat']] for o in rows])
    hit = {}
    for pi, gi in zip(*tree.query(pts, predicate='within')):
        hit.setdefault(int(pi), int(gi))
    miss = [i for i in range(len(rows)) if i not in hit]
    if miss:  # 해안선 밖 등 → 가장 가까운 경계
        for pi, gi in zip(*tree.query_nearest(pts[miss])):
            hit.setdefault(miss[int(pi)], int(gi))
    for i, o in enumerate(rows):
        o['city'] = meta[hit[i]]
    return len(miss)


print('nearest fallback', assign(offices))

# ---------- 3. 지난번과 비교 ----------
def load_rows(path):
    if not os.path.exists(path):
        return []
    d = json.load(open(path))
    return [dict(zip(d['cols'], r)) for r in d['rows']]


prev = load_rows(os.path.join(data_dir, 'offices.json'))
moved_path = os.path.join(data_dir, 'moved.json')
moved = json.load(open(moved_path)) if os.path.exists(moved_path) else {}
now_ids = {o['id'] for o in offices}
prev_ids = {p['id'] for p in prev}
new_by_name = collections.defaultdict(list)
for o in offices:
    new_by_name[o['name']].append(o)

kept_gone = newly_gone = newly_moved = 0
for p in prev:
    if p['id'] in now_ids or p['id'].startswith('u'):
        continue
    target = next((o for o in new_by_name.get(p['name'], []) if dist_m(o, p) <= SAME_PLACE_M), None)
    if target and target['id'] not in prev_ids:
        moved[p['id']] = target['id']
        newly_moved += 1
        continue
    if not p.get('gone'):
        newly_gone += 1
    else:
        kept_gone += 1
    offices.append({**{k: p.get(k, '') for k in COLS}, 'gone': 1})
print(f'moved +{newly_moved}, gone +{newly_gone} (kept {kept_gone})')

# ---------- 4. みんなで追加 승인분 ----------
shared = 0
for r in json.load(open(changes_path))['rows']:
    if not r['id'].startswith('u') or r['hidden'] or r['lat'] is None:
        continue
    osm = next((o for o in new_by_name.get(r['name'], []) if dist_m(o, r) <= SAME_PLACE_M), None)
    if osm:  # OSM에도 생김 → OSM 국으로 합침
        moved[r['id']] = osm['id']
        continue
    shared += 1
    offices.append({
        'id': r['id'], 'name': r['name'], 'kana': '', 'lat': r['lat'], 'lon': r['lon'],
        'city': r['city'], 'kani': r['kani'], 'closed': 0, 'addr': '', 'gone': 1 if r['abolished'] else 0,
    })
print('shared offices', shared)

# 이동이 이어지면(a→b, b→c) 끝까지 따라감
for k in list(moved):
    seen, v = {k}, moved[k]
    while v in moved and v not in seen:
        seen.add(v)
        v = moved[v]
    moved[k] = v

# ---------- 5. 출력 ----------
os.makedirs(data_dir, exist_ok=True)
rows = [[o['id'], o['name'], o['kana'], o['lat'], o['lon'], o['city'],
         int(o['kani']), int(o['closed']), o['addr'], int(o.get('gone', 0))] for o in offices]
rows.sort(key=lambda r: (r[5], r[2] or r[1]))


def dump(name, obj):
    with open(os.path.join(data_dir, name), 'w') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))


dump('offices.json', {'v': 2, 'date': date, 'cols': COLS, 'rows': rows})
used = {r[5] for r in rows}
dump('areas.json', {'prefs': sorted(pref_names.items()),
                    'cities': sorted((c, n) for c, n in city_names.items() if c in used)})
dump('moved.json', moved)
active = sum(1 for r in rows if not r[9])
dump('meta.json', {'date': date, 'offices': active, 'gone': len(rows) - active, 'moved': len(moved)})
print(f'{date}: {active} offices, {len(rows) - active} gone, {len(moved)} moved')
