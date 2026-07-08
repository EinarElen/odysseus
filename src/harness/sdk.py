from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import uuid
from collections import namedtuple
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Iterable, List, Optional, Protocol

from .base import (
    CONTROL_REQUEST_TYPES,
    HarnessCapabilities,
    HarnessControlRequest,
    HarnessControlResult,
    HarnessEvent,
    HarnessSessionRef,
)

logger = logging.getLogger(__name__)


class HarnessSdkError(RuntimeError):
    """Raised when an SDK-hosted harness bridge fails its protocol contract."""


@dataclass(frozen=True)
class HarnessToolDefinition:
    """Serializable tool definition exposed by Odysseus to a harness runtime."""

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    label: Optional[str] = None
    prompt_snippet: Optional[str] = None
    prompt_guidelines: List[str] = field(default_factory=list)
    execution_mode: Optional[str] = None

    @classmethod
    def from_openai_function_schema(cls, schema: Dict[str, Any]) -> "HarnessToolDefinition":
        function = schema.get("function") if isinstance(schema, dict) else {}
        if not isinstance(function, dict):
            function = {}
        name = str(function.get("name") or "").strip()
        if not name:
            raise ValueError("tool schema is missing function.name")
        parameters = function.get("parameters")
        return cls(
            name=name,
            label=str(function.get("label") or name),
            description=str(function.get("description") or ""),
            parameters=parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}},
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "label": self.label or self.name,
            "description": self.description,
            "parameters": dict(self.parameters or {}),
        }
        if self.prompt_snippet:
            out["promptSnippet"] = self.prompt_snippet
        if self.prompt_guidelines:
            out["promptGuidelines"] = list(self.prompt_guidelines)
        if self.execution_mode:
            out["executionMode"] = self.execution_mode
        return out


@dataclass(frozen=True)
class HarnessToolCall:
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessToolResult:
    content: str
    details: Dict[str, Any] = field(default_factory=dict)
    is_error: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "details": dict(self.details or {}),
            "isError": self.is_error,
        }


class HarnessToolBroker(Protocol):
    """Tool broker used by SDK-hosted harnesses to call back into Odysseus."""

    def list_tools(self) -> List[HarnessToolDefinition]:
        ...

    async def execute(
        self,
        call: HarnessToolCall,
        *,
        session_id: Optional[str] = None,
        owner: Optional[str] = None,
        workspace: Optional[str] = None,
        disabled_tools: Optional[set[str]] = None,
        progress_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> HarnessToolResult:
        ...


class HarnessControlBroker(Protocol):
    """Broker for cooperative harness yield points handled by Odysseus."""

    async def handle(
        self,
        request: HarnessControlRequest,
        *,
        session_id: Optional[str] = None,
        owner: Optional[str] = None,
        workspace: Optional[str] = None,
    ) -> HarnessControlResult:
        ...


class OdysseusControlBroker:
    """Default control broker for yield points the stream cannot satisfy inline."""

    async def handle(
        self,
        request: HarnessControlRequest,
        *,
        session_id: Optional[str] = None,
        owner: Optional[str] = None,
        workspace: Optional[str] = None,
    ) -> HarnessControlResult:
        del session_id, owner, workspace
        if request.kind == "policy_check":
            return HarnessControlResult(
                id=request.id,
                kind=request.kind,
                status="allowed",
                data={"reason": "No harness-specific policy denied this request."},
            )
        if request.kind == "state_update":
            return HarnessControlResult(id=request.id, kind=request.kind, status="accepted")
        if request.blocking:
            return HarnessControlResult(
                id=request.id,
                kind=request.kind,
                status="unsupported",
                data={"reason": "This Odysseus stream cannot satisfy blocking harness control requests yet."},
            )
        return HarnessControlResult(id=request.id, kind=request.kind, status="observed")


class OdysseusToolBroker:
    """Expose Odysseus native tools through the generic harness SDK contract."""

    def __init__(self, tool_names: Optional[Iterable[str]] = None) -> None:
        self._tool_names = {str(name) for name in tool_names or [] if str(name).strip()}

    def list_tools(self) -> List[HarnessToolDefinition]:
        from src.agent_tools import FUNCTION_TOOL_SCHEMAS

        tools: List[HarnessToolDefinition] = []
        for schema in FUNCTION_TOOL_SCHEMAS:
            try:
                definition = HarnessToolDefinition.from_openai_function_schema(schema)
            except ValueError:
                continue
            if self._tool_names and definition.name not in self._tool_names:
                continue
            tools.append(definition)
        return tools

    async def execute(
        self,
        call: HarnessToolCall,
        *,
        session_id: Optional[str] = None,
        owner: Optional[str] = None,
        workspace: Optional[str] = None,
        disabled_tools: Optional[set[str]] = None,
        progress_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> HarnessToolResult:
        from src.tool_execution import execute_tool_block

        ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])
        content = json.dumps(call.arguments or {}, ensure_ascii=False)
        desc, result = await execute_tool_block(
            ToolBlock(call.name, content),
            session_id=session_id,
            owner=owner,
            workspace=workspace,
            disabled_tools=disabled_tools,
            progress_cb=progress_cb,
        )
        result = result if isinstance(result, dict) else {"output": str(result)}
        output = result.get("output")
        error = result.get("error")
        text = str(output if output is not None else (error if error is not None else desc))
        exit_code = result.get("exit_code")
        is_error = bool(error) or (isinstance(exit_code, int) and exit_code != 0)
        details = dict(result)
        details.setdefault("description", desc)
        return HarnessToolResult(content=text, details=details, is_error=is_error)


