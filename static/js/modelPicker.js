// Model Picker — chatbox model selector dropdown
// Extracted from sessions.js

import { providerLogo } from './providers.js';
import uiModule from './ui.js';
import settingsModule from './settings.js';
import { sortModelObjects } from './modelSort.js';

const API_BASE = window.location.origin;

// ── Recent + Favorites persistence ──
// Recent is auto-tracked (last 5 picks, most-recent-first) and lives in its
// own key. Favorites is the SAME key the sidebar Models section uses, so a
// favorite toggled here shows up there and vice-versa.
const RECENT_KEY = 'odysseus-model-recent';
const FAVORITES_KEY = 'odysseus-model-favorites';
const RECENT_MAX = 5;
// Catalogs at or below this size are small enough that hiding everything
// behind search would be a regression — keep listing them in browse mode.
const BROWSE_ALL_LIMIT = 12;

function _loadList(key) {
  try {
    const a = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(a) ? a : [];
  } catch { return []; }
}
function _saveList(key, list) {
  try { localStorage.setItem(key, JSON.stringify(list)); } catch { /* quota / private mode */ }
}
function _loadRecent() { return _loadList(RECENT_KEY); }
function _pushRecent(mid) {
  if (!mid) return;
  const next = _loadRecent().filter(x => x !== mid);
  next.unshift(mid);
  _saveList(RECENT_KEY, next.slice(0, RECENT_MAX));
}
function _loadFavorites() { return _loadList(FAVORITES_KEY); }
function _toggleFavorite(mid) {
  const favs = _loadFavorites();
  const i = favs.indexOf(mid);
  if (i >= 0) favs.splice(i, 1);
  else favs.push(mid);
  _saveList(FAVORITES_KEY, favs);
  // Keep the sidebar Models section (same key) in sync if it's mounted.
  try {
    if (window.modelsModule && typeof window.modelsModule.refreshModels === 'function') {
      window.modelsModule.refreshModels();
    }
  } catch { /* sidebar not present */ }
  return i < 0; // true when now favorited
}

// ── Shared keyboard nav for model pickers ──
function _handlePickerKeydown(e, listEl, itemSelector, closeFn) {
  if (e.key === 'Escape') { closeFn(); return; }
  if (e.key === 'Enter') {
    e.preventDefault();
    const active = listEl.querySelector(itemSelector + '.kb-active') || listEl.querySelector(itemSelector);
    if (active) active.click();
    return;
  }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const items = [...listEl.querySelectorAll(itemSelector)].filter(el => el.style.display !== 'none');
    if (!items.length) return;
    const cur = items.findIndex(el => el.classList.contains('kb-active'));
    items.forEach(el => el.classList.remove('kb-active'));
    let next;
    if (e.key === 'ArrowDown') next = cur < items.length - 1 ? cur + 1 : 0;
    else next = cur > 0 ? cur - 1 : items.length - 1;
    items[next].classList.add('kb-active');
    items[next].scrollIntoView({ block: 'nearest' });
  }
}

// Dependencies injected via initModelPicker()
let _deps = null;
let _autoSelectingDefault = false;
let _defaultChatPickInFlight = false;
let _harnessCatalog = [];
let _harnessCatalogFetchedAt = 0;
let _harnessCatalogInFlight = null;
let _harnessDefaults = {};
let _harnessDefaultsFetchedAt = 0;
let _harnessDefaultsInFlight = null;
let _harnessSessionChoice = 'resume';
const HARNESS_CATALOG_TTL_MS = 30000;
const HARNESS_DEFAULTS_TTL_MS = 30000;

function _modelExists(modelId, url) {
  if (!modelId || !window.modelsModule || !window.modelsModule.getCachedItems) return false;
  const items = window.modelsModule.getCachedItems() || [];
  if (!items.length) return true;
  const targetUrl = (url || '').replace(/\/+$/, '');
  return items.some(item => {
    if (item.offline) return false;
    const itemUrl = (item.url || '').replace(/\/+$/, '');
    const models = (item.models || []).concat(item.models_extra || []);
    return models.includes(modelId) && (!targetUrl || itemUrl === targetUrl);
  });
}

function _firstAvailableModel() {
  if (!window.modelsModule || !window.modelsModule.getCachedItems) return null;
  const items = window.modelsModule.getCachedItems() || [];
  for (const item of items) {
    if (item.offline) continue;
    const models = (item.models || []).concat(item.models_extra || []);
    if (!models.length) continue;
    return {
      url: item.url,
      modelId: models[0],
      endpointId: item.endpoint_id || '',
      providerOptions: {},
    };
  }
  return null;
}

function _optionSchemaForModel(modelId, endpointUrl) {
  if (!modelId || !window.modelsModule || !window.modelsModule.getCachedItems) return [];
  const targetUrl = (endpointUrl || '').replace(/\/+$/, '');
  const items = window.modelsModule.getCachedItems() || [];
  for (const item of items) {
    const itemUrl = (item.url || '').replace(/\/+$/, '');
    if (targetUrl && itemUrl !== targetUrl) continue;
    const perModel = item.model_provider_options_schema || {};
    if (perModel[modelId]) return perModel[modelId] || [];
    const models = (item.models || []).concat(item.models_extra || []);
    if (models.includes(modelId)) return item.provider_options_schema || [];
  }
  return [];
}

function _endpointIdForModel(modelId, endpointUrl) {
  if (!modelId || !window.modelsModule || !window.modelsModule.getCachedItems) return '';
  const targetUrl = (endpointUrl || '').replace(/\/+$/, '');
  const items = window.modelsModule.getCachedItems() || [];
  for (const item of items) {
    const itemUrl = (item.url || '').replace(/\/+$/, '');
    if (targetUrl && itemUrl !== targetUrl) continue;
    const models = (item.models || []).concat(item.models_extra || []);
    if (models.includes(modelId)) return item.endpoint_id || '';
  }
  return '';
}

function _normalizeProviderOptions(schema, raw) {
  const src = raw && typeof raw === 'object' ? raw : {};
  const out = {};
  (schema || []).forEach(item => {
    const key = item && item.key;
    if (!key) return;
    const options = Array.isArray(item.options) ? item.options.map(o => String(o.value)) : [];
    const fallback = String(item.default || '');
    const val = String(src[key] || fallback || '');
    out[key] = options.includes(val) ? val : fallback;
  });
  return out;
}

function _harnessConfigFromOptions(options) {
  const cfg = options && typeof options === 'object' ? options.harness : null;
  return cfg && typeof cfg === 'object' && cfg.id ? cfg : null;
}

