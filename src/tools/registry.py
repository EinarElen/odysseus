from __future__ import annotations

import json
import logging
from collections import namedtuple
from typing import Any, Dict, Iterable, List, Optional

from src.tool_security import BUILTIN_EMAIL_TOOLS
from src.tools.model import (
    ToolContext,
    ToolDefinition,
    ToolExecutionRecord,
    ToolInvocation,
)

logger = logging.getLogger(__name__)

TextToolAction = namedtuple("TextToolAction", ["tool_type", "content"])

_REQUIRED_NATIVE_TOOL_ARGS = {
    "web_search": ("query", "queries"),
    "web_fetch": ("url",),
    "read_file": ("path",),
    "write_file": ("path",),
    "edit_file": ("path",),
}

_TOOL_NAME_ALIASES = {
    "shell": "bash",
    "terminal": "bash",
    "command": "bash",
    "execute": "bash",
    "run": "bash",
    "code": "python",
    "search": "web_search",
    "websearch": "web_search",
    "google_search": "web_search",
    "google_search_retrieval": "web_search",
    "google_search_grounding": "web_search",
    "webfetch": "web_fetch",
    "fetch_url": "web_fetch",
    "fetch": "web_fetch",
    "read": "read_file",
    "cat": "read_file",
    "write": "write_file",
    "save": "write_file",
    "document": "update_document",
    "edit": "edit_document",
    "search_conversations": "search_chats",
    "find_chat": "search_chats",
    "ask_model": "chat_with_model",
    "chat_model": "chat_with_model",
    "new_session": "create_session",
    "message_session": "send_to_session",
    "chain": "pipeline",
    "session_control": "manage_session",
    "memory": "manage_memory",
    "tasks": "manage_tasks",
    "schedule": "manage_tasks",
    "models": "list_models",
    "available_models": "list_models",
    "ui": "ui_control",
    "control": "ui_control",
    "api": "api_call",
    "integration": "api_call",
    "teacher": "ask_teacher",
    "skills": "manage_skills",
    "skill": "manage_skills",
    "suggest": "suggest_document",
    "review_document": "suggest_document",
    "endpoints": "manage_endpoints",
    "mcp_servers": "manage_mcp",
    "webhooks": "manage_webhooks",
    "tokens": "manage_tokens",
    "documents": "manage_documents",
    "list_research": "manage_research",
    "read_research": "manage_research",
    "open_research": "manage_research",
    "delete_research": "manage_research",
    "settings": "manage_settings",
    "preferences": "manage_settings",
    "notes": "manage_notes",
    "todo": "manage_notes",
    "todos": "manage_notes",
    "bg_jobs": "manage_bg_jobs",
    "background_jobs": "manage_bg_jobs",
}


