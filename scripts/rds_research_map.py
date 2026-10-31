"""Offline research note graph; edges express only recorded relationships."""
import json


def graph(notes, nudges):
    edges, previous = [], {}
    for note in notes:
        for ref in note.get('ref', []):
            edges.append({'source': note['id'], 'target': ref, 'kind': 'ref'})
        if note['type'] == 'attempt' and note.get('route'):
            route = note['route']
            if route in previous:
                edges.append({'source': previous[route], 'target': note['id'], 'kind': 'route'})
            previous[route] = note['id']
    return {'nodes': [dict(n) for n in notes], 'edges': edges}


def render(notes, nudges):
    data = {**graph(notes, nudges), 'nudges': nudges}
    payload = (json.dumps(data, ensure_ascii=False, allow_nan=False)
               .replace('&', '\\u0026').replace('<', '\\u003c')
               .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))
    return PAGE.replace('__DATA__', payload)


PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'">
<title>研究图谱</title>
<style>
:root { color-scheme: light dark; --bg:#f3f5f9; --panel:#fff; --text:#1b293c; --muted:#586a80; --line:#cbd5e1; --accent:#285aab; --warn:#a12e20; --warnbg:#fff3ee; --route:#69839f; }
@media (prefers-color-scheme: dark) { :root { --bg:#111925; --panel:#1b2636; --text:#ecf2fb; --muted:#b5c3d6; --line:#475b72; --accent:#9fc2ff; --warn:#ffb0a2; --warnbg:#422623; --route:#a1b5cc; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,'Microsoft YaHei',sans-serif; }
header { background:var(--panel); border-bottom:1px solid var(--line); padding:18px 24px; }
h1,h2,h3,p { margin:0; } h1 { font-size:23px; } h2 { font-size:17px; } h3 { font-size:15px; }
.summary { display:flex; flex-wrap:wrap; gap:8px 22px; margin-top:8px; color:var(--muted); overflow-wrap:anywhere; }
.summary strong { color:var(--text); }
.layout { display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:16px; padding:16px 24px; }
main,.sidebar section { min-width:0; background:var(--panel); border:1px solid var(--line); border-radius:10px; }
button,input,select { font:inherit; color:inherit; border:1px solid var(--line); border-radius:7px; background:var(--panel); padding:7px 10px; }
button { cursor:pointer; } button:focus-visible,button:hover { outline:2px solid var(--accent); outline-offset:1px; }
.toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; padding:14px; border-bottom:1px solid var(--line); }
.toolbar input { flex:1; min-width:120px; } .maphead { padding:14px 14px 0; }
.muted,.legend { color:var(--muted); } .legend { padding:12px 14px; font-size:12px; }
#map { position:relative; padding:10px 14px; } #wires { position:absolute; inset:0; width:100%; height:100%; overflow:visible; pointer-events:none; z-index:2; }
.wire { fill:none; stroke:var(--route); stroke-width:2; opacity:.8; } .wire.ref { stroke:var(--accent); stroke-dasharray:5 3; }
.wire.dim { opacity:.08; } .wire.active { opacity:1; stroke-width:3; }
.lane { position:relative; border-top:1px solid var(--line); padding:10px 0 12px; }
.lane-head { display:flex; gap:8px 14px; flex-wrap:wrap; align-items:baseline; margin-bottom:8px; overflow-wrap:anywhere; }
.lane-head span { color:var(--muted); font-size:13px; }
.cards { display:flex; flex-wrap:wrap; gap:14px 18px; }
.node { position:relative; z-index:3; width:150px; min-height:68px; text-align:left; overflow-wrap:anywhere; border-left:4px solid var(--accent); font-size:12px; line-height:1.35; }
.node strong,.node span { display:block; } .node span { color:var(--muted); margin-top:4px; }
.node.dim { opacity:.25; } .node.active { outline:2px solid var(--accent); outline-offset:2px; }
.sidebar { display:grid; align-content:start; gap:12px; } .sidebar section { padding:15px; overflow-wrap:anywhere; }
.warning { background:var(--warnbg)!important; border-color:var(--warn)!important; } .warning h2 { color:var(--warn); }
.alert { border-top:1px solid var(--line); padding:10px 0; } .alert button { margin:5px 5px 0 0; }
.detail p,.detail pre { white-space:pre-wrap; overflow-wrap:anywhere; } .detail pre { font:13px/1.5 system-ui; }
.relations { padding-left:18px; } .relations button { border:0; padding:2px; color:var(--accent); text-align:left; }
.empty { padding:20px 0; } details { margin-top:10px; } summary { cursor:pointer; }
@media(max-width:800px) { header { padding:15px; } .layout { display:flex; flex-direction:column; padding:12px; } .sidebar { order:-1; } .node { width:min(150px,100%); } }
</style>
</head>
<body>
<header>
  <h1>研究图谱</h1>
  <div id="summary" class="summary"></div>
  <p class="muted">记录按各路线自身顺序排列 · 记录不等于科学验证</p>
</header>
<div class="layout">
  <main>
    <div class="toolbar">
      <label for="search">搜索</label><input id="search" type="search" placeholder="搜索全文、编号或路线">
      <label for="type">类型</label><select id="type"><option value="">全部类型</option></select>
    </div>
    <div class="maphead"><h2>研究路线与记录</h2><p class="muted">点击记录高亮关联；点击折叠组展开明细。编号保留原始顺序。</p></div>
    <div id="map"></div>
    <p class="legend">实线：路线顺序　虚线：引用　·　边只表示已记录的关系</p>
  </main>
  <aside class="sidebar">
    <section class="warning"><h2>开放提醒 · <span id="count">0</span></h2><div id="alerts"></div><details id="closed"><summary>已关闭提醒</summary><div id="closed-alerts"></div></details></section>
    <section id="detail" class="detail"><h2>记录详情</h2><p class="muted">选择一条记录查看全文和关联。</p></section>
  </aside>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
'use strict';
const data = JSON.parse(document.getElementById('data').textContent);
const nodes = data.nodes, edges = data.edges, byId = new Map(nodes.map(n => [n.id, n]));
const types = {goal:['◆','目标'], expect:['◇','预期'], observe:['●','观测'], attempt:['■','尝试'], claim:['▲','主张'], constraint:['⬟','约束'], answer:['✚','回答'], dismiss:['✕','忽略'], shown:['○','已提示']};
const outcomes = {ok:'成功', fail:'失败', unknown:'未知'};
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = String(text); if (cls) e.className = cls; return e; };
const type = n => types[n.type] || ['○', n.type];
const result = n => outcomes[n.outcome] || '未知';
const map = document.getElementById('map'), detail = document.getElementById('detail');
const filter = document.getElementById('type'), search = document.getElementById('search');
const expanded = new Set();
let selected = null, positions = new Map();
const routes = new Set(nodes.filter(n => n.type === 'attempt' && n.route).map(n => n.route));
const goals = nodes.filter(n => n.type === 'goal');
const open = data.nudges.filter(n => n.status === 'OPEN');
const summary = document.getElementById('summary');
for (const [label, value] of [['目标', goals.map(n => n.text || '未填写').join('；') || '尚未记录'], ['路线', routes.size], ['观测', nodes.filter(n => n.type === 'observe').length], ['未关闭提醒', open.length]]) {
  const item = el('span'); item.append(el('strong', label + '：'), document.createTextNode(String(value))); summary.append(item);
}
for (const [key, value] of Object.entries(types)) {
  if (nodes.some(n => n.type === key)) { const option = el('option', value[1]); option.value = key; filter.append(option); }
}
document.getElementById('count').textContent = open.length;
function showReminder(n, container) {
  const box = el('div', undefined, 'alert');
  box.append(el('strong', n.key), el('p', n.question || ''));
  if (n.status !== 'OPEN') box.append(el('p', n.status === 'ANSWERED' ? '已回答' : '已忽略', 'muted'));
  for (const id of n.anchors || []) {
    if (!byId.has(id)) continue;
    const button = el('button', '查看 ' + id);
    button.onclick = () => reveal(id);
    box.append(button);
  }
  container.append(box);
}
const alerts = document.getElementById('alerts'), closedAlerts = document.getElementById('closed-alerts');
if (!open.length) alerts.append(el('p', '当前没有开放提醒。', 'muted'));
open.forEach(n => showReminder(n, alerts));
const closed = data.nudges.filter(n => n.status !== 'OPEN');
closed.forEach(n => showReminder(n, closedAlerts));
if (!closed.length) closedAlerts.append(el('p', '没有已关闭提醒。', 'muted'));
document.querySelector('#closed summary').textContent = `已关闭提醒 · ${closed.length}`;
function inspect(n) {
  selected = n.id;
  detail.replaceChildren(el('h2', `${type(n)[0]} ${type(n)[1]} · ${n.id}`), el('p', n.text || '（无正文）'));
  const fields = Object.entries(n).filter(([k]) => !['text','id','type','kind'].includes(k));
  if (fields.length) detail.append(el('pre', fields.map(([k,v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join('\n')));
  detail.append(el('h3', '关联记录'));
  const list = el('ul', undefined, 'relations');
  for (const edge of edges.filter(e => e.source === n.id || e.target === n.id)) {
    const other = edge.source === n.id ? edge.target : edge.source;
    const li = el('li'), button = el('button', `${edge.kind === 'ref' ? '引用' : '路线'} · ${edge.source} → ${edge.target}${byId.has(other) ? '' : ' · 记录未找到'}`);
    button.disabled = !byId.has(other); button.onclick = () => reveal(other); li.append(button); list.append(li);
  }
  if (!list.children.length) list.append(el('li', '无已记录关联'));
  detail.append(list); paintSelection();
}
function reveal(id) {
  search.value = ''; filter.value = '';
  const group = positions.get(id);
  if (group && group.ids.length > 1) expanded.add(group.ids[0]);
  draw(); inspect(byId.get(id));
  positions.get(id)?.button.scrollIntoView({behavior:'smooth', block:'center', inline:'nearest'});
}
function paintSelection() {
  const related = new Set(selected ? [selected] : []);
  for (const edge of edges) if (edge.source === selected || edge.target === selected) { related.add(edge.source); related.add(edge.target); }
  for (const group of new Set(positions.values())) {
    group.button.classList.toggle('active', !!selected && group.ids.some(id => related.has(id)));
    group.button.classList.toggle('dim', !!selected && !group.ids.some(id => related.has(id)));
  }
  for (const path of map.querySelectorAll('.wire')) {
    const active = path.dataset.source === selected || path.dataset.target === selected;
    path.classList.toggle('active', !!selected && active); path.classList.toggle('dim', !!selected && !active);
  }
}
function drawEdges() {
  const svg = map.querySelector('svg'); if (!svg) return;
  svg.replaceChildren();
  const base = map.getBoundingClientRect();
  for (const edge of edges) {
    const a = positions.get(edge.source), b = positions.get(edge.target);
    if (!a || !b) continue;
    const ar = a.button.getBoundingClientRect(), br = b.button.getBoundingClientRect();
    const x1 = ar.left + ar.width / 2 - base.left, y1 = ar.top + ar.height / 2 - base.top;
    const x2 = br.left + br.width / 2 - base.left, y2 = br.top + br.height / 2 - base.top;
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', a === b ? `M ${x1} ${y1} c 45 -65 65 -65 40 0` : `M ${x1} ${y1} C ${x1} ${(y1+y2)/2}, ${x2} ${(y1+y2)/2}, ${x2} ${y2}`);
    path.setAttribute('class', 'wire ' + edge.kind); path.dataset.source = edge.source; path.dataset.target = edge.target; svg.append(path);
  }
  paintSelection();
}
function draw() {
  map.replaceChildren(); positions = new Map();
  const query = search.value.trim().toLocaleLowerCase(), chosen = filter.value;
  const match = n => (!chosen || n.type === chosen) && (!query || JSON.stringify(n).toLocaleLowerCase().includes(query));
  const lanes = new Map();
  for (const n of nodes) {
    const name = n.type === 'attempt' ? (n.route || '未命名路线') : ({goal:'目标', observe:'观测', claim:'结论'}[n.type] || '其他记录');
    const key = (n.type === 'attempt' ? 'route:' : 'category:') + name;
    if (!lanes.has(key)) lanes.set(key, {name, rows:[]});
    lanes.get(key).rows.push(n);
  }
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.id = 'wires'; svg.setAttribute('aria-hidden', 'true'); map.append(svg);
  for (const {name, rows: all} of lanes.values()) {
    const visible = all.filter(match); if (!visible.length) continue;
    const lane = el('section', undefined, 'lane'), heading = el('div', undefined, 'lane-head');
    heading.append(el('h3', name));
    const attempts = all.filter(n => n.type === 'attempt');
    if (attempts.length) heading.append(el('span', `尝试 ${attempts.length} · 失败 ${attempts.filter(n => n.outcome === 'fail').length} · 最近 ${result(attempts[attempts.length-1])}`));
    lane.append(heading);
    const cards = el('div', undefined, 'cards'), groups = [];
    for (const n of visible) {
      const prev = groups[groups.length-1];
      if (n.type === 'attempt' && prev && prev[0].type === 'attempt' && (prev[0].outcome || 'unknown') === (n.outcome || 'unknown') && all.indexOf(n) === all.indexOf(prev[prev.length-1]) + 1) prev.push(n);
      else groups.push([n]);
    }
    for (const group of groups) {
      const parts = group.length > 1 && !expanded.has(group[0].id) ? [group] : group.map(n => [n]);
      for (const part of parts) {
        const n = part[0], button = el('button', undefined, 'node');
        button.append(el('strong', part.length > 1 ? `${result(n)} × ${part.length}` : `${type(n)[0]} ${type(n)[1]} · ${n.id}`));
        button.append(el('span', part.length > 1 ? `${n.id} — ${part[part.length-1].id} · 点击展开` : (n.type === 'attempt' ? result(n) + ' · ' : '') + (n.text || '（无正文）')));
        button.title = part.length > 1 ? '展开连续尝试' : (n.text || n.id);
        const entry = {button, ids:part.map(item => item.id)};
        for (const id of entry.ids) positions.set(id, entry);
        button.onclick = () => { if (part.length > 1) { expanded.add(group[0].id); draw(); } else inspect(n); };
        cards.append(button);
      }
    }
    lane.append(cards); map.append(lane);
  }
  if (!positions.size) map.append(el('p', nodes.length ? '没有匹配的记录。' : '还没有研究记录。先记录研究目标，再生成图谱。', 'empty'));
  requestAnimationFrame(drawEdges);
}
search.oninput = draw; filter.onchange = draw; window.addEventListener('resize', drawEdges); draw();
</script>
</body>
</html>'''
