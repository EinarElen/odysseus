// static/js/devMode.js
//
// Launch-gated maintenance UI for hacking on the running Odysseus checkout.

import uiModule from './ui.js';
import workspaceModule from './workspace.js';

const API_BASE = window.location.origin;
const HOT_KEY = 'odysseus-dev-hot-reload';
const SHUT_KEY = 'odysseus-dev-hot-reload-shutup';
const SNOOZE_KEY = 'odysseus-dev-hot-reload-snooze-until';
const POLL_MS = 1400;
const SNOOZE_MS = 2 * 60 * 1000;
const CLIENT_ID = (() => {
  try {
    const key = 'odysseus-dev-client-id';
    let id = sessionStorage.getItem(key);
    if (!id) {
      id = globalThis.crypto?.randomUUID?.() || `dev-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
      sessionStorage.setItem(key, id);
    }
    return id;
  } catch (_) {
    return `dev-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }
})();

let state = {
  status: null,
  revision: null,
  poll: null,
  initialized: false,
  reloadPromptToken: null,
  reconnectToast: false,
};

function esc(value) {
  return uiModule?.esc ? uiModule.esc(String(value ?? '')) : String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

async function api(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-Odysseus-Dev-Client': CLIENT_ID, ...(options.headers || {}) },
  });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { detail: text }; }
  if (!res.ok) {
    const message = data.detail || data.error || `HTTP ${res.status}`;
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
  }
  return data;
}

