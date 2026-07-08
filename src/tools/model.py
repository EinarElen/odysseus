from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Literal, Optional


ToolSource = Literal["openai", "subscription", "pi", "harness", "mcp", "legacy"]
ToolExposure = Literal["direct", "deferred", "hidden", "legacy_only"]
ToolExecutionMode = Literal["parallel", "sequential", "exclusive"]


@dataclass(frozen=True)
class ToolInvocation:
    """Provider-neutral request to run one Odysseus tool."""

    name: str
    arguments: Any = field(default_factory=dict)
    call_id: Optional[str] = None
    source: ToolSource = "legacy"
    raw: Any = None


@dataclass(frozen=True)
class ToolContext:
    """Execution context shared by provider, harness, and legacy callers."""

    session_id: Optional[str] = None
    owner: Optional[str] = None
    workspace: Optional[str] = None
    disabled_tools: Optional[set[str]] = None
    tool_policy: Any = None
    progress_cb: Any = None


@dataclass(frozen=True)
class ToolExecutionRecord:
    """Normalized result plus the legacy display description."""

    invocation: ToolInvocation
    description: str
    result: Dict[str, Any]

    @property
    def text(self) -> str:
        output = self.result.get("output")
        error = self.result.get("error")
        if output is not None:
            return str(output)
        if error is not None:
            return str(error)
        return self.description

    @property
    def is_error(self) -> bool:
        exit_code = self.result.get("exit_code")
        return bool(self.result.get("error")) or (isinstance(exit_code, int) and exit_code != 0)


NormalizeToolArguments = Callable[[ToolInvocation], Any]
ExecuteTool = Callable[[ToolInvocation, ToolContext], Awaitable[ToolExecutionRecord]]


@dataclass(frozen=True)
class ToolDefinition:
    """Canonical definition for one callable tool."""

    name: str
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    exposure: ToolExposure = "direct"
    execution_mode: ToolExecutionMode = "parallel"
    normalize: Optional[NormalizeToolArguments] = None
    execute: Optional[ExecuteTool] = None

    def openai_function_schema(self, *, strict: bool = False) -> Dict[str, Any]:
        function: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters or {"type": "object", "properties": {}}),
        }
        if strict:
            function["parameters"] = strict_json_schema(function["parameters"])
            function["strict"] = True
        return {"type": "function", "function": function}


def strict_json_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Return an OpenAI strict-mode object schema without mutating input."""

    def convert(node: Any, *, required_by_parent: bool = True) -> Any:
        if not isinstance(node, dict):
            return node
        out: Dict[str, Any] = {k: convert(v) for k, v in node.items() if k not in {"required", "additionalProperties"}}
        node_type = out.get("type")
        if node_type == "object" or "properties" in out:
            props = out.get("properties")
            if not isinstance(props, dict):
                props = {}
            required = set(node.get("required") or [])
            converted_props: Dict[str, Any] = {}
            for key, prop_schema in props.items():
                prop = convert(prop_schema, required_by_parent=key in required)
                if key not in required:
                    prop = _nullable_schema(prop)
                converted_props[key] = prop
            out["type"] = "object"
            out["properties"] = converted_props
            out["required"] = list(converted_props.keys())
            out["additionalProperties"] = False
        elif node_type == "array" and isinstance(out.get("items"), dict):
            out["items"] = convert(out["items"])
        return out

    return convert(schema)


def _nullable_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {"anyOf": [schema, {"type": "null"}]}
    if schema.get("type") == "null":
        return schema
    if "enum" in schema:
        return {"anyOf": [schema, {"type": "null"}]}
    if "anyOf" in schema:
        variants = list(schema.get("anyOf") or [])
        if not any(isinstance(v, dict) and v.get("type") == "null" for v in variants):
            variants.append({"type": "null"})
        out = dict(schema)
        out["anyOf"] = variants
        return out
    if "type" in schema:
        out = dict(schema)
        typ = out.get("type")
        if isinstance(typ, list):
            out["type"] = typ if "null" in typ else [*typ, "null"]
        else:
            out["type"] = [typ, "null"]
        return out
    return {"anyOf": [schema, {"type": "null"}]}