class ToolRegistry:
    """Canonical Odysseus tool registry.

    Provider and harness adapters submit typed ToolInvocation objects here.
    Text/fenced tool calls are handled by a separate adapter and are not part
    of provider-native tool exposure.
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

    def canonical_name(self, name: str) -> str:
        raw = str(name or "").strip()
        return self._aliases.get(raw) or _TOOL_NAME_ALIASES.get(raw, raw)

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._definitions.get(self.canonical_name(name))

    def names(self) -> List[str]:
        return sorted(self._definitions)

    def openai_function_schemas(
        self,
        *,
        names: Optional[Iterable[str]] = None,
        strict: bool = False,
    ) -> List[Dict[str, Any]]:
        wanted = {self.canonical_name(n) for n in names or [] if str(n).strip()}
        schemas: List[Dict[str, Any]] = []
        for definition in self._definitions.values():
            if wanted and definition.name not in wanted:
                continue
            if definition.exposure in {"hidden", "internal"}:
                continue
            schemas.append(definition.openai_function_schema(strict=strict))
        return schemas

    async def invoke(self, invocation: ToolInvocation, context: ToolContext) -> ToolExecutionRecord:
        name = self.canonical_name(invocation.name)
        definition = self.get(name)
        if definition is None and name.startswith("mcp__"):
            definition = ToolDefinition(name=name, description="MCP tool", exposure="internal")
        if definition is None:
            result = {"error": f"Unknown tool: {invocation.name}", "exit_code": 1}
            return ToolExecutionRecord(invocation, f"{invocation.name}: ERROR", result)

        executor = definition.execute or self._execute_builtin
        return await executor(
            ToolInvocation(
                name=name,
                arguments=invocation.arguments,
                call_id=invocation.call_id,
                source=invocation.source,
                raw=invocation.raw,
            ),
            context,
        )

    async def _execute_builtin(
        self,
        invocation: ToolInvocation,
        context: ToolContext,
    ) -> ToolExecutionRecord:
        content = tool_content_for_invocation(invocation)
        if content is None:
            result = {"error": f"Could not normalize tool arguments for {invocation.name}", "exit_code": 1}
            return ToolExecutionRecord(invocation, f"{invocation.name}: ERROR", result)
        from src.tool_execution import _execute_text_tool_call_impl

        desc, result = await _execute_text_tool_call_impl(
            invocation.name,
            content,
            session_id=context.session_id,
            disabled_tools=context.disabled_tools,
            owner=context.owner,
            progress_cb=context.progress_cb,
            tool_policy=context.tool_policy,
        )
        if not isinstance(result, dict):
            result = {"output": str(result), "exit_code": 0}
        return ToolExecutionRecord(invocation, desc, result)


_REGISTRY: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ToolRegistry(_load_builtin_definitions())
    return _REGISTRY


def tool_content_for_invocation(invocation: ToolInvocation) -> Optional[str]:
    name = get_tool_registry().canonical_name(invocation.name)
    if invocation.source == "text":
        return "" if invocation.arguments is None else str(invocation.arguments)

    args = _decode_arguments(name, invocation.arguments)
    if args is None:
        return None

    if name in BUILTIN_EMAIL_TOOLS:
        return json.dumps(args) if args else "{}"
    if name.startswith("mcp__"):
        return json.dumps(args) if args else "{}"

    required_args = _REQUIRED_NATIVE_TOOL_ARGS.get(name)
    if required_args and not any(str(args.get(key) or "").strip() for key in required_args):
        logger.warning("Rejecting empty required arguments for tool call %s: %r", name, args)
        return None

    return _structured_args_to_text(name, args)


def display_action_for_invocation(invocation: ToolInvocation) -> Optional[TextToolAction]:
    registry = get_tool_registry()
    name = registry.canonical_name(invocation.name)
    if registry.get(name) is None and not name.startswith("mcp__"):
        logger.warning("Unknown tool call: %s", invocation.name)
        return None
    content = tool_content_for_invocation(
        ToolInvocation(
            name=name,
            arguments=invocation.arguments,
            call_id=invocation.call_id,
            source=invocation.source,
            raw=invocation.raw,
        )
    )
    if content is None:
        return None
    return TextToolAction(name, content)


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


def _decode_arguments(name: str, arguments: Any) -> Optional[Dict[str, Any]]:
    try:
        if not arguments or (isinstance(arguments, str) and not arguments.strip()):
            args: Any = {}
        elif isinstance(arguments, str):
            args = json.loads(arguments)
        else:
            args = arguments
    except (json.JSONDecodeError, TypeError):
        logger.error("Failed to parse tool call arguments for %s: %r", name, arguments)
        return None

    if not isinstance(args, dict):
        if name.startswith("mcp__email__") or name in BUILTIN_EMAIL_TOOLS:
            logger.warning("Non-object email tool arguments for %s: %r; rejecting", name, args)
            return None
        logger.warning("Non-object tool arguments for %s: %r; treating as empty", name, args)
        return {}
    return args


def _structured_args_to_text(name: str, args: Dict[str, Any]) -> str:
    if name == "bash":
        return str(args.get("command", ""))
    if name == "python":
        return str(args.get("code", ""))
    if name == "web_search":
        queries = args.get("queries")
        if isinstance(queries, list) and queries:
            query = str(queries[0])
        elif queries:
            query = str(queries)
        else:
            query = str(args.get("query", ""))
        tf = args.get("time_filter")
        if query and isinstance(tf, str) and tf in ("day", "week", "month", "year"):
            return json.dumps({"query": query, "time_filter": tf})
        return query
    if name == "web_fetch":
        return json.dumps(args) if args.get("full") or args.get("max_bytes") else str(args.get("url", ""))
    if name == "read_file":
        return json.dumps(args) if args.get("offset") or args.get("limit") else str(args.get("path", ""))
    if name in {"grep", "glob", "ls"}:
        return json.dumps(args) if args else "{}"
    if name == "get_workspace":
        return ""
    if name == "write_file":
        return str(args.get("path", "")) + "\n" + str(args.get("content", ""))
    if name == "edit_file":
        return json.dumps(args)
    if name == "create_document":
        parts = [str(args.get("title", "Untitled"))]
        if args.get("language"):
            parts.append(str(args["language"]))
        parts.append(str(args.get("content", "")))
        return "\n".join(parts)
    if name == "edit_document":
        blocks = []
        edits = args.get("edits", [])
        if not isinstance(edits, list):
            edits = []
        for edit in edits:
            if isinstance(edit, dict):
                blocks.append(
                    f'<<<FIND>>>\n{edit.get("find", "")}\n<<<REPLACE>>>\n{edit.get("replace", "")}\n<<<END>>>'
                )
        return "\n".join(blocks)
    if name == "suggest_document":
        blocks = []
        suggestions = args.get("suggestions", [])
        if not isinstance(suggestions, list):
            suggestions = []
        for suggestion in suggestions:
            if isinstance(suggestion, dict):
                blocks.append(
                    f'<<<FIND>>>\n{suggestion.get("find", "")}\n<<<SUGGEST>>>\n{suggestion.get("replace", "")}\n<<<REASON>>>\n{suggestion.get("reason", "")}\n<<<END>>>'
                )
        return "\n".join(blocks)
    if name == "update_document":
        return str(args.get("content", ""))
    if name == "search_chats":
        return str(args.get("query", ""))
    if name == "chat_with_model":
        return str(args.get("model", "")) + "\n" + str(args.get("message", ""))
    if name == "create_session":
        return str(args.get("name", "Untitled")) + "\n" + str(args.get("model", ""))
    if name == "list_sessions":
        return str(args.get("filter", ""))
    if name == "send_to_session":
        return str(args.get("session_id", "")) + "\n" + str(args.get("message", ""))
    if name == "pipeline":
        return json.dumps({"steps": args.get("steps", [])})
    if name == "manage_session":
        action = str(args.get("action", ""))
        value = str(args.get("value", ""))
        if action == "list":
            keyword = str(args.get("session_id", "") or args.get("keyword", "") or value)
            return "list" + (("\n" + keyword) if keyword and keyword.lower() != "current" else "")
        sid = str(args.get("session_id", "current"))
        content = action + "\n" + sid
        if value:
            content += "\n" + value
        return content
    if name == "manage_memory":
        action = str(args.get("action", ""))
        if action == "add":
            text = args.get("text") or args.get("value") or args.get("content") or ""
            if not text and args.get("key"):
                text = str(args.get("key") or "")
            content = "add\n" + str(text)
            if args.get("category"):
                content += "\n" + str(args["category"])
            elif args.get("key"):
                content += "\n" + str(args["key"])
            return content
        if action == "edit":
            return "edit\n" + str(args.get("memory_id", "")) + "\n" + str(args.get("text", ""))
        if action == "delete":
            return "delete\n" + str(args.get("memory_id", ""))
        if action == "search":
            return "search\n" + str(args.get("text") or args.get("tex") or args.get("query") or "")
        if action == "list":
            return "list" + (("\n" + str(args["category"])) if args.get("category") else "")
        return action
    if name == "list_models":
        return str(args.get("filter", ""))
    if name == "ui_control":
        return _ui_control_content(args)
    if name in {
        "manage_tasks", "manage_skills", "api_call", "manage_endpoints",
        "manage_mcp", "manage_webhooks", "manage_tokens", "manage_documents",
        "manage_settings", "manage_notes", "manage_calendar", "download_model",
        "serve_model", "list_served_models", "stop_served_model",
        "tail_serve_output", "list_downloads", "cancel_download",
        "search_hf_models", "list_cached_models", "app_api",
        "list_serve_presets", "serve_preset", "adopt_served_model",
        "list_cookbook_servers", "edit_image", "trigger_research",
        "manage_research", "resolve_contact", "manage_contact",
        "vault_search", "vault_get", "vault_unlock", "update_plan",
        "manage_bg_jobs",
    }:
        return json.dumps(args)
    if name == "ask_teacher":
        return str(args.get("model", "auto")) + "\n" + str(args.get("problem", ""))
    if name == "ask_user":
        return json.dumps(args, ensure_ascii=False)
    return json.dumps(args)


def _ui_control_content(args: Dict[str, Any]) -> str:
    action = str(args.get("action", ""))
    name = str(args.get("name", ""))
    value = str(args.get("value", ""))
    if action == "toggle":
        return f"toggle {name} {value}"
    if action == "open_panel":
        return f"open_panel {name or value}"
    if action == "open_email_reply":
        uid = args.get("uid") or name
        folder = args.get("folder") or value or "INBOX"
        mode = args.get("mode") or "reply"
        content = f"open_email_reply {uid} {folder} {mode}"
        body = args.get("body") or args.get("extra") or args.get("content") or ""
        if body:
            content += f" {body}"
        return content
    if action == "set_mode":
        return f"set_mode {value or name}"
    if action == "switch_model":
        return f"switch_model {value or name}"
    if action == "set_theme":
        return f"set_theme {value or name}"
    if action != "create_theme":
        return action
    colors = args.get("colors", {})
    colors = colors if isinstance(colors, dict) else {}
    theme_name = name or value or "custom"
    content = (
        f"create_theme {theme_name} {colors.get('bg', '#282c34')} "
        f"{colors.get('fg', '#9cdef2')} {colors.get('panel', '#111111')} "
        f"{colors.get('border', '#355a66')} {colors.get('accent', '#e06c75')}"
    )
    adv_keys = [
        "userBubbleBg", "aiBubbleBg", "bubbleBorder", "sidebarBg",
        "sectionAccent", "brandColor", "inputBg", "inputBorder",
        "sendBtnBg", "sendBtnHover", "codeBg", "codeFg",
        "toggleBg", "toggleActive", "accentPrimary", "accentError",
    ]
    for key in adv_keys:
        if colors.get(key):
            content += f" {key}={colors[key]}"
    return content


def _load_builtin_definitions() -> List[ToolDefinition]:
    definitions: List[ToolDefinition] = []
    seen: set[str] = set()
    try:
        from src.agent_tools import FUNCTION_TOOL_SCHEMAS, TOOL_TAGS
    except Exception as exc:
        logger.warning("Could not load built-in tool schemas: %s", exc)
        FUNCTION_TOOL_SCHEMAS = []
        TOOL_TAGS = set()
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
    for name in sorted(str(t) for t in TOOL_TAGS if str(t).strip()):
        if name in seen:
            continue
        seen.add(name)
        definitions.append(
            ToolDefinition(
                name=name,
                description="Text-only Odysseus tool",
                parameters={"type": "object", "properties": {}, "required": []},
                exposure="internal",
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
