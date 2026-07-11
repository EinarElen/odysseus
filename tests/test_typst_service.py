"""Regression coverage for Typst live-preview compilation."""

from __future__ import annotations

import asyncio
import shutil

import pytest

from services.typst_service import (
    CliTypstBackend,
    CompileResult,
    TypstPreviewPage,
    TypstRevisionConflict,
    TypstSessionManager,
    _page_output_sort_key,
    _preview_page_from_svg,
)


class _MultiPageBackend:
    name = "fake-typst"
    available = True

    async def compile(self, session, source, revision, fmt="svg"):
        return CompileResult(
            ok=True,
            revision=revision,
            backend=self.name,
            pages=[
                TypstPreviewPage(1, 100.0, 200.0, "page-one", "<svg width=\"100pt\" height=\"200pt\"/>"),
                TypstPreviewPage(2, 300.0, 400.0, "page-two", "<svg width=\"300pt\" height=\"400pt\"/>"),
            ],
        )


class _BlockingBackend:
    name = "blocking-typst"
    available = True

    def __init__(self):
        import asyncio

        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def compile(self, session, source, revision, fmt="svg"):
        self.calls.append((source, revision))
        self.started.set()
        await self.release.wait()
        return CompileResult(
            ok=True,
            revision=revision,
            backend=self.name,
            pages=[TypstPreviewPage(1, None, None, source, f"<svg>{source}</svg>")],
        )


def test_split_svg_pages_assigns_requested_page_number():
    page = _preview_page_from_svg('<svg width="100pt" height="200pt"></svg>', page=2)

    assert page.page == 2
    assert page.width == 100.0
    assert page.height == 200.0


def test_page_outputs_sort_numerically(tmp_path):
    outputs = [tmp_path / "out-10.svg", tmp_path / "out-2.svg", tmp_path / "out-1.svg"]

    assert [path.name for path in sorted(outputs, key=_page_output_sort_key)] == [
        "out-1.svg",
        "out-2.svg",
        "out-10.svg",
    ]


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("typst") is None, reason="Typst CLI is not installed")
async def test_typst_cli_compiles_and_orders_multiple_svg_pages():
    backend = CliTypstBackend("typst")
    session = TypstSessionManager().create(
        owner="owner",
        owner_type="document",
        owner_id="doc",
        source="first page\n#pagebreak()\nsecond page",
        backend="typst-cli",
    )

    result = await backend.compile(session, session.source, revision=1)

    assert result.ok is True
    assert [page.page for page in result.pages] == [1, 2]
    assert all(page.svg.startswith("<svg") for page in result.pages)


@pytest.mark.asyncio
async def test_svg_export_rejects_silently_truncating_multiple_pages(monkeypatch):
    backend = CliTypstBackend("typst")

    async def fake_compile(source, suffix, timeout=20.0):
        return 0, [b"<svg>one</svg>", b"<svg>two</svg>"], "", ""

    monkeypatch.setattr(backend, "_run_compile", fake_compile)
    session = TypstSessionManager().create(
        owner="owner", owner_type="document", owner_id="doc", source="pages", backend="typst-cli"
    )

    with pytest.raises(RuntimeError, match="Multi-page SVG export"):
        await backend.export(session, session.source, "svg")


@pytest.mark.asyncio
async def test_compile_payload_keeps_all_compiled_pages_addressable():
    manager = TypstSessionManager()
    manager.backends["tinymist"] = _MultiPageBackend()
    session = manager.create(
        owner="owner", owner_type="document", owner_id="doc", source="hello", backend="tinymist"
    )

    result = await manager.request_compile(session)
    payload = manager._result_payload(session, result)

    assert result.ok is True
    assert [page["page"] for page in payload["pages"]] == [1, 2]
    assert manager.page_svg(session, 2) == '<svg width="300pt" height="400pt"/>'


@pytest.mark.asyncio
async def test_compile_uses_the_source_snapshot_for_its_requested_revision():
    manager = TypstSessionManager()
    backend = _BlockingBackend()
    manager.backends["tinymist"] = backend
    session = manager.create(
        owner="owner", owner_type="document", owner_id="doc", source="first", backend="tinymist"
    )

    first_compile = asyncio.create_task(manager.request_compile(session))
    await backend.started.wait()
    manager.update_source(session, "second", revision=1)
    second_compile = asyncio.create_task(manager.request_compile(session, revision=1))
    await asyncio.sleep(0)
    assert second_compile.done() is False
    backend.release.set()
    first_result = await first_compile
    second_result = await second_compile

    assert backend.calls == [("first", 0), ("second", 1)]
    assert first_result.revision == 0
    assert first_result.pages[0].svg == "<svg>first</svg>"
    assert second_result.revision == 1
    assert second_result.pages[0].svg == "<svg>second</svg>"
    assert session.last_compiled_revision == 1
    assert session.pages[0].svg == "<svg>second</svg>"