function _harnessConfigFromSession(session) {
  const fromOptions = _harnessConfigFromOptions(session && session.provider_options);
  if (fromOptions) return fromOptions;
  const url = String((session && session.endpoint_url) || '');
  if (url.startsWith('harness://')) {
    const id = url.slice('harness://'.length).split(/[/?#]/)[0];
    return id ? { id } : null;
  }
  return null;
}

function _harnessConfigFromPending(pending) {
  const fromOptions = _harnessConfigFromOptions(pending && pending.providerOptions);
  if (fromOptions) return fromOptions;
  const url = String((pending && pending.url) || '');
  if (url.startsWith('harness://')) {
    const id = url.slice('harness://'.length).split(/[/?#]/)[0];
    return id ? { id } : null;
  }
  return null;
}

function _isHarnessUrl(url) {
  return String(url || '').startsWith('harness://');
}

function _harnessById(id) {
  const needle = String(id || '').toLowerCase();
  return (_harnessCatalog || []).find(h => String(h.id || '').toLowerCase() === needle) || null;
}

function _harnessLabel(idOrConfig) {
  const id = typeof idOrConfig === 'string' ? idOrConfig : (idOrConfig && idOrConfig.id);
  const h = _harnessById(id);
  return (h && h.label) || (id ? String(id).charAt(0).toUpperCase() + String(id).slice(1) : 'Harness');
}

function _harnessDefaultModel(harness) {
  const defaults = harness && harness.defaults && typeof harness.defaults === 'object' ? harness.defaults : {};
  if (defaults.model || defaults.model_id) return defaults.model || defaults.model_id;
  const id = String((harness && harness.id) || '');
  return id || 'harness';
}

function _defaultHarnessProviderOptions(harness) {
  const modes = Array.isArray(harness && harness.modes) ? harness.modes : [];
  const catalogDefaults = harness && harness.defaults && typeof harness.defaults === 'object' ? harness.defaults : {};
  const savedDefaults = (_harnessDefaults && _harnessDefaults[harness.id]) || {};
  const defaults = { ...catalogDefaults, ...(savedDefaults && typeof savedDefaults === 'object' ? savedDefaults : {}) };
  const cfg = {
    id: harness.id,
    mode: defaults.mode || (modes.includes('bridged') ? 'bridged' : (modes[0] || 'observe')),
    model: _harnessDefaultModel(harness),
    provide_odysseus_tools: true,
    accept_harness_tools: true,
  };
  Object.assign(cfg, defaults);
  cfg.id = harness.id;
  return { harness: cfg };
}

function _stripHarnessSessionIdentity(config) {
  const out = { ...(config || {}) };
  ['session_file', 'sessionFile'].forEach(k => { delete out[k]; });
  out.resume = false;
  out.resume_mode = 'create';
  out.new_session = true;
  return out;
}

function _currentHarnessSessionIdentity(harnessId) {
  const ctx = _currentHarnessContext();
  const cfg = ctx && ctx.config ? ctx.config : null;
  if (!cfg) return null;
  if (harnessId && String(cfg.id || '').toLowerCase() !== String(harnessId || '').toLowerCase()) return null;
  const file = cfg.session_file || cfg.sessionFile;
  const dir = cfg.session_dir || cfg.sessionDir;
  return file || dir ? { file, dir } : null;
}

function _applyHarnessSessionChoice(providerOptions) {
  const options = { ...(providerOptions || {}) };
  const cfg = { ...(options.harness || {}) };
  const existing = _currentHarnessSessionIdentity(cfg.id);
  if (_harnessSessionChoice === 'new') {
    options.harness = _stripHarnessSessionIdentity(cfg);
    return options;
  }
  cfg.new_session = false;
  if (existing && existing.file && !cfg.session_file && !cfg.sessionFile) {
    cfg.session_file = existing.file;
  }
  if (existing && existing.dir && !cfg.session_dir && !cfg.sessionDir) {
    cfg.session_dir = existing.dir;
  }
  if (cfg.session_file || cfg.sessionFile) {
    cfg.resume_mode = 'open';
    cfg.resume = true;
  } else {
    return { ...options, harness: _stripHarnessSessionIdentity(cfg) };
  }
  options.harness = cfg;
  return options;
}

async function _loadHarnesses(force = false) {
  const now = Date.now();
  if (!force && _harnessCatalog.length && now - _harnessCatalogFetchedAt < HARNESS_CATALOG_TTL_MS) {
    return _harnessCatalog;
  }
  if (_harnessCatalogInFlight) return _harnessCatalogInFlight;
  _harnessCatalogInFlight = (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/harnesses`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      _harnessCatalog = Array.isArray(data.harnesses) ? data.harnesses : [];
      _harnessCatalogFetchedAt = Date.now();
    } catch (e) {
      console.warn('[model-picker] failed to load harnesses', e);
    } finally {
      _harnessCatalogInFlight = null;
    }
    return _harnessCatalog;
  })();
  return _harnessCatalogInFlight;
}

async function _loadHarnessDefaults(force = false) {
  const now = Date.now();
  if (!force && now - _harnessDefaultsFetchedAt < HARNESS_DEFAULTS_TTL_MS) return _harnessDefaults;
  if (_harnessDefaultsInFlight) return _harnessDefaultsInFlight;
  _harnessDefaultsInFlight = (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/auth/settings`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      _harnessDefaults = data && typeof data.harness_defaults === 'object' && data.harness_defaults ? data.harness_defaults : {};
      _harnessDefaultsFetchedAt = Date.now();
    } catch (_) {
      _harnessDefaults = {};
    } finally {
      _harnessDefaultsInFlight = null;
    }
    return _harnessDefaults;
  })();
  return _harnessDefaultsInFlight;
}

function _currentHarnessContext() {
  if (!_deps) return null;
  const currentSessionId = _deps.getCurrentSessionId && _deps.getCurrentSessionId();
  const sessions = _deps.getSessions ? _deps.getSessions() : [];
  const pending = _deps.getPendingChat && _deps.getPendingChat();
  const session = sessions.find(x => x.id === currentSessionId);
  const sessionCfg = _harnessConfigFromSession(session);
  if (sessionCfg) {
    return {
      source: 'session',
      sessionId: currentSessionId,
      session,
      config: sessionCfg,
      modelId: session.model || sessionCfg.model || sessionCfg.model_id || sessionCfg.id,
      endpointUrl: session.endpoint_url || `harness://${sessionCfg.id}`,
    };
  }
  const pendingCfg = _harnessConfigFromPending(pending);
  if (pendingCfg) {
    return {
      source: 'pending',
      pending,
      config: pendingCfg,
      modelId: pending.modelId || pendingCfg.model || pendingCfg.model_id || pendingCfg.id,
      endpointUrl: pending.url || `harness://${pendingCfg.id}`,
    };
  }
  return null;
}

async function _saveHarnessConfigForContext(ctx, nextConfig) {
  if (!_deps || !ctx || !nextConfig || !nextConfig.id) return;
  const providerOptions = { ...((ctx.source === 'pending' ? ctx.pending?.providerOptions : ctx.session?.provider_options) || {}), harness: nextConfig };
  const modelId = ctx.modelId || nextConfig.model || nextConfig.model_id || nextConfig.id;
  const endpointUrl = ctx.endpointUrl || `harness://${nextConfig.id}`;
  if (ctx.source === 'pending') {
    _deps.setPendingChat({
      url: endpointUrl,
      modelId,
      endpointId: '',
      source: 'manual',
      providerOptions,
    });
    updateModelPicker();
    return;
  }
  const fd = new FormData();
  fd.append('model', modelId);
  fd.append('endpoint_url', endpointUrl);
  fd.append('provider_options', JSON.stringify(providerOptions));
  const res = await fetch(`${API_BASE}/api/session/${ctx.sessionId}`, { method: 'PATCH', body: fd, credentials: 'same-origin' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  if (ctx.session) {
    ctx.session.model = modelId;
    ctx.session.endpoint_url = endpointUrl;
    ctx.session.provider_options = providerOptions;
  }
  updateModelPicker();
}

async function _sendHarnessCommand(sessionId, command, payload = {}) {
  const res = await fetch(`${API_BASE}/api/harnesses/${sessionId}/command`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command, payload }),
  });
  let data = null;
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    const msg = (data && (data.detail || data.error)) || `Harness command failed (${res.status})`;
    throw new Error(msg);
  }
  return data && data.data;
}

