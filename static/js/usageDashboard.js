const charts = new Map();
let echartsPromise;
let activeTab = 'overview';
let refreshTimer;

const el = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = value => value == null ? '—' : Number(value).toLocaleString();
const money = micros => micros == null ? '—' : `$${(Number(micros) / 1_000_000).toFixed(Number(micros) < 10000 ? 4 : 2)}`;
const range = () => el('usage-range')?.value || '7d';
const filterValues = () => ({
  kind: el('usage-filter-kind')?.value.trim() || '',
  provider: el('usage-filter-provider')?.value.trim() || '',
  model: el('usage-filter-model')?.value.trim() || '',
  status: el('usage-filter-status')?.value || '',
  cache_status: el('usage-filter-cache')?.value || '',
});
const query = extra => new URLSearchParams(Object.fromEntries(Object.entries({ from: range(), ...filterValues(), ...extra }).filter(([,value]) => value !== ''))).toString();

function persistView() {
  const view = { range: range(), tab: activeTab, ...filterValues() };
  localStorage.setItem('ody-usage-view', JSON.stringify(view));
  const url = new URL(window.location.href);
  url.searchParams.set('usage', '1');
  for (const [key, value] of Object.entries(view)) value ? url.searchParams.set(`usage_${key}`, value) : url.searchParams.delete(`usage_${key}`);
  history.replaceState(history.state, '', url);
}

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

function chart(id, option, onClick) {
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
      aria: { enabled: true, decal: { show: true } },
      animationDuration: 250,
      ...option,
    }, true);
    instance.off('click');
    if (onClick) instance.on('click', onClick);
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
  el('usage-cache-kpis').innerHTML = [
    ...data.slice(2,4),
    ['Cache savings', money(t.cache_savings_micros), t.cache_savings_micros==null?'pricing unavailable':'versus fresh input'],
    ['Warm Runs', number(t.cache_hit_runs), `${number(t.cache_unknown_runs)} unknown`],
  ].map(([label, value, note]) => `<article><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`).join('');
  el('usage-capacity-kpis').innerHTML = [
    ['Median latency', t.duration_median_ms == null ? '—' : `${number(t.duration_median_ms)} ms`, 'completed Runs'],
    ['p95 latency', t.duration_p95_ms == null ? '—' : `${number(t.duration_p95_ms)} ms`, 'completed Runs'],
    ['Cost / Run', t.total_cost_micros == null || !t.runs ? '—' : money(t.total_cost_micros / t.runs), 'successful and failed'],
    ['Cost / output', t.total_cost_micros == null || !t.output_tokens ? '—' : money(t.total_cost_micros / t.output_tokens), 'per token'],
    ['Near context limit', number(t.near_context_limit_runs), 'Runs at 80% or more'],
    ['Median generation', t.generation_tps_median==null?'—':`${number(t.generation_tps_median)} tok/s`, 'provider/backend timing'],
  ].map(([label,value,note]) => `<article><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`).join('');
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
  chart('usage-model-chart', { tooltip:{trigger:'axis',axisPointer:{type:'shadow'}}, xAxis:{type:'value'}, yAxis:{type:'category',data:models.items.map(i=>i.key).reverse()}, series:[{type:'bar',name:'Tokens',data:models.items.map(i=>i.input_tokens+i.output_tokens).reverse()}] }, event => { el('usage-filter-model').value=event.name; persistView(); refresh(); });
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
  const runStart = new Date(run.started_at).getTime();
  const runDuration = Math.max(run.duration_ms || 1, 1);
  const modelSpans = run.spans.filter(span=>span.kind==='model'&&span.usage).sort((a,b)=>a.sequence-b.sequence);
  const growth = modelSpans.slice(1).map((span,index)=>{
    const previous=modelSpans[index];
    const between=run.spans.filter(item=>item.sequence>previous.sequence&&item.sequence<span.sequence&&['tool','retrieval','compaction'].includes(item.kind));
    return {round:span.agent_round||index+2,delta:(span.usage.input_tokens??0)-(previous.usage.input_tokens??0),activities:between.map(item=>item.name),confidence:between.length===1?'high':'grouped'};
  });
  target.innerHTML = `<button type="button" class="usage-detail-close" aria-label="Close detail">×</button><button type="button" class="usage-detail-delete">Delete usage Run</button><h2>${esc(run.kind)} Run</h2><p><code>${esc(run.id)}</code> · ${esc(run.status)} · ${number(run.duration_ms)} ms</p><div class="usage-waterfall">${run.spans.map(span => {
    const indent = span.parent_span_id ? 1 : 0;
    const usage = span.usage ? `${number(span.usage.input_tokens)} in / ${number(span.usage.output_tokens)} out / ${number(span.usage.cache_read_tokens)} cached / ${money(span.usage.total_cost_micros)}` : '';
    const offset=Math.max(0,new Date(span.started_at).getTime()-runStart); const left=Math.min(100,100*offset/runDuration); const width=Math.max(1,Math.min(100-left,100*(span.duration_ms||1)/runDuration));
    const route=span.requested_model||span.actual_model?`${span.requested_model||'?'}${span.actual_model&&span.actual_model!==span.requested_model?` → ${span.actual_model}`:''}${span.provider?` via ${span.provider}`:''}`:'';
    return `<div style="--depth:${indent}"><strong>${esc(span.name)}</strong><div class="usage-waterfall-track"><i style="left:${left}%;width:${width}%"></i></div><span>${esc(route)} ${esc(span.status)} · ${number(span.duration_ms)} ms ${esc(usage)}</span></div>`;
  }).join('')}</div>${growth.length?`<h2>Context growth after activities</h2><table class="usage-growth"><thead><tr><th>Round</th><th>Input delta</th><th>Intervening activity</th><th>Attribution</th></tr></thead><tbody>${growth.map(item=>`<tr><td>${item.round}</td><td>${item.delta>0?'+':''}${number(item.delta)}</td><td>${esc(item.activities.join(', ')||'shared context')}</td><td>${esc(item.confidence)}</td></tr>`).join('')}</tbody></table>`:''}`;
  target.querySelector('.usage-detail-close').addEventListener('click', () => { target.hidden = true; });
  target.querySelector('.usage-detail-delete').addEventListener('click', async () => {
    if (!window.confirm('Delete this usage Run? Conversation content is not affected.')) return;
    const response = await fetch(`/api/usage/runs/${encodeURIComponent(id)}`, {method:'DELETE',credentials:'same-origin'});
    if (!response.ok) throw new Error('Usage Run could not be deleted');
    target.hidden = true; await refresh();
  });
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

