import uiModule from './ui.js';
import { makeWindowDraggable } from './windowDrag.js';
import { topPortalZ } from './toolWindowZOrder.js';
import * as Modals from './modalManager.js';

const API = window.location.origin;
const MODAL_ID = 'skills-lab-modal';

let _wired = false;
let _loaded = false;
let _skills = [];
let _index = [];
let _currentName = '';
let _currentSkill = null;
let _sourceTouched = false;
let _bodyExtra = '';
let _testPoll = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => uiModule.esc(String(s ?? ''));

function _slug(text, fallback = 'skill') {
  const raw = String(text || '').trim().toLowerCase();
  const slug = raw.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60);
  return slug || fallback;
}

function _splitTags(text) {
  return String(text || '').split(',').map((t) => t.trim()).filter(Boolean);
}

function _lineItems(text) {
  return String(text || '').split(/\n+/)
    .map((line) => line.replace(/^\s*(?:[-*]|\d+[.)])\s+/, '').trim())
    .filter(Boolean);
}

function _itemsText(items) {
  return Array.isArray(items) ? items.filter(Boolean).join('\n') : String(items || '');
}

function _confidenceValue(value, fallback = 0.8) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(0, Math.min(1, n > 1 ? n / 100 : n));
}

function _yamlScalar(value) {
  if (Array.isArray(value)) return `[${value.map(_yamlScalar).join(', ')}]`;
  if (value === null || value === undefined) return '';
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  const s = String(value);
  if (!s) return '""';
  if (/^[a-zA-Z0-9._/-]+(?: [a-zA-Z0-9._/-]+)*$/.test(s)) return s;
  return JSON.stringify(s);
}

function _parseScalar(raw) {
  const s = String(raw || '').trim();
  if (!s) return '';
  if (s.startsWith('[') && s.endsWith(']')) {
    const inner = s.slice(1, -1).trim();
    if (!inner) return [];
    return inner.split(',').map((part) => _parseScalar(part)).filter((v) => v !== '');
  }
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) {
    try { return JSON.parse(s); } catch (_) { return s.slice(1, -1); }
  }
  if (/^(true|false)$/i.test(s)) return s.toLowerCase() === 'true';
  const n = Number(s);
  if (Number.isFinite(n) && /^-?\d+(?:\.\d+)?$/.test(s)) return n;
  return s;
}