@dataclass
class HarnessBridgeProcess:
    """JSONL bridge process shared by SDK-hosted harness adapters."""

    key: str
    command: List[str]
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    proc: Optional[asyncio.subprocess.Process] = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    pending: List[Dict[str, Any]] = field(default_factory=list)
    reader_task: Optional[asyncio.Task] = None
    stderr_task: Optional[asyncio.Task] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            return
        logger.info("Starting harness SDK bridge for %s: %s cwd=%s", self.key, self.command, self.cwd or os.getcwd())
        self.proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=self.env,
        )
        self.reader_task = asyncio.create_task(self._read_stdout())
        self.stderr_task = asyncio.create_task(self._read_stderr())

    async def send_json(self, payload: Dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None or self.proc.returncode is not None:
            raise HarnessSdkError("harness SDK bridge is not running")
        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"
        self.proc.stdin.write(line.encode("utf-8"))
        await self.proc.stdin.drain()

    async def request(self, command_type: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await self.request_with_events(command_type, payload)

    async def request_with_events(
        self,
        command_type: str,
        payload: Optional[Dict[str, Any]] = None,
        event_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        request_id = f"ody-{uuid.uuid4().hex}"
        await self.send_json({"id": request_id, "type": command_type, **dict(payload or {})})
        while True:
            raw = await self.queue.get()
            if raw.get("type") == "response" and raw.get("id") == request_id:
                if raw.get("success"):
                    data = raw.get("data")
                    return data if isinstance(data, dict) else {}
                raise HarnessSdkError(str(raw.get("error") or f"{command_type} failed"))
            if event_cb is not None:
                await event_cb(raw)
            else:
                self.pending.append(raw)

    async def next_message(self) -> Dict[str, Any]:
        if self.pending:
            return self.pending.pop(0)
        return await self.queue.get()

    async def close(self) -> None:
        for task in (self.reader_task, self.stderr_task):
            if task and not task.done():
                task.cancel()
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()

    async def _read_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                await self.queue.put({"type": "error", "error": "harness SDK bridge exited"})
                return
            try:
                payload = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                logger.debug("Ignoring non-JSON harness SDK stdout: %r", line[:200])
                continue
            if isinstance(payload, dict):
                await self.queue.put(payload)

    async def _read_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                return
            logger.debug("Harness SDK stderr: %s", line.decode("utf-8", errors="replace").rstrip())


class SdkHarnessAdapter:
    """Generic adapter for SDK-hosted harnesses controlled by Odysseus."""

    id: str
    label: str
    capabilities: HarnessCapabilities

    def __init__(
        self,
        *,
        adapter_id: str,
        label: str,
        command: List[str] | str,
        capabilities: HarnessCapabilities,
        env: Optional[Dict[str, str]] = None,
        tool_broker_factory: Optional[Callable[[Dict[str, Any]], HarnessToolBroker]] = None,
        control_broker_factory: Optional[Callable[[Dict[str, Any]], HarnessControlBroker]] = None,
    ) -> None:
        self.id = adapter_id
        self.label = label
        self.bridge_command = shlex.split(command) if isinstance(command, str) else list(command)
        self.capabilities = capabilities
        self.env = dict(env or {})
        self.tool_broker_factory = tool_broker_factory or (lambda _config: OdysseusToolBroker())
        self.control_broker_factory = control_broker_factory or (lambda _config: OdysseusControlBroker())
        self._processes: Dict[str, HarnessBridgeProcess] = {}
        self._refs: Dict[str, HarnessSessionRef] = {}
        self._brokers: Dict[str, HarnessToolBroker] = {}
        self._control_brokers: Dict[str, HarnessControlBroker] = {}

    async def start(
        self,
        config: Dict[str, Any],
        startup_event_cb: Optional[Callable[[HarnessEvent], Awaitable[None]]] = None,
    ) -> HarnessSessionRef:
        session_id = str(config.get("odysseus_session_id") or "")
        if not session_id:
            raise ValueError(f"{self.label} harness requires odysseus_session_id")
        workspace = str(config.get("workspace") or "").strip() or None
        cwd = workspace if workspace and os.path.isdir(workspace) else None
        process = self._processes.get(session_id)
        existing_ref = self._refs.get(session_id)
        force_new_session = bool(config.get("new_session"))
        if process is None or process.proc is None or process.proc.returncode is not None:
            env = os.environ.copy()
            env.update(self.env)
            process = HarnessBridgeProcess(session_id, self.bridge_command, cwd=cwd, env=env)
            await process.start()
            self._processes[session_id] = process
            existing_ref = None
        broker = self.tool_broker_factory(config)
        self._brokers[session_id] = broker
        self._control_brokers[session_id] = self.control_broker_factory(config)
        if existing_ref is not None and not force_new_session:
            return HarnessSessionRef(
                adapter_id=existing_ref.adapter_id,
                odysseus_session_id=existing_ref.odysseus_session_id,
                harness_session_id=existing_ref.harness_session_id,
                workspace=workspace,
                config=dict(config),
            )
        provide_tools = bool(config.get("provide_odysseus_tools") or config.get("mode") == "bridged")
        tools = [tool.to_dict() for tool in broker.list_tools()] if provide_tools else []
        async def _forward_startup_event(raw: Dict[str, Any]) -> None:
            if startup_event_cb is None:
                return
            event = self.normalize_event(raw)
            if event is not None:
                await startup_event_cb(event)

        start_data = await process.request_with_events(
            "start_session",
            {"config": dict(config), "tools": tools},
            event_cb=_forward_startup_event if startup_event_cb is not None else None,
        )
        harness_session_id = str(
            start_data.get("harnessSessionId")
            or start_data.get("harness_session_id")
            or start_data.get("sessionId")
            or start_data.get("session_id")
            or start_data.get("sessionFile")
            or session_id
        )
        ref_config = dict(config)
        session_file = start_data.get("session_file") or start_data.get("sessionFile")
        session_dir = start_data.get("session_dir") or start_data.get("sessionDir")
        if session_file:
            ref_config["session_file"] = session_file
        if session_dir:
            ref_config["session_dir"] = session_dir
        ref = HarnessSessionRef(
            adapter_id=self.id,
            odysseus_session_id=session_id,
            harness_session_id=harness_session_id,
            workspace=workspace,
            config=ref_config,
        )
        self._refs[session_id] = ref
        return ref

    async def send(
        self,
        ref: HarnessSessionRef,
        message: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        reconciliation: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[HarnessEvent]:
        process = self._require_process(ref)
        async with process.lock:
            request_id = f"ody-{uuid.uuid4().hex}"
            await process.send_json({
                "id": request_id,
                "type": "prompt",
                "message": message,
                "attachments": list(attachments or []),
                "reconciliation": dict(reconciliation or {}),
                "config": dict(ref.config or {}),
            })
            saw_prompt_ack = False
            saw_harness_activity = False
            while True:
                try:
                    raw = await asyncio.wait_for(process.next_message(), timeout=90)
                except asyncio.TimeoutError:
                    if not saw_harness_activity:
                        yield HarnessEvent("error", {"message": f"{self.label} did not emit any prompt activity"})
                    else:
                        yield HarnessEvent("error", {"message": f"{self.label} prompt stalled"})
                    return
                if raw.get("type") == "response" and raw.get("id") == request_id:
                    if not raw.get("success"):
                        yield HarnessEvent("error", {"message": raw.get("error") or f"{self.label} prompt failed"})
                        return
                    saw_prompt_ack = True
                    continue
                if raw.get("type") == "tool_call":
                    async for event in self._handle_tool_call(ref, raw):
                        yield event
                    continue
                if raw.get("type") in CONTROL_REQUEST_TYPES:
                    async for event in self._handle_control_request(ref, raw):
                        yield event
                    continue
                event = self.normalize_event(raw)
                if event is None:
                    continue
                saw_harness_activity = True
                yield event
                if event.type == "done" and saw_prompt_ack:
                    return
                if event.type == "final_text" and event.data.get("done") and saw_prompt_ack:
                    return

    async def command(
        self,
        ref: HarnessSessionRef,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        process = self._require_process(ref)
        return await process.request("command", {"command": command, "payload": dict(payload or {})})

    async def close(self, ref: HarnessSessionRef) -> None:
        key = ref.odysseus_session_id
        self._refs.pop(key, None)
        self._brokers.pop(key, None)
        self._control_brokers.pop(key, None)
        process = self._processes.pop(key, None)
        if process:
            await process.close()

    def normalize_event(self, raw: Dict[str, Any]) -> Optional[HarnessEvent]:
        etype = raw.get("type")
        if etype == "error":
            return HarnessEvent("error", {"message": raw.get("error") or "harness SDK bridge error"})
        if etype in {
            "text_delta",
            "thinking_delta",
            "final_text",
            "tool_start",
            "tool_update",
            "tool_end",
            "harness_ui_request",
            "harness_status",
            "control_request",
            "harness_event",
            "done",
        }:
            data = raw.get("data")
            return HarnessEvent(etype, data if isinstance(data, dict) else {})
        if etype == "event":
            event_type = str(raw.get("event_type") or raw.get("eventType") or "harness_event")
            data = raw.get("data")
            if event_type == "done":
                return HarnessEvent("done", data if isinstance(data, dict) else {})
            return HarnessEvent(event_type, data if isinstance(data, dict) else {"event": raw})
        return None

    def control_request_from_raw(self, raw: Dict[str, Any]) -> HarnessControlRequest:
        request_id = str(raw.get("id") or raw.get("requestId") or raw.get("request_id") or uuid.uuid4().hex)
        raw_type = str(raw.get("type") or "control_yield")
        kind = str(raw.get("kind") or raw.get("requestKind") or raw_type).strip()
        kind = {
            "approval_request": "approval",
            "user_input_request": "user_input",
            "ui_request": "ui",
        }.get(kind, kind)
        data = raw.get("data")
        if not isinstance(data, dict):
            data = {
                key: value
                for key, value in raw.items()
                if key not in {"id", "requestId", "request_id", "type", "kind", "requestKind", "blocking"}
            }
        return HarnessControlRequest(
            id=request_id,
            kind=kind or "control",
            data=data,
            blocking=bool(raw.get("blocking") or raw.get("requiresResult")),
        )

    async def _handle_control_request(
        self,
        ref: HarnessSessionRef,
        raw: Dict[str, Any],
    ) -> AsyncIterator[HarnessEvent]:
        request = self.control_request_from_raw(raw)
        yield request.to_event()
        broker = self._control_brokers.get(ref.odysseus_session_id)
        if broker is None:
            result = HarnessControlResult(
                id=request.id,
                kind=request.kind,
                status="unsupported",
                data={"reason": "Control broker is not available."},
            )
        else:
            try:
                result = await broker.handle(
                    request,
                    session_id=ref.odysseus_session_id,
                    owner=ref.config.get("owner"),
                    workspace=ref.workspace,
                )
            except Exception as exc:
                logger.exception("Harness SDK control request failed")
                result = HarnessControlResult(
                    id=request.id,
                    kind=request.kind,
                    status="error",
                    data={"reason": str(exc)},
                )
        await self._send_control_result(ref, raw, result)
        yield HarnessEvent("control_result", result.to_dict())

    async def _handle_tool_call(
        self,
        ref: HarnessSessionRef,
        raw: Dict[str, Any],
    ) -> AsyncIterator[HarnessEvent]:
        call_id = str(raw.get("toolCallId") or raw.get("tool_call_id") or raw.get("id") or uuid.uuid4().hex)
        name = str(raw.get("name") or raw.get("tool") or "").strip()
        arguments = raw.get("arguments") or raw.get("args") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        broker = self._brokers.get(ref.odysseus_session_id)
        if not broker:
            await self._send_tool_result(ref, raw, HarnessToolResult("Tool broker is not available", is_error=True))
            return

        yield HarnessEvent("tool_start", {"id": call_id, "name": name, "input": arguments, "external": True})

        async def progress_cb(update: Dict[str, Any]) -> None:
            del update

        try:
            result = await broker.execute(
                HarnessToolCall(name=name, arguments=arguments, id=call_id, raw=raw),
                session_id=ref.odysseus_session_id,
                owner=ref.config.get("owner"),
                workspace=ref.workspace,
                disabled_tools=set(ref.config.get("disabled_tools") or []),
                progress_cb=progress_cb,
            )
        except Exception as exc:
            logger.exception("Harness SDK tool call failed")
            result = HarnessToolResult(str(exc), is_error=True)
        await self._send_tool_result(ref, raw, result)
        yield HarnessEvent(
            "tool_end",
            {
                "id": call_id,
                "name": name,
                "result": result.to_dict(),
                "is_error": result.is_error,
                "external": True,
            },
        )

    async def _send_tool_result(self, ref: HarnessSessionRef, raw: Dict[str, Any], result: HarnessToolResult) -> None:
        process = self._require_process(ref)
        response_id = raw.get("id") or raw.get("requestId") or raw.get("request_id")
        await process.send_json({
            "id": response_id,
            "type": "tool_result",
            "toolCallId": raw.get("toolCallId") or raw.get("tool_call_id"),
            "result": result.to_dict(),
        })

    async def _send_control_result(
        self,
        ref: HarnessSessionRef,
        raw: Dict[str, Any],
        result: HarnessControlResult,
    ) -> None:
        process = self._require_process(ref)
        response_id = raw.get("id") or raw.get("requestId") or raw.get("request_id") or result.id
        await process.send_json({
            "id": response_id,
            "type": "control_result",
            "result": result.to_dict(),
        })

    async def _send_bridge_event(self, ref: HarnessSessionRef, payload: Dict[str, Any]) -> None:
        process = self._require_process(ref)
        await process.send_json(payload)

    def _require_process(self, ref: HarnessSessionRef) -> HarnessBridgeProcess:
        key = ref.odysseus_session_id
        process = self._processes.get(key)
        if process is None or process.proc is None or process.proc.returncode is not None:
            raise HarnessSdkError("harness SDK bridge is not running")
        return process
