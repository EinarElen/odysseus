const charts = new Map();
let echartsPromise;
let activeTab = 'overview';
let refreshTimer;

const el = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = value => value == null ? '—' : Number(value).toLocaleString();
const money = micros => micros == null ? '—' : `$${(Number(micros) / 1_000_000).toFixed(Number(micros) < 10000 ? 4 : 2)}`;
const range = () => el('usage-range')?.value || '7d';
const query = extra => new URLSearchParams({ from: range(), ...extra }).toString();

function loadECharts() {
  if (window.echarts) return Promise.resolve(window.echarts);
  if (!echartsPromise) echartsPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = '/static/lib/echarts.min.js';
    script.onload = () => resolve(window.echarts);
    script.onerror = () => reject(new Error('Could not load chart renderer'));
    document.head.appendChild(script);
  });
  return echartsPromise;
}

async function get(path) {
  const response = await fetch(path, { credentials: 'same-origin' });
  if (!response.ok) throw new Error((await response.text()) || `Request failed (${response.status})`);
  return response.json();
}

function chart(id, option) {
  return loadECharts().then(echarts => {
    const target = el(id);
    if (!target) return;
    const instance = charts.get(id) || echarts.init(target, null, { renderer: 'canvas' });
    charts.set(id, instance);
    instance.setOption({
      backgroundColor: 'transparent',
      textStyle: { color: getComputedStyle(document.documentElement).getPropertyValue('--fg') || '#bbb' },
      tooltip: { trigger: 'axis' },
      grid: { left: 55, right: 20, top: 25, bottom: 45 },
      animationDuration: 250,
      ...option,
    }, true);
  });
}

function accessibleTable(targetId, headers, rows) {
  const target = el(targetId);
  if (!target) return;
  target.innerHTML = `<details class="usage-data-table"><summary>View chart data</summary><table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></details>`;
}

function kpis(summary) {
  const t = summary.totals;
  const rate = t.cache_read_tokens != null && t.fresh_input_tokens != null && (t.cache_read_tokens + t.fresh_input_tokens) > 0
    ? `${(100 * t.cache_read_tokens / (t.cache_read_tokens + t.fresh_input_tokens)).toFixed(1)}%` : 'Unavailable';
  const data = [
    ['Input', number(t.input_tokens), summary.quality], ['Output', number(t.output_tokens), summary.quality],
    ['Cache read', number(t.cache_read_tokens), t.cache_read_tokens == null ? 'unknown' : 'exact'],
    ['Cache hit rate', rate, t.fresh_input_tokens == null ? 'unknown semantics' : 'exact'],
    ['Cost', money(t.total_cost_micros), t.total_cost_micros == null ? 'not priced' : 'USD'],
    ['Runs', number(t.runs), `${number(t.failed_runs)} failed`],
  ];
  el('usage-kpis').innerHTML = data.map(([label, value, note]) => `<article><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`).join('');
  el('usage-cache-kpis').innerHTML = data.slice(2, 4).map(([label, value, note]) => `<article><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`).join('');
}

async function loadOverview() {
  const [summary, timeline, models] = await Promise.all([
    get(`/api/usage/summary?${query()}`), get(`/api/usage/timeseries?${query({bucket: range() === '24h' ? 'hour' : 'day'})}`),
    get(`/api/usage/breakdown?${query({group_by:'model'})}`),
  ]);
  kpis(summary);
  const times = timeline.points.map(p => new Date(p.time).toLocaleString([], {month:'short', day:'numeric', hour: range() === '24h' ? 'numeric' : undefined}));
  const tokenSeries = [
    ['Input', timeline.points.map(p => p.input_tokens)], ['Output', timeline.points.map(p => p.output_tokens)],
    ['Cache read', timeline.points.map(p => p.cache_read_tokens || 0)], ['Cache write', timeline.points.map(p => p.cache_write_tokens || 0)],
  ];
  chart('usage-token-chart', { legend: {}, xAxis: {type:'category', data:times}, yAxis:{type:'value'}, dataZoom:[{type:'inside'},{type:'slider'}], series:tokenSeries.map(([name,data]) => ({name,type:'line',stack:name.includes('Cache')?'cache':undefined,showSymbol:false,areaStyle:{opacity:.08},data})) });
  accessibleTable('usage-token-table', ['Time','Input','Output','Cache read','Cache write'], timeline.points.map((p,i) => [times[i],p.input_tokens,p.output_tokens,p.cache_read_tokens ?? 'unknown',p.cache_write_tokens ?? 'unknown']));
  chart('usage-model-chart', { tooltip:{trigger:'axis',axisPointer:{type:'shadow'}}, xAxis:{type:'value'}, yAxis:{type:'category',data:models.items.map(i=>i.key).reverse()}, series:[{type:'bar',name:'Tokens',data:models.items.map(i=>i.input_tokens+i.output_tokens).reverse()}] });
  accessibleTable('usage-model-table', ['Model','Input','Output','Runs'], models.items.map(i => [i.key,i.input_tokens,i.output_tokens,i.runs]));
  renderCache(timeline);
}

function renderCache(timeline) {
  const labels = timeline.points.map(p => new Date(p.time).toLocaleDateString());
  chart('usage-cache-chart', { legend:{}, xAxis:{type:'category',data:labels}, yAxis:{type:'value'}, series:[
    {type:'bar',stack:'input',name:'Fresh input',data:timeline.points.map(p=>p.fresh_input_tokens)},
    {type:'bar',stack:'input',name:'Cache read',data:timeline.points.map(p=>p.cache_read_tokens)},
    {type:'line',name:'Cache write',data:timeline.points.map(p=>p.cache_write_tokens)},
  ]});
  accessibleTable('usage-cache-table',['Time','Fresh input','Cache read','Cache write'],timeline.points.map((p,i)=>[labels[i],p.fresh_input_tokens??'unknown',p.cache_read_tokens??'unknown',p.cache_write_tokens??'unknown']));
}