function _renderHarnessRuntimeStrip() {
  const strip = document.getElementById('harness-runtime-strip');
  if (!strip || !_deps) return;
  const ctx = _currentHarnessContext();
  if (!ctx || (window.groupModule && window.groupModule.isActive && window.groupModule.isActive())) {
    strip.classList.add('hidden');
    strip.innerHTML = '';
    return;
  }

  const config = ctx.config || {};
  const label = _harnessLabel(config);
  const canCommand = ctx.source === 'session' && !!ctx.sessionId;
  strip.classList.remove('hidden');
  strip.innerHTML = '';

  const title = document.createElement('span');
  title.className = 'harness-runtime-title';
  title.textContent = `${label} harness`;
  strip.appendChild(title);

  const mode = document.createElement('span');
  mode.className = 'harness-runtime-pill';
  mode.textContent = config.mode || 'bridged';
  strip.appendChild(mode);

  const thinking = document.createElement('select');
  thinking.className = 'harness-runtime-select';
  thinking.title = 'Harness thinking level';
  ['minimal', 'low', 'medium', 'high', 'xhigh'].forEach(value => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = value === 'xhigh' ? 'x-high' : value;
    thinking.appendChild(opt);
  });
  thinking.value = config.thinking_level || 'medium';
  thinking.addEventListener('change', async () => {
    const nextConfig = { ...config, thinking_level: thinking.value };
    try {
      await _saveHarnessConfigForContext(ctx, nextConfig);
      if (canCommand) await _sendHarnessCommand(ctx.sessionId, 'set_thinking_level', { level: thinking.value });
      uiModule.showToast('Harness settings saved');
    } catch (e) {
      uiModule.showError(e.message || 'Failed to update harness');
    }
  });
  strip.appendChild(thinking);

  const stateBtn = document.createElement('button');
  stateBtn.type = 'button';
  stateBtn.className = 'harness-runtime-btn';
  stateBtn.textContent = 'State';
  stateBtn.disabled = !canCommand;
  stateBtn.title = canCommand ? 'Read harness state' : 'Starts after the first message';
  stateBtn.addEventListener('click', async () => {
    try {
      const state = await _sendHarnessCommand(ctx.sessionId, 'get_state');
      uiModule.showToast(state && state.sessionFile ? `Harness state: ${state.sessionFile}` : 'Harness state loaded');
      console.debug('[harness state]', state);
    } catch (e) {
      uiModule.showError(e.message || 'Harness is not running');
    }
  });
  strip.appendChild(stateBtn);

  const abortBtn = document.createElement('button');
  abortBtn.type = 'button';
  abortBtn.className = 'harness-runtime-btn danger';
  abortBtn.textContent = 'Abort';
  abortBtn.disabled = !canCommand;
  abortBtn.title = canCommand ? 'Abort harness run' : 'Starts after the first message';
  abortBtn.addEventListener('click', async () => {
    try {
      await _sendHarnessCommand(ctx.sessionId, 'abort');
      uiModule.showToast('Harness abort sent');
    } catch (e) {
      uiModule.showError(e.message || 'Harness is not running');
    }
  });
  strip.appendChild(abortBtn);
}

async function _ensureModelCacheForFallback() {
  if (!window.modelsModule || !window.modelsModule.getCachedItems) return;
  const items = window.modelsModule.getCachedItems() || [];
  if (items.length) return;
  if (typeof window.modelsModule.refreshModels === 'function') {
    try { await window.modelsModule.refreshModels(false); } catch (_) {}
  }
}

async function _ensureDefaultPendingChat() {
  if (!_deps || _defaultChatPickInFlight) return;
  if (_deps.getCurrentSessionId && _deps.getCurrentSessionId()) return;
  const pending = _deps.getPendingChat && _deps.getPendingChat();
  if (pending && pending.modelId && pending.source === 'manual') return;
  _defaultChatPickInFlight = true;
  try {
    await _ensureModelCacheForFallback();
    let dc = null;
    try {
      const res = await fetch(`${API_BASE}/api/default-chat`, { credentials: 'same-origin' });
      if (res.ok) dc = await res.json();
    } catch (_) {}
    if (dc && dc.endpoint_url && dc.model && _modelExists(dc.model, dc.endpoint_url)) {
      const pendingUrl = String((pending && pending.url) || '').replace(/\/+$/, '');
      const defaultUrl = String(dc.endpoint_url || '').replace(/\/+$/, '');
      _deps.setPendingChat({
        url: dc.endpoint_url,
        modelId: dc.model,
        endpointId: dc.endpoint_id || '',
        source: 'default',
        providerOptions: dc.provider_options || {},
      });
      try { window.__odysseusDefaultChat = dc; } catch (_) {}
      if (!pending || pending.modelId !== dc.model || pendingUrl !== defaultUrl || pending.source !== 'default') {
        updateModelPicker();
      }
      return;
    }
    if (pending && pending.modelId) return;
    // No configured default, or the configured default is gone/offline:
    // preserve the convenience fallback and keep the picker usable.
    const fallback = _firstAvailableModel();
    if (fallback) {
      _deps.setPendingChat({ ...fallback, source: 'fallback' });
      updateModelPicker();
    }
  } finally {
    _defaultChatPickInFlight = false;
  }
}

/**
 * Initialize the model picker dropdown.
 * @param {Object} deps
 * @param {function} deps.getCurrentSessionId - returns current session ID
 * @param {function} deps.getSessions - returns sessions array
 * @param {function} deps.getPendingChat - returns _pendingChat object
 * @param {function} deps.setPendingChat - sets _pendingChat object
 * @param {function} deps.createDirectChat - creates a new direct chat session
 */
export function initModelPicker(deps) {
  _deps = deps;
  _initModelPickerDropdown();
}

