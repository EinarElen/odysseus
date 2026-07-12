"""Terminal Client API routes."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.models import ChatMessage
from routes.chat_helpers import (
    _enforce_chat_privileges,
    build_chat_context,
    clean_thinking_for_save,
    resolve_session_auth,
    save_assistant_response,
)
from routes.session_routes import _verify_session_owner
from src import agent_runs, execution_service, terminal_client_runs
from src.agent_access import AgentAccess, resolve_agent_access
from src.agent_loop import stream_agent_loop
from src.agent_runtime import resolve_agent_execution_limits
from src.auth_helpers import effective_user
from src.constants import TERMINAL_EVENT_STREAM_MEDIA_TYPE
from src.endpoint_resolver import resolve_chat_fallback_candidates, resolve_endpoint_for_model
from src.harness import get_harness_adapter
from src.llm_core import stream_llm_with_fallback
from src.model_context import estimate_tokens
from src.terminal_client_auth import (
    CONTENT_READ_SCOPES,
    CONTENT_WRITE_SCOPES,
    DOCUMENT_READ_SCOPES,
    DOCUMENT_WRITE_SCOPES,
    EVENT_RAW_SCOPES,
    EVENT_READ_SCOPES,
    HARNESS_CONTROL_SCOPES,
    RUN_READ_SCOPES,
    RUN_START_SCOPES,
    RUN_STOP_SCOPES,
    SESSION_READ_SCOPES,
    USAGE_EXPORT_SCOPES,
    USAGE_READ_SCOPES,
    require_terminal_scope,
)
from routes.document_helpers import DocumentCreate, DocumentUpdate
from routes.usage_routes import _time
from src.subscription_usage import subscription_usage_store
from src.usage_observability import usage_store
from src.tool_policy import build_effective_tool_policy


class RunStartRequest(BaseModel):
    kind: str = "chat"
    session_id: str | None = None
    message: str = Field(default="")
    endpoint_url: str | None = None
    model: str | None = None
    preset_id: str | None = None
    harness_adapter_id: str | None = None
    harness_session_id: str | None = None
    harness_mode: str = "observe"
    workspace: str | None = None
    # For agent runs: the document the AI should edit. When omitted, an agent
    # run falls back to the session's most recent active document, so a client
    # can keep editing the same doc across turns by echoing its doc_id.
    active_doc_id: str | None = None
    # Interactive planning. plan_mode=true proposes a plan (emits plan_update
    # events and ends the turn); a follow-up run with approved_plan=<checklist>
    # executes it. Mirrors the web plan/approve loop.
    plan_mode: bool = False
    approved_plan: str | None = None
    # Upload ids (from POST /api/upload) to attach to an agent run — multimodal
    # input and "document from file" flows.
    attachments: list[str] | None = None


# --- Typed response models for the fixed-shape endpoints (OpenAPI / client gen).
# All fields are declared and permissive so response_model never drops or
# rejects a value; verified against live responses with a before/after diff.

class CapabilitiesEvents(BaseModel):
    envelope: list[str] = Field(default_factory=list)
    kinds: dict[str, Any] = Field(default_factory=dict)


class CapabilitiesOut(BaseModel):
    owner: str | None = None
    auth_mode: str
    scopes: list[str] = Field(default_factory=list)
    event_schema: str
    run_kinds: list[str] = Field(default_factory=list)
    run_inputs: list[str] = Field(default_factory=list)
    terminal_domains: list[str] = Field(default_factory=list)
    reachable_via_owner_token: list[str] = Field(default_factory=list)
    events: CapabilitiesEvents = Field(default_factory=CapabilitiesEvents)


class ModelInfo(BaseModel):
    model: str
    endpoint_id: str
    endpoint_url: str
    endpoint_name: str


class ModelsOut(BaseModel):
    models: list[ModelInfo] = Field(default_factory=list)
    default_model: str | None = None


class DocumentOut(BaseModel):
    id: str
    session_id: str | None = None
    title: str | None = None
    language: str | None = None
    current_content: str | None = None
    version_count: int | None = None
    is_active: bool | None = None
    archived: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None
    source_email_uid: str | None = None
    source_email_folder: str | None = None
    source_email_account_id: str | None = None
    source_email_message_id: str | None = None


class DocumentSummary(BaseModel):
    id: str
    title: str | None = None
    language: str | None = None
    version_count: int | None = None
    session_id: str | None = None
    is_active: bool | None = None
    archived: bool | None = None
    updated_at: str | None = None


class DocumentsListOut(BaseModel):
    documents: list[DocumentSummary] = Field(default_factory=list)


class SessionSummaryOut(BaseModel):
    session_id: str
    name: str | None = None
    model: str | None = None
    archived: bool | None = None
    message_count: int | None = None
    created_at: Any = None
    updated_at: Any = None


class SessionsListOut(BaseModel):
    sessions: list[SessionSummaryOut] = Field(default_factory=list)


class MessageOut(BaseModel):
    role: str
    content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HistoryOut(BaseModel):
    session: SessionSummaryOut
    history: list[MessageOut] = Field(default_factory=list)
    runs: list[dict[str, Any]] = Field(default_factory=list)


class BootstrapOut(BaseModel):
    """One-call init for a new frontend."""
    capabilities: CapabilitiesOut
    sessions: list[SessionSummaryOut] = Field(default_factory=list)
    models: list[ModelInfo] = Field(default_factory=list)
    default_model: str | None = None


# Machine-readable payload schema per event kind, published in /capabilities so
# a client (or codegen) doesn't have to reverse-engineer the loose payloads.
# "?" marks an optional field; the envelope fields are always present.
EVENT_KINDS_DOC: dict[str, Any] = {
    "envelope": [
        "schema", "id", "seq", "time", "session_id", "run_id",
        "source", "kind", "level", "summary", "payload",
    ],
    "kinds": {
        "message.delta": {"delta": "str", "thinking": "bool?"},
        "run.status": {"status": "str"},
        "metrics": {"data": "object (usage/timing)"},
        "message_saved": {"id": "str"},
        "model_info": {"model": "str"},
        "fallback": {"answered_by": "str", "reason": "str"},
        "tool_start": {"tool": "str", "summary": "str?"},
        "tool_progress": {"tool": "str", "summary": "str?"},
        "tool_output": {"tool": "str", "summary": "str?"},
        "agent_step": {"round": "int"},
        "doc_stream_open": {"title": "str", "language": "str"},
        "doc_stream_delta": {"content": "str (cumulative content so far)"},
        "doc_update": {"doc_id": "str", "content": "str", "version": "int", "title": "str", "language": "str"},
        "doc_suggestions": {"doc_id": "str", "suggestions": "list"},
        "plan_update": {"plan": "object"},
        "ask_user": {"question": "str", "options": "list", "multi": "bool?"},
        "web_sources": {"data": "list"},
        "rag_sources": {"data": "list"},
        "memories_used": {"data": "list"},
        "error": {"message": "str?", "text": "str?", "status": "int?"},
        "heartbeat": {},
    },
}


def _require_chat_runtime(session_manager: Any, chat_handler: Any) -> None:
    if session_manager is None or chat_handler is None:
        raise HTTPException(503, "Terminal Client chat Runs require the Odysseus chat runtime")


def _require_agent_runtime(session_manager: Any, chat_handler: Any, chat_processor: Any) -> None:
    _require_chat_runtime(session_manager, chat_handler)
    if chat_processor is None:
        raise HTTPException(503, "Terminal Client agent Runs require the Odysseus agent runtime")


def _get_or_create_chat_session(
    *,
    request: Request,
    session_manager: Any,
    payload: RunStartRequest,
    owner: str | None,
) -> tuple[str, Any]:
    if payload.session_id:
        _verify_session_owner(request, payload.session_id, session_manager)
        try:
            session = session_manager.get_session(payload.session_id)
        except KeyError:
            raise HTTPException(404, f"Session {payload.session_id} not found") from None
        if not getattr(session, "model", "").strip() or not getattr(session, "endpoint_url", "").strip():
            raise HTTPException(400, "No model selected for this chat")
        return payload.session_id, session

    endpoint_url = (payload.endpoint_url or "").strip()
    model = (payload.model or "").strip()
    resolved_headers: dict[str, Any] = {}
    if not endpoint_url and not model:
        from src.endpoint_resolver import resolve_endpoint

        resolved_url, resolved_model, headers = resolve_endpoint("default", owner=owner)
        endpoint_url = (resolved_url or "").strip()
        model = (resolved_model or "").strip()
        resolved_headers = dict(headers or {})
        if not endpoint_url or not model:
            raise HTTPException(400, "No default chat model is configured; pass endpoint_url and model")
    elif model and not endpoint_url:
        resolved = resolve_endpoint_for_model(model, owner=owner)
        if resolved is None:
            raise HTTPException(400, f"No enabled endpoint for model '{model}'; pass endpoint_url explicitly")
        endpoint_url, model, headers = resolved
        resolved_headers = dict(headers or {})
    elif endpoint_url and not model:
        raise HTTPException(400, "Starting a new chat Run with endpoint_url requires model")

    session_id = f"ses_{uuid.uuid4().hex[:16]}"
    try:
        session = session_manager.create_session(
            session_id=session_id,
            name=f"ody-term {payload.kind}",
            endpoint_url=endpoint_url,
            model=model,
            owner=owner,
        )
    except TypeError:
        session = session_manager.create_session(session_id, f"ody-term {payload.kind}", endpoint_url, model)
    if resolved_headers:
        session.headers = resolved_headers
    return session_id, session


def _session_messages(sess: Any) -> list[dict[str, Any]]:
    if hasattr(sess, "get_context_messages"):
        messages = sess.get_context_messages()
    else:
        messages = []
        for message in getattr(sess, "history", []) or []:
            if hasattr(message, "to_dict"):
                messages.append(message.to_dict())
            elif isinstance(message, dict):
                messages.append(message)
    return [message for message in messages if isinstance(message, dict)]


def _terminal_chat_stream(
    *,
    request: Request,
    session_manager: Any,
    chat_handler: Any,
    chat_processor: Any = None,
    session_id: str,
    sess: Any,
    message: str,
    preset_id: str | None,
    attachments: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    async def _stream() -> AsyncGenerator[str, None]:
        resolve_session_auth(sess, session_id, owner=effective_user(request))
        if attachments:
            # Attachments need preprocessing (inline file content), which the
            # lite path below doesn't do — build the context like the web/agent
            # paths so chat-kind runs honour them instead of dropping them.
            ctx = await build_chat_context(
                sess, request, chat_handler, chat_processor,
                message, session_id, preset_id=preset_id, att_ids=attachments,
            )
            messages = ctx.messages
        else:
            sess.add_message(ChatMessage("user", message))
            if hasattr(chat_handler, "update_session_name_if_needed"):
                chat_handler.update_session_name_if_needed(sess, message)
            messages = _session_messages(sess)
        full_response = ""
        thinking_response = ""
        metrics: dict[str, Any] | None = None
        started = time.time()
        requested_model = str(getattr(sess, "model", "") or "")
        actual_model = None

        try:
            # Primary is the session's own (url, model, headers); append the
            # owner's configured default-model fallback chain so a pre-content
            # failure (e.g. a 404 on the primary model) falls back instead of
            # surfacing to the client. Mirrors the web /api/chat_stream path
            # and the terminal agent path, which both apply this chain.
            candidates = [(sess.endpoint_url, sess.model, getattr(sess, "headers", {}) or {})]
            candidates += resolve_chat_fallback_candidates(owner=effective_user(request))
            async for chunk in stream_llm_with_fallback(
                candidates,
                messages,
                temperature=None,
                max_tokens=None,
                prompt_type=preset_id,
                tools=None,
                session_id=session_id,
                provider_options=getattr(sess, "provider_options", None) or {},
            ):
                if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                    try:
                        data = json.loads(chunk[6:])
                    except json.JSONDecodeError:
                        yield chunk
                        continue
                    if "delta" in data:
                        if data.get("thinking"):
                            thinking_response += str(data["delta"])
                        else:
                            full_response += str(data["delta"])
                        yield chunk
                    elif data.get("type") == "model_actual":
                        actual_model = str(data.get("model") or actual_model or "")
                        data["requested_model"] = requested_model
                        yield f"data: {json.dumps(data)}\n\n"
                    elif data.get("type") in {"usage", "metrics"}:
                        raw_metrics = data.get("data", {})
                        metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
                        metrics["requested_model"] = requested_model
                        metrics["model"] = metrics.get("model") or actual_model or requested_model
                        metrics.setdefault("response_time", round(time.time() - started, 2))
                        yield f"data: {json.dumps({'type': 'metrics', 'data': metrics})}\n\n"
                    else:
                        yield chunk
                elif chunk == "data: [DONE]\n\n":
                    if not metrics and full_response:
                        elapsed = time.time() - started
                        output_tokens = len(full_response) // 4
                        metrics = {
                            "response_time": round(elapsed, 2),
                            "input_tokens": estimate_tokens(messages),
                            "output_tokens": output_tokens,
                            "tokens_per_second": round(output_tokens / elapsed, 2) if elapsed > 0 else 0,
                            "model": actual_model or requested_model,
                            "requested_model": requested_model,
                            "usage_source": "estimated",
                        }
                        yield f"data: {json.dumps({'type': 'metrics', 'data': metrics})}\n\n"
                    if full_response:
                        metrics_to_save = dict(metrics or {})
                        if thinking_response.strip() and not metrics_to_save.get("thinking"):
                            metrics_to_save["thinking"] = thinking_response.strip()
                        saved_id = save_assistant_response(
                            sess,
                            session_manager,
                            session_id,
                            full_response,
                            metrics_to_save,
                        )
                        if saved_id:
                            yield f"data: {json.dumps({'type': 'message_saved', 'id': saved_id})}\n\n"
                    yield chunk
                else:
                    yield chunk
        except (GeneratorExit, asyncio.CancelledError):
            if full_response:
                content, metadata = clean_thinking_for_save(
                    full_response,
                    {"stopped": True, "model": actual_model or requested_model, "requested_model": requested_model},
                )
                sess.add_message(ChatMessage("assistant", content, metadata=metadata))
                session_manager.save_sessions()
            raise

    return _stream()


def _resolve_active_document(owner: str | None, session_id: str, active_doc_id: str | None) -> Any:
    """Load the document an agent run should edit: the explicit id when given,
    else the session's most recent active document. Detached so it stays usable
    after the lookup session closes (the agent only reads its content/title)."""
    from core.database import Document as DBDocument, SessionLocal
    from routes.document_helpers import _owner_session_filter

    db = SessionLocal()
    try:
        doc = None
        if active_doc_id:
            query = db.query(DBDocument).filter(DBDocument.id == active_doc_id)
            doc = _owner_session_filter(query, owner).first()
        if doc is None and session_id:
            query = db.query(DBDocument).filter(
                DBDocument.session_id == session_id,
                DBDocument.is_active == True,  # noqa: E712 - SQLAlchemy column truthiness
            )
            doc = _owner_session_filter(query, owner).order_by(DBDocument.updated_at.desc()).first()
        if doc is not None:
            # Force-load the attributes the agent reads, then detach.
            _ = (doc.id, doc.current_content, doc.title, doc.language, doc.session_id, doc.is_active)
            db.expunge(doc)
        return doc
    except Exception:
        return None
    finally:
        db.close()


def _terminal_agent_stream(
    *,
    request: Request,
    session_manager: Any,
    chat_handler: Any,
    chat_processor: Any,
    session_id: str,
    sess: Any,
    message: str,
    preset_id: str | None,
    owner: str | None,
    access: AgentAccess,
    workspace: str | None,
    active_doc_id: str | None = None,
    plan_mode: bool = False,
    approved_plan: str | None = None,
    attachments: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    async def _stream() -> AsyncGenerator[str, None]:
        resolve_session_auth(sess, session_id, owner=owner)
        ctx = await build_chat_context(
            sess,
            request,
            chat_handler,
            chat_processor,
            message,
            session_id,
            preset_id=preset_id,
            att_ids=attachments or None,
            agent_mode=True,
        )
        disabled_tools = set(access.disabled_tools)
        tool_policy = build_effective_tool_policy(
            disabled_tools=disabled_tools,
            last_user_message=message,
        )

        limits = resolve_agent_execution_limits()

        full_response = ""
        thinking_response = ""
        metrics: dict[str, Any] = {}
        requested_model = str(getattr(sess, "model", "") or "")
        try:
            async for chunk in stream_agent_loop(
                sess.endpoint_url,
                sess.model,
                ctx.messages,
                headers=getattr(sess, "headers", {}) or {},
                temperature=ctx.preset.temperature if ctx.preset.temperature is not None else 0.3,
                max_tokens=ctx.preset.max_tokens if ctx.preset.max_tokens is not None else 4096,
                prompt_type=preset_id,
                max_tool_calls=limits.max_tool_calls,
                max_rounds=limits.max_rounds,
                context_length=ctx.context_length,
                session_id=session_id,
                disabled_tools=disabled_tools or None,
                tool_policy=tool_policy,
                owner=owner,
                uploaded_files=ctx.uploaded_files,
                fallbacks=resolve_chat_fallback_candidates(owner=owner),
                workspace=workspace,
                active_document=_resolve_active_document(owner, session_id, active_doc_id),
                plan_mode=plan_mode,
                approved_plan=approved_plan or None,
                provider_options=getattr(sess, "provider_options", None) or {},
            ):
                if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                    try:
                        data = json.loads(chunk[6:])
                    except json.JSONDecodeError:
                        yield chunk
                        continue
                    if "delta" in data:
                        if data.get("thinking"):
                            thinking_response += str(data["delta"])
                        else:
                            full_response += str(data["delta"])
                    elif data.get("type") == "metrics":
                        raw_metrics = data.get("data", {})
                        metrics = dict(raw_metrics) if isinstance(raw_metrics, dict) else {}
                        metrics["requested_model"] = requested_model
                        metrics["model"] = metrics.get("model") or requested_model
                        chunk = f"data: {json.dumps({'type': 'metrics', 'data': metrics})}\n\n"
                    yield chunk
                elif chunk == "data: [DONE]\n\n":
                    if full_response or metrics.get("tool_events"):
                        response_to_save = full_response or "Done."
                        if thinking_response.strip() and not metrics.get("thinking"):
                            metrics["thinking"] = thinking_response.strip()
                        saved_id = save_assistant_response(
                            sess,
                            session_manager,
                            session_id,
                            response_to_save,
                            metrics,
                            character_name=ctx.preset.character_name or "",
                            rag_sources=ctx.rag_sources,
                            used_memories=ctx.used_memories,
                        )
                        if saved_id:
                            yield f"data: {json.dumps({'type': 'message_saved', 'id': saved_id})}\n\n"
                    yield chunk
                else:
                    yield chunk
        except (GeneratorExit, asyncio.CancelledError):
            if full_response:
                content, metadata = clean_thinking_for_save(
                    full_response,
                    {"stopped": True, "model": requested_model, "requested_model": requested_model},
                )
                sess.add_message(ChatMessage("assistant", content, metadata=metadata))
                session_manager.save_sessions()
            raise

    return _stream()


def _terminal_harness_stream(
    *,
    request: Request,
    session_manager: Any,
    chat_handler: Any,
    chat_processor: Any,
    session_id: str,
    sess: Any,
    message: str,
    preset_id: str | None,
    owner: str | None,
    access: AgentAccess,
    adapter: Any,
    harness_session_id: str | None,
    harness_mode: str,
    workspace: str | None,
) -> AsyncGenerator[str, None]:
    async def _stream() -> AsyncGenerator[str, None]:
        resolve_session_auth(sess, session_id, owner=owner)
        ctx = await build_chat_context(
            sess,
            request,
            chat_handler,
            chat_processor,
            message,
            session_id,
            preset_id=preset_id,
            agent_mode=True,
        )
        config = {
            "id": adapter.id,
            "mode": harness_mode,
            "odysseus_session_id": session_id,
            "requested_session_id": harness_session_id,
            "workspace": workspace,
            "owner": owner,
            "disabled_tools": sorted(access.disabled_tools),
        }
        ref = None
        full_response = ""
        thinking_response = ""
        started = time.time()
        try:
            ref = await adapter.start(config)
            yield f"data: {json.dumps({'type': 'harness_start', 'harness_adapter_id': adapter.id, 'harness_session_id': ref.harness_session_id, 'workspace': ref.workspace, 'mode': harness_mode})}\n\n"
            from src.harness.context import reconcile_prompt_for_harness

            reconciliation = reconcile_prompt_for_harness(
                messages=ctx.messages,
                current_message=message,
                harness_id=adapter.id,
            )
            async for event in adapter.send(
                ref,
                message,
                attachments=ctx.uploaded_files,
                reconciliation=reconciliation,
            ):
                event_type = str(event.type)
                data = dict(event.data or {})
                if event_type == "text_delta":
                    delta = str(data.get("text") or "")
                    if delta:
                        full_response += delta
                        yield f"data: {json.dumps({'delta': delta})}\n\n"
                elif event_type == "final_text":
                    text = str(data.get("text") or "")
                    if text and not full_response:
                        full_response = text
                        yield f"data: {json.dumps({'delta': text})}\n\n"
                elif event_type == "thinking_delta":
                    delta = str(data.get("text") or "")
                    if delta:
                        thinking_response += delta
                        yield f"data: {json.dumps({'delta': delta, 'thinking': True})}\n\n"
                elif event_type == "error":
                    yield f"event: error\ndata: {json.dumps({'status': 502, 'text': data.get('message') or 'Harness request failed'})}\n\n"
                    return
                elif event_type == "done":
                    break
                else:
                    yield f"data: {json.dumps({'type': event_type, 'data': data})}\n\n"

            elapsed = time.time() - started
            metrics = {
                "response_time": round(elapsed, 2),
                "input_tokens": estimate_tokens(ctx.messages),
                "output_tokens": len(full_response) // 4,
                "tokens_per_second": round((len(full_response) // 4) / elapsed, 2) if elapsed > 0 else 0,
                "model": str(getattr(sess, "model", "") or ""),
                "harness": adapter.id,
                "harness_session_id": ref.harness_session_id,
                "usage_source": "estimated",
            }
            if thinking_response.strip():
                metrics["thinking"] = thinking_response.strip()
            yield f"data: {json.dumps({'type': 'metrics', 'data': metrics})}\n\n"
            if full_response:
                saved_id = save_assistant_response(
                    sess,
                    session_manager,
                    session_id,
                    full_response,
                    metrics,
                    character_name=ctx.preset.character_name or "",
                    rag_sources=ctx.rag_sources,
                    used_memories=ctx.used_memories,
                )
                if saved_id:
                    yield f"data: {json.dumps({'type': 'message_saved', 'id': saved_id})}\n\n"
            yield "data: [DONE]\n\n"
        except (GeneratorExit, asyncio.CancelledError):
            if ref is not None:
                await adapter.close(ref)
            raise

    return _stream()


def _ambiguous_run_error(session_id: str, exc: ValueError) -> HTTPException:
    choices = [
        terminal_client_runs.run_summary(run)
        for run in exc.args[0]
        if isinstance(run, terminal_client_runs.TerminalRun)
    ]
    return HTTPException(409, {"code": "ambiguous_run", "session_id": session_id, "choices": choices})


def setup_terminal_client_routes(
    session_manager=None,
    chat_handler=None,
    chat_processor=None,
    upload_handler=None,
    **_deps: Any,
) -> APIRouter:
    router = APIRouter(prefix="/api/terminal", tags=["terminal_client"])

    def authorize_events(request: Request, run: terminal_client_runs.TerminalRun, *, include_raw: bool) -> None:
        require_terminal_scope(request, EVENT_RAW_SCOPES if include_raw else EVENT_READ_SCOPES)
        _verify_session_owner(request, run.session_id, session_manager)

    def render_events(payload: dict[str, Any], *, include_raw: bool) -> dict[str, Any]:
        rendered = dict(payload)
        rendered["events"] = [dict(event) for event in payload["events"]]
        if not include_raw:
            for event in rendered["events"]:
                event.pop("raw", None)
        return rendered

    def session_summary(session: Any) -> dict[str, Any]:
        history = _session_messages(session)
        return {
            "session_id": str(getattr(session, "id", "")),
            "name": str(getattr(session, "name", "") or ""),
            "model": str(getattr(session, "model", "") or ""),
            "archived": bool(getattr(session, "archived", False)),
            "message_count": len(history),
            "created_at": getattr(session, "created_at", None),
            "updated_at": getattr(session, "updated_at", None),
        }

    def owned_session(request: Request, session_id: str) -> Any:
        require_terminal_scope(request, SESSION_READ_SCOPES)
        if session_manager is None:
            raise HTTPException(503, "Terminal Client Session reads require the Odysseus session runtime")
        _verify_session_owner(request, session_id, session_manager)
        try:
            return session_manager.get_session(session_id)
        except KeyError:
            raise HTTPException(404, f"Session {session_id} not found") from None

    def usage_owner(request: Request, *, export: bool = False) -> str:
        return require_terminal_scope(request, USAGE_EXPORT_SCOPES if export else USAGE_READ_SCOPES) or effective_user(request) or "local"

    def usage_filters(
        provider: str | None, model: str | None, kind: str | None, status: str | None,
        tool: str | None, usage_source: str | None, cache_status: str | None,
    ) -> dict[str, str]:
        return {key: value for key, value in {
            "provider": provider, "model": model, "kind": kind, "status": status,
            "tool": tool, "usage_source": usage_source, "cache_status": cache_status,
        }.items() if value is not None}

    @router.get("/usage/summary")
    async def usage_summary(
        request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None,
        provider: str | None = None, model: str | None = None, kind: str | None = None,
        status: str | None = None, tool: str | None = None, usage_source: str | None = None,
        cache_status: str | None = None,
    ) -> dict[str, Any]:
        owner = usage_owner(request)
        return usage_store.query_summary(owner=owner, start=_time(from_), end=_time(to), **usage_filters(provider, model, kind, status, tool, usage_source, cache_status))

    @router.get("/usage/breakdown")
    async def usage_breakdown(
        request: Request, group_by: str = "model", from_: str | None = Query(None, alias="from"), to: str | None = None,
        provider: str | None = None, model: str | None = None, kind: str | None = None,
        status: str | None = None, tool: str | None = None, usage_source: str | None = None,
        cache_status: str | None = None,
    ) -> dict[str, Any]:
        owner = usage_owner(request)
        try:
            return usage_store.query_breakdown(owner=owner, group_by=group_by, start=_time(from_), end=_time(to), **usage_filters(provider, model, kind, status, tool, usage_source, cache_status))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/usage/timeseries")
    async def usage_timeseries(
        request: Request, bucket: str = "day", timezone: str = "UTC",
        from_: str | None = Query(None, alias="from"), to: str | None = None,
        provider: str | None = None, model: str | None = None, kind: str | None = None,
        status: str | None = None, tool: str | None = None, usage_source: str | None = None,
        cache_status: str | None = None,
    ) -> dict[str, Any]:
        owner = usage_owner(request)
        try:
            return usage_store.query_timeseries(owner=owner, start=_time(from_), end=_time(to), bucket=bucket, timezone_name=timezone, **usage_filters(provider, model, kind, status, tool, usage_source, cache_status))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/usage/runs")
    async def usage_runs(
        request: Request, limit: int = 100, offset: int = 0, from_: str | None = Query(None, alias="from"), to: str | None = None,
        provider: str | None = None, model: str | None = None, kind: str | None = None,
        status: str | None = None, tool: str | None = None, usage_source: str | None = None,
        cache_status: str | None = None,
    ) -> dict[str, Any]:
        owner = usage_owner(request)
        return usage_store.list_runs(owner=owner, start=_time(from_), end=_time(to), limit=max(1, min(limit, 500)), offset=max(0, offset), **usage_filters(provider, model, kind, status, tool, usage_source, cache_status))

    @router.get("/usage/runs/{run_id}")
    async def usage_run(request: Request, run_id: str) -> dict[str, Any]:
        result = usage_store.get_run(owner=usage_owner(request), run_id=run_id)
        if result is None:
            raise HTTPException(404, "Usage Run not found")
        return result

    @router.get("/usage/cache")
    async def usage_cache(
        request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None,
        provider: str | None = None, model: str | None = None, kind: str | None = None,
        status: str | None = None, tool: str | None = None, usage_source: str | None = None,
        cache_status: str | None = None,
    ) -> dict[str, Any]:
        owner = usage_owner(request)
        return usage_store.query_summary(owner=owner, start=_time(from_), end=_time(to), **usage_filters(provider, model, kind, status, tool, usage_source, cache_status))

    @router.get("/usage/subscription")
    async def usage_subscription(request: Request) -> dict[str, Any]:
        owner = usage_owner(request)
        return await asyncio.to_thread(subscription_usage_store.refresh_if_stale, owner=owner)

    @router.get("/usage/export")
    async def usage_export(
        request: Request, format: str = "jsonl", from_: str | None = Query(None, alias="from"), to: str | None = None,
        provider: str | None = None, model: str | None = None, kind: str | None = None,
        status: str | None = None, tool: str | None = None, usage_source: str | None = None,
        cache_status: str | None = None,
    ) -> StreamingResponse:
        owner = usage_owner(request, export=True)
        try:
            chunks = usage_store.export(owner=owner, format=format, start=_time(from_), end=_time(to), **usage_filters(provider, model, kind, status, tool, usage_source, cache_status))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        media = "application/x-ndjson" if format == "jsonl" else "text/csv"
        return StreamingResponse(chunks, media_type=media, headers={"Content-Disposition": f'attachment; filename="odysseus-usage.{format}"'})

    @router.get("/usage/live")
    async def usage_live(
        request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None,
        provider: str | None = None, model: str | None = None, kind: str | None = None,
        status: str | None = None, tool: str | None = None, usage_source: str | None = None,
        cache_status: str | None = None,
    ) -> StreamingResponse:
        owner = usage_owner(request)
        filters = usage_filters(provider, model, kind, status, tool, usage_source, cache_status)

        async def events() -> AsyncGenerator[str, None]:
            fingerprints: dict[str, str] = {}
            while not await request.is_disconnected():
                page = usage_store.list_runs(owner=owner, start=_time(from_), end=_time(to), limit=50, **filters)
                current: dict[str, str] = {}
                for run in reversed(page["runs"]):
                    fingerprint = json.dumps(run, sort_keys=True, separators=(",", ":"), default=str)
                    current[run["id"]] = fingerprint
                    if fingerprints.get(run["id"]) != fingerprint:
                        yield json.dumps({"schema": "ody.usage.event.v1", "schema_version": 1, "type": "usage.run", "data": run}) + "\n"
                fingerprints = current
                await asyncio.sleep(2)

        return StreamingResponse(events(), media_type=TERMINAL_EVENT_STREAM_MEDIA_TYPE)

    @router.get("/capabilities", response_model=CapabilitiesOut)
    async def capabilities(request: Request) -> dict[str, Any]:
        is_token = bool(getattr(request.state, "api_token", False))
        return {
            "owner": effective_user(request),
            "auth_mode": "token" if is_token else "session",
            "scopes": list(getattr(request.state, "api_token_scopes", []) or []) if is_token else ["*"],
            "event_schema": "ody.event.v1",
            "run_kinds": ["chat", "agent", "harness"],
            "run_inputs": [
                "message", "session_id", "model", "endpoint_url", "preset_id",
                "active_doc_id", "plan_mode", "approved_plan", "attachments",
                "workspace", "harness_adapter_id", "harness_session_id", "harness_mode",
            ],
            "terminal_domains": [
                "runs", "events", "sessions", "usage", "models",
                "documents (crud)", "notes (read)", "tasks (read)", "capabilities",
            ],
            # Owner-attributed tokens reach the full web route surface directly.
            "reachable_via_owner_token": [
                "email", "calendar", "memory", "skills", "presets", "gallery",
                "research", "compare", "cookbook", "search", "uploads", "settings",
                "notes (write)", "tasks (write)", "harness /command",
            ],
            "events": EVENT_KINDS_DOC,
        }

    @router.get("/notes")
    async def list_notes_terminal(request: Request, include_archived: bool = False, limit: int = 200) -> dict[str, Any]:
        require_terminal_scope(request, SESSION_READ_SCOPES)
        owner = effective_user(request)
        from core.database import Note, SessionLocal
        from routes.note_routes import _note_to_dict
        db = SessionLocal()
        try:
            query = db.query(Note).filter(Note.owner == owner)
            if not include_archived:
                query = query.filter(Note.archived == False)  # noqa: E712
            rows = (
                query.order_by(Note.pinned.desc(), Note.sort_order.asc(), Note.updated_at.desc())
                .limit(max(1, min(limit, 1000)))
                .all()
            )
            return {"notes": [_note_to_dict(n) for n in rows]}
        finally:
            db.close()

    @router.get("/notes/{note_id}")
    async def get_note_terminal(request: Request, note_id: str) -> dict[str, Any]:
        require_terminal_scope(request, SESSION_READ_SCOPES)
        owner = effective_user(request)
        from core.database import Note, SessionLocal
        from routes.note_routes import _note_to_dict
        db = SessionLocal()
        try:
            note = db.query(Note).filter(Note.id == note_id).first()
            if not note or note.owner != owner:
                raise HTTPException(404, "Note not found")
            return _note_to_dict(note)
        finally:
            db.close()

    @router.post("/notes")
    async def create_note_terminal(request: Request, body: dict) -> dict[str, Any]:
        require_terminal_scope(request, CONTENT_WRITE_SCOPES)
        owner = effective_user(request)
        import json as _json, uuid as _uuid
        from core.database import Note, SessionLocal
        from routes.note_routes import _note_to_dict
        db = SessionLocal()
        try:
            note = Note(
                id=str(_uuid.uuid4()),
                owner=owner,
                title=body.get("title"),
                content=body.get("content"),
                items=_json.dumps(body["items"]) if body.get("items") is not None else None,
                note_type=body.get("note_type"),
                color=body.get("color"),
                label=body.get("label"),
                pinned=bool(body.get("pinned", False)),
                due_date=body.get("due_date"),
                source=body.get("source") or "terminal",
                session_id=body.get("session_id"),
                repeat=body.get("repeat") or "none",
                sort_order=int(body.get("sort_order") or 0),
            )
            db.add(note)
            db.commit()
            db.refresh(note)
            return _note_to_dict(note)
        finally:
            db.close()

    @router.put("/notes/{note_id}")
    async def update_note_terminal(request: Request, note_id: str, body: dict) -> dict[str, Any]:
        require_terminal_scope(request, CONTENT_WRITE_SCOPES)
        owner = effective_user(request)
        import json as _json
        from sqlalchemy.orm.attributes import flag_modified
        from core.database import Note, SessionLocal
        from routes.note_routes import _note_to_dict
        db = SessionLocal()
        try:
            note = db.query(Note).filter(Note.id == note_id).first()
            if not note or note.owner != owner:
                raise HTTPException(404, "Note not found")
            for field in ("title", "content", "note_type", "color", "label", "pinned",
                          "archived", "due_date", "image_url", "repeat", "sort_order",
                          "agent_session_id"):
                if field in body and body[field] is not None:
                    setattr(note, field, body[field])
            if body.get("items") is not None:
                note.items = _json.dumps(body["items"])
                flag_modified(note, "items")
            db.commit()
            db.refresh(note)
            return _note_to_dict(note)
        finally:
            db.close()

    @router.delete("/notes/{note_id}")
    async def delete_note_terminal(request: Request, note_id: str) -> dict[str, Any]:
        require_terminal_scope(request, CONTENT_WRITE_SCOPES)
        owner = effective_user(request)
        from core.database import Note, SessionLocal
        db = SessionLocal()
        try:
            note = db.query(Note).filter(Note.id == note_id).first()
            if not note or note.owner != owner:
                raise HTTPException(404, "Note not found")
            db.delete(note)
            db.commit()
            return {"ok": True, "id": note_id}
        finally:
            db.close()

    @router.get("/tasks")
    async def list_tasks_terminal(request: Request, limit: int = 200) -> dict[str, Any]:
        require_terminal_scope(request, SESSION_READ_SCOPES)
        owner = effective_user(request)
        from core.database import ScheduledTask, SessionLocal
        from routes.task_routes import _task_to_dict
        db = SessionLocal()
        try:
            rows = (
                db.query(ScheduledTask)
                .filter(ScheduledTask.owner == owner)
                .order_by(ScheduledTask.next_run.asc())
                .limit(max(1, min(limit, 1000)))
                .all()
            )
            return {"tasks": [_task_to_dict(t) for t in rows]}
        finally:
            db.close()

    @router.get("/tasks/{task_id}")
    async def get_task_terminal(request: Request, task_id: str) -> dict[str, Any]:
        require_terminal_scope(request, SESSION_READ_SCOPES)
        owner = effective_user(request)
        from core.database import ScheduledTask, SessionLocal
        from routes.task_routes import _task_to_dict
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task or task.owner != owner:
                raise HTTPException(404, "Task not found")
            return _task_to_dict(task, include_last_run_result=True)
        finally:
            db.close()

    @router.post("/tasks")
    async def create_task_terminal(request: Request, body: dict) -> dict[str, Any]:
        require_terminal_scope(request, CONTENT_WRITE_SCOPES)
        owner = effective_user(request)
        import uuid as _uuid
        from datetime import datetime as _dt
        from core.database import ScheduledTask, SessionLocal
        from routes.task_routes import _task_to_dict
        from src.task_scheduler import compute_next_run
        task_type = body.get("task_type") or "llm"
        trigger_type = body.get("trigger_type") or "schedule"
        if task_type in ("llm", "research") and not body.get("prompt"):
            raise HTTPException(400, "prompt is required for llm/research tasks")
        if task_type == "action":
            # Shell-executing actions stay owner-gated on the web side; keep the
            # token contract to safe (non-shell) task types.
            raise HTTPException(400, "action tasks aren't creatable over the token contract")
        next_run = None
        if trigger_type == "schedule":
            sched_date = None
            if body.get("scheduled_date"):
                try:
                    sched_date = _dt.fromisoformat(str(body["scheduled_date"]).replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    raise HTTPException(400, "Invalid scheduled_date")
            next_run = compute_next_run(
                body.get("schedule") or "once", body.get("scheduled_time"),
                body.get("scheduled_day"), sched_date,
                cron_expression=body.get("cron_expression"),
            )
        db = SessionLocal()
        try:
            task = ScheduledTask(
                id=str(_uuid.uuid4()),
                owner=owner,
                name=body.get("name") or "Untitled Task",
                prompt=body.get("prompt"),
                task_type=task_type,
                trigger_type=trigger_type,
                trigger_event=body.get("trigger_event"),
                trigger_count=body.get("trigger_count"),
                schedule=body.get("schedule"),
                scheduled_time=body.get("scheduled_time"),
                scheduled_day=body.get("scheduled_day"),
                cron_expression=body.get("cron_expression"),
                next_run=next_run,
                status="active",
                model=body.get("model"),
                output_target=body.get("output_target") or "none",
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            result = _task_to_dict(task)
        finally:
            db.close()
        return result

    @router.put("/tasks/{task_id}")
    async def update_task_terminal(request: Request, task_id: str, body: dict) -> dict[str, Any]:
        require_terminal_scope(request, CONTENT_WRITE_SCOPES)
        owner = effective_user(request)
        from core.database import ScheduledTask, SessionLocal
        from routes.task_routes import _task_to_dict
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task or task.owner != owner:
                raise HTTPException(404, "Task not found")
            for field in ("name", "prompt", "status", "schedule", "scheduled_time",
                          "scheduled_day", "cron_expression", "trigger_event",
                          "trigger_count", "model", "output_target"):
                if field in body and body[field] is not None:
                    setattr(task, field, body[field])
            db.commit()
            db.refresh(task)
            result = _task_to_dict(task)
        finally:
            db.close()
        return result

    @router.delete("/tasks/{task_id}")
    async def delete_task_terminal(request: Request, task_id: str) -> dict[str, Any]:
        require_terminal_scope(request, CONTENT_WRITE_SCOPES)
        owner = effective_user(request)
        from core.database import ScheduledTask, SessionLocal
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task or task.owner != owner:
                raise HTTPException(404, "Task not found")
            db.delete(task)
            db.commit()
            return {"ok": True, "id": task_id}
        finally:
            db.close()

    @router.post("/tasks/{task_id}/run")
    async def run_task_terminal(request: Request, task_id: str, force: bool = False) -> dict[str, Any]:
        require_terminal_scope(request, CONTENT_WRITE_SCOPES)
        owner = effective_user(request)
        from core.database import ScheduledTask, SessionLocal
        from src.event_bus import get_task_scheduler
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task or task.owner != owner:
                raise HTTPException(404, "Task not found")
        finally:
            db.close()
        scheduler = get_task_scheduler()
        if not scheduler:
            raise HTTPException(503, "Task scheduler not available")
        started = await scheduler.run_task_now(task_id, force=force)
        if not started:
            raise HTTPException(409, "Task is already running")
        return {"ok": True, "id": task_id}

    @router.get("/models", response_model=ModelsOut)
    async def list_models(request: Request) -> dict[str, Any]:
        require_terminal_scope(request, SESSION_READ_SCOPES)
        import json as _json
        from core.database import ModelEndpoint, SessionLocal
        try:
            from src.settings import load_settings
            default_model = load_settings().get("default_model")
        except Exception:
            default_model = None
        db = SessionLocal()
        try:
            out: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for ep in db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all():  # noqa: E712
                if (ep.model_type or "llm") != "llm":
                    continue
                try:
                    hidden = set(_json.loads(ep.hidden_models or "[]"))
                except Exception:
                    hidden = set()
                models: list[str] = []
                for field in (ep.pinned_models, ep.cached_models):
                    try:
                        models.extend(_json.loads(field or "[]"))
                    except Exception:
                        pass
                for model in models:
                    if model in hidden or (ep.id, model) in seen:
                        continue
                    seen.add((ep.id, model))
                    out.append({
                        "model": model,
                        "endpoint_id": ep.id,
                        "endpoint_url": ep.base_url,
                        "endpoint_name": ep.name,
                    })
            return {"models": out, "default_model": default_model}
        finally:
            db.close()

    # ---- Documents: token-scoped CRUD for the writing surface ----

    @router.get("/documents", response_model=DocumentsListOut)
    async def list_documents(request: Request, limit: int = 100, include_archived: bool = False) -> dict[str, Any]:
        require_terminal_scope(request, DOCUMENT_READ_SCOPES)
        owner = effective_user(request)
        from core.database import Document as DBDocument, SessionLocal
        from routes.document_helpers import _doc_to_dict, _owner_session_filter
        db = SessionLocal()
        try:
            query = db.query(DBDocument)
            if not include_archived:
                query = query.filter(DBDocument.archived == False)  # noqa: E712
            rows = (
                _owner_session_filter(query, owner)
                .order_by(DBDocument.updated_at.desc())
                .limit(max(1, min(limit, 500)))
                .all()
            )
            keep = ("id", "title", "language", "version_count", "session_id", "is_active", "archived", "updated_at")
            return {"documents": [{k: _doc_to_dict(doc)[k] for k in keep} for doc in rows]}
        finally:
            db.close()

    @router.get("/documents/{doc_id}", response_model=DocumentOut)
    async def get_terminal_document(request: Request, doc_id: str) -> dict[str, Any]:
        require_terminal_scope(request, DOCUMENT_READ_SCOPES)
        owner = effective_user(request)
        from core.database import Document as DBDocument, SessionLocal
        from routes.document_helpers import _doc_to_dict, _verify_doc_owner
        db = SessionLocal()
        try:
            doc = db.query(DBDocument).filter(DBDocument.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, owner)
            return _doc_to_dict(doc)
        finally:
            db.close()

    @router.post("/documents", response_model=DocumentOut)
    async def create_terminal_document(request: Request, payload: DocumentCreate) -> dict[str, Any]:
        require_terminal_scope(request, DOCUMENT_WRITE_SCOPES)
        owner = effective_user(request)
        from core.database import SessionLocal
        from routes.document_helpers import create_document_record
        db = SessionLocal()
        try:
            return create_document_record(
                db,
                owner=owner,
                title=payload.title,
                content=payload.content,
                language=payload.language,
                session_id=payload.session_id,
            )
        finally:
            db.close()

    @router.put("/documents/{doc_id}", response_model=DocumentOut)
    async def update_terminal_document(request: Request, doc_id: str, payload: DocumentUpdate) -> dict[str, Any]:
        require_terminal_scope(request, DOCUMENT_WRITE_SCOPES)
        owner = effective_user(request)
        from core.database import Document as DBDocument, SessionLocal
        from routes.document_helpers import (
            apply_document_update, coerce_document_content, _verify_doc_owner,
            _assert_pdf_marker_upload_owned,
        )
        db = SessionLocal()
        try:
            doc = db.query(DBDocument).filter(DBDocument.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, owner)
            content = coerce_document_content(doc, payload.content)
            # Same guard the web update path runs: reject content whose
            # pdf_source marker points at another user's upload.
            _assert_pdf_marker_upload_owned(request, content, owner, upload_handler)
            return apply_document_update(
                db, doc,
                content=content,
                summary=payload.summary,
                force_version=payload.force_version,
            )
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, f"Failed to update document: {exc}")
        finally:
            db.close()

    @router.delete("/documents/{doc_id}")
    async def delete_terminal_document(request: Request, doc_id: str) -> dict[str, str]:
        require_terminal_scope(request, DOCUMENT_WRITE_SCOPES)
        owner = effective_user(request)
        from core.database import Document as DBDocument, SessionLocal
        from routes.document_helpers import _verify_doc_owner
        db = SessionLocal()
        try:
            doc = db.query(DBDocument).filter(DBDocument.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, owner)
            doc.is_active = False
            try:
                from src.agent_tools.document_tools import clear_active_document
                clear_active_document(doc_id)
            except Exception:
                pass
            db.commit()
            return {"status": "deleted", "id": doc_id}
        finally:
            db.close()

    @router.post("/documents/{doc_id}/archive")
    async def archive_terminal_document(request: Request, doc_id: str, archived: bool = True) -> dict[str, Any]:
        require_terminal_scope(request, DOCUMENT_WRITE_SCOPES)
        owner = effective_user(request)
        from core.database import Document as DBDocument, SessionLocal
        from routes.document_helpers import _verify_doc_owner
        db = SessionLocal()
        try:
            doc = db.query(DBDocument).filter(DBDocument.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, owner)
            doc.archived = bool(archived)
            db.commit()
            return {"ok": True, "id": doc_id, "archived": doc.archived}
        finally:
            db.close()

    @router.get("/documents/{doc_id}/versions")
    async def list_terminal_document_versions(request: Request, doc_id: str) -> dict[str, Any]:
        require_terminal_scope(request, DOCUMENT_READ_SCOPES)
        owner = effective_user(request)
        from core.database import Document as DBDocument, DocumentVersion, SessionLocal
        from routes.document_helpers import _verify_doc_owner
        db = SessionLocal()
        try:
            doc = db.query(DBDocument).filter(DBDocument.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, owner)
            versions = (
                db.query(DocumentVersion)
                .filter(DocumentVersion.document_id == doc_id)
                .order_by(DocumentVersion.version_number.desc())
                .all()
            )
            return {"versions": [{
                "id": v.id,
                "version_number": v.version_number,
                "content": v.content,
                "summary": v.summary,
                "source": v.source,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            } for v in versions]}
        finally:
            db.close()

    @router.post("/documents/{doc_id}/restore/{num}", response_model=DocumentOut)
    async def restore_terminal_document_version(request: Request, doc_id: str, num: int) -> dict[str, Any]:
        require_terminal_scope(request, DOCUMENT_WRITE_SCOPES)
        owner = effective_user(request)
        import uuid as _uuid
        from core.database import Document as DBDocument, DocumentVersion, SessionLocal
        from routes.document_helpers import _doc_to_dict, _verify_doc_owner
        db = SessionLocal()
        try:
            doc = db.query(DBDocument).filter(DBDocument.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, owner)
            old = (
                db.query(DocumentVersion)
                .filter(DocumentVersion.document_id == doc_id, DocumentVersion.version_number == num)
                .first()
            )
            if not old:
                raise HTTPException(404, "Version not found")
            new_num = (doc.version_count or 1) + 1
            db.add(DocumentVersion(
                id=str(_uuid.uuid4()),
                document_id=doc_id,
                version_number=new_num,
                content=old.content,
                summary=f"Restored from v{num}",
                source="user",
            ))
            doc.current_content = old.content
            doc.version_count = new_num
            db.commit()
            db.refresh(doc)
            return _doc_to_dict(doc)
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, str(exc))
        finally:
            db.close()

    @router.get("/sessions", response_model=SessionsListOut)
    async def list_sessions(request: Request) -> dict[str, Any]:
        require_terminal_scope(request, SESSION_READ_SCOPES)
        owner = effective_user(request)
        if session_manager is None:
            raise HTTPException(503, "Terminal Client Session reads require the Odysseus session runtime")
        visible = session_manager.get_sessions_for_user(owner)
        sessions = list(visible.values()) if isinstance(visible, dict) else list(visible)
        return {"sessions": [session_summary(session) for session in sessions]}

    @router.get("/bootstrap", response_model=BootstrapOut)
    async def bootstrap(request: Request) -> dict[str, Any]:
        # One request for a new frontend to render its initial UI: composes the
        # existing typed handlers so there is no duplicated logic.
        caps = await capabilities(request)
        sess = await list_sessions(request)
        mods = await list_models(request)
        return {
            "capabilities": caps,
            "sessions": sess.get("sessions", []),
            "models": mods.get("models", []),
            "default_model": mods.get("default_model"),
        }

    @router.get("/sessions/{session_id}")
    async def show_session(request: Request, session_id: str) -> dict[str, Any]:
        session = owned_session(request, session_id)
        runs = [run for run in terminal_client_runs.list_runs() if run.get("session_id") == session_id]
        return {"session": session_summary(session), "runs": runs}

    @router.get("/sessions/{session_id}/history", response_model=HistoryOut)
    async def session_history(request: Request, session_id: str) -> dict[str, Any]:
        session = owned_session(request, session_id)
        runs = [run for run in terminal_client_runs.list_runs() if run.get("session_id") == session_id]
        return {"session": session_summary(session), "history": _session_messages(session), "runs": runs}

    @router.get("/sessions/{session_id}/export")
    async def export_session(request: Request, session_id: str, format: str = "md") -> dict[str, Any]:
        session = owned_session(request, session_id)
        history = _session_messages(session)
        if format == "json":
            content = json.dumps({"session": session_summary(session), "messages": history}, indent=2, ensure_ascii=False)
            media_type = "application/json"
        elif format == "txt":
            content = "\n\n".join(f"[{str(message.get('role') or '').upper()}]\n{message.get('content') or ''}" for message in history)
            media_type = "text/plain"
        elif format == "md":
            content = "\n\n".join(f"## {str(message.get('role') or '').upper()}\n\n{message.get('content') or ''}" for message in history)
            media_type = "text/markdown"
        else:
            raise HTTPException(400, "Session export format must be md, txt, or json")
        return {
            "session": session_summary(session),
            "format": format,
            "media_type": media_type,
            "content": content,
        }

    @router.post("/runs")
    async def start_run(request: Request, payload: RunStartRequest) -> dict[str, Any]:
        if execution_service.should_proxy():
            return await execution_service.proxy(request, request.url.path, streaming=False)
        require_terminal_scope(request, RUN_START_SCOPES)
        if agent_runs.is_draining():
            raise HTTPException(
                503,
                "Server restart is waiting for active AI runs to finish; retry after reconnect",
                headers={"Retry-After": "2"},
            )
        if payload.kind not in {"chat", "agent", "harness"}:
            raise HTTPException(400, "Terminal Client run start supports kind=chat, kind=agent, or kind=harness")
        if payload.kind in {"agent", "harness"}:
            _require_agent_runtime(session_manager, chat_handler, chat_processor)
        else:
            _require_chat_runtime(session_manager, chat_handler)
        if payload.kind == "harness":
            require_terminal_scope(request, HARNESS_CONTROL_SCOPES)
        owner = effective_user(request)
        access = resolve_agent_access(request, owner) if payload.kind in {"agent", "harness"} else None
        if access is not None and not access.agent_allowed:
            raise HTTPException(403, "Agent execution is not permitted for this user")
        adapter = None
        if payload.kind == "harness":
            adapter_id = str(payload.harness_adapter_id or "").strip()
            if not adapter_id:
                raise HTTPException(400, {"code": "missing_harness_adapter", "supported": False})
            try:
                adapter = get_harness_adapter(adapter_id)
            except KeyError:
                raise HTTPException(
                    400,
                    {"code": "unknown_harness_adapter", "adapter": adapter_id, "supported": False},
                ) from None
            supported_modes = list(getattr(adapter.capabilities, "modes", []) or [])
            if supported_modes and payload.harness_mode not in supported_modes:
                raise HTTPException(
                    409,
                    {
                        "code": "unsupported_harness_mode",
                        "adapter": adapter_id,
                        "mode": payload.harness_mode,
                        "supported_modes": supported_modes,
                    },
                )
        session_id, sess = _get_or_create_chat_session(
            request=request,
            session_manager=session_manager,
            payload=payload,
            owner=owner,
        )
        _enforce_chat_privileges(request, sess)
        if payload.kind == "harness":
            assert access is not None and adapter is not None
            harness_options = {
                "id": adapter.id,
                "mode": payload.harness_mode,
                "requested_session_id": payload.harness_session_id,
                "workspace": payload.workspace,
            }
            provider_options = dict(getattr(sess, "provider_options", None) or {})
            provider_options["harness"] = harness_options
            sess.provider_options = provider_options
            session_manager.save_sessions()
            stream = _terminal_harness_stream(
                request=request,
                session_manager=session_manager,
                chat_handler=chat_handler,
                chat_processor=chat_processor,
                session_id=session_id,
                sess=sess,
                message=payload.message,
                preset_id=payload.preset_id,
                owner=owner,
                access=access,
                adapter=adapter,
                harness_session_id=payload.harness_session_id,
                harness_mode=payload.harness_mode,
                workspace=payload.workspace,
            )
        elif payload.kind == "agent":
            assert access is not None
            stream = _terminal_agent_stream(
                request=request,
                session_manager=session_manager,
                chat_handler=chat_handler,
                chat_processor=chat_processor,
                session_id=session_id,
                sess=sess,
                message=payload.message,
                preset_id=payload.preset_id,
                owner=owner,
                access=access,
                workspace=payload.workspace,
                active_doc_id=payload.active_doc_id,
                plan_mode=payload.plan_mode,
                approved_plan=payload.approved_plan,
                attachments=payload.attachments,
            )
        else:
            stream = _terminal_chat_stream(
                request=request,
                session_manager=session_manager,
                chat_handler=chat_handler,
                chat_processor=chat_processor,
                session_id=session_id,
                sess=sess,
                message=payload.message,
                preset_id=payload.preset_id,
                attachments=payload.attachments,
            )
        try:
            return terminal_client_runs.create_run(
                kind=payload.kind,
                session_id=session_id,
                message=payload.message,
                stream=stream,
                harness_adapter_id=adapter.id if adapter is not None else None,
                harness_session_id=None,
            )
        except agent_runs.RunDrainingError as exc:
            await stream.aclose()
            raise HTTPException(503, str(exc), headers={"Retry-After": "2"})

    @router.get("/runs")
    async def list_runs(request: Request, kind: str | None = None, status: str | None = None) -> dict[str, Any]:
        if execution_service.should_proxy():
            return await execution_service.proxy(request, request.url.path, streaming=False)
        require_terminal_scope(request, RUN_READ_SCOPES)
        visible = []
        for run in terminal_client_runs.list_runs(kind=kind, status=status):
            try:
                _verify_session_owner(request, str(run["session_id"]), session_manager)
            except HTTPException as exc:
                if exc.status_code == 404:
                    continue
                raise
            visible.append(run)
        return {"runs": visible}

    @router.get("/events")
    async def query_events(
        request: Request,
        run_id: str | None = None,
        session_id: str | None = None,
        cursor: int | None = None,
        source: str | None = None,
        kind: str | None = None,
        level: str | None = None,
        limit: int = terminal_client_runs.DEFAULT_EVENT_QUERY_LIMIT,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        if execution_service.should_proxy():
            return await execution_service.proxy(request, request.url.path, streaming=False)
        if not run_id and not session_id:
            raise HTTPException(400, "Terminal event query requires run_id or session_id")
        try:
            run = terminal_client_runs.resolve_run(run_id=run_id, session_id=session_id)
            authorize_events(request, run, include_raw=include_raw)
            payload = await terminal_client_runs.query_events(
                run_id=run_id,
                session_id=session_id,
                cursor=cursor,
                source=source,
                kind=kind,
                level=level,
                limit=limit,
            )
            return render_events(payload, include_raw=include_raw)
        except ValueError as exc:
            raise _ambiguous_run_error(str(session_id), exc) from None
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    @router.get("/events/stream")
    async def stream_events(
        request: Request,
        run_id: str | None = None,
        session_id: str | None = None,
        cursor: int | None = None,
        source: str | None = None,
        kind: str | None = None,
        level: str | None = None,
        batch_limit: int = terminal_client_runs.DEFAULT_EVENT_QUERY_LIMIT,
        include_raw: bool = False,
    ) -> StreamingResponse:
        if execution_service.should_proxy():
            return await execution_service.proxy(request, request.url.path, streaming=True)
        if not run_id and not session_id:
            raise HTTPException(400, "Terminal event stream requires run_id or session_id")
        try:
            run = terminal_client_runs.resolve_run(run_id=run_id, session_id=session_id)
            authorize_events(request, run, include_raw=include_raw)
        except ValueError as exc:
            raise _ambiguous_run_error(str(session_id), exc) from None
        except KeyError:
            raise HTTPException(404, "Run not found") from None

        async def jsonl_stream() -> AsyncGenerator[str, None]:
            async for event in terminal_client_runs.stream_events(
                run_id=run.run_id,
                cursor=cursor,
                source=source,
                kind=kind,
                level=level,
                batch_limit=batch_limit,
            ):
                rendered = dict(event)
                if not include_raw:
                    rendered.pop("raw", None)
                yield json.dumps(rendered, sort_keys=True, separators=(",", ":")) + "\n"

        return StreamingResponse(jsonl_stream(), media_type=TERMINAL_EVENT_STREAM_MEDIA_TYPE)

    @router.get("/runs/by-session/{session_id}")
    async def run_status_by_session(request: Request, session_id: str) -> dict[str, Any]:
        if execution_service.should_proxy():
            return await execution_service.proxy(request, request.url.path, streaming=False)
        try:
            require_terminal_scope(request, RUN_READ_SCOPES)
            run = terminal_client_runs.resolve_run(session_id=session_id)
            _verify_session_owner(request, run.session_id, session_manager)
        except ValueError as exc:
            raise _ambiguous_run_error(session_id, exc) from None
        except KeyError:
            raise HTTPException(404, "Run not found") from None
        return {"run": terminal_client_runs.run_summary(run)}

    @router.get("/runs/by-session/{session_id}/events")
    async def run_events_by_session(
        request: Request,
        session_id: str,
        cursor: int | None = None,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        if execution_service.should_proxy():
            return await execution_service.proxy(request, request.url.path, streaming=False)
        try:
            run = terminal_client_runs.resolve_run(session_id=session_id)
            authorize_events(request, run, include_raw=include_raw)
            payload = await terminal_client_runs.attach_run(run_id=run.run_id, cursor=cursor)
            return render_events(payload, include_raw=include_raw)
        except ValueError as exc:
            raise _ambiguous_run_error(session_id, exc) from None
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    @router.post("/runs/by-session/{session_id}/stop")
    async def stop_run_by_session(request: Request, session_id: str) -> dict[str, Any]:
        if execution_service.should_proxy():
            return await execution_service.proxy(request, request.url.path, streaming=False)
        try:
            require_terminal_scope(request, RUN_STOP_SCOPES)
            run = terminal_client_runs.resolve_run(session_id=session_id)
            _verify_session_owner(request, run.session_id, session_manager)
            return await terminal_client_runs.stop_run(run_id=run.run_id)
        except ValueError as exc:
            raise _ambiguous_run_error(session_id, exc) from None
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    @router.get("/runs/{run_id}")
    async def run_status(request: Request, run_id: str) -> dict[str, Any]:
        if execution_service.should_proxy():
            return await execution_service.proxy(request, request.url.path, streaming=False)
        try:
            require_terminal_scope(request, RUN_READ_SCOPES)
            run = terminal_client_runs.resolve_run(run_id=run_id)
            _verify_session_owner(request, run.session_id, session_manager)
        except KeyError:
            raise HTTPException(404, "Run not found") from None
        return {"run": terminal_client_runs.run_summary(run)}

    @router.get("/runs/{run_id}/events")
    async def run_events(
        request: Request,
        run_id: str,
        cursor: int | None = None,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        if execution_service.should_proxy():
            return await execution_service.proxy(request, request.url.path, streaming=False)
        try:
            run = terminal_client_runs.resolve_run(run_id=run_id)
            authorize_events(request, run, include_raw=include_raw)
            payload = await terminal_client_runs.attach_run(run_id=run_id, cursor=cursor)
            return render_events(payload, include_raw=include_raw)
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    @router.post("/runs/{run_id}/stop")
    async def stop_run(request: Request, run_id: str) -> dict[str, Any]:
        if execution_service.should_proxy():
            return await execution_service.proxy(request, request.url.path, streaming=False)
        try:
            require_terminal_scope(request, RUN_STOP_SCOPES)
            run = terminal_client_runs.resolve_run(run_id=run_id)
            _verify_session_owner(request, run.session_id, session_manager)
            return await terminal_client_runs.stop_run(run_id=run_id)
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    return router