function icon(markup) {
  return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${markup}</svg>`;
}

function injectStyle() {
  if (document.getElementById('dev-mode-style')) return;
  const style = document.createElement('style');
  style.id = 'dev-mode-style';
  style.textContent = `
    [data-settings-panel="developer"] .dev-mode-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:10px; }
    [data-settings-panel="developer"] .dev-mode-section { display:flex; flex-direction:column; gap:8px; min-width:0; }
    [data-settings-panel="developer"] .dev-mode-row { display:flex; align-items:center; justify-content:space-between; gap:10px; min-width:0; }
    [data-settings-panel="developer"] .dev-mode-actions { display:flex; flex-wrap:wrap; gap:7px; align-items:center; }
    [data-settings-panel="developer"] .dev-mode-pill { display:inline-flex; align-items:center; gap:5px; border:1px solid var(--border); border-radius:999px; padding:3px 8px; font-size:11px; color:color-mix(in srgb,var(--fg) 78%,transparent); max-width:100%; }
    [data-settings-panel="developer"] .dev-mode-pill.ok { color:#57d68d; border-color:color-mix(in srgb,#57d68d 45%,var(--border)); }
    [data-settings-panel="developer"] .dev-mode-pill.warn { color:#f0b85a; border-color:color-mix(in srgb,#f0b85a 45%,var(--border)); }
    [data-settings-panel="developer"] .dev-mode-kv { display:grid; grid-template-columns:minmax(74px,auto) minmax(0,1fr); gap:4px 9px; font-size:12px; }
    [data-settings-panel="developer"] .dev-mode-kv span:nth-child(odd) { color:color-mix(in srgb,var(--fg) 48%,transparent); }
    [data-settings-panel="developer"] .dev-mode-kv code, [data-settings-panel="developer"] .dev-mode-output { overflow:auto; white-space:pre-wrap; word-break:break-word; }
    [data-settings-panel="developer"] .dev-mode-output { max-height:260px; margin:0; padding:9px; border:1px solid var(--border); border-radius:6px; background:color-mix(in srgb,var(--panel) 75%,#000 12%); font-size:11px; line-height:1.45; }
    [data-settings-panel="developer"] .dev-mode-list { display:flex; flex-direction:column; gap:6px; max-height:220px; overflow:auto; }
    [data-settings-panel="developer"] .dev-mode-list-row { display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:8px; align-items:center; font-size:12px; border:1px solid var(--border); border-radius:6px; padding:6px 7px; }
    [data-settings-panel="developer"] .dev-mode-link { color:var(--accent,var(--red)); text-decoration:none; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    [data-settings-panel="developer"] .dev-mode-muted { color:color-mix(in srgb,var(--fg) 48%,transparent); font-size:12px; }
    [data-settings-panel="developer"] .dev-mode-toggle { display:inline-flex; align-items:center; gap:7px; font-size:12px; user-select:none; }
    [data-settings-panel="developer"] .dev-mode-toggle input { accent-color:var(--accent,var(--red)); }
    .toast .dev-reload-body { display:flex; flex-direction:column; gap:2px; min-width:0; }
    .toast .dev-reload-title { font-weight:650; white-space:nowrap; }
    .toast .dev-reload-detail { font-size:11px; opacity:0.78; max-width:min(480px,70vw); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .toast .dev-reload-actions { display:flex; gap:6px; align-items:center; margin-left:10px; flex-wrap:wrap; pointer-events:auto; }
    .toast .dev-reload-actions button { padding:3px 8px; border:1px solid color-mix(in srgb,var(--fg) 65%,transparent); border-radius:4px; background:none; color:var(--fg); cursor:pointer; font-size:12px; line-height:1.2; }
    .toast .dev-reload-actions button:hover { background:color-mix(in srgb,var(--fg) 10%,transparent); }
    @media (max-width: 720px) {
      [data-settings-panel="developer"] .dev-mode-grid { grid-template-columns:1fr; }
      [data-settings-panel="developer"] .dev-mode-row { align-items:flex-start; flex-direction:column; }
      .toast .dev-reload-detail { max-width:70vw; white-space:normal; }
    }
  `;
  document.head.appendChild(style);
}

function ensurePanel() {
  if (document.querySelector('[data-settings-tab="developer"]')) return true;
  const modal = document.getElementById('settings-modal');
  const sidebar = modal?.querySelector('.settings-sidebar');
  const panels = modal?.querySelector('.settings-panels');
  if (!modal || !sidebar || !panels) return false;

  const divider = document.createElement('div');
  divider.className = 'settings-sidebar-divider admin-only dev-mode-nav';
  const label = document.createElement('div');
  label.className = 'settings-sidebar-label admin-only dev-mode-nav';
  label.textContent = 'Local Dev';
  const button = document.createElement('button');
  button.className = 'settings-nav-item admin-only dev-mode-nav';
  button.dataset.settingsTab = 'developer';
  button.innerHTML = `${icon('<path d="m18 16 4-4-4-4"/><path d="m6 8-4 4 4 4"/><path d="m14.5 4-5 16"/>')}<span>Developer</span>`;
  sidebar.appendChild(divider);
  sidebar.appendChild(label);
  sidebar.appendChild(button);

  const panel = document.createElement('div');
  panel.dataset.settingsPanel = 'developer';
  panel.className = 'hidden';
  panel.innerHTML = `
    <div class="admin-card dev-mode-section">
      <div class="dev-mode-row">
        <h2 style="margin:0;display:flex;align-items:center;gap:6px;">${icon('<path d="m18 16 4-4-4-4"/><path d="m6 8-4 4 4 4"/><path d="m14.5 4-5 16"/>')}Developer Mode</h2>
        <div id="dev-mode-badges" class="dev-mode-actions"></div>
      </div>
      <div id="dev-mode-status" class="dev-mode-kv"></div>
      <div class="dev-mode-actions">
        <button type="button" class="theme-io-btn" id="dev-use-workspace">${icon('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>')}Use repo workspace</button>
        <button type="button" class="theme-io-btn" id="dev-refresh">${icon('<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 15.74-6.26L21 8"/><path d="M16 8h5V3"/>')}Refresh</button>
        <button type="button" class="theme-io-btn" id="dev-server-reload">${icon('<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>')}Restart server</button>
        <label class="dev-mode-toggle"><input type="checkbox" id="dev-hot-toggle"> Hot reload emulation</label>
      </div>
      <div id="dev-hot-note" class="dev-mode-muted"></div>
    </div>

    <div class="dev-mode-grid" style="margin-top:10px;">
      <div class="admin-card dev-mode-section">
        <h2 style="margin:0;">Git Snapshot</h2>
        <div id="dev-git-body" class="dev-mode-output">Not loaded</div>
      </div>
      <div class="admin-card dev-mode-section">
        <h2 style="margin:0;">Checks</h2>
        <div id="dev-checks" class="dev-mode-actions"></div>
        <pre id="dev-check-output" class="dev-mode-output">No check run yet</pre>
      </div>
    </div>

    <div class="dev-mode-grid" style="margin-top:10px;">
      <div class="admin-card dev-mode-section">
        <div class="dev-mode-row">
          <h2 style="margin:0;">Introspection</h2>
          <button type="button" class="theme-io-btn" id="dev-load-introspection">Load</button>
        </div>
        <div id="dev-introspection" class="dev-mode-output">Routes and built-in tools are available on demand.</div>
      </div>
      <div class="admin-card dev-mode-section">
        <div class="dev-mode-row">
          <h2 style="margin:0;">PRs and Issues</h2>
          <button type="button" class="theme-io-btn" id="dev-refresh-github">Refresh via gh</button>
        </div>
        <div id="dev-github" class="dev-mode-list"></div>
      </div>
    </div>
  `;
  panels.appendChild(panel);

  button.addEventListener('click', () => activateDeveloperTab());
  modal.addEventListener('click', (event) => {
    const tab = event.target?.closest?.('[data-settings-tab]');
    if (tab && tab.dataset.settingsTab !== 'developer') panel.classList.add('hidden');
  }, true);
  return true;
}

function activateDeveloperTab() {
  const modal = document.getElementById('settings-modal');
  if (!modal) return;
  modal.querySelectorAll('[data-settings-tab]').forEach(btn => btn.classList.toggle('active', btn.dataset.settingsTab === 'developer'));
  modal.querySelectorAll('[data-settings-panel]').forEach(panel => panel.classList.toggle('hidden', panel.dataset.settingsPanel !== 'developer'));
  document.body.classList.remove('settings-appearance-open');
  refreshAll();
}

function renderStatus(status) {
  state.status = status;
  const badges = document.getElementById('dev-mode-badges');
  const body = document.getElementById('dev-mode-status');
  const note = document.getElementById('dev-hot-note');
  if (!badges || !body) return;
  badges.innerHTML = `
    <span class="dev-mode-pill ${status.enabled ? 'ok' : 'warn'}">${status.enabled ? 'Enabled' : 'Disabled'}</span>
    <span class="dev-mode-pill ${status.reload_active ? 'ok' : 'warn'}">${status.reload_active ? 'Auto server reload' : 'Manual server reload'}</span>
  `;
  body.innerHTML = `
    <span>Root</span><code title="${esc(status.root)}">${esc(status.root)}</code>
    <span>Branch</span><code>${esc(status.branch || 'unknown')}</code>
    <span>Commit</span><code>${esc(status.commit || 'none')}</code>
    <span>Dirty</span><code>${esc(status.dirty_count ?? 0)} files</code>
    <span>Clients</span><code>${esc(status.client_count ?? 0)} active</code>
    <span>Remote</span><code>${esc(status.repo || 'none')}</code>
    <span>Reason</span><code>${esc(status.reason || '')}</code>
  `;
  if (note) {
    note.textContent = status.reload_active || status.manual_reload_supported === false
      ? 'Auto server reload is enabled. Save a server file to let the external reload supervisor restart Odysseus.'
      : 'Dev launches use interactive reload by default. Frontend changes prompt here; Python/server changes wait for Restart server.';
  }
  const reloadBtn = document.getElementById('dev-server-reload');
  if (reloadBtn) {
    const supported = status.manual_reload_supported !== false;
    reloadBtn.disabled = !supported;
    reloadBtn.title = supported ? 'Restart the server after Python/server changes' : 'Manual restart is disabled while auto reload is active';
  }
}

function setHotReload(on) {
  localStorage.setItem(HOT_KEY, on ? '1' : '0');
  if (on) localStorage.removeItem(SHUT_KEY);
  const toggle = document.getElementById('dev-hot-toggle');
  if (toggle) toggle.checked = on;
  if (on) startPolling();
  else stopPolling();
}

function stopPolling() {
  if (state.poll) clearInterval(state.poll);
  state.poll = null;
}

async function pollRevision() {
  if (!state.status?.enabled) return;
  try {
    const next = await api('/api/dev/revision');
    dismissReconnectToast();
    const prev = state.revision;
    if (!prev) {
      state.revision = next;
      return;
    }
    const change = classifyRevisionChange(prev, next);
    if (!change) {
      state.revision = next;
      return;
    }
    handleRevisionChange(change, next);
  } catch (err) {
    // During server reload, the app can briefly disappear. Keep polling, but
    // surface that state so it does not look like the watcher stalled.
    if (state.status?.reload_active && state.revision) {
      showReconnectToast();
    }
  }
}

function startPolling() {
  if (state.poll) return;
  pollRevision();
  state.poll = setInterval(pollRevision, POLL_MS);
}

function refreshStylesheets(token) {
  document.querySelectorAll('link[rel="stylesheet"][href]').forEach(link => {
    try {
      const url = new URL(link.href, window.location.href);
      url.searchParams.set('dev_css', token);
      link.href = url.toString();
    } catch (_) {}
  });
}

function classifyRevisionChange(prev, next) {
  const serverChanged = next.server_token !== prev.server_token;
  const codeChanged = next.frontend_code_token !== prev.frontend_code_token;
  const cssChanged = next.css_token !== prev.css_token;
  if (serverChanged) {
    return {
      kind: 'full',
      title: 'Full app change detected',
      detail: next.reload_active
        ? 'Server reload is active. This tab will reconnect after restart.'
        : 'Restart the server to run updated Python, then this tab will reconnect.',
      primary: next.reload_active ? 'Reload tab' : 'Restart server',
      autoMs: 0,
    };
  }
  if (codeChanged) {
    return {
      kind: 'frontend',
      title: 'Frontend-only change detected',
      detail: 'Reload this tab to load updated UI code.',
      primary: 'Reload now',
      autoMs: 0,
    };
  }
  if (cssChanged) {
    return {
      kind: 'css',
      title: 'Frontend style change detected',
      detail: 'Apply CSS without reloading the page.',
      primary: 'Apply CSS',
      autoMs: 0,
    };
  }
  return null;
}

function handleRevisionChange(change, next) {
  if (localStorage.getItem(SHUT_KEY) === '1') {
    state.revision = next;
    return;
  }
  const snoozeUntil = parseInt(localStorage.getItem(SNOOZE_KEY) || '0', 10) || 0;
  if (Date.now() < snoozeUntil) return;
  const toast = document.getElementById('toast');
  if (state.reloadPromptToken === next.token && toast?.dataset.devReload === '1' && toast.querySelector('.dev-reload-body')) return;
  showReloadToast(change, next);
}

function reloadBlockers() {
  const reasons = [];
  try {
    if (window.__odysseusChatBusy) reasons.push('active chat stream');
    const submit = document.getElementById('submit') || document.querySelector('.send-btn');
    if (submit?.dataset?.mode === 'streaming') reasons.push('active chat stream');
    if (window.compareModule?.isActive?.()) reasons.push('compare mode');
    const message = document.getElementById('message');
    if (message?.value?.trim()) reasons.push('unsent message');
    if (document.querySelector('[aria-busy="true"], [data-busy="1"], .ge-btn-busy-label')) reasons.push('active operation');
  } catch (_) {}
  return [...new Set(reasons)];
}

async function confirmReloadIfBusy(actionText) {
  const reasons = reloadBlockers();
  if (!reasons.length) return true;
  const msg = `Reload now? Active work may be interrupted: ${reasons.join(', ')}.`;
  if (typeof window.styledConfirm === 'function') {
    return !!(await window.styledConfirm(msg, { confirmText: actionText || 'Reload anyway', danger: true }));
  }
  return window.confirm(msg);
}

async function applyRevisionChange(change, next) {
  if (change.kind === 'css') {
    dismissReloadToast();
    state.revision = next;
    refreshStylesheets(next.css_token);
    return;
  }
  if (!(await confirmReloadIfBusy(change.primary || 'Reload anyway'))) return;
  dismissReloadToast();
  if (change.kind === 'full' && !next.reload_active) {
    await requestServerReload(next, { skipBusyCheck: true });
    return;
  }
  state.revision = next;
  window.location.reload();
}

async function requestServerReload(next = null, options = {}) {
  if (!options.skipBusyCheck && !(await confirmReloadIfBusy('Restart anyway'))) return;
  try {
    await api('/api/dev/server/reload', {
      method: 'POST',
      headers: { 'X-Odysseus-Dev-Action': 'server-reload' },
      body: JSON.stringify({}),
    });
    if (next) state.revision = next;
    showReconnectToast();
  } catch (err) {
    uiModule?.showToast?.(`Server restart failed: ${err.message}`, 7000, 'error');
  }
}

function dismissReloadToast() {
  const toast = document.getElementById('toast');
  if (!toast || toast.dataset.devReload !== '1') return;
  clearTimeout(toast._hideTimer);
  toast.classList.add('exiting');
  toast.classList.remove('show');
  toast.style.pointerEvents = '';
  delete toast.dataset.devReload;
  state.reloadPromptToken = null;
}

function showReconnectToast() {
  if (state.reconnectToast) return;
  const toast = document.getElementById('toast');
  if (!toast) return;
  clearTimeout(toast._hideTimer);
  toast.textContent = '';
  toast.classList.remove('error', 'exiting');
  toast.classList.add('show');
  toast.dataset.devReload = '1';
  toast.dataset.devReconnect = '1';
  toast.style.pointerEvents = 'auto';
  toast.style.left = '';
  toast.style.transform = '';

  const body = document.createElement('span');
  body.className = 'dev-reload-body';
  const title = document.createElement('span');
  title.className = 'dev-reload-title';
  title.textContent = 'Server restarting';
  const detail = document.createElement('span');
  detail.className = 'dev-reload-detail';
  detail.textContent = 'Waiting for Odysseus to come back online...';
  body.appendChild(title);
  body.appendChild(detail);
  toast.appendChild(body);
  state.reconnectToast = true;
}

function dismissReconnectToast() {
  const toast = document.getElementById('toast');
  if (!toast || toast.dataset.devReconnect !== '1') return;
  clearTimeout(toast._hideTimer);
  toast.classList.add('exiting');
  toast.classList.remove('show');
  toast.style.pointerEvents = '';
  delete toast.dataset.devReload;
  delete toast.dataset.devReconnect;
  state.reconnectToast = false;
}

function showReloadToast(change, next) {
  const toast = document.getElementById('toast');
  if (!toast) {
    applyRevisionChange(change, next);
    return;
  }
  clearTimeout(toast._hideTimer);
  toast.textContent = '';
  toast.classList.remove('error', 'exiting');
  toast.classList.add('show');
  toast.dataset.devReload = '1';
  state.reloadPromptToken = next.token;
  toast.style.pointerEvents = 'auto';
  toast.style.left = '';
  toast.style.transform = '';

  const body = document.createElement('span');
  body.className = 'dev-reload-body';
  const title = document.createElement('span');
  title.className = 'dev-reload-title';
  title.textContent = change.title;
  const detail = document.createElement('span');
  detail.className = 'dev-reload-detail';
  detail.textContent = change.detail;
  body.appendChild(title);
  body.appendChild(detail);
  toast.appendChild(body);

  const actions = document.createElement('span');
  actions.className = 'dev-reload-actions';
  const primary = document.createElement('button');
  primary.type = 'button';
  primary.textContent = change.primary;
  primary.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    void applyRevisionChange(change, next);
  });
  const snooze = document.createElement('button');
  snooze.type = 'button';
  snooze.textContent = 'Snooze';
  snooze.title = 'Snooze reload prompts for 2 minutes';
  snooze.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    localStorage.setItem(SNOOZE_KEY, String(Date.now() + SNOOZE_MS));
    dismissReloadToast();
  });
  const shut = document.createElement('button');
  shut.type = 'button';
  shut.textContent = 'Shut up';
  shut.title = 'Disable hot reload prompts';
  shut.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    localStorage.setItem(SHUT_KEY, '1');
    setHotReload(false);
    state.revision = next;
    dismissReloadToast();
  });
  actions.appendChild(primary);
  actions.appendChild(snooze);
  actions.appendChild(shut);
  toast.appendChild(actions);

  if (change.autoMs > 0) {
    toast._hideTimer = setTimeout(() => applyRevisionChange(change, next), change.autoMs);
  }
}

async function refreshStatus() {
  const status = await api('/api/dev/status');
  renderStatus(status);
  return status;
}

async function refreshGit() {
  const body = document.getElementById('dev-git-body');
  if (!body) return;
  try {
    const data = await api('/api/dev/git');
    body.textContent = [
      `$ git status --short`,
      data.short_status || '(clean)',
      '',
      `$ git diff --stat`,
      data.diff_stat || '(no unstaged diff)',
      '',
      `$ git log --oneline --decorate -10`,
      data.recent_log || '',
    ].join('\n');
  } catch (err) {
    body.textContent = err.message;
  }
}

async function refreshChecks() {
  const wrap = document.getElementById('dev-checks');
  if (!wrap) return;
  try {
    const data = await api('/api/dev/tests/suggest');
    wrap.innerHTML = (data.suggestions || []).map(item =>
      `<button type="button" class="theme-io-btn" data-dev-check="${esc(item.id)}" title="${esc(item.command_text)}">${esc(item.label)}</button>`
    ).join('');
    wrap.querySelectorAll('[data-dev-check]').forEach(btn => {
      btn.addEventListener('click', () => runCheck(btn.dataset.devCheck));
    });
  } catch (err) {
    wrap.innerHTML = `<span class="dev-mode-muted">${esc(err.message)}</span>`;
  }
}

async function runCheck(kind) {
  const out = document.getElementById('dev-check-output');
  if (!out || !kind) return;
  out.textContent = 'Running...';
  try {
    const data = await api('/api/dev/tests/run', {
      method: 'POST',
      body: JSON.stringify({ kind }),
    });
    out.textContent = [
      `$ ${data.command || kind}`,
      `exit ${data.exit_code} in ${data.duration_s ?? '?'}s`,
      '',
      data.stdout || '',
      data.stderr ? `\n[stderr]\n${data.stderr}` : '',
    ].join('\n').trim();
  } catch (err) {
    out.textContent = err.message;
  }
}

async function loadIntrospection() {
  const out = document.getElementById('dev-introspection');
  if (!out) return;
  out.textContent = 'Loading...';
  try {
    const data = await api('/api/dev/introspection');
    const routes = (data.routes || []).slice(0, 80).map(r => `${(r.methods || []).join(',') || 'GET'} ${r.path}`).join('\n');
    const tools = (data.built_in_tools || []).join(', ');
    out.textContent = [
      `${data.route_count || 0} routes`,
      routes,
      '',
      `${data.built_in_tool_count || 0} built-in tools`,
      tools,
      '',
      `Settings keys: ${(data.settings_keys || []).join(', ')}`,
    ].join('\n');
  } catch (err) {
    out.textContent = err.message;
  }
}

function renderGithub(cache) {
  const wrap = document.getElementById('dev-github');
  if (!wrap) return;
  const prs = cache?.prs?.items || [];
  const issues = cache?.issues?.items || [];
  const errors = cache?.errors || [];
  const rows = [];
  for (const pr of prs.slice(0, 10)) {
    rows.push(`<div class="dev-mode-list-row"><span>PR</span><a class="dev-mode-link" href="${esc(pr.url)}" target="_blank" rel="noopener noreferrer">#${esc(pr.number)} ${esc(pr.title)}</a><span class="dev-mode-muted">${esc((pr.author || {}).login || '')}</span></div>`);
  }
  for (const issue of issues.slice(0, 10)) {
    rows.push(`<div class="dev-mode-list-row"><span>Issue</span><a class="dev-mode-link" href="${esc(issue.url)}" target="_blank" rel="noopener noreferrer">#${esc(issue.number)} ${esc(issue.title)}</a><span class="dev-mode-muted">${esc((issue.author || {}).login || '')}</span></div>`);
  }
  if (errors.length) rows.unshift(`<div class="dev-mode-muted">${esc(errors.join(' '))}</div>`);
  wrap.innerHTML = rows.join('') || '<div class="dev-mode-muted">No cached PRs or issues. Refreshing is manual and may be stale.</div>';
}

async function refreshGithub(manual = false) {
  try {
    const cache = manual
      ? await api('/api/dev/github/refresh', { method: 'POST', body: JSON.stringify({ kinds: ['prs', 'issues'] }) })
      : await api('/api/dev/github/cache');
    renderGithub(cache);
  } catch (err) {
    renderGithub({ errors: [err.message] });
  }
}

async function refreshAll() {
  try {
    const status = await refreshStatus();
    if (!status.enabled) return;
    if (!state.revision) state.revision = await api('/api/dev/revision').catch(() => null);
    refreshGit();
    refreshChecks();
    refreshGithub(false);
  } catch (_) {}
}

function bindActions() {
  document.getElementById('dev-use-workspace')?.addEventListener('click', () => {
    const root = state.status?.root;
    if (!root) return;
    workspaceModule.setWorkspace(root);
    uiModule?.showToast?.('Workspace set to Odysseus checkout');
  });
  document.getElementById('dev-refresh')?.addEventListener('click', refreshAll);
  document.getElementById('dev-server-reload')?.addEventListener('click', () => {
    if (state.status?.manual_reload_supported === false) {
      uiModule?.showToast?.('Manual restart is disabled while auto reload is active', 5000, 'error');
      return;
    }
    dismissReloadToast();
    void requestServerReload(state.revision);
  });
  document.getElementById('dev-hot-toggle')?.addEventListener('change', event => setHotReload(!!event.target.checked));
  document.getElementById('dev-load-introspection')?.addEventListener('click', loadIntrospection);
  document.getElementById('dev-refresh-github')?.addEventListener('click', () => refreshGithub(true));
}

async function initDevMode() {
  if (state.initialized) return;
  let status;
  try {
    status = await api('/api/dev/status');
  } catch (_) {
    return;
  }
  if (!status.launch_requested && !status.enabled) return;
  injectStyle();
  if (!ensurePanel()) return;
  state.initialized = true;
  renderStatus(status);
  bindActions();
  setHotReload(localStorage.getItem(SHUT_KEY) !== '1');
}

document.addEventListener('DOMContentLoaded', () => {
  setTimeout(initDevMode, 0);
});

export default { initDevMode };
