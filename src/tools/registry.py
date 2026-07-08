from __future__ import annotations

import json
import logging
from collections import namedtuple
from typing import Any, Dict, Iterable, List, Optional

from src.tools.model import (
    ToolContext,
    ToolDefinition,
    ToolExecutionRecord,
    ToolInvocation,
)

logger = logging.getLogger(__name__)

ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])


class ToolRegistry:
    """Canonical Odysseus tool registry.

    Provider and harness adapters submit typed ToolInvocation objects here.
    The current concrete tools still consume their historic text content; that
    detail is now localized to this registry instead of leaking into every
    provider adapter.
    """

    def __init__(self, definitions: Iterable[ToolDefinition]) -> None:
        self._definitions: Dict[str, ToolDefinition] = {}
        self._aliases: Dict[str, str] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ToolDefinition) -> None:
        self._definitions[definition.name] = definition
        self._aliases[definition.name] = definition.name
        for alias in definition.aliases:
            self._aliases[alias] = definition.name

    def get(self, name: str) -> Optional[ToolDefinition]:
        canonical = self._aliases.get(str(name or "").strip())
        return self._definitions.get(canonical) if canonical else None

    def names(self) -> List[str]:
        return sorted(self._definitions)

    def openai_function_schemas(
        self,
        *,
        names: Optional[Iterable[str]] = None,
        strict: bool = False,
    ) -> List[Dict[str, Any]]:
        wanted = {str(n) for n in names or [] if str(n).strip()}
        schemas: List[Dict[str, Any]] = []
        for definition in self._definitions.values():
            if wanted and definition.name not in wanted:
                continue
            if definition.exposure in {"hidden", "legacy_only"}:
                continue
            schemas.append(definition.openai_function_schema(strict=strict))
        return schemas

    def legacy_block_for_invocation(self, invocation: ToolInvocation) -> Optional[ToolBlock]:
        name = self._aliases.get(str(invocation.name or "").strip(), str(invocation.name or "").strip())
        if not name:
            return None
        if invocation.source == "legacy":
            return ToolBlock(name, "" if invocation.arguments is None else str(invocation.arguments))

        arguments = invocation.arguments
        if isinstance(arguments, str):
            arg_text = arguments
        else:
            arg_text = json.dumps(arguments or {}, ensure_ascii=False)
        try:
            from src.agent_tools import function_call_to_tool_block

            return function_call_to_tool_block(name, arg_text)
        except Exception as exc:
            logger.warning("Failed to normalize tool invocation %s: %s", name, exc)
            return None

    async def invoke(self, invocation: ToolInvocation, context: ToolContext) -> ToolExecutionRecord:
        definition = self.get(invocation.name)
        if definition is None and str(invocation.name or "").startswith("mcp__"):
            definition = ToolDefinition(name=str(invocation.name), description="MCP tool")
        if definition is None:
            result = {"error": f"Unknown tool: {invocation.name}", "exit_code": 1}
            return ToolExecutionRecord(invocation, f"{invocation.name}: ERROR", result)

        executor = definition.execute or self._legacy_execute
        return await executor(invocation, context)

    async def _legacy_execute(
        self,
        invocation: ToolInvocation,
        context: ToolContext,
    ) -> ToolExecutionRecord:
        block = self.legacy_block_for_invocation(invocation)
        if block is None:
            result = {"error": f"Could not normalize tool arguments for {invocation.name}", "exit_code": 1}
            return ToolExecutionRecord(invocation, f"{invocation.name}: ERROR", result)
        from src.tool_execution import _execute_legacy_tool_block_impl

        desc, result = await _execute_legacy_tool_block_impl(
            block,
            session_id=context.session_id,
            disabled_tools=context.disabled_tools,
            owner=context.owner,
            progress_cb=context.progress_cb,
            tool_policy=context.tool_policy,
        )
        if not isinstance(result, dict):
            result = {"output": str(result), "exit_code": 0}
        normalized = ToolInvocation(
            name=block.tool_type,
            arguments=block.content,
            call_id=invocation.call_id,
            source=invocation.source,
            raw=invocation.raw,
        )
        return ToolExecutionRecord(normalized, desc, result)


_REGISTRY: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ToolRegistry(_load_builtin_definitions())
    return _REGISTRY


def _load_builtin_definitions() -> List[ToolDefinition]:
    definitions: List[ToolDefinition] = []
    seen: set[str] = set()
    try:
        from src.agent_tools import FUNCTION_TOOL_SCHEMAS
    except Exception as exc:
        logger.warning("Could not load built-in tool schemas: %s", exc)
        FUNCTION_TOOL_SCHEMAS = []
    for schema in FUNCTION_TOOL_SCHEMAS:
        function = schema.get("function") if isinstance(schema, dict) else {}
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        definitions.append(
            ToolDefinition(
                name=name,
                description=str(function.get("description") or ""),
                parameters=function.get("parameters") if isinstance(function.get("parameters"), dict) else {},
                execution_mode=_execution_mode_for(name),
            )
        )
    return definitions


def _execution_mode_for(name: str) -> str:
    if name in {"write_file", "edit_file", "create_document", "update_document", "edit_document", "suggest_document"}:
        return "sequential"
    if name in {"bash", "python", "manage_bg_jobs"}:
        return "exclusive"
    return "parallel"


def native_call_to_invocation(call: Dict[str, Any], *, source: str = "openai") -> ToolInvocation:
    name = str(call.get("name") or "")
    arguments = call.get("arguments", {})
    return ToolInvocation(
        name=name,
        arguments=arguments,
        call_id=call.get("id") or call.get("call_id"),
        source=source,  # type: ignore[arg-type]
        raw=call,
    )


def native_call_to_tool_block(call: Dict[str, Any], *, source: str = "openai") -> Optional[ToolBlock]:
    return get_tool_registry().legacy_block_for_invocation(native_call_to_invocation(call, source=source))