async function loadRuns() {
  const data = await get(`/api/usage/runs?${query({limit:'200'})}`);
  const body = el('usage-runs-body');
  body.innerHTML = data.runs.map(run => `<tr tabindex="0" data-run-id="${esc(run.id)}"><td>${esc(new Date(run.started_at).toLocaleString())}</td><td>${esc(run.kind)}</td><td><span class="usage-status usage-status-${esc(run.status)}">${esc(run.status)}</span></td><td>${esc(run.source_surface)}</td><td>${number(run.input_tokens)}</td><td>${number(run.output_tokens)}</td><td>${number(run.cache_read_tokens)}</td><td>${run.duration_ms == null?'—':`${number(run.duration_ms)} ms`}</td><td>${money(run.total_cost_micros)}</td></tr>`).join('') || '<tr><td colspan="9">No usage Runs in this range.</td></tr>';
  body.querySelectorAll('[data-run-id]').forEach(row => {
    const open = () => loadRunDetail(row.dataset.runId);
    row.addEventListener('click', open);
    row.addEventListener('keydown', event => { if (event.key === 'Enter') open(); });
  });
}

async function loadRunDetail(id) {
  const run = await get(`/api/usage/runs/${encodeURIComponent(id)}`);
  const target = el('usage-run-detail');
  target.hidden = false;
  target.innerHTML = `<button type="button" class="usage-detail-close" aria-label="Close detail">×</button><h2>${esc(run.kind)} Run</h2><p><code>${esc(run.id)}</code> · ${esc(run.status)} · ${number(run.duration_ms)} ms</p><div class="usage-waterfall">${run.spans.map(span => {
    const indent = span.parent_span_id ? 1 : 0;
    const usage = span.usage ? `${number(span.usage.input_tokens)} in / ${number(span.usage.output_tokens)} out / ${number(span.usage.cache_read_tokens)} cached` : '';
    return `<div style="--depth:${indent}"><strong>${esc(span.name)}</strong><span>${esc(span.status)} · ${number(span.duration_ms)} ms ${esc(usage)}</span></div>`;
  }).join('')}</div>`;
  target.querySelector('.usage-detail-close').addEventListener('click', () => { target.hidden = true; });
}

async function loadActivity() {
  const [kinds, tools] = await Promise.all([
    get(`/api/usage/breakdown?${query({group_by:'kind'})}`), get(`/api/usage/breakdown?${query({group_by:'tool'})}`),
  ]);
  for (const [id, tableId, data] of [['usage-kind-chart','usage-kind-table',kinds],['usage-tool-chart','usage-tool-table',tools]]) {
    const isTools = data.group_by === 'tool';
    chart(id,{tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},xAxis:{type:'value'},yAxis:{type:'category',data:data.items.map(i=>i.key).reverse()},series:[{type:'bar',name:isTools?'Invocations':'Tokens',data:data.items.map(i=>isTools?i.invocations:i.input_tokens+i.output_tokens).reverse()}]});
    accessibleTable(tableId,isTools?['Tool','Invocations','Failed','Duration']:['Activity','Input','Output','Runs'],data.items.map(i=>isTools?[i.key,i.invocations,i.failed,`${i.duration_ms} ms`]:[i.key,i.input_tokens,i.output_tokens,i.runs]));
  }
}

async function refresh() {
  el('usage-error').textContent = '';
  try {
    await loadOverview();
    if (activeTab === 'runs') await loadRuns();
    if (activeTab === 'activity') await loadActivity();
  } catch (error) {
    el('usage-error').textContent = error.message || 'Usage data could not be loaded.';
  }
}

function selectTab(tab) {
  activeTab = tab;
  document.querySelectorAll('[data-usage-tab]').forEach(button => button.classList.toggle('active', button.dataset.usageTab === tab));
  document.querySelectorAll('.usage-view').forEach(view => { view.hidden = view.id !== `usage-${tab}`; });
  if (tab === 'runs') loadRuns().catch(error => { el('usage-error').textContent = error.message; });
  if (tab === 'activity') loadActivity().catch(error => { el('usage-error').textContent = error.message; });
  setTimeout(() => charts.forEach(instance => instance.resize()), 0);
}

export function open() {
  const workspace = el('usage-workspace');
  workspace.classList.add('open');
  workspace.setAttribute('aria-hidden','false');
  document.body.classList.add('usage-open');
  refresh();
  clearInterval(refreshTimer);
  refreshTimer = setInterval(refresh, 15_000);
}

export function close() {
  const workspace = el('usage-workspace');
  workspace.classList.remove('open');
  workspace.setAttribute('aria-hidden','true');
  document.body.classList.remove('usage-open');
  clearInterval(refreshTimer);
  refreshTimer = null;
}

export function init() {
  el('usage-close')?.addEventListener('click', close);
  el('usage-range')?.addEventListener('change', refresh);
  el('usage-export')?.addEventListener('click', () => { window.location.href = `/api/usage/export?${query({format:'jsonl'})}`; });
  document.querySelectorAll('[data-usage-tab]').forEach(button => button.addEventListener('click', () => selectTab(button.dataset.usageTab)));
  window.addEventListener('resize', () => charts.forEach(instance => instance.resize()));
}

export default { init, open, close };