@pytest.mark.asyncio
async def test_compile_rejects_a_revision_without_a_matching_source_snapshot():
    manager = TypstSessionManager()
    manager.backends["tinymist"] = _MultiPageBackend()
    session = manager.create(
        owner="owner", owner_type="document", owner_id="doc", source="newest", backend="tinymist"
    )
    manager.update_source(session, "newest", revision=2)

    with pytest.raises(TypstRevisionConflict) as exc_info:
        await manager.request_compile(session, revision=1)

    assert exc_info.value.current_revision == 2


def test_source_updates_reject_stale_writes_without_overwriting_newer_source():
    manager = TypstSessionManager()
    session = manager.create(
        owner="owner", owner_type="document", owner_id="doc", source="initial", backend="tinymist"
    )

    manager.update_source(session, "newer", revision=1)

    with pytest.raises(TypstRevisionConflict) as exc_info:
        manager.update_source(session, "stale", revision=0)

    assert exc_info.value.current_revision == 1
    assert session.source_revision == 1
    assert session.source == "newer"


def test_source_updates_reject_different_content_at_the_same_revision():
    manager = TypstSessionManager()
    session = manager.create(
        owner="owner", owner_type="document", owner_id="doc", source="initial", backend="tinymist"
    )
    manager.update_source(session, "newer", revision=1)

    with pytest.raises(TypstRevisionConflict):
        manager.update_source(session, "competing", revision=1)

    assert manager.update_source(session, "newer", revision=1) == 1
    assert session.source == "newer"


def test_create_reuses_document_session_and_applies_requested_configuration():
    manager = TypstSessionManager()

    original = manager.create(
        owner="alice", owner_type="document", owner_id="doc-1", source="first",
        backend="typst-cli", auto_refresh=False,
    )
    reused = manager.create(
        owner="alice", owner_type="document", owner_id="doc-1", source="second",
        backend="tinymist", auto_refresh=True,
    )

    assert reused is original
    assert len(manager.sessions) == 1
    assert reused.source == "second"
    assert reused.source_revision == 1
    assert reused.backend_name == "tinymist"
    assert reused.auto_refresh is True


def test_create_reuse_normalizes_an_invalid_requested_backend():
    manager = TypstSessionManager()
    original = manager.create(
        owner="alice", owner_type="document", owner_id="doc-1", source="first", backend="typst-cli"
    )

    reused = manager.create(
        owner="alice", owner_type="document", owner_id="doc-1", source="first", backend="unknown"
    )

    assert reused is original
    assert reused.backend_name == "tinymist"


def test_create_reuse_is_isolated_by_owner_and_owner_scope():
    manager = TypstSessionManager()

    alice = manager.create(owner="alice", owner_type="document", owner_id="doc-1", source="a")
    bob = manager.create(owner="bob", owner_type="document", owner_id="doc-1", source="b")
    note = manager.create(owner="alice", owner_type="note", owner_id="doc-1", source="c")
    other_doc = manager.create(owner="alice", owner_type="document", owner_id="doc-2", source="d")

    assert len({alice.id, bob.id, note.id, other_doc.id}) == 4
    assert len(manager.sessions) == 4


def test_delete_removes_owner_scope_reuse_index():
    manager = TypstSessionManager()
    original = manager.create(owner="alice", owner_type="document", owner_id="doc-1", source="first")

    assert manager.delete(original.id, "alice") is True
    replacement = manager.create(owner="alice", owner_type="document", owner_id="doc-1", source="second")

    assert replacement.id != original.id
    assert replacement.source == "second"
    assert replacement.source_revision == 0


def test_anonymous_owner_scopes_are_safe_and_reused_only_when_identical():
    manager = TypstSessionManager()

    first = manager.create(owner="", owner_type="document", owner_id="doc-1", source="first")
    reused = manager.create(owner="", owner_type="document", owner_id="doc-1", source="first")
    other = manager.create(owner="", owner_type="document", owner_id="doc-2", source="first")

    assert reused is first
    assert other.id != first.id


def test_sessions_without_a_complete_owner_scope_are_not_reused():
    manager = TypstSessionManager()

    first = manager.create(owner="alice", owner_type="document", owner_id=None, source="first")
    second = manager.create(owner="alice", owner_type="document", owner_id=None, source="first")

    assert second.id != first.id