async function loadCapacity() {
  const [timeline, runs] = await Promise.all([
    get(`/api/usage/timeseries?${query({bucket:range()==='24h'?'hour':'day'})}`),
    get(`/api/usage/runs?${query({limit:'500'})}`),
  ]);
  const labels = timeline.points.map(p => new Date(p.time).toLocaleString([], {month:'short',day:'numeric'}));
  chart('usage-cost-chart',{xAxis:{type:'category',data:labels},yAxis:{type:'value',axisLabel:{formatter:v=>`$${(v/1e6).toFixed(2)}`}},series:[{type:'bar',name:'USD micros',data:timeline.points.map(p=>p.total_cost_micros)}]});
  accessibleTable('usage-cost-table',['Time','Cost'],timeline.points.map((p,i)=>[labels[i],money(p.total_cost_micros)]));
  const buckets = [1000,5000,15000,30000,60000,300000];
  const counts = buckets.map((limit,index)=>runs.runs.filter(r=>r.duration_ms!=null&&r.duration_ms<=limit&&(index===0||r.duration_ms>buckets[index-1])).length);
  const bucketLabels = ['<1s','1–5s','5–15s','15–30s','30–60s','1–5m'];
  chart('usage-latency-chart',{xAxis:{type:'category',data:bucketLabels},yAxis:{type:'value'},series:[{type:'bar',data:counts}]});
  accessibleTable('usage-latency-table',['Latency','Runs'],bucketLabels.map((label,i)=>[label,counts[i]]));
}

async function loadAnomalies() {
  const data = await get(`/api/usage/anomalies?${query()}`);
  el('usage-anomalies-body').innerHTML = data.anomalies.map(item=>`<tr data-run-id="${esc(item.run_id)}"><td>${esc(new Date(item.started_at).toLocaleString())}</td><td><code>${esc(item.run_id)}</code></td><td>${esc(item.reasons.join(', '))}</td><td>${number(item.duration_ms)} ms</td><td>${money(item.total_cost_micros)}</td></tr>`).join('') || '<tr><td colspan="5">No deterministic anomalies in this range.</td></tr>';
  el('usage-anomalies-body').querySelectorAll('[data-run-id]').forEach(row=>row.addEventListener('click',()=>loadRunDetail(row.dataset.runId)));
}

async function refresh() {
  el('usage-error').textContent = '';
  try {
    await loadOverview();
    if (activeTab === 'runs') await loadRuns();
    if (activeTab === 'activity') await loadActivity();
    if (activeTab === 'cost') await loadCapacity();
    if (activeTab === 'anomalies') await loadAnomalies();
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
  if (tab === 'cost') loadCapacity().catch(error => { el('usage-error').textContent = error.message; });
  if (tab === 'anomalies') loadAnomalies().catch(error => { el('usage-error').textContent = error.message; });
  persistView();
  setTimeout(() => charts.forEach(instance => instance.resize()), 0);
}

export function open() {
  const workspace = el('usage-workspace');
  workspace.classList.add('open');
  workspace.setAttribute('aria-hidden','false');
  document.body.classList.add('usage-open');
  const params = new URLSearchParams(window.location.search);
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('ody-usage-view') || '{}'); } catch (_) {}
  const restore = key => params.get(`usage_${key}`) ?? saved[key] ?? '';
  if (restore('range')) el('usage-range').value = restore('range');
  for (const [key,id] of [['kind','usage-filter-kind'],['provider','usage-filter-provider'],['model','usage-filter-model'],['status','usage-filter-status'],['cache_status','usage-filter-cache']]) if (el(id)) el(id).value = restore(key);
  if (restore('tab')) selectTab(restore('tab'));
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
  el('usage-range')?.addEventListener('change', () => { persistView(); refresh(); });
  ['usage-filter-kind','usage-filter-provider','usage-filter-model','usage-filter-status','usage-filter-cache'].forEach(id => el(id)?.addEventListener('change', () => { persistView(); refresh(); }));
  el('usage-filter-clear')?.addEventListener('click', () => {
    ['usage-filter-kind','usage-filter-provider','usage-filter-model','usage-filter-status','usage-filter-cache'].forEach(id => { if (el(id)) el(id).value=''; });
    persistView(); refresh();
  });
  el('usage-export')?.addEventListener('click', () => { window.location.href = `/api/usage/export?${query({format:'jsonl'})}`; });
  document.querySelectorAll('[data-usage-tab]').forEach(button => button.addEventListener('click', () => selectTab(button.dataset.usageTab)));
  window.addEventListener('resize', () => charts.forEach(instance => instance.resize()));
}

export default { init, open, close };
