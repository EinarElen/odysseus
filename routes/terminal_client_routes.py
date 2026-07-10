"""Terminal Client API routes."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
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
from src import terminal_client_runs
from src.agent_access import AgentAccess, resolve_agent_access
from src.agent_loop import stream_agent_loop
from src.agent_runtime import resolve_agent_execution_limits
from src.auth_helpers import effective_user
from src.constants import TERMINAL_EVENT_STREAM_MEDIA_TYPE
from src.llm_core import stream_llm_with_fallback
from src.model_context import estimate_tokens
from src.terminal_client_auth import (
    EVENT_RAW_SCOPES,
    EVENT_READ_SCOPES,
    RUN_READ_SCOPES,
    RUN_START_SCOPES,
    RUN_STOP_SCOPES,
    require_terminal_scope,
)
from src.tool_policy import build_effective_tool_policy


class RunStartRequest(BaseModel):
    kind: str = "chat"
    session_id: str | None = None
    message: str = Field(default="")
    endpoint_url: str | None = None
    model: str | None = None
    preset_id: str | None = None


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
    elif not endpoint_url or not model:
        raise HTTPException(400, "Starting a new chat Run requires both endpoint_url and model")

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
    session_id: str,
    sess: Any,
    message: str,
    preset_id: str | None,
) -> AsyncGenerator[str, None]:
    async def _stream() -> AsyncGenerator[str, None]:
        resolve_session_auth(sess, session_id, owner=effective_user(request))
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
            candidates = [(sess.endpoint_url, sess.model, getattr(sess, "headers", {}) or {})]
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

    @router.post("/runs")
    async def start_run(request: Request, payload: RunStartRequest) -> dict[str, Any]:
        require_terminal_scope(request, RUN_START_SCOPES)
        if payload.kind not in {"chat", "agent"}:
            raise HTTPException(400, "Terminal Client run start currently supports kind=chat or kind=agent")
        if payload.kind == "agent":
            _require_agent_runtime(session_manager, chat_handler, chat_processor)
        else:
            _require_chat_runtime(session_manager, chat_handler)
        owner = effective_user(request)
        access = resolve_agent_access(request, owner) if payload.kind == "agent" else None
        if access is not None and not access.agent_allowed:
            raise HTTPException(403, "Agent execution is not permitted for this user")
        session_id, sess = _get_or_create_chat_session(
            request=request,
            session_manager=session_manager,
            payload=payload,
            owner=owner,
        )
        _enforce_chat_privileges(request, sess)
        if payload.kind == "agent":
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
            )
        else:
            stream = _terminal_chat_stream(
                request=request,
                session_manager=session_manager,
                chat_handler=chat_handler,
                session_id=session_id,
                sess=sess,
                message=payload.message,
                preset_id=payload.preset_id,
            )
        return terminal_client_runs.create_run(
            kind=payload.kind,
            session_id=session_id,
            message=payload.message,
            stream=stream,
        )

    @router.get("/runs")
    async def list_runs(request: Request, kind: str | None = None, status: str | None = None) -> dict[str, Any]:
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
        try:
            run = terminal_client_runs.resolve_run(run_id=run_id)
            authorize_events(request, run, include_raw=include_raw)
            payload = await terminal_client_runs.attach_run(run_id=run_id, cursor=cursor)
            return render_events(payload, include_raw=include_raw)
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    @router.post("/runs/{run_id}/stop")
    async def stop_run(request: Request, run_id: str) -> dict[str, Any]:
        try:
            require_terminal_scope(request, RUN_STOP_SCOPES)
            run = terminal_client_runs.resolve_run(run_id=run_id)
            _verify_session_owner(request, run.session_id, session_manager)
            return await terminal_client_runs.stop_run(run_id=run_id)
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    return router