function _parseMarkdown(markdown) {
  let body = String(markdown || '');
  const fm = {};
  if (body.startsWith('---')) {
    const end = body.indexOf('\n---', 3);
    if (end >= 0) {
      const fmText = body.slice(3, end).trim();
      body = body.slice(end + 4).replace(/^\s*\n/, '');
      let pending = '';
      for (const line of fmText.splitlines ? fmText.splitlines() : fmText.split(/\r?\n/)) {
        if (!line.trim() || line.trim().startsWith('#')) continue;
        const listMatch = line.match(/^\s*-\s+(.*)$/);
        if (listMatch && pending) {
          if (!Array.isArray(fm[pending])) fm[pending] = [];
          fm[pending].push(_parseScalar(listMatch[1]));
          continue;
        }
        const match = line.match(/^([a-z_][a-z0-9_]*):\s*(.*)$/i);
        if (!match) continue;
        pending = '';
        if (!match[2].trim()) {
          pending = match[1];
          fm[pending] = [];
        } else {
          fm[match[1]] = _parseScalar(match[2]);
        }
      }
    }
  }

  const sections = {
    when_to_use: '',
    procedure: [],
    pitfalls: [],
    verification: [],
    body_extra: '',
  };
  const buckets = {
    when_to_use: [],
    procedure: [],
    pitfalls: [],
    verification: [],
    body_extra: [],
  };
  const headingMap = {
    'when to use': 'when_to_use',
    procedure: 'procedure',
    steps: 'procedure',
    pitfalls: 'pitfalls',
    verification: 'verification',
  };
  let key = 'body_extra';
  for (const line of body.split(/\r?\n/)) {
    const heading = line.match(/^##\s+(.*?)\s*$/);
    if (heading) {
      const next = headingMap[heading[1].trim().toLowerCase()];
      key = next || 'body_extra';
      if (!next) buckets.body_extra.push(line);
      continue;
    }
    buckets[key].push(line);
  }
  sections.when_to_use = buckets.when_to_use.join('\n').trim();
  sections.procedure = _lineItems(buckets.procedure.join('\n'));
  sections.pitfalls = _lineItems(buckets.pitfalls.join('\n'));
  sections.verification = _lineItems(buckets.verification.join('\n'));
  sections.body_extra = buckets.body_extra.join('\n').trim();

  return {
    name: fm.name || '',
    description: fm.description || '',
    version: fm.version || '1.0.0',
    category: fm.category || 'general',
    tags: Array.isArray(fm.tags) ? fm.tags : _splitTags(fm.tags),
    status: fm.status || 'draft',
    confidence: _confidenceValue(fm.confidence, 0.8),
    source: fm.source || 'user',
    teacher_model: fm.teacher_model || '',
    created: fm.created || '',
    ...sections,
  };
}

function _readForm() {
  const name = _slug($('skills-lab-name')?.value || $('skills-lab-description')?.value || 'new-skill');
  const confidence = _confidenceValue($('skills-lab-confidence')?.value || 80);
  return {
    name,
    description: ($('skills-lab-description')?.value || '').trim(),
    category: ($('skills-lab-category')?.value || 'general').trim() || 'general',
    tags: _splitTags($('skills-lab-tags')?.value || ''),
    status: $('skills-lab-status')?.value || 'draft',
    confidence,
    when_to_use: ($('skills-lab-when')?.value || '').trim(),
    procedure: _lineItems($('skills-lab-procedure')?.value || ''),
    pitfalls: _lineItems($('skills-lab-pitfalls')?.value || ''),
    verification: _lineItems($('skills-lab-verification')?.value || ''),
    source: _currentSkill?.source || 'user',
    teacher_model: _currentSkill?.teacher_model || '',
    created: _currentSkill?.created || '',
    body_extra: _bodyExtra || '',
  };
}

function _emitMarkdown(fields) {
  const fm = [
    ['name', fields.name],
    ['description', fields.description],
    ['version', _currentSkill?.version || '1.0.0'],
    ['category', fields.category || 'general'],
    ['tags', fields.tags || []],
    ['status', fields.status || 'draft'],
    ['confidence', Number(fields.confidence || 0).toFixed(3).replace(/0+$/, '').replace(/\.$/, '')],
    ['source', fields.source || 'user'],
    ['teacher_model', fields.teacher_model || ''],
    ['created', fields.created || ''],
  ].filter(([, value]) => !(value === '' || value === null || value === undefined || (Array.isArray(value) && !value.length)));

  const parts = ['---', ...fm.map(([key, value]) => `${key}: ${_yamlScalar(value)}`), '---', ''];
  if (fields.when_to_use) parts.push(`## When to Use\n\n${fields.when_to_use}`, '');
  if (fields.procedure?.length) {
    parts.push(`## Procedure\n\n${fields.procedure.map((item, index) => `${index + 1}. ${item}`).join('\n')}`, '');
  }
  if (fields.pitfalls?.length) {
    parts.push(`## Pitfalls\n\n${fields.pitfalls.map((item) => `- ${item}`).join('\n')}`, '');
  }
  if (fields.verification?.length) {
    parts.push(`## Verification\n\n${fields.verification.map((item) => `- ${item}`).join('\n')}`, '');
  }
  if (fields.body_extra) parts.push(fields.body_extra.trim(), '');
  return parts.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd() + '\n';
}

async function _api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = '';
    try {
      const data = await res.json();
      detail = data.detail || data.error || JSON.stringify(data);
    } catch (_) {
      detail = await res.text();
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  if (res.status === 204) return {};
  return res.json();
}

async function _loadSkills(selectName = '') {
  const [skillsData, indexData] = await Promise.all([
    _api('/api/skills'),
    _api('/api/skills/index').catch(() => ({ index: [] })),
  ]);
  const seen = new Set();
  _skills = (skillsData.skills || []).filter((skill) => {
    const key = String(skill?.name || skill?.id || '').toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  _index = Array.isArray(indexData.index) ? indexData.index : [];
  _loaded = true;
  _renderList();
  _renderStats();

  const target = selectName || _currentName || (_skills[0]?.name || '');
  if (target) await _selectSkill(target, { skipLoad: true });
  else _newSkill();
}

function _renderStats() {
  const total = _skills.length;
  const published = _skills.filter((skill) => skill.status === 'published').length;
  const draft = _skills.filter((skill) => (skill.status || 'draft') !== 'published').length;
  if ($('skills-lab-stat-total')) $('skills-lab-stat-total').textContent = String(total);
  if ($('skills-lab-stat-published')) $('skills-lab-stat-published').textContent = String(published);
  if ($('skills-lab-stat-draft')) $('skills-lab-stat-draft').textContent = String(draft);
}

function _indexedNames() {
  return new Set(_index.map((item) => String(item?.name || item?.id || '').toLowerCase()).filter(Boolean));
}

function _passesFilter(skill) {
  const filter = $('skills-lab-filter')?.value || 'all';
  const query = String($('skills-lab-search')?.value || '').trim().toLowerCase();
  const name = String(skill.name || skill.id || '');
  const haystack = [
    name,
    skill.description,
    skill.category,
    ...(skill.tags || []),
    skill.when_to_use,
  ].join(' ').toLowerCase();
  if (query && !haystack.includes(query)) return false;
  if (filter === 'draft' && (skill.status || 'draft') === 'published') return false;
  if (filter === 'published' && skill.status !== 'published') return false;
  if (filter === 'needs-work') {
    const verdict = String(skill.audit_verdict || skill.test_verdict || '').toLowerCase();
    if (!/fail|needs|unknown|inconclusive/.test(verdict) && Number(skill.confidence || 0) >= 0.75) return false;
  }
  if (filter === 'user' && String(skill.source || '') !== 'user') return false;
  return true;
}

function _renderList() {
  const list = $('skills-lab-list');
  if (!list) return;
  const indexed = _indexedNames();
  const visible = _skills
    .filter(_passesFilter)
    .sort((a, b) => String(a.name || '').localeCompare(String(b.name || '')));

  if ($('skills-lab-count')) {
    $('skills-lab-count').textContent = `${visible.length} ${visible.length === 1 ? 'skill' : 'skills'}`;
  }
  if (!visible.length) {
    list.innerHTML = `<div class="skills-lab-empty">No matching skills.</div>`;
    return;
  }
  list.innerHTML = visible.map((skill) => {
    const name = skill.name || skill.id || '';
    const isActive = name === _currentName;
    const status = skill.status === 'published' ? 'published' : 'draft';
    const verdict = String(skill.audit_verdict || skill.test_verdict || '').replace(/_/g, ' ');
    const isIndexed = indexed.has(String(name).toLowerCase());
    const chips = [
      `<span class="skills-lab-chip ${status === 'published' ? 'is-live' : ''}">${esc(status)}</span>`,
      isIndexed ? '<span class="skills-lab-chip">index</span>' : '',
      verdict ? `<span class="skills-lab-chip ${/fail|needs|unknown|inconclusive/i.test(verdict) ? 'is-warn' : 'is-ok'}">${esc(verdict)}</span>` : '',
    ].filter(Boolean).join('');
    return `
      <button type="button" class="skills-lab-row ${isActive ? 'active' : ''}" data-name="${esc(name)}" role="listitem">
        <span class="skills-lab-row-title">${esc(name || 'unnamed')}</span>
        <span class="skills-lab-row-desc">${esc(skill.description || '')}</span>
        <span class="skills-lab-row-chips">${chips}</span>
      </button>
    `;
  }).join('');
}

async function _selectSkill(name, { skipLoad = false } = {}) {
  const skill = _skills.find((item) => item.name === name || item.id === name);
  if (!skill) {
    _newSkill();
    return;
  }
  _currentName = skill.name || skill.id || '';
  _currentSkill = skill;
  _sourceTouched = false;
  _bodyExtra = skill.body_extra || '';
  let markdown = '';
  try {
    const data = await _api(`/api/skills/${encodeURIComponent(_currentName)}/markdown`);
    markdown = data.markdown || '';
  } catch (_) {
    markdown = _emitMarkdown({ ...skill, confidence: _confidenceValue(skill.confidence) });
  }
  _writeForm(skill, markdown);
  _renderList();
  _setButtonStates();
  _refreshInspector();
  if (!skipLoad) _renderStats();
}

function _writeForm(skill, markdown = '') {
  const parsed = markdown ? _parseMarkdown(markdown) : {};
  const name = _currentName || parsed.name || skill.name || skill.id || '';
  const confidence = Math.round(_confidenceValue(parsed.confidence ?? skill.confidence, 0.8) * 100);
  if ($('skills-lab-name')) {
    $('skills-lab-name').value = name;
    $('skills-lab-name').readOnly = Boolean(_currentName);
  }
  if ($('skills-lab-description')) $('skills-lab-description').value = parsed.description || skill.description || '';
  if ($('skills-lab-category')) $('skills-lab-category').value = parsed.category || skill.category || 'general';
  if ($('skills-lab-tags')) $('skills-lab-tags').value = (parsed.tags?.length ? parsed.tags : skill.tags || []).join(', ');
  if ($('skills-lab-status')) $('skills-lab-status').value = parsed.status || skill.status || 'draft';
  if ($('skills-lab-confidence')) $('skills-lab-confidence').value = String(confidence);
  if ($('skills-lab-confidence-readout')) $('skills-lab-confidence-readout').textContent = `${confidence}%`;
  if ($('skills-lab-when')) $('skills-lab-when').value = parsed.when_to_use || skill.when_to_use || '';
  if ($('skills-lab-procedure')) $('skills-lab-procedure').value = _itemsText(parsed.procedure?.length ? parsed.procedure : skill.procedure || skill.steps || []);
  if ($('skills-lab-pitfalls')) $('skills-lab-pitfalls').value = _itemsText(parsed.pitfalls?.length ? parsed.pitfalls : skill.pitfalls || []);
  if ($('skills-lab-verification')) $('skills-lab-verification').value = _itemsText(parsed.verification?.length ? parsed.verification : skill.verification || []);
  _bodyExtra = parsed.body_extra || skill.body_extra || '';
  if ($('skills-lab-markdown')) $('skills-lab-markdown').value = markdown || _emitMarkdown(_readForm());
  if ($('skills-lab-source-label')) {
    $('skills-lab-source-label').textContent = _currentSkill?.path || `${name || 'new-skill'}/SKILL.md`;
  }
}

function _newSkill() {
  _currentName = '';
  _currentSkill = null;
  _sourceTouched = false;
  _bodyExtra = '';
  const blank = {
    name: '',
    description: '',
    category: 'general',
    tags: [],
    status: 'draft',
    confidence: 0.8,
    source: 'user',
    when_to_use: '',
    procedure: [],
    pitfalls: [],
    verification: [],
    body_extra: '',
  };
  _writeForm(blank, _emitMarkdown({ ...blank, name: 'new-skill' }));
  if ($('skills-lab-name')) {
    $('skills-lab-name').value = '';
    $('skills-lab-name').readOnly = false;
    $('skills-lab-name').focus();
  }
  if ($('skills-lab-test-log')) $('skills-lab-test-log').textContent = 'Save the skill before testing.';
  _renderList();
  _setButtonStates();
  _refreshInspector();
}

function _setButtonStates() {
  const hasCurrent = Boolean(_currentName);
  for (const id of ['skills-lab-test', 'skills-lab-clone', 'skills-lab-delete']) {
    const btn = $(id);
    if (btn) btn.disabled = !hasCurrent;
  }
  const publish = $('skills-lab-publish');
  if (publish) {
    publish.disabled = false;
    publish.textContent = ($('skills-lab-status')?.value || 'draft') === 'published' ? 'Move to Draft' : 'Publish';
  }
}

function _syncMarkdownFromForm() {
  if (_sourceTouched) return;
  const md = _emitMarkdown(_readForm());
  if ($('skills-lab-markdown')) $('skills-lab-markdown').value = md;
}

function _onBlueprintChanged() {
  const name = $('skills-lab-name');
  if (name && !name.readOnly) {
    const pos = name.selectionStart;
    name.value = _slug(name.value, '');
    if (typeof name.setSelectionRange === 'function' && Number.isInteger(pos)) {
      name.setSelectionRange(pos, pos);
    }
  }
  if ($('skills-lab-confidence-readout')) {
    $('skills-lab-confidence-readout').textContent = `${$('skills-lab-confidence')?.value || 0}%`;
  }
  _sourceTouched = false;
  _syncMarkdownFromForm();
  _setButtonStates();
  _refreshInspector();
}

function _syncBlueprintFromSource() {
  const parsed = _parseMarkdown($('skills-lab-markdown')?.value || '');
  const name = _currentName || parsed.name || '';
  _currentSkill = _currentSkill || { source: parsed.source || 'user' };
  _bodyExtra = parsed.body_extra || '';
  _writeForm({ ...parsed, name }, $('skills-lab-markdown')?.value || '');
  if ($('skills-lab-name')) $('skills-lab-name').readOnly = Boolean(_currentName);
  _sourceTouched = false;
  _refreshInspector();
}

function _auditFindings(fields) {
  const findings = [];
  const wordCount = (text) => String(text || '').trim().split(/\s+/).filter(Boolean).length;
  if (!fields.description) findings.push({ level: 'error', text: 'Missing description.' });
  if (wordCount(fields.when_to_use) < 8) findings.push({ level: 'warn', text: 'Trigger is too thin.' });
  if (/\b(anything|everything|always|all tasks)\b/i.test(fields.when_to_use)) {
    findings.push({ level: 'warn', text: 'Trigger looks broad.' });
  }
  if (fields.procedure.length < 2) findings.push({ level: 'warn', text: 'Procedure needs at least two concrete steps.' });
  if (!fields.verification.length) findings.push({ level: 'warn', text: 'Verification is empty.' });
  if (!fields.pitfalls.length) findings.push({ level: 'note', text: 'No pitfalls captured.' });
  if (!fields.tags.length) findings.push({ level: 'note', text: 'No tags set.' });
  if (fields.status === 'published' && fields.confidence < 0.85) {
    findings.push({ level: 'warn', text: 'Published confidence is below the approval threshold.' });
  }
  const base = fields.name.replace(/-(variant|copy|\d+)$/i, '');
  const sibling = _skills.find((skill) => skill.name !== _currentName && String(skill.name || '').startsWith(base) && base.length > 5);
  if (sibling) findings.push({ level: 'note', text: `Related skill: ${sibling.name}` });
  const verdict = String(_currentSkill?.audit_verdict || _currentSkill?.test_verdict || '').replace(/_/g, ' ');
  if (verdict && /fail|needs|unknown|inconclusive/i.test(verdict)) {
    findings.push({ level: 'warn', text: `Last verdict: ${verdict}.` });
  }
  if (!findings.length) findings.push({ level: 'ok', text: 'Ready for review.' });
  return findings;
}

function _setupPreview(fields) {
  const name = fields.name || 'new-skill';
  const path = _currentSkill?.path || `data/skills/<owner>/${name}/SKILL.md`;
  const indexed = _indexedNames().has(String(name).toLowerCase());
  return [
    `file: ${path}`,
    `slash: /${name}`,
    `status: ${fields.status}`,
    `injection: ${fields.status === 'published' ? 'eligible' : 'draft only'}`,
    `index: ${indexed ? 'present' : 'pending save/audit'}`,
    `source: ${fields.source || 'user'}`,
    '',
    'frontmatter:',
    `  name: ${name}`,
    `  category: ${fields.category || 'general'}`,
    `  tags: [${fields.tags.join(', ')}]`,
  ].join('\n');
}

function _refreshInspector() {
  let fields = _readForm();
  if (_sourceTouched) {
    const parsed = _parseMarkdown($('skills-lab-markdown')?.value || '');
    fields = {
      ...fields,
      ...parsed,
      name: _currentName || parsed.name || fields.name,
      tags: parsed.tags?.length ? parsed.tags : fields.tags,
      procedure: parsed.procedure?.length ? parsed.procedure : fields.procedure,
      pitfalls: parsed.pitfalls?.length ? parsed.pitfalls : fields.pitfalls,
      verification: parsed.verification?.length ? parsed.verification : fields.verification,
    };
  }
  if ($('skills-lab-setup-code')) $('skills-lab-setup-code').textContent = _setupPreview(fields);
  const findings = _auditFindings(fields);
  const target = $('skills-lab-findings');
  if (target) {
    target.innerHTML = findings.map((finding) => `
      <div class="skills-lab-finding is-${esc(finding.level)}">
        <span class="skills-lab-finding-dot"></span>
        <span>${esc(finding.text)}</span>
      </div>
    `).join('');
  }
}

async function _saveSkill() {
  const fields = _readForm();
  if (!fields.name) {
    uiModule.showError('Skill name is required.');
    return;
  }
  if (!fields.description) {
    uiModule.showError('Skill description is required.');
    return;
  }
  const markdown = _sourceTouched ? ($('skills-lab-markdown')?.value || '') : _emitMarkdown(fields);
  try {
    if (_currentName) {
      await _api(`/api/skills/${encodeURIComponent(_currentName)}/markdown`, {
        method: 'POST',
        body: JSON.stringify({ markdown }),
      });
      uiModule.showToast('Skill saved');
      await _loadSkills(_currentName);
    } else {
      const data = await _api('/api/skills/add', {
        method: 'POST',
        body: JSON.stringify({
          name: fields.name,
          description: fields.description,
          category: fields.category,
          tags: fields.tags,
          when_to_use: fields.when_to_use,
          procedure: fields.procedure,
          pitfalls: fields.pitfalls,
          verification: fields.verification,
          status: fields.status,
          confidence: fields.confidence,
          source: 'user',
        }),
      });
      const name = data.skill?.name || fields.name;
      uiModule.showToast(data.deduped ? 'Existing skill reused' : 'Skill created');
      await _loadSkills(name);
    }
  } catch (err) {
    uiModule.showError(`Could not save skill: ${err.message}`);
  }
}

function _uniqueCloneName(base) {
  const names = new Set(_skills.map((skill) => String(skill.name || '').toLowerCase()));
  const root = _slug(base || 'skill');
  let candidate = `${root}-variant`;
  let i = 2;
  while (names.has(candidate.toLowerCase())) {
    candidate = `${root}-variant-${i}`;
    i += 1;
  }
  return candidate;
}

async function _cloneSkill() {
  if (!_currentName) return;
  const fields = _readForm();
  const name = _uniqueCloneName(fields.name);
  try {
    const data = await _api('/api/skills/add', {
      method: 'POST',
      body: JSON.stringify({
        name,
        description: fields.description,
        category: fields.category,
        tags: fields.tags,
        when_to_use: fields.when_to_use,
        procedure: fields.procedure,
        pitfalls: fields.pitfalls,
        verification: fields.verification,
        status: 'draft',
        confidence: Math.min(fields.confidence || 0.8, 0.8),
        source: 'user',
      }),
    });
    uiModule.showToast('Skill cloned');
    await _loadSkills(data.skill?.name || name);
  } catch (err) {
    uiModule.showError(`Could not clone skill: ${err.message}`);
  }
}

async function _deleteSkill() {
  if (!_currentName) return;
  const ok = await uiModule.styledConfirm(`Delete skill "${_currentName}"?`, {
    confirmText: 'Delete',
    cancelText: 'Cancel',
    danger: true,
  });
  if (!ok) return;
  try {
    await _api(`/api/skills/${encodeURIComponent(_currentName)}`, { method: 'DELETE' });
    uiModule.showToast('Skill deleted');
    _currentName = '';
    _currentSkill = null;
    await _loadSkills();
  } catch (err) {
    uiModule.showError(`Could not delete skill: ${err.message}`);
  }
}

async function _togglePublish() {
  const status = $('skills-lab-status');
  if (!status) return;
  status.value = status.value === 'published' ? 'draft' : 'published';
  _onBlueprintChanged();
  await _saveSkill();
}

function _appendUnique(textareaId, items) {
  const textarea = $(textareaId);
  if (!textarea) return;
  const current = _lineItems(textarea.value);
  const lower = new Set(current.map((item) => item.toLowerCase()));
  for (const item of items) {
    if (!lower.has(item.toLowerCase())) current.push(item);
  }
  textarea.value = current.join('\n');
}

function _applyAugmentation(kind) {
  const fields = _readForm();
  const subject = fields.description || fields.name.replace(/-/g, ' ');
  if (kind === 'trigger') {
    const trigger = `Use when the user asks to ${subject.toLowerCase()} and the work depends on a repeatable procedure, tool sequence, or project-specific convention.`;
    const when = $('skills-lab-when');
    if (when && when.value.trim().length < trigger.length) when.value = trigger;
  } else if (kind === 'verification') {
    _appendUnique('skills-lab-verification', [
      'Run the fastest relevant check for the changed behavior.',
      'Inspect the final output against the requested scope.',
    ]);
  } else if (kind === 'pitfalls') {
    _appendUnique('skills-lab-pitfalls', [
      'Do not apply this skill when the request only needs a direct answer.',
      'Prefer the existing project conventions before adding new structure.',
    ]);
  } else if (kind === 'checklist') {
    _appendUnique('skills-lab-procedure', [
      'Inspect the relevant local context before changing anything.',
      'Apply the smallest change that satisfies the request.',
      'Verify the result with the fastest relevant check.',
    ]);
  }
  _onBlueprintChanged();
}

function _formatTestStatus(status) {
  if (!status || status.status === 'none') return 'No run selected.';
  const lines = [`status: ${status.status}`];
  if (status.model) lines.push(`model: ${status.model}`);
  if (status.task) lines.push(`task: ${status.task}`);
  if (status.verdict) {
    lines.push('');
    lines.push(`verdict: ${status.verdict.verdict || status.verdict}`);
    if (status.verdict.summary) lines.push(`summary: ${status.verdict.summary}`);
    if (Array.isArray(status.verdict.issues) && status.verdict.issues.length) {
      lines.push('issues:');
      status.verdict.issues.forEach((issue) => lines.push(`- ${issue}`));
    }
  }
  const log = Array.isArray(status.log) ? status.log.slice(-6) : [];
  if (log.length) {
    lines.push('');
    lines.push('log:');
    for (const entry of log) {
      if (typeof entry === 'string') lines.push(`- ${entry}`);
      else lines.push(`- ${entry.type || 'event'}${entry.message ? `: ${entry.message}` : ''}`);
    }
  }
  return lines.join('\n');
}

async function _pollTest() {
  if (!_currentName) return;
  try {
    const status = await _api(`/api/skills/${encodeURIComponent(_currentName)}/test-status`);
    if ($('skills-lab-test-log')) $('skills-lab-test-log').textContent = _formatTestStatus(status);
    if (!['running', 'queued'].includes(status.status)) {
      clearInterval(_testPoll);
      _testPoll = null;
      await _loadSkills(_currentName);
    }
  } catch (err) {
    if ($('skills-lab-test-log')) $('skills-lab-test-log').textContent = `Could not read test status: ${err.message}`;
    clearInterval(_testPoll);
    _testPoll = null;
  }
}

async function _runTest() {
  if (!_currentName) return;
  try {
    await _api(`/api/skills/${encodeURIComponent(_currentName)}/test`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    if ($('skills-lab-test-log')) $('skills-lab-test-log').textContent = 'status: running';
    clearInterval(_testPoll);
    _testPoll = setInterval(_pollTest, 1500);
    await _pollTest();
  } catch (err) {
    if ($('skills-lab-test-log')) $('skills-lab-test-log').textContent = `Test could not start: ${err.message}`;
  }
}

function _setTab(tab) {
  document.querySelectorAll('#skills-lab-modal .skills-lab-tab').forEach((button) => {
    button.classList.toggle('active', button.dataset.skillsLabTab === tab);
  });
  document.querySelectorAll('#skills-lab-modal [data-skills-lab-panel]').forEach((panel) => {
    panel.classList.toggle('hidden', panel.dataset.skillsLabPanel !== tab);
  });
}

function _ensureModalRegistration() {
  const modal = $(MODAL_ID);
  if (!modal) return;
  if (!Modals.isRegistered(MODAL_ID)) {
    Modals.register(MODAL_ID, {
      railBtnId: 'rail-skills-lab',
      sidebarBtnId: 'tool-skills-lab-btn',
      closeFn: () => closeSkillsLab(),
      restoreFn: () => {
        modal.classList.remove('hidden', 'modal-minimized');
        modal.style.zIndex = String(topPortalZ());
      },
    });
  }
  Modals.injectMinimizeButton(modal, MODAL_ID);
}

function _wireOnce() {
  if (_wired) return;
  _wired = true;
  const modal = $(MODAL_ID);
  const content = modal?.querySelector('.skills-lab-content');
  const header = $('skills-lab-modal-header');
  if (modal && content && header) {
    makeWindowDraggable(modal, {
      content,
      header,
      minWidth: 760,
      minHeight: 520,
      resizeStorageKey: 'winsize-skills-lab-modal',
    });
  }
  _ensureModalRegistration();

  $('close-skills-lab-modal')?.addEventListener('click', closeSkillsLab);
  $('skills-lab-new')?.addEventListener('click', _newSkill);
  $('skills-lab-save')?.addEventListener('click', _saveSkill);
  $('skills-lab-clone')?.addEventListener('click', _cloneSkill);
  $('skills-lab-delete')?.addEventListener('click', _deleteSkill);
  $('skills-lab-test')?.addEventListener('click', _runTest);
  $('skills-lab-publish')?.addEventListener('click', _togglePublish);
  $('skills-lab-open-brain')?.addEventListener('click', () => {
    document.getElementById('tool-memory-btn')?.click();
    setTimeout(() => document.querySelector('.memory-tab[data-memory-tab="skills"]')?.click(), 120);
  });
  $('skills-lab-build')?.addEventListener('click', () => {
    _sourceTouched = false;
    _syncMarkdownFromForm();
    _setTab('source');
  });
  $('skills-lab-sync')?.addEventListener('click', _syncBlueprintFromSource);
  $('skills-lab-augment-trigger')?.addEventListener('click', () => _applyAugmentation('trigger'));
  $('skills-lab-augment-verification')?.addEventListener('click', () => _applyAugmentation('verification'));
  $('skills-lab-augment-pitfalls')?.addEventListener('click', () => _applyAugmentation('pitfalls'));
  $('skills-lab-augment-checklist')?.addEventListener('click', () => _applyAugmentation('checklist'));
  $('skills-lab-search')?.addEventListener('input', _renderList);
  $('skills-lab-filter')?.addEventListener('change', _renderList);
  $('skills-lab-list')?.addEventListener('click', (event) => {
    const row = event.target.closest('.skills-lab-row');
    if (row?.dataset.name) _selectSkill(row.dataset.name);
  });
  $('skills-lab-markdown')?.addEventListener('input', () => {
    _sourceTouched = true;
    _refreshInspector();
  });
  document.querySelectorAll('#skills-lab-modal .skills-lab-tab').forEach((button) => {
    button.addEventListener('click', () => _setTab(button.dataset.skillsLabTab || 'blueprint'));
  });
  [
    'skills-lab-name',
    'skills-lab-description',
    'skills-lab-category',
    'skills-lab-tags',
    'skills-lab-status',
    'skills-lab-confidence',
    'skills-lab-when',
    'skills-lab-procedure',
    'skills-lab-pitfalls',
    'skills-lab-verification',
  ].forEach((id) => $(id)?.addEventListener('input', _onBlueprintChanged));
  $('skills-lab-status')?.addEventListener('change', _onBlueprintChanged);
}

export async function openSkillsLab(options = {}) {
  const modal = $(MODAL_ID);
  if (!modal) return;
  _wireOnce();
  _ensureModalRegistration();
  if (Modals.isRegistered(MODAL_ID) && Modals.isMinimized(MODAL_ID)) {
    Modals.restore(MODAL_ID);
    return;
  }
  modal.classList.remove('hidden', 'modal-minimized');
  modal.style.zIndex = String(topPortalZ());
  if (!_loaded || options.reload) {
    try {
      await _loadSkills(options.name || '');
    } catch (err) {
      uiModule.showError(`Could not load skills: ${err.message}`);
      _newSkill();
    }
  } else if (options.name) {
    await _selectSkill(options.name);
  } else {
    _renderList();
    _refreshInspector();
  }
}

export function closeSkillsLab() {
  const modal = $(MODAL_ID);
  if (modal) modal.classList.add('hidden');
  clearInterval(_testPoll);
  _testPoll = null;
}

window.skillsLabModule = { openSkillsLab, closeSkillsLab };

export default { openSkillsLab, closeSkillsLab };
