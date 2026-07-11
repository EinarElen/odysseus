"""Regression guards for AI document updates while Markdown Preview is visible (#2182)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parent.parent / "static/js/document.js"


def _function_body(name: str) -> str:
    text = SRC.read_text(encoding="utf-8")
    match = re.search(rf"\n\s*(?:export\s+)?(?:async\s+)?function\s+{name}\([^)]*\)\s*\{{", text)
    assert match, f"{name} not found"

    start = match.end()
    depth = 1
    i = start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"{name} body did not close"
    return text[start : i - 1]


def test_markdown_preview_refresh_rerenders_visible_preview():
    body = _function_body("_refreshMarkdownPreviewIfVisible")

    assert "_isMarkdownPreviewVisible()" in body
    assert "lang !== 'markdown'" in body
    assert "textarea.value = content;" in body
    assert "syncHighlighting();" in body
    assert "_setMarkdownPreviewActive(true, { remember: false });" in body


def test_typst_session_recovery_renders_lower_revision_after_not_found():
    """A replacement session's first result must render after a stale-session 404."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for frontend behavior coverage")

    functions = "\n\n".join(
        f"{prefix} {name}{params} {{\n{_function_body(name)}\n}}"
        for prefix, name, params in (
            ("async function", "_ensureTypstSession", "()"),
            ("async function", "_syncAndCompileTypst", "({ force = false, conflictRetry = false, sessionRecovery = false } = {})"),
            ("function", "_applyTypstResult", "(result)"),
        )
    )
    harness = f"""
const docs = new Map([['typst-doc', {{ language: 'typst', content: '= Old', _typstSessionId: 'stale-session' }}]]);
let activeDocId = 'typst-doc';
let API_BASE = '';
let _typstPreviewActive = true;
let _typstSessionId = 'stale-session';
let _typstRevision = 2;
let _typstLatestRenderedRevision = 2;
let _typstEventSource = null;
const textarea = {{ value: '= Recovered' }};
const pages = {{ children: [], _html: '', set innerHTML(value) {{ this._html = value; this.children = []; }}, get innerHTML() {{ return this._html; }}, appendChild(child) {{ this.children.push(child); }} }};
const status = {{ textContent: '' }};
const document = {{
  getElementById(id) {{ return {{ 'doc-editor-textarea': textarea, 'doc-typst-pages': pages, 'doc-typst-status': status, 'doc-typst-diagnostics': null }}[id] || null; }},
  createElement(tag) {{ return {{ tagName: tag, style: {{}}, appendChild(child) {{ this.child = child; }} }}; }},
}};
const window = {{ EventSource: null }};
function _closeTypstEvents() {{ _typstEventSource = null; }}
function _openTypstEvents() {{}}
function _setTypstStatus(text) {{ status.textContent = text; }}
function _renderTypstDiagnostics() {{}}
const responses = [
  {{ status: 404, ok: false, json: async () => ({{}}) }},
  {{ status: 200, ok: true, json: async () => ({{ session: {{ id: 'replacement-session', sourceRevision: 0 }} }}) }},
  {{ status: 200, ok: true, json: async () => ({{ compile: {{ revision: 1, ok: true, pages: [{{ page: 1, svgUrl: '/page.svg', hash: 'new' }}], durationMs: 4, backend: 'tinymist' }} }}) }},
];
async function fetch() {{ const response = responses.shift(); if (!response) throw new Error('unexpected fetch'); return response; }}
{functions}
(async () => {{
  await _syncAndCompileTypst({{ force: true }});
  if (responses.length) throw new Error('not all requests were used');
  if (_typstSessionId !== 'replacement-session') throw new Error('cached session was not replaced');
  if (_typstRevision !== 1) throw new Error(`expected recovered revision 1, got ${{_typstRevision}}`);
  if (_typstLatestRenderedRevision !== 1) throw new Error(`lower recovered result was discarded: ${{_typstLatestRenderedRevision}}`);
  if (pages.children.length !== 1) throw new Error('recovered result did not render a page');
  process.stdout.write(JSON.stringify({{ status: status.textContent, image: pages.children[0].child.src }}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", harness],
        check=True,
        capture_output=True,
        text=True,
    )
    rendered = json.loads(result.stdout)
    assert rendered["status"].startswith("Preview up to date r1")
    assert "rev=1" in rendered["image"]


def test_doc_update_refreshes_preview_instead_of_hidden_editor_animation():
    body = _function_body("handleDocUpdate")

    visible = "const markdownPreviewWasVisible = _isMarkdownPreviewVisible();"
    exit_preview = "if (markdownPreviewWasVisible) _setMarkdownPreviewActive(false, { remember: false });"
    diff = "enterDiffMode(oldContent, newContent);"
    refresh = "markdownPreviewWasVisible && _refreshMarkdownPreviewIfVisible(docId, newContent)"
    animate = "_animateDocEdit(textarea, newContent);"

    assert visible in body
    assert exit_preview in body
    assert diff in body
    assert body.index(exit_preview) < body.index(diff)
    assert refresh in body
    assert body.index(refresh) < body.index(animate)
    assert "_refreshMarkdownPreviewIfVisible(docId, newContent);" in body