function _initModelPickerDropdown() {
  const wrap = document.getElementById('model-picker-wrap');
  const btn = document.getElementById('model-picker-btn');
  const menu = document.getElementById('model-picker-menu');
  const search = document.getElementById('model-picker-search');
  const listEl = document.getElementById('model-picker-list');
  const searchRow = menu ? menu.querySelector('.model-picker-search-row') : null;
  const refreshBtn = document.getElementById('model-picker-refresh-btn');
  if (!wrap || !btn || !menu || !search || !listEl) return;

  const providerOptionsEl = document.createElement('div');
  providerOptionsEl.className = 'mp-provider-options hidden';
  if (searchRow && searchRow.parentNode) {
    searchRow.parentNode.insertBefore(providerOptionsEl, searchRow.nextSibling);
  }
  const harnessSessionEl = document.createElement('div');
  harnessSessionEl.className = 'mp-harness-session hidden';
  harnessSessionEl.innerHTML = '<span class="mp-harness-session-label">Pi session</span><div class="mp-harness-session-toggle" role="group" aria-label="Pi session mode"><button type="button" data-harness-session-choice="resume">Resume</button><button type="button" data-harness-session-choice="new">New</button></div>';
  if (providerOptionsEl && providerOptionsEl.parentNode) {
    providerOptionsEl.parentNode.insertBefore(harnessSessionEl, providerOptionsEl.nextSibling);
  }

  function _currentModelSelection() {
    const currentSessionId = _deps.getCurrentSessionId();
    const sessions = _deps.getSessions();
    const pending = _deps.getPendingChat();
    const s = sessions.find(x => x.id === currentSessionId);
    const harnessCfg = _harnessConfigFromSession(s) || _harnessConfigFromPending(pending);
    if (harnessCfg) return null;
    if (s && s.model) {
      return {
        source: 'session',
        sessionId: currentSessionId,
        modelId: s.model,
        url: s.endpoint_url || '',
        endpointId: s.endpoint_id || _endpointIdForModel(s.model, s.endpoint_url || ''),
        providerOptions: s.provider_options || {},
      };
    }
    if (pending && pending.modelId) {
      return {
        source: 'pending',
        modelId: pending.modelId,
        url: pending.url || '',
        endpointId: pending.endpointId || '',
        providerOptions: pending.providerOptions || {},
      };
    }
    return null;
  }

  async function _saveCurrentProviderOptions(selection, values) {
    if (!selection) return;
    if (selection.source === 'pending') {
      _deps.setPendingChat({
        url: selection.url,
        modelId: selection.modelId,
        endpointId: selection.endpointId,
        source: 'manual',
        providerOptions: values,
      });
      updateModelPicker();
      return;
    }
    const fd = new FormData();
    fd.append('model', selection.modelId);
    fd.append('endpoint_url', selection.url);
    if (selection.endpointId) fd.append('endpoint_id', selection.endpointId);
    fd.append('provider_options', JSON.stringify(values || {}));
    try {
      const res = await fetch(`${API_BASE}/api/session/${selection.sessionId}`, { method: 'PATCH', body: fd });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const sessions = _deps.getSessions();
      const s = sessions.find(x => x.id === selection.sessionId);
      if (s) s.provider_options = values || {};
      updateModelPicker();
    } catch (e) {
      uiModule.showError('Failed to save model options');
    }
  }

  function _renderProviderOptionsControls() {
    const selection = _currentModelSelection();
    const schema = selection ? _optionSchemaForModel(selection.modelId, selection.url) : [];
    if (!providerOptionsEl || !schema.length) {
      if (providerOptionsEl) {
        providerOptionsEl.classList.add('hidden');
        providerOptionsEl.innerHTML = '';
      }
      return;
    }
    const values = _normalizeProviderOptions(schema, selection.providerOptions);
    providerOptionsEl.classList.remove('hidden');
    providerOptionsEl.innerHTML = '';
    schema.forEach(item => {
      if (!item || item.type !== 'select') return;
      const wrap = document.createElement('label');
      wrap.className = 'mp-provider-option';
      const text = document.createElement('span');
      text.textContent = item.label || item.key;
      const select = document.createElement('select');
      select.className = 'mp-provider-option-select';
      (item.options || []).forEach(opt => {
        const o = document.createElement('option');
        o.value = opt.value;
        o.textContent = opt.label || opt.value;
        select.appendChild(o);
      });
      select.value = values[item.key] || item.default || '';
      select.addEventListener('change', () => {
        values[item.key] = select.value;
        _saveCurrentProviderOptions(selection, values);
      });
      wrap.appendChild(text);
      wrap.appendChild(select);
      providerOptionsEl.appendChild(wrap);
    });
  }

  function _renderHarnessSessionChoice() {
    if (!harnessSessionEl) return;
    const hasHarness = (_harnessCatalog || []).some(h => h && h.id);
    if (!hasHarness) {
      harnessSessionEl.classList.add('hidden');
      return;
    }
    const currentHarness = _currentHarnessContext();
    const currentHarnessId = currentHarness && currentHarness.config && currentHarness.config.id;
    const hasExisting = (_harnessCatalog || []).some(h => h && h.id && _currentHarnessSessionIdentity(h.id))
      || !!(currentHarnessId && _currentHarnessSessionIdentity(currentHarnessId));
    if (!hasExisting && _harnessSessionChoice === 'resume') _harnessSessionChoice = 'new';
    harnessSessionEl.classList.remove('hidden');
    harnessSessionEl.querySelectorAll('button[data-harness-session-choice]').forEach(button => {
      const choice = button.dataset.harnessSessionChoice;
      const disabled = choice === 'resume' && !hasExisting;
      button.disabled = disabled;
      button.classList.toggle('active', choice === _harnessSessionChoice);
      button.title = choice === 'resume'
        ? (hasExisting ? 'Use the Pi session attached to this chat' : 'No Pi session is attached to this chat')
        : 'Create a fresh Pi session for this chat';
    });
  }

  function _close() {
    if (menu.classList.contains('hidden')) return;
    // Restore scroll button
    const _scrollBtn = document.getElementById('scroll-bottom-btn');
    if (_scrollBtn) _scrollBtn.style.display = '';
    menu.classList.add('closing');
    menu.addEventListener('animationend', function _onDone() {
      menu.removeEventListener('animationend', _onDone);
      menu.classList.remove('closing');
      menu.classList.add('hidden');
      search.value = '';
    }, { once: true });
    // Fallback if animationend doesn't fire
    setTimeout(() => {
      if (!menu.classList.contains('hidden')) {
        menu.classList.remove('closing');
        menu.classList.add('hidden');
        search.value = '';
      }
    }, 200);
  }

  function _openPickerShortcut(kind) {
    _close();
    try {
      if (kind === 'cookbook') {
        if (window.cookbookModule && typeof window.cookbookModule.open === 'function') {
          window.cookbookModule.open();
        } else {
          const btn = document.getElementById('tool-cookbook-btn') || document.getElementById('rail-cookbook');
          if (btn) btn.click();
          else location.hash = '#cookbook';
        }
      } else if (kind === 'settings') {
        if (settingsModule && typeof settingsModule.open === 'function') settingsModule.open();
      } else if (window.adminModule && typeof window.adminModule.open === 'function') {
        window.adminModule.open('services');
      } else if (settingsModule && typeof settingsModule.open === 'function') {
        settingsModule.open('services');
      }
    } catch (_) {}
  }

  // Local endpoint health — only probed for LOCAL endpoints, since
  // cloud APIs are essentially always up. Cached briefly on the
  // server side too (8s TTL). Picker opens trigger a refresh.
  let _localProbe = {};            // {endpoint_id: {alive, latency_ms, error}}
  let _localProbeFetchedAt = 0;
  const _LOCAL_PROBE_TTL_MS = 5000;

  async function _refreshLocalProbe() {
    try {
      if (window.__odysseusChatBusy || Date.now() < (window.__odysseusChatBusyUntil || 0)) return;
    } catch (_) {}
    const now = Date.now();
    if (now - _localProbeFetchedAt < _LOCAL_PROBE_TTL_MS) return;
    _localProbeFetchedAt = now;
    try {
      const r = await fetch('/api/model-endpoints/probe-local', { credentials: 'same-origin' });
      if (r.ok) _localProbe = (await r.json()) || {};
    } catch (_) { /* leave stale data; picker still works */ }
  }

  function _getAllModels() {
    const items = (window.modelsModule && window.modelsModule.getCachedItems) ? window.modelsModule.getCachedItems() : [];
    const result = [];
    const seen = new Set();
    items.forEach(item => {
      // Previously: offline endpoints were skipped entirely, so a server
      // that briefly went down disappeared from the picker — confusing
      // when the user can still see it (offline-tagged) in Settings.
      // Now: include offline-endpoint models too but flag them
      // `stale: true` so the row renderer dims them + shows the offline
      // pill. The user can still click and try anyway (matches the
      // existing "local server appears offline" path on line 301).
      const epOffline = !!item.offline;
      const allModels = (item.models || []).concat(item.models_extra || []);
      const allDisplay = (item.models_display || []).concat(item.models_extra_display || []);
      const perModelOptions = item.model_provider_options_schema || {};
      // Mark local endpoints whose live probe failed.
      const probeResult = item.endpoint_id ? _localProbe[item.endpoint_id] : null;
      const isLocalDead = !!(probeResult && probeResult.alive === false);
      allModels.forEach((mid, i) => {
        // Deduplicate by model ID — prefer ONLINE endpoint entries over
        // offline duplicates so the user gets a working endpoint first
        // when the same model is exposed by both.
        if (seen.has(mid)) return;
        seen.add(mid);
        result.push({
          mid,
          display: (allDisplay[i] || mid).split('/').pop(),
          url: item.url,
          endpointId: item.endpoint_id,
          epName: item.endpoint_name || '',
          providerText: [
            item.endpoint_name || '',
            item.category || '',
            item.host || '',
            item.url || '',
          ].filter(Boolean).join(' '),
          stale: isLocalDead || epOffline,
          staleReason: epOffline
            ? (item.ping_error || 'endpoint offline')
            : (isLocalDead ? (probeResult.error || 'not responding') : ''),
          offline: epOffline,
          providerOptionsSchema: perModelOptions[mid] || item.provider_options_schema || [],
        });
      });
    });
    return sortModelObjects(result);
  }

  // ── Provider display names and grouping ──
  const _PROVIDER_NAMES = {
    '01-ai': 'Yi', 'abacusai': 'Abacus AI', 'adept': 'Adept',
    'ai21': 'AI21 Labs', 'ai21labs': 'AI21 Labs', 'aion-labs': 'Aion Labs',
    'aisingapore': 'AI Singapore', 'allenai': 'Allen AI', 'amazon': 'Amazon',
    'anthracite-org': 'Anthracite', 'anthropic': 'Anthropic', 'arcee-ai': 'Arcee AI',
    'baai': 'BAAI', 'baidu': 'Baidu', 'bigcode': 'BigCode',
    'black-forest-labs': 'Black Forest Labs', 'bytedance': 'ByteDance',
    'bytedance-seed': 'ByteDance', 'cognitivecomputations': 'Cognitive Computations',
    'cohere': 'Cohere', 'databricks': 'Databricks', 'deepcogito': 'DeepCogito',
    'deepseek': 'DeepSeek', 'deepseek-ai': 'DeepSeek', 'essentialai': 'Essential AI',
    'google': 'Google', 'gryphe': 'Gryphe', 'ibm': 'IBM',
    'ibm-granite': 'IBM Granite', 'inception': 'Inception',
    'inclusionai': 'Inclusion AI', 'inflection': 'Inflection',
    'kwaipilot': 'KwaiPilot', 'liquid': 'Liquid AI', 'mancer': 'Mancer',
    'meta': 'Llama', 'meta-llama': 'Llama', 'microsoft': 'Microsoft',
    'minimax': 'MiniMax', 'minimaxai': 'MiniMax', 'mistralai': 'Mistral',
    'moonshotai': 'Moonshot', 'morph': 'Morph', 'nex-agi': 'Nex AGI',
    'nousresearch': 'Nous Research', 'nv-mistralai': 'NVIDIA x Mistral',
    'nvidia': 'NVIDIA', 'openai': 'OpenAI', 'openrouter': 'OpenRouter',
    'perceptron': 'Perceptron', 'perplexity': 'Perplexity', 'poolside': 'Poolside',
    'prime-intellect': 'Prime Intellect', 'qwen': 'Qwen', 'rekaai': 'Reka',
    'relace': 'Relace', 'sao10k': 'Sao10k', 'sarvamai': 'Sarvam AI',
    'snowflake': 'Snowflake', 'stepfun': 'StepFun', 'stepfun-ai': 'StepFun',
    'stockmark': 'Stockmark', 'switchpoint': 'SwitchPoint', 'tencent': 'Tencent',
    'thedrummer': 'TheDrummer', 'undi95': 'Undi95', 'upstage': 'Upstage',
    'writer': 'Writer', 'x-ai': 'xAI', 'xiaomi': 'Xiaomi',
    'z-ai': 'Zhipu', 'zyphra': 'Zyphra',
    '~anthropic': 'Anthropic', '~google': 'Google',
    '~moonshotai': 'Moonshot', '~openai': 'OpenAI',
  };
  const _PROVIDER_ALIAS = {
    'meta-llama': 'meta', 'deepseek': 'deepseek-ai', 'minimaxai': 'minimax',
    'stepfun-ai': 'stepfun', 'ai21labs': 'ai21', 'ibm-granite': 'ibm',
    'bytedance-seed': 'bytedance', '~anthropic': 'anthropic',
    '~google': 'google', '~moonshotai': 'moonshotai', '~openai': 'openai',
  };
  function _providerDisplayName(slug) {
    return _PROVIDER_NAMES[slug] || slug.charAt(0).toUpperCase() + slug.slice(1).replace(/-/g, ' ');
  }
  function _providerSlug(mid) {
    const slash = mid.indexOf('/');
    let slug = slash > 0 ? mid.substring(0, slash) : 'other';
    return _PROVIDER_ALIAS[slug] || slug;
  }
  const _collapsedProviders = new Set(_loadList('odysseus-model-collapsed'));
  let _justExpandedProvider = null;

  function _populate(filter) {
    listEl.innerHTML = '';
    _renderProviderOptionsControls();
    const all = _getAllModels();
    const harnesses = (_harnessCatalog || []).filter(h => h && h.id);
    const q = (filter || '').trim().toLowerCase();
    const hasAnyModel = all.length > 0;
    const hasAnyChoice = hasAnyModel || harnesses.length > 0;
    _renderHarnessSessionChoice();
    listEl.classList.toggle('is-empty', !hasAnyChoice);
    menu.classList.toggle('no-models', !hasAnyChoice);
    if (search) {
      search.placeholder = hasAnyChoice ? 'Search models or harnesses...' : 'No models or harnesses connected';
    }
    if (searchRow) {
      searchRow.classList.toggle('searching', !!q);
    }

    if (!hasAnyChoice) return; // collapsed empty list — nothing to render

    // Unique lookup so Recent/Favorites (stored as bare model IDs) can be
    // resolved back to full model objects; drops anything no longer offered.
    const byId = new Map();
    all.forEach(m => { if (!byId.has(m.mid)) byId.set(m.mid, m); });

    const favs = _loadFavorites();

    function _addSection(label) {
      const el = document.createElement('div');
      el.className = 'mp-section-label';
      el.textContent = label;
      listEl.appendChild(el);
    }
    function _addEmpty(text) {
      const empty = document.createElement('div');
      empty.className = 'model-switch-empty';
      empty.textContent = text;
      listEl.appendChild(empty);
    }
    function _addRow(m) {
      const row = document.createElement('div');
      row.className = 'model-switch-item';
      if (m.stale) {
        row.classList.add('model-switch-stale');
        row.style.opacity = '0.45';
        row.title = `Local server appears offline: ${m.staleReason}. Click to try anyway, or relaunch in Cookbook.`;
      }
      const _mlogo = providerLogo(m.mid);
      if (_mlogo) {
        const logoSpan = document.createElement('span');
        logoSpan.className = 'provider-logo';
        logoSpan.style.opacity = '0.6';
        logoSpan.innerHTML = _mlogo;
        row.appendChild(logoSpan);
      }
      const nameSpan = document.createElement('span');
      nameSpan.className = 'mp-model-name';
      nameSpan.textContent = m.display;
      // Long model names are clipped with ellipsis — expose the full name on
      // hover so the suffix/variant tag is still discoverable (#1982).
      nameSpan.title = m.display;
      row.appendChild(nameSpan);
      // Offline state is already conveyed by the row's reduced opacity —
      // a redundant "offline" pill on top of that just added clutter.
      // (Class kept on `row` so the opacity rule still applies; the text
      // badge is gone.)
      const epSpan = document.createElement('span');
      epSpan.className = 'model-switch-ep';
      // Don't show endpoint name if it matches the model name (local self-hosted)
      const _epDisplay = m.epName && !m.display.toLowerCase().includes(m.epName.toLowerCase().split('/').pop()) ? m.epName : '';
      epSpan.textContent = _epDisplay;
      row.appendChild(epSpan);

      // Inline favorite dot — toggles favorite, never picks the model.
      const favDot = document.createElement('button');
      favDot.type = 'button';
      favDot.className = 'mp-fav-dot' + (favs.includes(m.mid) ? ' active' : '');
      favDot.textContent = '●';
      const _setFavState = (on) => {
        favDot.classList.toggle('active', on);
        favDot.title = on ? 'Remove from favorites' : 'Add to favorites';
        favDot.setAttribute('aria-label', on ? 'Remove from favorites' : 'Add to favorites');
        favDot.setAttribute('aria-pressed', on ? 'true' : 'false');
      };
      _setFavState(favs.includes(m.mid));
      favDot.addEventListener('click', (e) => {
        e.stopPropagation();
        const nowFav = _toggleFavorite(m.mid);
        _setFavState(nowFav);
        favDot.classList.remove('pulse');
        void favDot.offsetWidth;
        favDot.classList.add('pulse');
        // Keep our in-memory copy aligned so a follow-up re-render is correct.
        const idx = favs.indexOf(m.mid);
        if (nowFav && idx < 0) favs.push(m.mid);
        else if (!nowFav && idx >= 0) favs.splice(idx, 1);
        if (uiModule && uiModule.showToast) uiModule.showToast(nowFav ? 'Favorited' : 'Unfavorited');
        // In browse mode the Favorites section membership changed — rebuild
        // (cheap: Recent + Favorites). In search mode the row stays put, so
        // the in-place favorite update above is enough.
        if (!q) {
          const st = listEl.scrollTop;
          _populate('');
          listEl.scrollTop = st;
        }
      });
      row.appendChild(favDot);

      row.addEventListener('click', () => _pick(m));
      listEl.appendChild(row);
    }
    function _addHarnessRow(h) {
      const row = document.createElement('div');
      row.className = 'model-switch-item mp-harness-item';
      row.title = `${h.label || h.id} harness`;
      const chip = document.createElement('span');
      chip.className = 'mp-harness-chip';
      chip.textContent = 'H';
      row.appendChild(chip);
      const nameSpan = document.createElement('span');
      nameSpan.className = 'mp-model-name';
      nameSpan.textContent = h.label || _harnessLabel(h.id);
      row.appendChild(nameSpan);
      const epSpan = document.createElement('span');
      epSpan.className = 'model-switch-ep';
      const modes = Array.isArray(h.modes) && h.modes.length ? h.modes.join(', ') : 'harness';
      epSpan.textContent = `Harness · ${modes} · ${_harnessSessionChoice === 'resume' ? 'resume' : 'new'}`;
      row.appendChild(epSpan);
      row.addEventListener('click', () => _pickHarness(h));
      listEl.appendChild(row);
    }

    // ── Search mode: flat, filtered results across the whole catalog ──
    if (q) {
      const matches = all.filter(m => {
        const provName = _providerDisplayName(_providerSlug(m.mid)).toLowerCase();
        return [m.mid, m.display, m.epName, m.providerText, provName]
          .filter(Boolean).join(' ').toLowerCase().includes(q);
      });
      const harnessMatches = harnesses.filter(h => {
        return [h.id, h.label, h.modes && h.modes.join(' ')]
          .filter(Boolean).join(' ').toLowerCase().includes(q);
      });
      if (matches.length === 0 && harnessMatches.length === 0) _addEmpty('No matching models or harnesses');
      if (harnessMatches.length) {
        _addSection('Harnesses');
        harnessMatches.forEach(_addHarnessRow);
      }
      else matches.forEach(_addRow);
      if (harnessMatches.length && matches.length) {
        _addSection('Models');
        matches.forEach(_addRow);
      }
      return;
    }

    // ── Browse mode: Favorites (manual) + Recent (auto), with dedupe. ──
    // Rules:
    //   1. Never list the same model twice in the dropdown. Favorites
    //      win over Recent (if you favorited it, that's where it
    //      belongs — Recent shouldn't show it again as duplicate).
    //   2. Small catalogs (≤ BROWSE_ALL_LIMIT total) skip the Recent
    //      section entirely — when there's only ~10 models, the whole
    //      list fits below as "All models" and a separate Recent
    //      section just duplicates rows.
    const shown = new Set();
    if (harnesses.length) {
      _addSection('Harnesses');
      harnesses.forEach(_addHarnessRow);
    }
    const favModels = favs.map(id => byId.get(id)).filter(Boolean);
    if (favModels.length) {
      _addSection('Favorites');
      favModels.forEach(m => { shown.add(m.mid); _addRow(m); });
    }
    // Recent: only render when the catalog is big enough that surfacing
    // a recency shortlist is actually useful, AND only models that
    // aren't already in Favorites (dedupe).
    if (all.length > BROWSE_ALL_LIMIT) {
      const recentModels = _loadRecent()
        .map(id => byId.get(id))
        .filter(Boolean)
        .filter(m => !shown.has(m.mid))
        .slice(0, RECENT_MAX);
      if (recentModels.length) {
        _addSection('Recent');
        recentModels.forEach(m => { shown.add(m.mid); _addRow(m); });
      }
    }

    // Small catalogs: still list everything so users aren't forced to search.
    if (all.length <= BROWSE_ALL_LIMIT) {
      const rest = all.filter(m => !shown.has(m.mid));
      if (rest.length) {
        if (shown.size) _addSection('All models');
        rest.forEach(_addRow);
      }
    } else {
      // Large catalog: show provider groups with collapsible sections.
      const rest = all.filter(m => !shown.has(m.mid));
      const groups = new Map();
      rest.forEach(m => {
        const slug = _providerSlug(m.mid);
        if (!groups.has(slug)) groups.set(slug, []);
        groups.get(slug).push(m);
      });
      const sorted = [...groups.keys()].sort((a, b) =>
        _providerDisplayName(a).localeCompare(_providerDisplayName(b)));

      sorted.forEach(provider => {
        const models = groups.get(provider);
        const isCollapsed = _collapsedProviders.has(provider);
        const header = document.createElement('div');
        header.className = 'mp-provider-header';
        header.innerHTML =
          `<svg class="mp-provider-chevron${isCollapsed ? ' collapsed' : ''}" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`
          + `<span class="mp-provider-name">${_providerDisplayName(provider)}</span>`
          + `<span class="mp-provider-count">${models.length}</span>`;
        header.addEventListener('click', (e) => {
          e.stopPropagation();
          if (_collapsedProviders.has(provider)) {
            _collapsedProviders.delete(provider);
            _justExpandedProvider = provider;
          } else {
            _collapsedProviders.add(provider);
            _justExpandedProvider = null;
          }
          _saveList('odysseus-model-collapsed', [..._collapsedProviders]);
          const st = listEl.scrollTop;
          _populate('');
          listEl.scrollTop = st;
        });
        listEl.appendChild(header);
        if (!isCollapsed) {
          const group = document.createElement('div');
          group.className = 'mp-provider-group' + (_justExpandedProvider === provider ? ' mp-just-expanded' : '');
          models.forEach(m => {
            _addRow(m);
            // Move the just-appended row into the group container
            group.appendChild(listEl.lastElementChild);
          });
          listEl.appendChild(group);
          if (_justExpandedProvider === provider) _justExpandedProvider = null;
        }
      });
    }
  }

  async function _pick(m) {
    const currentSessionId = _deps.getCurrentSessionId();
    const _pendingChat = _deps.getPendingChat();

    // Remember this pick so it surfaces under "Recent" next time the picker
    // opens — the whole point of quick-switch.
    if (m && m.mid) _pushRecent(m.mid);

    // Broadcast immediately so listeners (e.g. the tour) can advance without
    // waiting for the async session-create/PATCH that follows.
    try { document.dispatchEvent(new CustomEvent('odysseus:model-picked', { detail: m })); } catch {}

    // Blur search input before closing to dismiss keyboard on mobile
    if (document.activeElement) document.activeElement.blur();
    _close();
    // Refocus main textarea — skip on mobile to avoid keyboard bounce
    if (window.innerWidth >= 768) {
      const _ta = document.getElementById('message');
      if (_ta) setTimeout(() => _ta.focus(), 50);
    }
    if (!currentSessionId && _pendingChat) {
      // Already have a deferred session — just update the model
      const providerOptions = _normalizeProviderOptions(m.providerOptionsSchema || [], _pendingChat.providerOptions || {});
      _deps.setPendingChat({ url: m.url, modelId: m.mid, endpointId: m.endpointId, source: 'manual', providerOptions });
      // Header stays as session name — model switch only updates picker
      updateModelPicker();
      uiModule.showToast(`Using ${m.display}`);
      return;
    } else if (!currentSessionId) {
      // No session yet — create one with this model
      await _deps.createDirectChat(m.url, m.mid, m.endpointId, _normalizeProviderOptions(m.providerOptionsSchema || [], {}));
    } else {
      // Existing session with no model — PATCH it
      const fd = new FormData();
      fd.append('model', m.mid);
      fd.append('endpoint_url', m.url);
      if (m.endpointId) fd.append('endpoint_id', m.endpointId);
      const providerOptions = _normalizeProviderOptions(m.providerOptionsSchema || [], {});
      if (Object.keys(providerOptions).length) fd.append('provider_options', JSON.stringify(providerOptions));
      try {
        const res = await fetch(`${API_BASE}/api/session/${currentSessionId}`, { method: 'PATCH', body: fd });
        if (!res.ok) {
          uiModule.showError('Failed to set model');
          return;
        }
        const sessions = _deps.getSessions();
        const s = sessions.find(x => x.id === currentSessionId);
        if (s) { s.model = m.mid; s.endpoint_url = m.url; s.provider_options = providerOptions; }
        // Header stays as session name — model info shown in picker only
      } catch (e) {
        uiModule.showError('Failed to set model: ' + e);
        return;
      }
    }
    // Update picker visibility — model is now set
    updateModelPicker();
    uiModule.showToast(`Using ${m.display}`);
  }

  async function _pickHarness(harness) {
    if (!harness || !harness.id) return;
    const currentSessionId = _deps.getCurrentSessionId();
    const _pendingChat = _deps.getPendingChat();
    const providerOptions = _applyHarnessSessionChoice(_defaultHarnessProviderOptions(harness));
    const config = providerOptions.harness || {};
    const modelId = config.model || config.model_id || _harnessDefaultModel(harness);
    const endpointUrl = `harness://${harness.id}`;
    try { document.dispatchEvent(new CustomEvent('odysseus:harness-picked', { detail: harness })); } catch {}
    if (document.activeElement) document.activeElement.blur();
    _close();
    if (window.innerWidth >= 768) {
      const _ta = document.getElementById('message');
      if (_ta) setTimeout(() => _ta.focus(), 50);
    }
    if (!currentSessionId && _pendingChat) {
      _deps.setPendingChat({ url: endpointUrl, modelId, endpointId: '', source: 'manual', providerOptions });
      updateModelPicker();
      uiModule.showToast(`Using ${harness.label || _harnessLabel(harness.id)}`);
      return;
    } else if (!currentSessionId) {
      await _deps.createDirectChat(endpointUrl, modelId, '', providerOptions);
    } else {
      const fd = new FormData();
      fd.append('model', modelId);
      fd.append('endpoint_url', endpointUrl);
      fd.append('provider_options', JSON.stringify(providerOptions));
      try {
        const res = await fetch(`${API_BASE}/api/session/${currentSessionId}`, { method: 'PATCH', body: fd, credentials: 'same-origin' });
        if (!res.ok) {
          uiModule.showError('Failed to set harness');
          return;
        }
        const sessions = _deps.getSessions();
        const s = sessions.find(x => x.id === currentSessionId);
        if (s) {
          s.model = modelId;
          s.endpoint_url = endpointUrl;
          s.provider_options = providerOptions;
        }
      } catch (e) {
        uiModule.showError('Failed to set harness: ' + e);
        return;
      }
    }
    updateModelPicker();
    uiModule.showToast(`Using ${harness.label || _harnessLabel(harness.id)}`);
  }

  document.addEventListener('odysseus:auto-select-model', async (e) => {
    const detail = (e && e.detail) || {};
    const currentSessionId = _deps.getCurrentSessionId();
    const sessions = _deps.getSessions();
    const current = sessions.find(x => x.id === currentSessionId);
    const pending = _deps.getPendingChat();
    if ((current && current.model) || (pending && pending.modelId)) return;

    if (window.modelsModule && window.modelsModule.refreshModels) {
      try { await window.modelsModule.refreshModels(false); } catch (_) {}
    }
    const items = window.modelsModule && window.modelsModule.getCachedItems ? window.modelsModule.getCachedItems() : [];
    const targetEndpointId = detail.endpointId ? String(detail.endpointId) : '';
    const targetModel = detail.modelId || '';
    let match = null;
    for (const item of items) {
      if (item.offline) continue;
      if (targetEndpointId && String(item.endpoint_id || '') !== targetEndpointId) continue;
      const models = (item.models || []).concat(item.models_extra || []);
      const displays = (item.models_display || []).concat(item.models_extra_display || []);
      const idx = targetModel ? models.indexOf(targetModel) : (models.length ? 0 : -1);
      if (idx >= 0) {
        match = {
          mid: models[idx],
          display: (displays[idx] || models[idx]).split('/').pop(),
          url: item.url || detail.url || '',
          endpointId: item.endpoint_id || detail.endpointId || '',
          epName: item.endpoint_name || detail.endpointName || '',
          providerText: [item.endpoint_name || detail.endpointName || '', item.url || detail.url || ''].filter(Boolean).join(' '),
          providerOptionsSchema: (item.model_provider_options_schema || {})[models[idx]] || item.provider_options_schema || [],
        };
        break;
      }
    }
    if (!match && detail.modelId && detail.url) {
      match = {
        mid: detail.modelId,
        display: String(detail.modelId).split('/').pop(),
        url: detail.url,
        endpointId: detail.endpointId || '',
        epName: detail.endpointName || '',
        providerText: [detail.endpointName || '', detail.url || ''].filter(Boolean).join(' '),
        providerOptionsSchema: _optionSchemaForModel(detail.modelId, detail.url),
      };
    }
    if (match) await _pick(match);
  });

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.classList.contains('hidden') || menu.classList.contains('closing')) {
      // Force-clear any in-progress close animation
      menu.classList.remove('closing', 'hidden');
      _populate('');
      _loadHarnessDefaults().then(() => {
        if (!menu.classList.contains('hidden')) _populate(search.value || '');
      }).catch(() => {});
      _loadHarnesses().then(() => {
        if (!menu.classList.contains('hidden')) _populate(search.value || '');
        updateModelPicker();
      }).catch(() => {});
      if (window.modelsModule && window.modelsModule.refreshModels) {
        window.modelsModule.refreshModels().then(() => {
          if (!menu.classList.contains('hidden')) _populate(search.value || '');
          updateModelPicker();
        }).catch(() => {});
      }
      if (window.innerWidth >= 768) search.focus();
      // Hide scroll button so it doesn't overlap
      const _scrollBtn = document.getElementById('scroll-bottom-btn');
      if (_scrollBtn) _scrollBtn.style.display = 'none';
    } else {
      _close();
    }
  });

  search.addEventListener('input', () => _populate(search.value));
  if (harnessSessionEl) {
    harnessSessionEl.addEventListener('click', (e) => {
      const button = e.target && e.target.closest ? e.target.closest('button[data-harness-session-choice]') : null;
      if (!button || button.disabled) return;
      _harnessSessionChoice = button.dataset.harnessSessionChoice === 'resume' ? 'resume' : 'new';
      _populate(search.value || '');
    });
  }
  search.addEventListener('click', (e) => e.stopPropagation());
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      refreshBtn.disabled = true;
      refreshBtn.classList.add('spinning');
      try {
        if (window.modelsModule && window.modelsModule.refreshModels) {
          await window.modelsModule.refreshModels(true);
        }
        await _loadHarnessDefaults(true);
        await _loadHarnesses(true);
        await _refreshLocalProbe();
        if (!menu.classList.contains('hidden')) _populate(search.value || '');
        updateModelPicker();
      } catch (_) {
        uiModule.showToast('Model refresh failed');
      } finally {
        refreshBtn.disabled = false;
        refreshBtn.classList.remove('spinning');
      }
    });
  }
  search.addEventListener('keydown', (e) => {
    _handlePickerKeydown(e, listEl, '.model-switch-item', _close);
  });
  const addModelsBtn = document.getElementById('model-picker-add-models-btn');
  if (addModelsBtn) {
    addModelsBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _openPickerShortcut('models');
    });
  }
  document.addEventListener('click', (e) => {
    if (!menu.classList.contains('hidden') && !menu.contains(e.target) && e.target !== btn) {
      _close();
    }
  });
}

