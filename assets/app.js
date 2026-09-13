/* 日本の航路マップ — data/routes.js（tools/build.py が生成）を読み、地図と一覧を描く */
(function () {
  'use strict';

  const DATA = window.FERRY_DATA || { routes: [] };
  const CATS = [
    { key: 'longferry', label: '長距離フェリー' },
    { key: 'ferry', label: 'フェリー' },
    { key: 'passenger', label: '旅客船・高速船' },
    { key: 'sightseeing', label: '観光船・遊覧船' },
    { key: 'waterbus', label: '水上バス・渡船' },
  ];
  const CAT_LABEL = Object.fromEntries(CATS.map((c) => [c.key, c.label]));
  const WATERS = [
    { key: 'sea', label: '海' },
    { key: 'river', label: '川・運河' },
    { key: 'lake', label: '湖' },
  ];
  const WATER_LABEL = Object.fromEntries(WATERS.map((w) => [w.key, w.label]));
  const REGIONS = ['北海道', '東北', '関東', '中部', '近畿', '中国', '四国', '九州', '沖縄', '国際'];
  const BASEMAPS = {
    light: { label: '淡色', url: 'https://tiles.openfreemap.org/styles/positron' },
    color: { label: 'カラー', url: 'https://tiles.openfreemap.org/styles/liberty' },
    dark: { label: 'ダーク', url: 'https://tiles.openfreemap.org/styles/dark' },
  };
  const JAPAN = [[122.6, 24.0], [146.0, 45.7]];
  const CONFIDENCE = {
    high: '運航会社などの公式情報で 2026年の運航を確認',
    medium: '公式資料などで確認（細部に不確かさあり）',
    low: '確認できた情報が古い可能性があります',
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 保存できない環境 */ } },
  };
  const isMobile = () => window.matchMedia('(max-width: 820px)').matches;
  const canHover = window.matchMedia('(hover: hover)').matches;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function keyEl(r) {
    const k = el('span', 'key ' + r.service + (r.international ? ' intl' : '') + (r.loop && !r.lines.length ? ' loop' : ''));
    k.style.setProperty('--k', 'var(--c-' + r.service + ')');
    k.setAttribute('aria-hidden', 'true');
    return k;
  }

  // ---------------------------------------------------------------- data
  function decode(str) {
    const pts = [];
    let i = 0, lat = 0, lon = 0;
    while (i < str.length) {
      let b, sh = 0, res = 0;
      do { b = str.charCodeAt(i++) - 63; res |= (b & 0x1f) << sh; sh += 5; } while (b >= 0x20);
      lat += (res & 1) ? ~(res >> 1) : (res >> 1);
      sh = 0; res = 0;
      do { b = str.charCodeAt(i++) - 63; res |= (b & 0x1f) << sh; sh += 5; } while (b >= 0x20);
      lon += (res & 1) ? ~(res >> 1) : (res >> 1);
      pts.push([lon / 1e5, lat / 1e5]);
    }
    return pts;
  }
  function norm(s) {
    return String(s || '').normalize('NFKC').toLowerCase()
      .replace(/[ァ-ヶ]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0x60))
      .replace(/[\s・〜~\-－―ー()（）「」『』【】,、。.]/g, '');
  }
  function distM(la1, lo1, la2, lo2) {
    const r = Math.PI / 180, x = (lo2 - lo1) * r * Math.cos((la1 + la2) / 2 * r), y = (la2 - la1) * r;
    return Math.sqrt(x * x + y * y) * 6371000;
  }

  const routes = DATA.routes.map((r, idx) => {
    const lines = (r.lines || []).map(decode);
    let x1 = 180, y1 = 90, x2 = -180, y2 = -90;
    const ext = (x, y) => { if (x < x1) x1 = x; if (x > x2) x2 = x; if (y < y1) y1 = y; if (y > y2) y2 = y; };
    lines.forEach((l) => l.forEach((p) => ext(p[0], p[1])));
    r.ports.forEach((p) => ext(p[2], p[1]));
    return Object.assign({}, r, {
      idx, lines, bbox: [[x1, y1], [x2, y2]],
      text: norm([r.name, r.operator, r.ports.map((p) => p[0]).join(' '), r.prefectures.join(' '), r.vessels, r.regions.join(' ')].join(' ')),
    });
  });

  // 港: 同じ名前で 1.5km 以内のものは 1 つにまとめる
  const ports = [];
  (function () {
    const byName = new Map();
    routes.forEach((r) => r.ports.forEach(([name, lat, lon]) => {
      let arr = byName.get(name);
      if (!arr) byName.set(name, (arr = []));
      let p = arr.find((q) => distM(q.lat, q.lon, lat, lon) < 1500);
      if (!p) { p = { id: ports.length, name, lat, lon, routes: [] }; arr.push(p); ports.push(p); }
      if (!p.routes.includes(r.idx)) p.routes.push(r.idx);
    }));
  })();

  // ---------------------------------------------------------------- state
  const fresh = () => ({ q: '', cats: new Set(), waters: new Set(), region: '', o: false, c: false, year: false, sus: false });
  let state = fresh();
  let visible = [];
  let visibleSet = new Set();
  let selected = null;
  let hovered = null;
  let listScroll = 0;
  let basemap = store.get('jfm.basemap');
  if (!BASEMAPS[basemap]) basemap = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';

  function readHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    const s = fresh();
    s.q = h.get('q') || '';
    (h.get('s') || '').split(',').forEach((k) => { if (CAT_LABEL[k]) s.cats.add(k); });
    (h.get('w') || '').split(',').forEach((k) => { if (WATER_LABEL[k]) s.waters.add(k); });
    if (REGIONS.includes(h.get('reg'))) s.region = h.get('reg');
    s.o = h.get('o') === '1'; s.c = h.get('c') === '1'; s.year = h.get('y') === '1'; s.sus = h.get('sus') === '1';
    const r = routes.find((x) => x.id === h.get('r'));
    return { s, sel: r ? r.idx : null };
  }
  function writeHash() {
    const h = new URLSearchParams();
    if (selected != null) h.set('r', routes[selected].id);
    if (state.q) h.set('q', state.q);
    if (state.cats.size) h.set('s', [...state.cats].join(','));
    if (state.waters.size) h.set('w', [...state.waters].join(','));
    if (state.region) h.set('reg', state.region);
    if (state.o) h.set('o', '1');
    if (state.c) h.set('c', '1');
    if (state.year) h.set('y', '1');
    if (state.sus) h.set('sus', '1');
    const str = h.toString();
    history.replaceState(null, '', str ? '#' + str : location.pathname + location.search);
  }

  function matches(r, tokens) {
    if (state.cats.size && !state.cats.has(r.service)) return false;
    if (state.waters.size && !state.waters.has(r.water)) return false;
    if (state.region && !r.regions.includes(state.region)) return false;
    if (state.o && !r.overnight) return false;
    if (state.c && !r.car) return false;
    if (state.year && r.seasonal) return false;
    if (!state.sus && r.status === 'suspended') return false;
    return tokens.every((t) => r.text.includes(t));
  }

  function apply() {
    const tokens = state.q.split(/[\s　]+/).map(norm).filter(Boolean);
    visible = routes.filter((r) => matches(r, tokens));
    visibleSet = new Set(visible.map((r) => r.idx));
    renderControls();
    renderCount();
    renderList();
    updateMapData();
    writeHash();
  }

  // ---------------------------------------------------------------- panel
  const panel = $('#panel');

  function chip(label, count, pressed, onClick) {
    const b = el('button', 'chip');
    b.type = 'button';
    b.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    b.append(el('span', null, label));
    if (count != null) b.append(el('span', 'n', String(count)));
    b.addEventListener('click', onClick);
    return b;
  }
  function toggleIn(set, k) { if (set.has(k)) set.delete(k); else set.add(k); }

  function renderControls() {
    const cat = $('#catChips');
    cat.textContent = '';
    CATS.forEach((c) => {
      const n = routes.filter((r) => r.service === c.key && (state.sus || r.status !== 'suspended')).length;
      const b = chip(c.label, n, state.cats.has(c.key), () => { toggleIn(state.cats, c.key); apply(); });
      b.prepend(keyEl({ service: c.key, international: false, loop: false, lines: [1] }));
      cat.append(b);
    });
    const legend = el('div', 'legend-note');
    [['intl', '国際航路（破線）'], ['sus', '休航中（点線）'], ['loop', '周遊コース（出発港に印）']].forEach(([cls, text]) => {
      const s = el('span');
      const k = el('span', 'key neutral ' + cls);
      k.setAttribute('aria-hidden', 'true');
      s.append(k, el('span', null, text));
      legend.append(s);
    });
    cat.append(legend);

    const water = $('#waterChips');
    water.textContent = '';
    WATERS.forEach((w) => {
      const n = routes.filter((r) => r.water === w.key && (state.sus || r.status !== 'suspended')).length;
      water.append(chip(w.label, n, state.waters.has(w.key), () => { toggleIn(state.waters, w.key); apply(); }));
    });

    const reg = $('#regionChips');
    reg.textContent = '';
    REGIONS.forEach((g) => {
      const n = routes.filter((r) => r.regions.includes(g) && (state.sus || r.status !== 'suspended')).length;
      if (!n) return;
      reg.append(chip(g, n, state.region === g, () => { state.region = state.region === g ? '' : g; apply(); }));
    });

    document.querySelectorAll('[data-toggle]').forEach((b) => {
      b.setAttribute('aria-pressed', state[b.dataset.toggle] ? 'true' : 'false');
    });
    $('#optYear').checked = state.year;
    $('#optSus').checked = state.sus;
    const q = $('#q');
    if (document.activeElement !== q) q.value = state.q;

    const parts = [];
    if (state.cats.size) parts.push([...state.cats].map((k) => CAT_LABEL[k]).join('・'));
    if (state.waters.size) parts.push([...state.waters].map((k) => WATER_LABEL[k]).join('・'));
    if (state.region) parts.push(state.region);
    if (state.year) parts.push('通年のみ');
    if (state.sus) parts.push('休航中を含む');
    $('#filterSummary').textContent = parts.join(' / ');
  }

  function renderCount() {
    const c = $('#count');
    c.textContent = '';
    const left = el('span');
    left.append(el('b', null, String(visible.length)), document.createTextNode(' 航路を表示'));
    c.append(left, el('span', null, '全 ' + routes.length + ' 航路'));
  }

  function badgeSet(r) {
    const s = el('span', 'badges');
    if (r.overnight) { const b = el('span', null, '🌙'); b.title = '夜行・船中泊あり'; s.append(b); }
    if (r.car) { const b = el('span', null, '🚗'); b.title = 'マイカー搭載可'; s.append(b); }
    return s;
  }

  function renderList() {
    const list = $('#list');
    list.textContent = '';
    if (!routes.length) {
      list.append(el('div', 'empty', '航路データがまだありません（tools/build.py で data/routes.js を作成してください）'));
      return;
    }
    if (!visible.length) {
      const e = el('div', 'empty', '条件に合う航路がありません。');
      const b = el('button', 'linkbtn', '条件をリセット');
      b.type = 'button';
      b.addEventListener('click', resetFilters);
      e.append(el('br'), b);
      list.append(e);
      return;
    }
    // 長距離フェリーと国際航路は地方をまたぐので、それぞれ独立した見出しにまとめる
    const groups = new Map();
    visible.forEach((r) => {
      const g = r.international ? '国際航路' : r.service === 'longferry' ? '長距離フェリー' : (r.regions[0] || '国際航路');
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(r);
    });
    const frag = document.createDocumentFragment();
    ['長距離フェリー'].concat(REGIONS.slice(0, -1), ['国際航路']).forEach((g) => {
      const arr = groups.get(g);
      if (!arr) return;
      const h = el('div', 'group', g);
      h.append(el('span', 'n', arr.length + '航路'));
      frag.append(h);
      arr.forEach((r) => {
        const b = el('button', 'item' + (r.status === 'suspended' ? ' sus' : ''));
        b.type = 'button';
        b.dataset.idx = r.idx;
        const main = el('span', 'main');
        const name = el('span', 'name', r.name);
        if (r.international) name.append(el('span', 'tag', '国際'));
        if (r.seasonal) name.append(el('span', 'tag', '季節'));
        if (r.status === 'suspended') name.append(el('span', 'tag warn', '休航中'));
        main.append(name, el('span', 'op', r.operator + ' · ' + CAT_LABEL[r.service]));
        b.append(keyEl(r), main, badgeSet(r));
        frag.append(b);
      });
    });
    list.append(frag);
  }

  $('#list').addEventListener('click', (e) => {
    const b = e.target.closest('.item');
    if (b) select(Number(b.dataset.idx), { fit: true });
  });
  $('#list').addEventListener('mouseover', (e) => {
    const b = e.target.closest('.item');
    setHover(b ? Number(b.dataset.idx) : null);
  });
  $('#list').addEventListener('mouseleave', () => setHover(null));

  function yesNo(v) { return v === true ? '可' : v === false ? '不可' : '—'; }

  function renderDetail(r) {
    const d = $('#detail');
    d.textContent = '';
    const back = el('button', 'back', '← 一覧に戻る');
    back.type = 'button';
    back.addEventListener('click', deselect);
    d.append(back);

    const cat = el('div', 'cat');
    cat.append(keyEl(r), el('span', null, CAT_LABEL[r.service] + '・' + WATER_LABEL[r.water] + (r.loop ? '・周遊' : '') + (r.international ? '・国際航路' : '')));
    d.append(cat, el('h2', null, r.name), el('p', 'op', r.operator));

    const pills = el('div', 'pills');
    const pill = (cls, text) => pills.append(el('span', 'pill ' + cls, text));
    pill(r.overnight ? 'yes' : 'no', r.overnight ? '🌙 夜行・船中泊あり' : '🌙 夜行・船中泊なし');
    pill(r.car ? 'yes' : 'no', r.car ? '🚗 マイカー搭載可' : '🚗 マイカー不可');
    if (r.international) pill('yes', '🌏 国際航路');
    if (r.seasonal) pill('', '📅 季節運航');
    if (r.status === 'suspended') pill('warn', '⚠ 休航中');
    d.append(pills);

    const actions = el('div', 'actions');
    const link = (href, text, primary) => {
      const a = el('a', 'btn' + (primary ? ' primary' : ''));
      a.href = href;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.append(el('span', null, text), el('span', 'ext', '↗'));
      actions.append(a);
    };
    link(r.operator_url, '運航会社のサイト', true);
    if (r.route_url && r.route_url !== r.operator_url) link(r.route_url, '時刻表・運賃のページ', false);
    d.append(actions);

    const kv = el('dl', 'kv');
    const row = (k, v) => {
      if (v == null || v === '') return;
      kv.append(el('dt', null, k));
      const dd = el('dd');
      if (typeof v === 'string') dd.textContent = v; else dd.append(v);
      kv.append(dd);
    };
    const stops = el('ol', 'stops');
    stops.style.setProperty('--k', 'var(--c-' + r.service + ')');
    r.ports.forEach((p) => stops.append(el('li', null, p[0])));
    if (r.loop) stops.append(el('li', null, '（' + r.ports[0][0] + ' に戻る）'));
    row('寄港地', stops);
    row('所要時間', r.duration);
    row('便数', r.frequency);
    row('使用船', r.vessels);
    row('マイカー', (r.car ? '載せられる' : '載せられない') + (r.car_note ? '（' + r.car_note + '）' : ''));
    row('バイク', yesNo(r.bike));
    row('自転車', yesNo(r.bicycle));
    row('夜行・船中泊', r.overnight ? (r.overnight_note || 'あり') : 'なし');
    row('運航期間', r.seasonal ? (r.season_note || '季節運航') : (r.season_note || ''));
    const COUNTRY = { KR: '韓国', CN: '中国', TW: '台湾', RU: 'ロシア' };
    row('都道府県', r.prefectures.join('・') + (r.international ? '（' + r.countries.filter((c) => c !== 'JP').map((c) => COUNTRY[c] || c).join('・') + '方面）' : ''));
    row('備考', r.notes);
    d.append(kv);

    const notes = [];
    if (CONFIDENCE[r.confidence]) notes.push('情報の確かさ: ' + CONFIDENCE[r.confidence] + '。');
    if (r.gsrc === 'straight') notes.push('線の形は港どうしを直線で結んだ概略です。');
    if (r.gsrc === 'partial') notes.push('一部の区間は直線で結んだ概略です。');
    if (r.gsrc === 'approx') notes.push('一部の区間の線は海岸線から推定した概略の経路です。');
    if (r.gsrc === 'none') notes.push('周遊コースのため、出発港に印を付けています。');
    notes.push('ダイヤ・運賃・運航状況は必ず公式サイトで確認してください。');
    d.append(el('p', 'note', notes.join(' ')));
    if (r.sources && r.sources.length) {
      const det = el('details');
      det.append(el('summary', 'note', '確認に使った情報源（' + r.sources.length + '件）'));
      const ul = el('ul', 'sources');
      r.sources.forEach((u) => {
        if (!/^https?:\/\//.test(u)) return;
        const li = el('li');
        const a = el('a', null, u);
        a.href = u; a.target = '_blank'; a.rel = 'noopener noreferrer';
        li.append(a);
        ul.append(li);
      });
      det.append(ul);
      d.append(det);
    }
  }

  function select(idx, opts) {
    const o = Object.assign({ fit: true }, opts);
    if (selected == null) listScroll = $('#scroll').scrollTop;
    selected = idx;
    const r = routes[idx];
    renderDetail(r);
    $('#browse').hidden = true;
    $('#detail').hidden = false;
    $('#scroll').scrollTop = 0;
    pop.remove();
    tip.remove();
    updateSelection();
    if (isMobile() && panel.classList.contains('peek')) setSheet('half');
    if (o.fit === true || (o.fit === 'ifNeeded' && !inView(r))) fitRoute(r);
    writeHash();
  }
  function deselect() {
    if (selected == null) return;
    selected = null;
    $('#detail').hidden = true;
    $('#browse').hidden = false;
    $('#scroll').scrollTop = listScroll;
    updateSelection();
    writeHash();
  }

  function resetFilters() {
    state = fresh();
    $('#q').value = '';
    apply();
  }

  let qTimer = 0;
  $('#q').addEventListener('input', (e) => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => { state.q = e.target.value.trim(); apply(); }, 150);
  });
  $('#q').addEventListener('focus', () => { if (isMobile() && !panel.classList.contains('full')) setSheet('full'); });
  document.querySelectorAll('[data-toggle]').forEach((b) => {
    b.addEventListener('click', () => { state[b.dataset.toggle] = !state[b.dataset.toggle]; apply(); });
  });
  $('#optYear').addEventListener('change', (e) => { state.year = e.target.checked; apply(); });
  $('#optSus').addEventListener('change', (e) => { state.sus = e.target.checked; apply(); });
  $('#reset').addEventListener('click', resetFilters);

  function setSheet(s) {
    panel.classList.remove('peek', 'half', 'full');
    panel.classList.add(s);
  }
  $('#sheetHandle').addEventListener('click', () => {
    setSheet(panel.classList.contains('peek') ? 'half' : panel.classList.contains('half') ? 'full' : 'peek');
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (pop.isOpen()) { pop.remove(); return; }
    if (selected != null) deselect();
  });

  // ---------------------------------------------------------------- map
  const map = new maplibregl.Map({
    container: 'map',
    style: BASEMAPS[basemap].url,
    bounds: JAPAN,
    fitBoundsOptions: { padding: 20 },
    attributionControl: { compact: true, customAttribution: '航路線形 © OpenStreetMap contributors' },
    localIdeographFontFamily: '"Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic", "Meiryo", sans-serif',
    dragRotate: false,
    pitchWithRotate: false,
    maxPitch: 0,
  });
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  window.__jfm = { map, routes, ports, get state() { return state; }, get visible() { return visible; } };

  class ViewControl {
    onAdd() {
      const c = el('div', 'maplibregl-ctrl maplibregl-ctrl-group basemap-ctl');
      Object.entries(BASEMAPS).forEach(([k, v]) => {
        const b = el('button', null, v.label);
        b.type = 'button';
        b.dataset.basemap = k;
        b.title = '地図の見た目: ' + v.label;
        b.setAttribute('aria-pressed', k === basemap ? 'true' : 'false');
        b.addEventListener('click', () => setBasemap(k));
        c.append(b);
      });
      this.c = c;
      return c;
    }
    onRemove() { this.c.remove(); }
  }
  class HomeControl {
    onAdd() {
      const c = el('div', 'maplibregl-ctrl maplibregl-ctrl-group home-ctl');
      const b = el('button', null, '全国');
      b.type = 'button';
      b.title = '日本全体を表示';
      b.addEventListener('click', () => map.fitBounds(JAPAN, { padding: mapPadding(), duration: 600 }));
      c.append(b);
      this.c = c;
      return c;
    }
    onRemove() { this.c.remove(); }
  }
  map.addControl(new ViewControl(), 'top-right');
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(new HomeControl(), 'top-right');
  map.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: false }, fitBoundsOptions: { maxZoom: 10 } }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

  const tip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 14, maxWidth: '300px', className: 'tip-pop' });
  const pop = new maplibregl.Popup({ closeButton: true, closeOnClick: true, offset: 10, maxWidth: '330px' });

  function mapPadding() {
    if (isMobile()) {
      const h = panel.getBoundingClientRect().height;
      return { top: 56, bottom: Math.min(h + 24, window.innerHeight * 0.7), left: 24, right: 56 };
    }
    return { top: 48, bottom: 48, left: 48, right: 72 };
  }
  function inView(r) {
    const b = map.getBounds();
    return b.contains(r.bbox[0]) && b.contains(r.bbox[1]);
  }
  function fitRoute(r) {
    const [[x1, y1], [x2, y2]] = r.bbox;
    if (x2 - x1 < 0.004 && y2 - y1 < 0.004) {
      map.easeTo({ center: [(x1 + x2) / 2, (y1 + y2) / 2], zoom: 13.5, padding: mapPadding(), duration: 700 });
    } else {
      map.fitBounds(r.bbox, { padding: mapPadding(), maxZoom: 14, duration: 700 });
    }
  }

  function colors() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    return {
      longferry: v('--c-longferry'), ferry: v('--c-ferry'), passenger: v('--c-passenger'),
      sightseeing: v('--c-sightseeing'), waterbus: v('--c-waterbus'),
      surface: v('--surface'), ink: v('--ink'), ink2: v('--ink-2'),
      casing: basemap === 'dark' ? '#0e1419' : '#ffffff',
    };
  }

  const FC = (features) => ({ type: 'FeatureCollection', features });
  function routeFeature(r) {
    return {
      type: 'Feature',
      properties: { idx: r.idx, cat: r.service, intl: r.international ? 1 : 0, sus: r.status === 'suspended' ? 1 : 0 },
      geometry: { type: 'MultiLineString', coordinates: r.lines },
    };
  }
  const routesFC = () => FC(visible.filter((r) => r.lines.length).map(routeFeature));
  const loopsFC = () => FC(visible.filter((r) => !r.lines.length && r.ports.length).map((r) => ({
    type: 'Feature',
    properties: { idx: r.idx, cat: r.service, intl: r.international ? 1 : 0, sus: r.status === 'suspended' ? 1 : 0 },
    geometry: { type: 'Point', coordinates: [r.ports[0][2], r.ports[0][1]] },
  })));
  const portsFC = () => FC(ports.filter((p) => p.routes.some((i) => visibleSet.has(i))).map((p) => ({
    type: 'Feature', properties: { pid: p.id, name: p.name }, geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
  })));
  const selFC = () => FC(selected != null && routes[selected].lines.length ? [routeFeature(routes[selected])] : []);
  const selPortsFC = () => FC(selected == null ? [] : routes[selected].ports.map((p) => ({
    type: 'Feature', properties: { name: p[0] }, geometry: { type: 'Point', coordinates: [p[2], p[1]] },
  })));

  function overlayReady() { return !!map.getSource('routes'); }

  function updateMapData() {
    if (!overlayReady()) return;
    map.getSource('routes').setData(routesFC());
    map.getSource('loops').setData(loopsFC());
    map.getSource('ports').setData(portsFC());
  }
  const DIM_LAYERS = [['routes-casing', 'line-opacity', 0.85], ['routes-line', 'line-opacity', 1], ['routes-intl', 'line-opacity', 1],
    ['routes-sus', 'line-opacity', 0.75], ['loops', 'circle-stroke-opacity', 1]];
  function updateSelection() {
    if (!overlayReady()) return;
    map.getSource('sel').setData(selFC());
    map.getSource('sel-ports').setData(selPortsFC());
    DIM_LAYERS.forEach(([id, prop, o]) => {
      map.setPaintProperty(id, prop, selected == null ? o : ['case', ['==', ['get', 'idx'], selected], o, o * 0.28]);
    });
  }
  function setHover(idx) {
    if (hovered === idx) return;
    hovered = idx;
    if (!overlayReady()) return;
    map.setFilter('routes-hover', ['==', ['get', 'idx'], idx == null ? -1 : idx]);
    map.setFilter('routes-hover-casing', ['==', ['get', 'idx'], idx == null ? -1 : idx]);
  }

  function tweakBasemap() {
    (map.getStyle().layers || []).forEach((l) => {
      if (l.type !== 'symbol') return;
      if (/shield|oneway|one_way|poi|highway|road/.test(l.id)) {
        map.setLayoutProperty(l.id, 'visibility', 'none');
        return;
      }
      const tf = l.layout && l.layout['text-field'];
      if (tf && JSON.stringify(tf).indexOf('name') >= 0) {
        map.setLayoutProperty(l.id, 'text-field', ['coalesce', ['get', 'name:ja'], ['get', 'name:nonlatin'], ['get', 'name']]);
      }
    });
    if (map.getLayer('water')) {
      if (basemap === 'light') map.setPaintProperty('water', 'fill-color', '#d4e2ea');
      if (basemap === 'dark') map.setPaintProperty('water', 'fill-color', '#17212b');
    }
  }

  function addOverlay() {
    const C = colors();
    const color = ['match', ['get', 'cat'], 'longferry', C.longferry, 'ferry', C.ferry, 'passenger', C.passenger,
      'sightseeing', C.sightseeing, 'waterbus', C.waterbus, C.ink2];
    // 色は3色なので、長距離フェリー（太い）と水上バス・渡船（細い）は線の太さで区別する
    const base = ['match', ['get', 'cat'], 'longferry', 4.4, 'ferry', 2.6, 'passenger', 2.3, 'sightseeing', 2.3, 'waterbus', 1.5, 2];
    const width = (scale, add) => ['interpolate', ['linear'], ['zoom'],
      4, ['+', ['*', base, 0.5 * scale], add],
      7, ['+', ['*', base, 0.75 * scale], add],
      10, ['+', ['*', base, 1.0 * scale], add],
      14, ['+', ['*', base, 1.5 * scale], add]];
    const round = { 'line-cap': 'round', 'line-join': 'round' };
    const firstSymbol = ((map.getStyle().layers || []).find((l) => l.type === 'symbol') || {}).id;

    map.addSource('routes', { type: 'geojson', data: routesFC() });
    map.addSource('loops', { type: 'geojson', data: loopsFC() });
    map.addSource('ports', { type: 'geojson', data: portsFC() });
    map.addSource('sel', { type: 'geojson', data: selFC() });
    map.addSource('sel-ports', { type: 'geojson', data: selPortsFC() });

    map.addLayer({ id: 'routes-casing', type: 'line', source: 'routes', layout: round,
      paint: { 'line-color': C.casing, 'line-width': width(1, 2.4), 'line-opacity': 0.85 } }, firstSymbol);
    map.addLayer({ id: 'routes-line', type: 'line', source: 'routes', layout: round,
      filter: ['all', ['==', ['get', 'intl'], 0], ['==', ['get', 'sus'], 0]],
      paint: { 'line-color': color, 'line-width': width(1, 0) } }, firstSymbol);
    map.addLayer({ id: 'routes-intl', type: 'line', source: 'routes', layout: { 'line-join': 'round' },
      filter: ['all', ['==', ['get', 'intl'], 1], ['==', ['get', 'sus'], 0]],
      paint: { 'line-color': color, 'line-width': width(1, 0.4), 'line-dasharray': [3, 1.6] } }, firstSymbol);
    map.addLayer({ id: 'routes-sus', type: 'line', source: 'routes', layout: round,
      filter: ['==', ['get', 'sus'], 1],
      paint: { 'line-color': color, 'line-width': width(0.9, 0.3), 'line-dasharray': [0.1, 2.2], 'line-opacity': 0.75 } }, firstSymbol);

    map.addLayer({ id: 'routes-hover-casing', type: 'line', source: 'routes', layout: round, filter: ['==', ['get', 'idx'], -1],
      paint: { 'line-color': C.ink, 'line-width': width(1, 7), 'line-opacity': 0.22 } });
    map.addLayer({ id: 'routes-hover', type: 'line', source: 'routes', layout: round, filter: ['==', ['get', 'idx'], -1],
      paint: { 'line-color': color, 'line-width': width(1, 2) } });
    map.addLayer({ id: 'sel-casing', type: 'line', source: 'sel', layout: round,
      paint: { 'line-color': C.ink, 'line-width': width(1, 7), 'line-opacity': 0.5 } });
    map.addLayer({ id: 'sel-inner', type: 'line', source: 'sel', layout: round,
      paint: { 'line-color': C.surface, 'line-width': width(1, 4.5) } });
    map.addLayer({ id: 'sel-line', type: 'line', source: 'sel', layout: round,
      paint: { 'line-color': color, 'line-width': width(1, 2.4) } });
    map.addLayer({ id: 'routes-hit', type: 'line', source: 'routes', layout: round,
      paint: { 'line-color': '#000', 'line-opacity': 0, 'line-width': 16 } });

    map.addLayer({ id: 'loops', type: 'circle', source: 'loops',
      paint: {
        // 周遊コースの印は全国表示では小さく（数が多いので線より目立たないように）
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 1.8, 7, 3, 10, 5.5, 13, 7.5],
        'circle-color': C.surface,
        'circle-stroke-color': color,
        'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 4, 1, 7, 1.7, 10, 3],
      } });
    map.addLayer({ id: 'ports', type: 'circle', source: 'ports', minzoom: 6,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 1.6, 9, 3, 13, 4.8],
        'circle-color': C.surface,
        'circle-stroke-color': C.ink2,
        'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 6, 0.7, 12, 1.4],
      } });
    map.addLayer({ id: 'ports-label', type: 'symbol', source: 'ports', minzoom: 9.5,
      layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 11.5,
        'text-anchor': 'top', 'text-offset': [0, 0.7], 'text-optional': true, 'text-padding': 3 },
      paint: { 'text-color': C.ink2, 'text-halo-color': C.surface, 'text-halo-width': 1.5 } });
    map.addLayer({ id: 'sel-ports', type: 'circle', source: 'sel-ports',
      paint: { 'circle-radius': 5.5, 'circle-color': C.surface, 'circle-stroke-color': C.ink, 'circle-stroke-width': 2.2 } });
    map.addLayer({ id: 'sel-ports-label', type: 'symbol', source: 'sel-ports',
      layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Bold'], 'text-size': 13,
        'text-anchor': 'top', 'text-offset': [0, 0.8], 'text-padding': 2 },
      paint: { 'text-color': C.ink, 'text-halo-color': C.surface, 'text-halo-width': 2 } });

    hovered = null;
    updateSelection();
  }

  map.on('style.load', () => {
    tweakBasemap();
    addOverlay();
  });

  function setBasemap(k) {
    if (k === basemap) return;
    basemap = k;
    store.set('jfm.basemap', k);
    document.documentElement.setAttribute('data-theme', k === 'dark' ? 'dark' : 'light');
    document.querySelectorAll('.basemap-ctl button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.basemap === k ? 'true' : 'false'));
    tip.remove();
    map.setStyle(BASEMAPS[k].url, { diff: false });
  }

  function hitRoutes(pt, pad) {
    if (!overlayReady()) return [];
    const fs = map.queryRenderedFeatures([[pt.x - pad, pt.y - pad], [pt.x + pad, pt.y + pad]], { layers: ['routes-hit', 'loops'] });
    const seen = new Set();
    const out = [];
    fs.forEach((f) => {
      const i = f.properties.idx;
      if (!seen.has(i)) { seen.add(i); out.push(i); }
    });
    return out;
  }
  function hitPort(pt, pad) {
    if (!overlayReady() || map.getZoom() < 6) return null;
    const fs = map.queryRenderedFeatures([[pt.x - pad, pt.y - pad], [pt.x + pad, pt.y + pad]], { layers: ['ports'] });
    return fs.length ? ports[fs[0].properties.pid] : null;
  }

  function routeRow(r) {
    const row = el('span', 't-row');
    row.append(keyEl(r), el('span', 't-name', r.name));
    return row;
  }
  function tipContent(hits) {
    const box = el('div', 'tip');
    hits.slice(0, 3).forEach((i) => {
      const r = routes[i];
      box.append(routeRow(r));
      const op = el('div', 't-op', r.operator + (r.overnight ? ' · 🌙' : '') + (r.car ? ' · 🚗' : ''));
      box.append(op);
    });
    if (hits.length > 3) box.append(el('div', 'more', 'ほか ' + (hits.length - 3) + ' 航路（クリックで一覧）'));
    return box;
  }
  function chooser(title, sub, idxs, lngLat) {
    const box = el('div', 'pop');
    box.append(el('h3', null, title));
    if (sub) box.append(el('p', 'sub', sub));
    const ul = el('ul');
    idxs.forEach((i) => {
      const r = routes[i];
      const b = el('button');
      b.type = 'button';
      const txt = el('span');
      txt.append(el('span', 'nm', r.name), el('span', 'op', r.operator));
      b.append(keyEl(r), txt, el('span', 'b', (r.overnight ? '🌙' : '') + (r.car ? '🚗' : '')));
      b.addEventListener('click', () => select(i, { fit: true }));
      const li = el('li');
      li.append(b);
      ul.append(li);
    });
    box.append(ul);
    pop.setLngLat(lngLat).setDOMContent(box).addTo(map);
  }

  map.on('mousemove', (e) => {
    if (!canHover) return;
    const port = hitPort(e.point, 5);
    const hits = port ? [] : hitRoutes(e.point, 7);
    map.getCanvas().style.cursor = port || hits.length ? 'pointer' : '';
    setHover(hits.length ? hits[0] : null);
    if (port) {
      const box = el('div', 'tip');
      box.append(el('div', 't-name', port.name));
      box.append(el('div', 'more', port.routes.filter((i) => visibleSet.has(i)).length + ' 航路（クリックで一覧）'));
      tip.setLngLat([port.lon, port.lat]).setDOMContent(box).addTo(map);
    } else if (hits.length) {
      tip.setLngLat(e.lngLat).setDOMContent(tipContent(hits)).addTo(map);
    } else {
      tip.remove();
    }
  });
  map.getCanvas().addEventListener('mouseleave', () => { setHover(null); tip.remove(); });

  map.on('click', (e) => {
    tip.remove();
    // 開いているポップアップは同じ click で閉じられるので、新しい一覧はイベント処理の後に開く
    const later = (fn) => setTimeout(fn, 0);
    const port = hitPort(e.point, canHover ? 5 : 9);
    if (port) {
      const shown = port.routes.filter((i) => visibleSet.has(i));
      const idxs = shown.length ? shown : port.routes;
      later(() => chooser(port.name, idxs.length + ' 航路' + (shown.length ? '' : '（絞り込み条件の対象外）'), idxs, [port.lon, port.lat]));
      return;
    }
    const hits = hitRoutes(e.point, canHover ? 7 : 12);
    if (hits.length === 1) select(hits[0], { fit: 'ifNeeded' });
    else if (hits.length > 1) later(() => chooser('この付近の航路', hits.length + ' 航路', hits, e.lngLat));
  });

  // ---------------------------------------------------------------- start
  const initial = readHash();
  state = initial.s;
  $('#subtitle').textContent = 'フェリー・高速船・観光船・水上バスなど ' + routes.length + ' 航路';
  if (isMobile()) $('#filters').open = false;
  apply();
  // 利用者が地図を動かすまでは、表示領域の大きさが変わるたびに日本全体（または選択中の航路）へ合わせ直す
  let touched = false;
  ['dragstart', 'zoomstart'].forEach((ev) => map.on(ev, (e) => { if (e.originalEvent) touched = true; }));
  const refit = () => {
    if (touched) return;
    if (selected != null) map.fitBounds(routes[selected].bbox, { padding: mapPadding(), maxZoom: 14, duration: 0 });
    else map.fitBounds(JAPAN, { padding: mapPadding(), duration: 0 });
  };
  map.on('resize', refit);
  // 共有リンクの航路は地図タイルの読み込み（load）を待たずに開く。線の強調は重ね合わせ層ができた時点で反映される
  if (initial.sel != null) select(initial.sel, { fit: true });
  map.once('load', () => {
    // 出典表示は MapLibre の標準動作のまま（最初は表示し、地図を動かすと ⓘ に畳まれる）
    if (initial.sel == null) refit();
  });
  // 共有リンクの貼り付けや戻る操作で URL の # 以降だけが変わったとき
  window.addEventListener('hashchange', () => {
    const h = readHash();
    state = h.s;
    apply();
    if (h.sel != null) select(h.sel, { fit: true });
    else deselect();
  });
})();