/**
 * Update the model picker label to show the current model.
 * Always visible — shows current model name or "Select model" if none.
 * Called after selectSession, createDirectChat, and model switch.
 */
export function updateModelPicker() {
  if (!_deps) return;
  const label = document.getElementById('model-picker-label');
  if (!label) return;
  // Hide model picker when group chat is active
  const wrap = document.getElementById('model-picker-wrap');
  if (window.groupModule && window.groupModule.isActive()) {
    if (wrap) { wrap.style.display = 'none'; }
    const strip = document.getElementById('harness-runtime-strip');
    if (strip) strip.classList.add('hidden');
    return;
  }
  // Reset inline visibility (may have been hidden by typing in previous session)
  if (wrap) {
    wrap.style.display = '';
    wrap.style.opacity = '';
    wrap.style.pointerEvents = '';
  }
  const currentSessionId = _deps.getCurrentSessionId();
  const sessions = _deps.getSessions();
  const _pendingChat = _deps.getPendingChat();
  const s = sessions.find(x => x.id === currentSessionId);
  const harnessCtx = _currentHarnessContext();
  _renderHarnessRuntimeStrip();
  if (harnessCtx) {
    const cfg = harnessCtx.config || {};
    const hLabel = _harnessLabel(cfg);
    const hModel = harnessCtx.modelId || cfg.model || cfg.model_id || cfg.id;
    const displayName = hModel && hModel !== cfg.id ? `${hLabel} · ${String(hModel).split('/').pop()}` : hLabel;
    label.title = `${hLabel} harness${hModel ? ` (${hModel})` : ''}`;
    label.textContent = displayName;
    if (!_harnessCatalog.length) {
      _loadHarnesses().then(() => updateModelPicker()).catch(() => {});
    }
    return;
  }
  let modelId = null;
  if (s && s.model) {
    modelId = s.model;
    if (!_isHarnessUrl(s.endpoint_url) && !_modelExists(modelId, s.endpoint_url || '')) {
      modelId = null;
    }
  } else if (_pendingChat && _pendingChat.modelId) {
    modelId = _pendingChat.modelId;
    if (!_isHarnessUrl(_pendingChat.url) && !_modelExists(modelId, _pendingChat.url || '')) {
      _deps.setPendingChat(null);
      modelId = null;
    }
  }
  // SECURITY: deliberately NOT auto-injecting `odysseus-model-favorites[0]`
  // here. localStorage favorites are per-browser, not per-user, so on a
  // shared browser the previous account's first favorited model would
  // silently pre-populate the chatbox of the next user that signed in. If
  // we have no session model and no pending-chat pick, fall through to
  // the "Select model" placeholder below.
  //
  // Check if selected model is still available — fall back ONLY for pending chats with no user selection
  // Never override an existing session's model — the user explicitly chose it
  if (modelId && !currentSessionId && _pendingChat && !_isHarnessUrl(_pendingChat.url) && window.modelsModule && window.modelsModule.getCachedItems) {
    const items = window.modelsModule.getCachedItems();
    const allAvailable = [];
    items.forEach(item => {
      if (item.offline) return;
      (item.models || []).concat(item.models_extra || []).forEach(m => allAvailable.push(m));
    });
    if (allAvailable.length > 0 && !allAvailable.includes(modelId)) {
      // Model no longer available — switch to first available
      const fallback = items.find(item => !item.offline && (item.models || []).length > 0);
      if (fallback) {
        modelId = fallback.models[0];
        _deps.setPendingChat({ url: fallback.url, modelId, endpointId: fallback.endpoint_id, source: 'fallback' });
      }
    }
  }
  const latestPending = _deps.getPendingChat && _deps.getPendingChat();
  if (
    !currentSessionId &&
    !_autoSelectingDefault &&
    window.modelsModule &&
    window.modelsModule.getCachedItems &&
    (!modelId || (latestPending && latestPending.source === 'fallback'))
  ) {
    _ensureDefaultPendingChat();
  }

  const displayName = modelId ? modelId.split('/').pop() : 'Select model';
  // The header indicator clips long names with ellipsis; show the full model
  // identifier on hover (#1982). No tooltip on the "Select model" placeholder.
  label.title = modelId || '';
  const logo = modelId ? providerLogo(modelId) : null;
  if (logo) {
    label.innerHTML = '<span class="model-picker-logo">' + logo + '</span> ' + displayName;
  } else {
    label.textContent = displayName;
  }
}
